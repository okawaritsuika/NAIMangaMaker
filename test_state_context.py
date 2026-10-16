"""Offline regressions for long-comic visual-state requests and saved responses."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from engine.iterative_comic import Workbench, read, save
from engine.prompt_management import prepare_request
from engine.story_context import compact_state_context


def panel(number=1, actor='actor1', appearance=None, held_objects=None):
    return dict(id='c' + format(number, '012x'), actor=actor,
        description=f'Alex pauses beside the garden table at moment {number}.',
        background='A sunlit garden table', dialogues=['The garden looks lovely.'],
        camera=dict(subjects=[actor], focus='Alex and the flowerpot',
                    framing='medium shot', angle='side view'),
        visual_states={actor: dict(appearance=appearance or ['adult gardener', 'green shirt', 'no shoes'],
            held_objects=held_objects or [], pose=f'looking toward flowerpot {number}')},
        origin=dict(operation='o' + format(number, '012x'), state_change=''),
        description_ko='정원의 탁자를 바라본다.', display_language='ko')


class StateContextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name) / 'operations' / 'o123456789abc'
        self.credentials = Mock(side_effect=AssertionError('No live credentials'))
        self.workbench = Workbench(temporary.name, key_provider=self.credentials)
        self.transport = self.enterContext(patch('urllib.request.urlopen',
            side_effect=AssertionError('No live network')))
        self.cast = {actor: dict(name=actor, appearance=['adult gardener', 'brown shoes'])
                     for actor in ('actor1', 'actor2')}
        self.calls = []

    def reply(self, folder, stage, task, **kwargs):
        self.assertEqual('state_resolution', stage)
        context = json.loads(task.rsplit('\nInput:\n', 1)[1])
        self.calls.append((task, context))
        return dict(panels=[dict(index=p['index'], visual_states=p['visual_states'])
                            for p in context['panels']])

    def prepare(self, prefix, raw=None):
        raw = raw or dict(panels=[panel(2001)])
        with patch.object(self.workbench, 'call', side_effect=self.reply):
            prepared = self.workbench.prepare(self.folder, raw, self.cast, prefix=prefix)
        self.credentials.assert_not_called()
        self.transport.assert_not_called()
        return prepared

    def test_thousand_panels_do_not_repeat_full_history_in_state_request(self):
        prefix = [panel(i) for i in range(1, 1001)]
        for row in prefix:
            row['unused_editor_metadata'] = 'unused ' * 500
        old_prefix = copy.deepcopy(prefix)
        raw = dict(panels=[panel(1001), panel(1002)])
        original = copy.deepcopy(raw)
        prepared = self.prepare(prefix, raw)
        task, context = self.calls[-1]
        self.assertEqual(original, raw)
        self.assertEqual(original, prepared)
        self.assertEqual(old_prefix, prefix)
        self.assertEqual([p['id'] for p in prefix[-8:]],
                         [p['id'] for p in context['accepted_prefix']])
        self.assertEqual(1, len(context['earlier_state_changes']))
        self.assertNotIn('unused_editor_metadata', task)
        self.assertNotIn('description_ko', task)
        # Even one token per UTF-8 byte plus the full output allowance fits the
        # reported 36,864-token window for this long-comic reproduction.
        self.assertLess(len(task.encode('utf-8')) + 4096 + 256, 36864)
        self.assertLess(len(task), len(json.dumps(prefix)) / 100)
        self.assertEqual(context, read(self.folder / 'state_context.json'))

    def test_old_offscreen_actor_and_actual_outfit_change_are_preserved(self):
        prefix = [panel(1, actor='actor2', appearance=['adult gardener', 'red hat'], held_objects=['book'])]
        prefix += [panel(i) for i in range(2, 31)]
        prefix[1]['origin']['state_change'] = 'Alex removes the shoes.'
        for row in prefix[5:]:
            row['visual_states']['actor1']['appearance'] = ['adult gardener', 'green shirt', 'blue boots']
        prefix[5]['description'] = 'Alex puts on blue boots.'
        self.prepare(prefix)
        context = self.calls[-1][1]
        self.assertEqual(prefix[0]['visual_states']['actor2'], context['latest_active_states']['actor2'])
        self.assertEqual(prefix[-1]['visual_states']['actor1'], context['latest_active_states']['actor1'])
        change = next(row for row in context['earlier_state_changes'] if row['id'] == prefix[5]['id'])
        self.assertEqual('Alex puts on blue boots.', change['description'])
        self.assertIn('blue boots', change['visual_states']['actor1']['appearance'])
        self.assertEqual('Alex removes the shoes.', context['persistent_state_requests'][0]['state_change'])
        self.assertNotIn('pose', change['visual_states']['actor1'])

    def test_requests_keep_order_deduplicate_and_exclude_future_insertions(self):
        prefix = [panel(i) for i in range(1, 31)]
        prefix[0]['origin']['state_change'] = 'Remove the shoes.'
        prefix[1]['origin'] = copy.deepcopy(prefix[0]['origin'])
        prefix[10]['origin']['state_change'] = 'Carry the book.'
        prefix[20]['origin']['state_change'] = 'Put the book down.'
        save(self.folder / 'input_en.json', dict(state_change='Wear a red hat.'))
        self.prepare(prefix[:20])
        context = self.calls[-1][1]
        self.assertEqual(['Remove the shoes.', 'Carry the book.'],
                         [r['state_change'] for r in context['persistent_state_requests']])
        self.assertEqual([1, 11], [r['prefix_index'] for r in context['persistent_state_requests']])
        self.assertEqual('Wear a red hat.', context['current_state_change'])
        self.assertEqual(prefix[19]['visual_states']['actor1'], context['latest_active_states']['actor1'])
        self.assertNotIn('Put the book down.', self.calls[-1][0])

    def test_legacy_state_requests_are_read_before_compaction(self):
        prefix = [panel(i) for i in range(1, 21)]
        operation = prefix[0]['origin']['operation']
        del prefix[0]['origin']['state_change']
        save(self.folder.parent / operation / 'input_en.json', dict(state_change='Keep the blue scarf.'))
        self.prepare(prefix)
        self.assertEqual('Keep the blue scarf.', self.calls[-1][1]['persistent_state_requests'][0]['state_change'])

    def test_empty_prefix_and_actorless_new_panel(self):
        raw = dict(panels=[dict(actor=None, description='A flowerpot rests on the table.',
            background='A garden', dialogues=[], camera=dict(subjects=[], focus='The flowerpot',
            framing='close-up', angle='side view'), visual_states={})])
        self.assertEqual(raw, self.prepare([], raw))
        context = self.calls[-1][1]
        self.assertEqual([], context['accepted_prefix'])
        self.assertEqual({}, context['latest_active_states'])
        self.assertEqual([], context['earlier_state_changes'])

    def test_compaction_is_idempotent(self):
        self.prepare([panel(i) for i in range(1, 20)])
        context = self.calls[-1][1]
        self.assertEqual(context, compact_state_context(context))

    def test_incomplete_offscreen_legacy_state_does_not_crash(self):
        prefix = [panel(i) for i in range(1, 20)]
        prefix[0]['visual_states']['actor2'] = dict(appearance=None, held_objects=None, pose=None)
        self.prepare(prefix)
        self.assertEqual(prefix[0]['visual_states']['actor2'], self.calls[-1][1]['latest_active_states']['actor2'])

    def test_verbose_recent_panels_have_a_size_budget(self):
        prefix = [panel(i) for i in range(1, 21)]
        for row in prefix:
            row['description'] += ' Visible garden detail.' * 200
        self.prepare(prefix)
        context = self.calls[-1][1]
        self.assertLess(len(context['accepted_prefix']), 8)
        self.assertEqual(prefix[-1]['id'], context['accepted_prefix'][-1]['id'])
        self.assertEqual(prefix[-1]['description'], context['accepted_prefix'][-1]['description'])
        self.assertLessEqual(len(json.dumps(context['accepted_prefix'], ensure_ascii=False).encode('utf-8')), 16000)

    def test_frequent_prop_and_outfit_changes_do_not_accumulate_old_states(self):
        prefix = [panel(i, appearance=['adult gardener', 'shirt ' + str(i)],
                        held_objects=['book' if i % 2 else 'cup']) for i in range(1, 1001)]
        self.prepare(prefix)
        task, context = self.calls[-1]
        self.assertEqual(1, len(context['earlier_state_changes']))
        self.assertEqual(prefix[-1]['visual_states']['actor1'], context['latest_active_states']['actor1'])
        self.assertLess(len(task.encode('utf-8')) + 4096 + 256, 36864)

    def test_active_boots_evidence_survives_later_coat_change_and_dropped_prop(self):
        prefix = [panel(i, appearance=['adult gardener', 'green shirt', 'blue boots'], held_objects=['book'])
                  for i in range(1, 31)]
        prefix[0]['description'] = 'Alex puts on blue boots.'
        for row in prefix[5:]:
            row['visual_states']['actor1']['appearance'].append('red coat')
        prefix[5]['description'] = 'Alex puts on a red coat.'
        for row in prefix[10:]:
            row['visual_states']['actor1']['held_objects'] = []
        prefix[10]['description'] = 'Alex sets the book on the table.'
        self.prepare(prefix)
        context = self.calls[-1][1]
        evidence = {row['id']: row for row in context['earlier_state_changes']}
        self.assertEqual(prefix[0]['description'], evidence[prefix[0]['id']]['description'])
        self.assertEqual(prefix[5]['description'], evidence[prefix[5]['id']]['description'])
        self.assertEqual([1, 6, 11], [row['prefix_index'] for row in context['earlier_state_changes']])
        self.assertEqual([], evidence[prefix[10]['id']]['visual_states']['actor1']['held_objects'])

    def test_changed_raw_cannot_overwrite_saved_state_source(self):
        raw = dict(panels=[panel(21)])
        self.prepare([], raw)
        changed = copy.deepcopy(raw)
        changed['panels'][0]['description'] = 'Alex walks away.'
        with self.assertRaisesRegex(ValueError, '최초 요청'):
            self.prepare([], changed)
        self.assertEqual(raw, read(self.folder / 'state_resolution_source.json'))

    def test_saved_success_reuses_old_request_without_new_generation(self):
        raw = dict(panels=[panel(21)])
        context = dict(cast=self.cast, accepted_prefix=[panel(i) for i in range(1, 21)],
            latest_active_states=panel(20)['visual_states'], persistent_state_requests=[],
            current_state_change='', panels=[dict(index=1, **raw['panels'][0])])
        task = 'Legacy state instructions\nInput:\n' + json.dumps(context, ensure_ascii=False)
        request = dict(model='glm-4-6', temperature=.25, max_tokens=4096,
            enable_thinking=False, stream=True, messages=[dict(role='system', content='You are a helpful assistant.'),
                                                        dict(role='user', content=task)])
        prepared = prepare_request(self.workbench, self.folder, 'state_resolution', request)
        save(self.folder / 'state_resolution_request.json', prepared)
        save(self.folder / 'state_context.json', context)
        target = self.folder / 'state_resolution'
        save(target / 'result.json', dict(http_status=200, finish_reason='stop'))
        (target / 'story.txt').write_text(json.dumps(dict(panels=[dict(index=1,
            visual_states=raw['panels'][0]['visual_states'])])), encoding='utf-8')
        with patch('engine.iterative_comic.generate', side_effect=AssertionError('Must reuse saved success')) as generate:
            actual = self.workbench.prepare(self.folder, raw, self.cast, prefix=[panel(i) for i in range(1, 21)])
        generate.assert_not_called()
        self.credentials.assert_not_called()
        self.assertEqual(raw, actual)
        self.assertEqual(request, read(self.folder / 'state_resolution_request.json'))
        self.assertEqual(context, read(self.folder / 'state_context.json'))

        # Saved responses must not be applied to a different scene or actor state.
        for field in ('cast', 'prefix', 'raw', 'evidence'):
            with self.subTest(changed=field):
                changed_cast = copy.deepcopy(self.cast)
                changed_prefix = [panel(i) for i in range(1, 21)]
                changed_raw = copy.deepcopy(raw)
                if field == 'cast':
                    changed_cast['actor1']['appearance'] = ['red shirt']
                elif field == 'prefix':
                    changed_prefix[-1]['visual_states']['actor1']['held_objects'] = ['book']
                elif field == 'evidence':
                    changed_prefix[0]['description'] = 'Alex puts on shoes.'
                else:
                    changed_raw['panels'][0]['description'] = 'Alex puts on a red shirt.'
                with patch('engine.iterative_comic.generate', side_effect=AssertionError('Must reject changed inputs')):
                    with self.assertRaisesRegex(ValueError, '최초 요청'):
                        self.workbench.prepare(self.folder, changed_raw, changed_cast, prefix=changed_prefix)
                self.assertEqual(request, read(self.folder / 'state_resolution_request.json'))
                self.assertEqual(raw, read(self.folder / 'state_resolution_source.json'))


if __name__ == '__main__':
    unittest.main()
