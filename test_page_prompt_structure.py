import copy
import tempfile
import unittest
from pathlib import Path

from engine.iterative_comic_render import build_settings, payload, SINGLE_VIEW_RENDER_VERSION
from engine.image_preferences import validate
from test_story_fixture import DemoWorkbench


class PagePromptStructureTests(unittest.TestCase):
    def test_style_first_unweighted_layout_and_views_only_for_multiple_panels(self):
        with tempfile.TemporaryDirectory() as folder:
            wb = DemoWorkbench(Path(folder))
            project = wb.create({'seed': 'A gardener plants flowers', 'count': 2})
            project['panels'] = [dict(copy.deepcopy(project['panels'][i % 2]), id=f'panel{i}') for i in range(5)]
            style = '1.4::ink::, paper texture'
            for count in (1, 2, 5):
                with self.subTest(count=count):
                    page = {'id': 'page1', 'panel_ids': [p['id'] for p in project['panels'][:count]],
                            'image_settings': validate({'style_prompt': style, 'color_mode': 'prompt'})}
                    settings, _ = build_settings(project, page)
                    prompt = settings['prompt']
                    self.assertTrue(prompt.startswith(style + ', '))
                    self.assertNotIn('2::comic page', prompt)
                    self.assertNotIn('2::single illustration', prompt)
                    self.assertEqual(prompt.count('multiple views'), int(count > 1))
                    if count > 1:
                        self.assertIn(f'comic page, exactly {count} panels', prompt)
                        self.assertIn('clear panel borders, multiple views', prompt)
                    else:
                        self.assertIn('single illustration, one continuous scene, undivided composition', prompt)
                        self.assertNotIn('comic page', prompt)
                    request = payload(settings)
                    self.assertEqual(request['input'], prompt)
                    self.assertEqual(request['parameters']['v4_prompt']['caption']['base_caption'], prompt)
                    self.assertIn('2::focus ', settings['v4_prompt']['caption']['char_captions'][0]['char_caption'])

    def test_previous_render_version_keeps_original_layout_for_saved_images(self):
        with tempfile.TemporaryDirectory() as folder:
            wb = DemoWorkbench(Path(folder))
            project = wb.create({'seed': 'A gardener plants flowers', 'count': 2})
            for count in (1, 2):
                page = {'id': 'page1', 'panel_ids': [p['id'] for p in project['panels'][:count]],
                        'image_settings': validate({'style_prompt': 'ink', 'color_mode': 'prompt'})}
                settings, audit = build_settings(project, page, render_version=SINGLE_VIEW_RENDER_VERSION)
                prompt = settings['prompt']
                self.assertTrue(prompt.startswith('2::single illustration' if count == 1 else '2::comic page'))
                self.assertIn('::, ink', prompt)
                self.assertNotIn('multiple views', prompt)
                self.assertEqual(audit['render_version'], SINGLE_VIEW_RENDER_VERSION)


if __name__ == '__main__':
    unittest.main()
