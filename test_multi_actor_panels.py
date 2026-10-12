"""Offline regressions for group scenes, editing, and image prompt previews."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from engine import image_studio
from engine.iterative_comic import Workbench
from engine.iterative_comic_render import build_settings, render_page


class MultiActorPanelTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.credentials = Mock(side_effect=AssertionError('No credentials in offline tests'))
        self.workbench = Workbench(Path(temporary.name), key_provider=self.credentials)
        self.transport = self.enterContext(patch('urllib.request.urlopen',
            side_effect=AssertionError('No network in group-scene tests')))
        self.model = self.enterContext(patch.object(self.workbench, 'call',
            side_effect=AssertionError('Unexpected model call in offline test')))

    def tearDown(self):
        self.credentials.assert_not_called()
        self.transport.assert_not_called()

    def project(self, count=3, panel_count=1):
        cast = {'actor' + str(i): dict(name='Person ' + str(i), personality='Friendly',
            appearance=['adult person', 'baseline coat ' + str(i)]) for i in range(1, count + 1)}
        subjects = list(cast)
        panels = []
        for index in range(panel_count):
            panel = dict(id='c' + format(index + 1, '012x'), actor=subjects[-1],
                description='The friends gather around a garden table.',
                background='A sunlit garden with a wooden table',
                dialogues=['We are all here.'], dialogues_ko=['우리 모두 여기 있네.'],
                camera=dict(subjects=subjects[:], focus='The gathered friends',
                    framing='wide shot', angle='eye level'),
                visual_states={actor: dict(appearance=['adult person', 'active jacket ' + str(i)],
                    held_objects=['flowerpot ' + str(i)], pose='leaning toward flowerpot ' + str(i))
                    for i, actor in enumerate(subjects, 1)}, importance='normal',
                display_language='en', dialogue_language='ko')
            panel.update(description_ko=panel['description'], background_ko=panel['background'],
                camera_ko=copy.deepcopy(panel['camera']), state_ko='The friends hold flowerpots.')
            panels.append(panel)
        page = dict(id='g000000000001', panel_ids=[panel['id'] for panel in panels],
            layout='auto', width=832, height=1216, seed=123, renders=[], stale=False)
        project = dict(id='p000000000001', cast=cast, setting='A garden', direction='Meet friends',
            panels=panels, pages=[page], deleted_pages=[], operations=[], panel_versions={},
            revision=1, generation_options=dict(translation_mode='dialogue'))
        self.workbench.commit(project)
        return project, page

    def assert_group_prompt(self, project, page):
        before = copy.deepcopy(project)
        settings, audit = build_settings(project, page)
        self.assertEqual(before, project)
        positives = settings['v4_prompt']['caption']['char_captions']
        negatives = settings['v4_negative_prompt']['caption']['char_captions']
        expected_count = sum(len(panel['camera']['subjects']) for panel in project['panels'])
        self.assertEqual(expected_count, len(positives))
        self.assertEqual(expected_count, len(negatives))
        for index, panel in enumerate(project['panels']):
            rows = [(positive, negative, actor) for positive, negative, actor
                    in zip(positives, negatives, audit['actors']) if actor['panel_id'] == panel['id']]
            self.assertEqual(panel['camera']['subjects'], [row[2]['actor'] for row in rows])
            x, y, width = audit['layout']['centers'][index]
            centers = []
            for positive, negative, actor in rows:
                actor_id = actor['actor']
                state = panel['visual_states'][actor_id]
                text = positive['char_caption']
                self.assertEqual(state, actor['visual_state'])
                self.assertEqual(state['appearance'], actor['appearance'])
                for attribute in state['appearance']:
                    self.assertIn(attribute, text)
                self.assertIn(state['pose'], text)
                self.assertIn('holding ' + state['held_objects'][0], text)
                self.assertNotIn(project['cast'][actor_id]['appearance'][-1], text)
                speaks = actor_id == panel['actor']
                self.assertEqual(int(speaks), actor['dialogue_count'])
                self.assertEqual(speaks, panel['dialogues_ko'][0] in text)
                self.assertEqual(speaks, 'speech bubble' in text)
                self.assertNotIn('solo', text)
                self.assertNotIn('multiple people', negative['char_caption'])
                self.assertEqual(positive['centers'], negative['centers'])
                center = positive['centers'][0]
                self.assertGreater(center['x'], x - width / 2)
                self.assertLess(center['x'], x + width / 2)
                self.assertAlmostEqual(y, center['y'])
                self.assertTrue(0 < center['x'] < 1 and 0 < center['y'] < 1)
                centers.append((center['x'], center['y']))
            self.assertEqual(len(rows), len(set(centers)))
        return settings, audit

    def test_groups_of_three_through_ten_people_validate_and_render(self):
        for count in (3, 4, 5, 6, 10):
            with self.subTest(count=count):
                project, page = self.project(count)
                self.assertEqual(project['panels'],
                    self.workbench.validate(dict(panels=project['panels']), project['cast'], 1))
                self.assert_group_prompt(project, page)

    def test_group_coordinates_remain_inside_each_panel(self):
        for count in (3, 4, 5, 6, 10):
            for layout in ('auto', 'top', 'middle', 'bottom'):
                with self.subTest(count=count, layout=layout):
                    project, page = self.project(count, panel_count=3)
                    page['layout'] = layout
                    self.assert_group_prompt(project, page)

    def test_two_people_keep_existing_relative_positions(self):
        project, page = self.project(2, panel_count=3)
        settings, audit = self.assert_group_prompt(project, page)
        captions = settings['v4_prompt']['caption']['char_captions']
        for index in range(3):
            x, y, width = audit['layout']['centers'][index]
            for offset, expected in enumerate((x - .16 * width, x + .16 * width)):
                center = captions[index * 2 + offset]['centers'][0]
                self.assertAlmostEqual(expected, center['x'])
                self.assertAlmostEqual(y, center['y'])

    def test_one_person_keeps_solo_constraint(self):
        project, page = self.project(1)
        settings, audit = build_settings(project, page)
        positive = settings['v4_prompt']['caption']['char_captions'][0]
        negative = settings['v4_negative_prompt']['caption']['char_captions'][0]
        self.assertIn('solo', positive['char_caption'])
        self.assertIn('multiple people, duplicate person', negative['char_caption'])
        self.assertTrue(audit['actors'][0]['single_visible_actor_constraint'])
        x, y, _ = audit['layout']['centers'][0]
        self.assertEqual([dict(x=x, y=y)], positive['centers'])

    def test_actorless_panel_remains_valid_and_has_no_people_or_dialogue(self):
        project, page = self.project()
        panel = project['panels'][0]
        panel.update(actor=None, dialogues=[], dialogues_ko=[], visual_states={},
            description='Flowerpots rest on the table.')
        panel['camera'].update(subjects=[], focus='The flowerpots')
        self.workbench.validate(dict(panels=[panel]), project['cast'], 1)
        settings, audit = build_settings(project, page)
        positive = settings['v4_prompt']['caption']['char_captions'][0]
        negative = settings['v4_negative_prompt']['caption']['char_captions'][0]
        self.assertEqual(1, len(audit['actors']))
        self.assertIsNone(audit['actors'][0]['actor'])
        self.assertIn('objects and setting only', positive['char_caption'])
        self.assertNotIn('speech bubble', positive['char_caption'])
        self.assertIn('people, person, human, character', negative['char_caption'])

    def test_unknown_and_duplicate_subjects_are_rejected(self):
        for subjects in (['actor1', 'actor2', 'unknown'], ['actor1', 'actor2', 'actor2']):
            with self.subTest(subjects=subjects):
                project, page = self.project()
                project['panels'][0]['camera']['subjects'] = subjects
                with self.assertRaises(ValueError):
                    self.workbench.validate(dict(panels=project['panels']), project['cast'], 1)
                with self.assertRaises(ValueError):
                    build_settings(project, page)

    def test_group_panel_replacement_preserves_history_and_marks_page_stale(self):
        for count in (3, 4, 5, 10):
            with self.subTest(count=count):
                project, page = self.project(count)
                original = copy.deepcopy(project['panels'][0])
                candidate = copy.deepcopy(original)
                candidate['camera']['angle'] = 'high angle'
                folder = self.workbench.operation(project['id'], dict(operation='test_replacement'))
                result = self.workbench._replace_panel(project, 0, candidate, folder, 'edit')
                self.assertEqual(candidate, result['panels'][0])
                self.assertEqual(original, result['panel_versions'][original['id']][0]['panel'])
                self.assertTrue(page['stale'])
                self.assertEqual(2, result['revision'])
                self.assert_group_prompt(result, page)

    def test_public_edit_preserves_all_visible_people_and_states(self):
        for count in (3, 4, 5, 10):
            with self.subTest(count=count):
                project, _ = self.project(count)
                original = project['panels'][0]
                with patch.object(self.workbench, 'english', return_value=dict(camera_ko=dict(angle='high angle'))):
                    result = self.workbench.edit_panel(project['id'], original['id'],
                        dict(camera_ko=dict(angle='높은 시점')))
                edited = result['panels'][0]
                self.assertEqual('high angle', edited['camera']['angle'])
                self.assertEqual(original['camera']['subjects'], edited['camera']['subjects'])
                self.assertEqual(original['visual_states'], edited['visual_states'])
                self.assert_group_prompt(result, result['pages'][0])

    def test_public_camera_reroll_keeps_group_and_dialogue_only_translation(self):
        for count in (3, 4, 5, 10):
            with self.subTest(count=count):
                project, _ = self.project(count)
                original = project['panels'][0]
                # Same-instant reroll must retain all anchor subjects even if the
                # camera model mistakenly returns only one actor.
                camera = dict(original['camera'], subjects=['actor1'], angle='high angle')
                self.model.side_effect = [dict(camera=camera, dialogues=original['dialogues']),
                    dict(panels=[dict(id=original['id'], dialogues_ko=original['dialogues_ko'])])]
                result = self.workbench.reroll_panel(project['id'], original['id'],
                    dict(mode='camera', dialogue='with', instruction='Show the group from above'))
                edited = result['panels'][0]
                self.assertEqual(original['camera']['subjects'], edited['camera']['subjects'])
                self.assertEqual(original['visual_states'], edited['visual_states'])
                self.assertEqual(original['description'], edited['description_ko'])
                self.assertEqual(original['dialogues_ko'], edited['dialogues_ko'])
                self.assertEqual('en', edited['display_language'])
                self.assertEqual('ko', edited['dialogue_language'])
                self.assert_group_prompt(result, result['pages'][0])

    def test_image_studio_and_public_preview_expose_every_group_member(self):
        for count in (3, 4, 5, 6, 10):
            with self.subTest(count=count):
                project, page = self.project(count)
                preview = self.workbench.preview_prompt(project['id'], page['id'])
                studio = image_studio.get(self.workbench, project['id'], page['id'])
                settings, audit = build_settings(project, page)
                for form in (preview, studio['prompt'], image_studio.form(settings, audit)):
                    self.assertEqual(list(project['cast']), [row['actor'] for row in form['characters']])
                    self.assertEqual(count, len({tuple(row['centers'][0].values()) for row in form['characters']}))
                    self.assertEqual(list(range(count)), [row['source_index'] for row in form['characters']])
                self.assertEqual(count, len(studio['character_slots']))

    @staticmethod
    def character_rows(count):
        return [dict(id='actor' + str(index), gender='auto', name='Person ' + str(index),
            personality='Friendly', appearance='adult person, baseline coat ' + str(index))
            for index in range(1, count + 1)]

    def create_group(self, count, *, auto_cast=False):
        fixture, _ = self.project(count)
        rows = self.character_rows(count)

        def reply(folder, stage, task, **kwargs):
            if stage == 'cast_plan':
                return dict(characters=copy.deepcopy(rows))
            if stage == 'story':
                return dict(title='Friends in the garden', setting=fixture['setting'],
                    cast=copy.deepcopy(fixture['cast']), panels=copy.deepcopy(fixture['panels']),
                    ended=False, conflict=None)
            context = json.loads(task.rsplit('\n', 1)[-1])
            if stage == 'state_resolution':
                return dict(panels=[dict(index=row['index'], visual_states=row['visual_states'])
                    for row in context['panels']])
            if stage == 'output_translation':
                return dict(panels=[dict(id=row['id'], dialogues_ko=['우리 모두 여기 있네.'])
                    for row in context['panels']])
            raise AssertionError('Unexpected model stage: ' + stage)

        self.model.side_effect = reply
        return self.workbench.create(dict(seed='A gathering of ' + str(count) + ' adult friends',
            direction='Meet friends', place='A garden', characters=[] if auto_cast else rows,
            _auto_prepare_cast=auto_cast, count=1, dialogue='with',
            generation_options=dict(translation_mode='dialogue')))

    def test_character_settings_accept_five_and_ten_people_without_truncating(self):
        for count in (5, 10):
            with self.subTest(count=count):
                rows = self.character_rows(count)
                self.assertEqual(rows, self.workbench.characters(rows))
                self.assertEqual(rows, self.workbench.characters(rows,
                    existing={row['id']: {} for row in rows}))

    def test_create_and_settings_preserve_five_and_ten_people(self):
        for count in (5, 10):
            with self.subTest(count=count):
                project = self.create_group(count)
                actor_ids = ['actor' + str(index) for index in range(1, count + 1)]
                self.assertEqual(actor_ids, list(project['cast']))
                self.assertEqual(actor_ids, [row['id'] for row in project['characters_ko']])
                self.assertEqual(actor_ids, project['panels'][0]['camera']['subjects'])
                self.assertEqual(set(actor_ids), set(project['panels'][0]['visual_states']))
                rows = copy.deepcopy(project['characters_ko'])
                rows[-1].update(personality='Kind organizer', appearance='adult person, silver coat')
                before_panels = copy.deepcopy(project['panels'])
                changed = self.workbench.settings(project['id'], dict(direction=project['direction_ko'],
                    place=project['place_ko'], characters=rows))
                self.assertEqual(actor_ids, list(changed['cast']))
                self.assertEqual(rows, changed['characters_ko'])
                self.assertEqual('Kind organizer', changed['cast'][actor_ids[-1]]['personality'])
                self.assertEqual(['adult person', 'silver coat'], changed['cast'][actor_ids[-1]]['appearance'])
                self.assertEqual(before_panels, changed['panels'])
                self.assertEqual(changed, self.workbench.load(project['id']))
                changed = self.workbench.page(project['id'], dict(panel_ids=[changed['panels'][0]['id']]))
                self.assert_group_prompt(changed, changed['pages'][0])

    def test_automatic_cast_plan_preserves_five_and_ten_requested_people(self):
        for count in (5, 10):
            with self.subTest(count=count):
                project = self.create_group(count, auto_cast=True)
                actor_ids = ['actor' + str(index) for index in range(1, count + 1)]
                self.assertEqual(actor_ids, list(project['cast']))
                self.assertEqual(actor_ids, [row['id'] for row in project['characters_ko']])
                self.assertEqual(actor_ids, project['panels'][0]['camera']['subjects'])
                self.assertEqual(set(actor_ids), set(project['panels'][0]['visual_states']))
                self.assertEqual(project, self.workbench.load(project['id']))

    def test_twenty_two_people_fit_the_image_character_prompt_limit(self):
        project, page = self.project(22)
        settings, _ = self.assert_group_prompt(project, page)
        self.assertEqual(22, len(settings['v4_prompt']['caption']['char_captions']))
        folder = self.workbench.folder(project['id']) / 'renders' / 'twenty_two_people'
        with patch('engine.iterative_comic_render.ensure_allowance') as allowance:
            # Reaching the key check proves the maximum-size scene passed the
            # character limit without requiring credentials or an image request.
            with self.assertRaisesRegex(ValueError, 'NovelAI API key is required'):
                render_page(project, page, folder, None)
        allowance.assert_not_called()

    def test_over_limit_page_can_be_previewed_but_fails_before_allowance(self):
        for count, panel_count in ((23, 1), (12, 2)):
            with self.subTest(count=count, panel_count=panel_count):
                project, page = self.project(count, panel_count)
                settings, _ = self.assert_group_prompt(project, page)
                expected = count * panel_count
                self.assertEqual(expected, len(settings['v4_prompt']['caption']['char_captions']))
                studio = image_studio.get(self.workbench, project['id'], page['id'])
                self.assertEqual(expected, len(studio['prompt']['characters']))
                self.assertEqual(expected, len(studio['character_slots']))
                folder = self.workbench.folder(project['id']) / 'renders' / ('over_limit_' + str(expected))
                with patch('engine.iterative_comic_render.ensure_allowance',
                           side_effect=AssertionError('Over-limit scenes must not check allowance')) as allowance:
                    with self.assertRaises(ValueError) as error:
                        render_page(project, page, folder, 'offline-test-key')
                self.assertIn('최대 22개', str(error.exception))
                self.assertIn('현재 페이지에는 ' + str(expected) + '개', str(error.exception))
                self.assertIn('같은 인물도 컷마다 별도 계산', str(error.exception))
                allowance.assert_not_called()
                self.assertFalse((folder / 'request.json').exists())

    def test_prompt_overrides_reduce_actual_render_count_below_limit(self):
        for count, panel_count, remaining in ((23, 1, 22), (12, 2, 20)):
            with self.subTest(count=count, panel_count=panel_count, remaining=remaining):
                project, page = self.project(count, panel_count)
                settings, audit = build_settings(project, page)
                form = image_studio.form(settings, audit)
                page['prompt_overrides'] = dict(prompt=form['prompt'], negative_prompt=form['negative_prompt'],
                    characters=[{key: row[key] for key in ('source_index', 'prompt', 'negative_prompt', 'centers')}
                        for row in form['characters'][:remaining]])
                page['prompt_overrides_source_sha256'] = audit['prompt_source_sha256']
                trimmed, trimmed_audit = build_settings(project, page)
                self.assertEqual(remaining, len(trimmed['v4_prompt']['caption']['char_captions']))
                self.assertEqual(remaining, len(trimmed['v4_negative_prompt']['caption']['char_captions']))
                self.assertEqual(list(range(remaining)), [row['source_index'] for row in trimmed_audit['actors']])
                folder = self.workbench.folder(project['id']) / 'renders' / ('trimmed_' + str(remaining))
                # Stop at the next boundary, before any account or image call.
                with patch('engine.iterative_comic_render.ensure_allowance',
                           side_effect=RuntimeError('Offline allowance boundary')) as allowance:
                    with self.assertRaisesRegex(RuntimeError, 'Offline allowance boundary'):
                        render_page(project, page, folder, 'offline-test-key')
                allowance.assert_called_once()
                actual = allowance.call_args.kwargs['settings']
                self.assertEqual(remaining, len(actual['v4_prompt']['caption']['char_captions']))
                self.assertFalse((folder / 'request.json').exists())


if __name__ == '__main__':
    unittest.main()
