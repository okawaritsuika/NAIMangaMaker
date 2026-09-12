import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_story_fixture import DemoWorkbench
from engine.iterative_comic_render import build_settings, payload, validate_prompt_overrides
from engine.iterative_page_rendering import _set_overrides
from engine import image_studio


class CharacterPromptDeletion(unittest.TestCase):
    def test_remaining_slot_identity_request_and_empty_list(self):
        with tempfile.TemporaryDirectory() as folder:
            wb = DemoWorkbench(Path(folder))
            p = wb.create(dict(seed='A gardener plants flowers', count=2))
            p = wb.page(p['id'], dict(panel_ids=[r['id'] for r in p['panels']]))
            page = p['pages'][0]
            before = wb.preview_prompt(p['id'], page['id'])
            self.assertEqual(len(before['characters']), 2)
            kept = before['characters'][1]
            overrides = dict(prompt=before['prompt'], negative_prompt=before['negative_prompt'],
                characters=[dict(source_index=1, prompt='adult gardener smiling', negative_prompt='blurry', centers=kept['centers'])])
            _set_overrides(p, page, overrides)
            wb.commit(p)
            settings, audit = build_settings(p, page)
            params = payload(settings)['parameters']
            self.assertEqual(len(params['v4_prompt']['caption']['char_captions']), 1)
            self.assertEqual(len(params['v4_negative_prompt']['caption']['char_captions']), 1)
            self.assertEqual(audit['actors'][0]['panel_id'], kept['panel_id'])
            view = image_studio.get(wb, p['id'], page['id'])
            self.assertEqual(view['prompt']['characters'][0]['source_index'], 1)
            self.assertEqual(len(view['character_slots']), 2)
            request = dict(source_sha256=view['source_sha256'], prompt_overrides=overrides,
                image_settings={k:before[k] for k in ('width','height','steps','scale','cfg_rescale','sampler','noise_schedule')})
            with patch('engine.iterative_page_rendering.render_project_page', return_value={}) as renderer:
                image_studio.render(wb, p['id'], page['id'], request)
                self.assertEqual(renderer.call_args.kwargs['payload']['prompt_overrides']['characters'][0]['source_index'], 1)
            empty = dict(copy.deepcopy(overrides), characters=[])
            _set_overrides(p, page, empty)
            settings, audit = build_settings(p, page)
            self.assertEqual(settings['v4_prompt']['caption']['char_captions'], [])
            self.assertEqual(settings['v4_negative_prompt']['caption']['char_captions'], [])
            self.assertEqual(audit['actors'], [])

    def test_invalid_and_duplicate_indices_rejected(self):
        row = dict(prompt='adult gardener', negative_prompt='')
        for rows in [[dict(row, source_index=True)], [dict(row, source_index=2)],
                     [dict(row, source_index=1), dict(row, source_index=1)], [row]]:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_prompt_overrides(dict(prompt='garden', negative_prompt='', characters=rows), 2)
        legacy = validate_prompt_overrides(dict(prompt='garden', negative_prompt='', characters=[row, row]), 2)
        self.assertEqual([r['source_index'] for r in legacy['characters']], [0, 1])


if __name__ == '__main__':
    unittest.main()
