"""Offline regressions for prompt-budget snapshots and large initial briefs."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from engine.iterative_comic import Workbench, read, save
from engine.iterative_recovery import digest
from engine import prompt_management
from test_story_fixture import DemoWorkbench


class PromptBudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workbench = Workbench(temporary.name,
            key_provider=Mock(side_effect=AssertionError('No live credentials')))
        self.folder = Path(temporary.name) / 'operations' / 'o123456789abc'
        self.stage = 'state_resolution'
        self.task = 'Resolve this existing scene. Input: {"panels":[]}'
        self.base = dict(model='glm-4-6', temperature=0.25, max_tokens=4096,
            enable_thinking=False, stream=True,
            messages=[dict(role='system', content='You are a helpful assistant.'),
                      dict(role='user', content=self.task)])
        self.snapshot = self.folder / (self.stage + '_prompt_profile.json')
        self.request_path = self.folder / (self.stage + '_request.json')
        self.network = self.enterContext(patch('urllib.request.urlopen',
            side_effect=AssertionError('No live network')))
        self.addCleanup(self.network.assert_not_called)
        self.addCleanup(self.workbench.key_provider.assert_not_called)

    def configure(self, label='Pinned', revision=7):
        config = dict(system_prompt=label + ' system', author_note=label + ' note',
            task_template=label + ' template:\n{{task}}',
            before_task=label + ' before', after_task=label + ' after')
        save(Path(self.workbench.directory) / '_settings' / 'prompt_profiles.json',
            dict(version=prompt_management.VERSION, revision=revision,
                 profiles={self.stage: config}))
        return config

    def prepare(self, base=None, folder=None):
        return prompt_management.prepare_request(self.workbench, folder or self.folder,
            self.stage, self.base if base is None else base)

    def test_profile_is_pinned_before_fitter_failure(self):
        config = self.configure()

        def fail(workbench, folder, stage, request):
            self.assertIs(self.workbench, workbench)
            self.assertEqual(self.folder, folder)
            self.assertEqual(self.stage, stage)
            pinned = read(self.snapshot)
            self.assertEqual(config, pinned['config'])
            self.assertEqual(7, pinned['profile_revision'])
            self.assertEqual(self.base, pinned['base_request'])
            self.assertEqual(request, pinned['request'])
            self.assertEqual('Pinned system', request['messages'][0]['content'])
            raise RuntimeError('Memory generation failed')

        with patch('engine.context_budget.fit_request', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Memory generation failed'):
                self.prepare()
        self.assertTrue(self.snapshot.exists())
        self.assertFalse(self.request_path.exists())
        self.assertEqual(digest(self.base), read(self.snapshot)['base_request_sha256'])

    def test_snapshot_only_retry_keeps_profile_after_global_settings_change(self):
        config = self.configure()
        with patch('engine.context_budget.fit_request', side_effect=RuntimeError('Interrupted memory')):
            with self.assertRaises(RuntimeError):
                self.prepare()
        original = read(self.snapshot)
        self.configure('New unrelated', revision=8)
        with patch('engine.context_budget.fit_request', side_effect=lambda _w, _f, _s, r: copy.deepcopy(r)) as fit:
            request = self.prepare()
        self.assertEqual(original['request'], request)
        self.assertEqual(original['request'], fit.call_args.args[3])
        self.assertEqual(config, read(self.snapshot)['config'])
        self.assertEqual(7, read(self.snapshot)['profile_revision'])
        self.assertNotIn('New unrelated', json.dumps(request))

    def test_budgeted_request_digest_is_saved_with_original_base(self):
        self.configure()
        original_base = copy.deepcopy(self.base)

        def compact(_workbench, _folder, _stage, request):
            result = copy.deepcopy(request)
            result['messages'][-1]['content'] = 'Compact scene memory; original constraints retained.'
            return result

        with patch('engine.context_budget.fit_request', side_effect=compact):
            request = self.prepare()
        pinned = read(self.snapshot)
        self.assertEqual(request, pinned['request'])
        self.assertEqual(digest(request), pinned['request_sha256'])
        self.assertEqual(original_base, pinned['base_request'])
        self.assertEqual(digest(original_base), pinned['base_request_sha256'])
        self.assertNotEqual(pinned['base_request_sha256'], pinned['request_sha256'])
        self.assertEqual(1, pinned['context_budget_version'])
        self.assertEqual(original_base, self.base)

    def test_completed_saved_request_skips_fitter_and_generation(self):
        with patch('engine.context_budget.fit_request', side_effect=lambda _w, _f, _s, r: copy.deepcopy(r)):
            request = self.prepare()
        save(self.request_path, request)
        target = self.folder / self.stage
        save(target / 'result.json', dict(http_status=200, finish_reason='stop'))
        (target / 'story.txt').write_text('{"panels":[]}', encoding='utf-8')
        self.configure('Later profile', revision=99)
        with patch('engine.context_budget.fit_request', side_effect=AssertionError('Must reuse saved request')) as fit:
            with patch('engine.iterative_comic.generate', side_effect=AssertionError('Must reuse saved response')) as generate:
                result = self.workbench.call(self.folder, self.stage, self.task)
        self.assertEqual(dict(panels=[]), result)
        self.assertEqual(request, read(self.request_path))
        fit.assert_not_called()
        generate.assert_not_called()

    def test_changed_base_is_rejected_for_snapshot_only_and_saved_request(self):
        for saved_request in (False, True):
            with self.subTest(saved_request=saved_request):
                folder = self.folder / ('saved' if saved_request else 'snapshot_only')
                with patch('engine.context_budget.fit_request', side_effect=lambda _w, _f, _s, r: copy.deepcopy(r)):
                    request = self.prepare(folder=folder)
                if saved_request:
                    save(folder / (self.stage + '_request.json'), request)
                snapshot = folder / (self.stage + '_prompt_profile.json')
                original_snapshot = snapshot.read_bytes()
                changed = copy.deepcopy(self.base)
                changed['messages'][-1]['content'] = 'Resolve a different scene.'
                with patch('engine.context_budget.fit_request', side_effect=AssertionError('Must reject changed base')) as fit:
                    with self.assertRaisesRegex(ValueError, '최초'):
                        self.prepare(changed, folder)
                fit.assert_not_called()
                self.assertEqual(original_snapshot, snapshot.read_bytes())
                if saved_request:
                    self.assertEqual(request, read(folder / (self.stage + '_request.json')))

    def test_small_default_request_remains_unchanged_with_real_fitter(self):
        original = copy.deepcopy(self.base)
        request = self.prepare()
        self.assertEqual(original, request)
        self.assertEqual(original, self.base)
        pinned = read(self.snapshot)
        self.assertEqual(original, pinned['base_request'])
        self.assertEqual(original, pinned['request'])


class LargeInitialBriefTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workbench = DemoWorkbench(temporary.name)
        self.network = self.enterContext(patch('urllib.request.urlopen',
            side_effect=AssertionError('No live network')))
        self.addCleanup(self.network.assert_not_called)

    def test_seed_at_200000_character_boundary_is_preserved_by_real_create(self):
        # The fixture replaces only model replies; create, validation, and commit run normally.
        self.assertIs(Workbench.create, DemoWorkbench.create)
        prefix = 'A quiet garden story. '
        seed = prefix + 'a' * (200000 - len(prefix))
        project = self.workbench.create(dict(seed=seed, count=1, dialogue='none',
            generation_options={'translation_mode': 'off'}))
        self.assertEqual(seed, project['seed_ko'])
        self.assertEqual(seed, self.workbench.load(project['id'])['seed_ko'])
        operation = project['operations'][0]['id']
        folder = self.workbench.folder(project['id']) / 'operations' / operation
        self.assertEqual(seed, read(folder / 'input_ko.json')['seed'])
        self.assertEqual(seed, read(folder / 'input_en.json')['seed'])
        self.assertEqual(1, len(project['panels']))
        self.assertIn('story', [call['stage'] for call in self.workbench.demo_calls])

    def test_seed_above_200000_characters_is_rejected_before_generation(self):
        with self.assertRaisesRegex(ValueError, '200,000'):
            self.workbench.create(dict(seed='a' * 200001, count=1))
        self.assertEqual([], self.workbench.demo_calls)
        self.assertEqual([], list(Path(self.workbench.directory).glob('p*')))


if __name__ == '__main__':
    unittest.main()
