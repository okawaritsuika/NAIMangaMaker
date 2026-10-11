"""Offline checks for V5 Medium requests, saved settings, and studio editing."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import image_cost, image_preferences, image_studio
from engine.iterative_comic import save
from engine.iterative_comic_render import build_settings, payload
from test_story_fixture import DemoWorkbench


HIGH = 'nai-diffusion-5-full'
MEDIUM = 'nai-diffusion-5-full-medium'
HEAVY_NEGATIVE = ('lowres, artistic error, film grain, scan artifacts, worst quality, '
    'bad quality, jpeg artifacts, very displeasing, chromatic aberration, dithering, '
    'halftone, screentone, multiple views, logo, too many watermarks, negative space, blank page')


class ImageMediumTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workbench = DemoWorkbench(Path(temporary.name))
        transport = patch('urllib.request.urlopen',
            side_effect=AssertionError('Medium regression tests must never use the network'))
        self.transport = transport.start()
        self.addCleanup(transport.stop)

    def page(self):
        project = self.workbench.create(dict(seed='Tend a small garden', count=1, dialogue='none'))
        project = self.workbench.page(project['id'], dict(panel_ids=[project['panels'][0]['id']]))
        return project, project['pages'][0]

    @staticmethod
    def raw_settings(model=MEDIUM, prompt='A sunny garden'):
        centers = [dict(x=.3, y=.7)]
        return dict(model=model, prompt=prompt, uc='user global exclusion', seed=123,
            width=832, height=1216, steps=48, scale=5, cfg_rescale=.7,
            sampler='k_dpmpp_2m', noise_schedule='native', sm=True, sm_dyn=True,
            skip_cfg_above_sigma=19, tag_hint_uc_preset=0,
            v4_prompt=dict(caption=dict(base_caption=prompt,
                char_captions=[dict(char_caption='An adult gardener', centers=centers)]),
                use_coords=True, use_order=True),
            v4_negative_prompt=dict(caption=dict(base_caption='user global exclusion',
                char_captions=[dict(char_caption='user character exclusion', centers=centers)]),
                legacy_uc=False))

    def test_legacy_defaults_stay_high_and_expose_both_models(self):
        public = image_preferences.public(self.workbench)
        self.assertEqual(HIGH, public['settings']['model'])
        choices = {row['id']: row for row in public['models']}
        self.assertEqual({HIGH, MEDIUM}, set(choices))
        self.assertEqual('high', choices[HIGH]['effort'])
        self.assertEqual('medium', choices[MEDIUM]['effort'])
        self.assertTrue(all(row['label'] for row in choices.values()))
        legacy = dict(public['settings'], steps=48, negative_prompt='preserved custom exclusions')
        legacy.pop('model')
        save(image_preferences.path(self.workbench), dict(settings=legacy))
        loaded = image_preferences.load(self.workbench)
        self.assertEqual(dict(legacy, model=HIGH), loaded)
        for invalid in ('unknown-model', '', None, 5):
            with self.subTest(model=invalid), self.assertRaises(ValueError):
                image_preferences.validate(dict(model=invalid))

    def test_medium_defaults_round_trip_keep_custom_negative_text(self):
        incoming = dict(model=MEDIUM, steps=48, cfg_rescale=.7, sampler='k_dpmpp_2m',
            noise_schedule='native', negative_prompt='preserved custom exclusions')
        original = copy.deepcopy(incoming)
        public = image_preferences.store(self.workbench, dict(settings=incoming))
        expected = public['settings']
        self.assertEqual(MEDIUM, expected['model'])
        self.assertEqual((14, 'k_euler_ancestral', 0, 'karras'),
            tuple(expected[key] for key in ('steps', 'sampler', 'cfg_rescale', 'noise_schedule')))
        self.assertEqual('preserved custom exclusions', expected['negative_prompt'])
        self.assertEqual(expected, image_preferences.load(DemoWorkbench(self.workbench.directory)))
        self.assertEqual(original, incoming)
        high = image_preferences.store(self.workbench,
            dict(settings=dict(model=HIGH, steps=48, cfg_rescale=.7)))['settings']
        self.assertEqual((HIGH, 48, .7, 'preserved custom exclusions'),
            tuple(high[key] for key in ('model', 'steps', 'cfg_rescale', 'negative_prompt')))

    def test_medium_payload_uses_fixed_parameters_and_heavy_negative(self):
        for prompt, expected_negative in (('A sunny garden', 'nsfw, ' + HEAVY_NEGATIVE),
                                         ('NSFW, a sunny garden', HEAVY_NEGATIVE)):
            with self.subTest(prompt=prompt):
                settings = self.raw_settings(prompt=prompt)
                original = copy.deepcopy(settings)
                body = payload(settings)
                params = body['parameters']
                self.assertEqual(MEDIUM, body['model'])
                self.assertEqual(prompt, body['input'])
                self.assertEqual((14, 'k_euler_ancestral', 'karras'),
                    tuple(params[key] for key in ('steps', 'sampler', 'noise_schedule')))
                self.assertEqual(expected_negative, params['negative_prompt'])
                self.assertEqual(expected_negative, params['v4_negative_prompt']['caption']['base_caption'])
                self.assertEqual('', params['v4_negative_prompt']['caption']['char_captions'][0]['char_caption'])
                self.assertEqual(settings['v4_negative_prompt']['caption']['char_captions'][0]['centers'],
                    params['v4_negative_prompt']['caption']['char_captions'][0]['centers'])
                self.assertEqual(settings['v4_prompt'], params['v4_prompt'])
                self.assertEqual(2, params['tag_hint_uc_preset'])
                for key in ('model', 'uc', 'cfg_rescale', 'sm', 'sm_dyn', 'skip_cfg_above_sigma'):
                    self.assertNotIn(key, params)
                self.assertEqual(original, settings)

    def test_high_payload_preserves_existing_custom_settings_and_legacy_model(self):
        for explicit_model in (True, False):
            with self.subTest(explicit_model=explicit_model):
                settings = self.raw_settings(model=HIGH)
                if not explicit_model:
                    settings.pop('model')
                body = payload(settings)
                params = body['parameters']
                self.assertEqual(HIGH, body['model'])
                for key in ('seed', 'width', 'height', 'steps', 'scale', 'cfg_rescale',
                            'sampler', 'noise_schedule', 'v4_prompt', 'v4_negative_prompt'):
                    self.assertEqual(settings[key], params[key], key)
                self.assertEqual(settings['uc'], params['negative_prompt'])
                self.assertNotIn('model', params)

    def test_new_pages_snapshot_model_without_changing_existing_pages(self):
        project, legacy_page = self.page()
        self.assertEqual(HIGH, self.workbench.preview_prompt(project['id'], legacy_page['id'])['model'])
        image_preferences.store(self.workbench, dict(settings=dict(model=MEDIUM)))
        project = self.workbench.page(project['id'], dict(panel_ids=legacy_page['panel_ids']))
        medium_page = copy.deepcopy(project['pages'][-1])
        self.assertEqual(MEDIUM, medium_page['image_settings']['model'])
        image_preferences.store(self.workbench, dict(settings=dict(model=HIGH, steps=32)))
        project = self.workbench.page(project['id'], dict(panel_ids=legacy_page['panel_ids']))
        self.assertEqual(legacy_page, project['pages'][0])
        self.assertEqual(medium_page, project['pages'][1])
        self.assertEqual(HIGH, project['pages'][2]['image_settings']['model'])
        self.assertEqual(32, project['pages'][2]['image_settings']['steps'])
        for page, model in zip(project['pages'], (HIGH, MEDIUM, HIGH)):
            with self.subTest(model=model, page=page['id']):
                self.assertEqual(model, self.workbench.preview_prompt(project['id'], page['id'])['model'])

    def test_medium_preview_and_studio_saved_form_show_effective_request(self):
        image_preferences.store(self.workbench, dict(settings=dict(model=MEDIUM)))
        project, page = self.page()
        settings, audit = build_settings(project, page)
        preview = self.workbench.preview_prompt(project['id'], page['id'])
        form = image_studio.form(settings, audit)
        for actual in (preview, form, image_studio.get(self.workbench, project['id'], page['id'])['prompt']):
            self.assertEqual(MEDIUM, actual['model'])
            self.assertEqual((14, 0, 'karras'), tuple(actual[key] for key in ('steps', 'cfg_rescale', 'noise_schedule')))
            self.assertEqual('nsfw, ' + HEAVY_NEGATIVE, actual['negative_prompt'])
            self.assertTrue(all(row['negative_prompt'] == '' for row in actual['characters']))
        for model in (MEDIUM, HIGH):
            with self.subTest(saved_model=model):
                saved_settings = dict(settings, model=model)
                row = dict(settings_path=model + '/settings.json', audit_path=model + '/audit.json')
                save(self.workbench.folder(project['id']) / row['settings_path'], saved_settings)
                save(self.workbench.folder(project['id']) / row['audit_path'], audit)
                restored = image_studio.saved_form(self.workbench, project['id'], row)
                self.assertEqual(model, restored['model'])

    def test_studio_render_accepts_and_normalizes_medium_without_calling_api(self):
        project, page = self.page()
        current = self.workbench.preview_prompt(project['id'], page['id'])
        data = dict(source_sha256=image_studio.source_hash(project, page), seed=4321,
            prompt_overrides={key: current[key] for key in ('prompt', 'negative_prompt', 'characters')},
            image_settings=dict(model=MEDIUM, steps=48, sampler='k_euler',
                cfg_rescale=.7, noise_schedule='native'))
        with patch('engine.iterative_page_rendering.render_project_page', return_value='offline candidate') as render:
            result = image_studio.render(self.workbench, project['id'], page['id'], data)
        self.assertEqual('offline candidate', result)
        request = render.call_args.kwargs['payload']
        self.assertEqual((MEDIUM, 14, 'k_euler_ancestral', 0, 'karras'),
            tuple(request['image_settings'][key] for key in
                  ('model', 'steps', 'sampler', 'cfg_rescale', 'noise_schedule')))
        self.assertFalse(request['apply_result'])
        self.assertEqual(4321, request['seed'])
        self.assertEqual(project, self.workbench.load(project['id']))
        self.transport.assert_not_called()

    def test_loading_defaults_in_studio_changes_model_and_keeps_character_prompts(self):
        project, page = self.page()
        before = image_studio.get(self.workbench, project['id'], page['id'])['prompt']
        image_preferences.store(self.workbench, dict(settings=dict(model=MEDIUM)))
        loaded = image_studio.with_defaults(self.workbench, project['id'], page['id'],
            dict(source_sha256=image_studio.source_hash(project, page)))
        self.assertEqual(MEDIUM, loaded['prompt']['model'])
        self.assertEqual(before['characters'], loaded['prompt']['characters'])
        self.assertEqual(project, self.workbench.load(project['id']))

    def test_cost_assessment_uses_medium_effective_steps(self):
        account = dict(active=True, tier=3, remaining_percent=80)
        for model, steps, status in ((MEDIUM, 14, 'free'), (HIGH, 48, 'paid')):
            with self.subTest(model=model):
                result = image_cost.assess(dict(model=model, width=832, height=1216, steps=48), account)
                self.assertEqual(model, result['model'])
                self.assertEqual(steps, result['steps'])
                self.assertEqual(status, result['anlas_status'])
                self.assertEqual(status == 'paid', result['requires_confirmation'])
                self.assertTrue(result['can_generate'])
        too_large = image_cost.assess(dict(model=MEDIUM, width=1536, height=1536, steps=48), account)
        self.assertEqual('paid', too_large['anlas_status'])
        self.assertTrue(too_large['requires_confirmation'])

    def test_medium_inpaint_keeps_model_and_restrictions_without_changing_high(self):
        from PIL import Image
        from engine.image_inpaint import request_body
        source = Image.new('RGB', (256, 256), 'white')
        mask = Image.new('L', source.size, 255)
        for model, expected_model, steps in ((MEDIUM, 'nai-diffusion-5-full-medium-inpainting', 14),
                (HIGH, 'nai-diffusion-5-full-inpainting', 48)):
            with self.subTest(model=model):
                settings = dict(self.raw_settings(model=model), color_mode='monochrome')
                body = request_body(settings, source, mask, .8)
                self.assertEqual(expected_model, body['model'])
                self.assertEqual('infill', body['action'])
                self.assertEqual(steps, body['parameters']['steps'])
                self.assertEqual(.8, body['parameters']['inpaintImg2ImgStrength'])
                self.assertNotIn('color_mode', body['parameters'])
                if model == MEDIUM:
                    self.assertNotIn('cfg_rescale', body['parameters'])
                    self.assertEqual('nsfw, ' + HEAVY_NEGATIVE, body['parameters']['negative_prompt'])
                else:
                    self.assertEqual(.7, body['parameters']['cfg_rescale'])
                    self.assertEqual(settings['uc'], body['parameters']['negative_prompt'])

    def test_medium_defaults_preserve_saved_styles_and_color_mode(self):
        style = dict(style_prompt='pencil sketch', negative_prompt='custom exclusion', color_mode='monochrome')
        image_preferences.save_style(self.workbench, dict(name='연필', settings=style))
        public = image_preferences.store(self.workbench, dict(settings=dict(style, model=MEDIUM)))
        self.assertEqual(style, public['styles'][0]['settings'])
        self.assertEqual('monochrome', public['settings']['color_mode'])
        project, page = self.page()
        settings, audit = build_settings(project, page)
        self.assertEqual('monochrome', image_studio.form(settings, audit)['color_mode'])
        self.assertNotIn('color_mode', payload(settings)['parameters'])


if __name__ == '__main__':
    unittest.main()
