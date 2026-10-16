"""Offline retries of saved text requests rejected for excess context."""
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import prompt_management
from engine.iterative_comic import Workbench, read, save
from engine.iterative_recovery import archive_failed_stage


class ContextBudgetRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workbench = Workbench(temporary.name, key_provider=lambda: 'offline-key')
        self.project = self.workbench.folder('p123456789abc')
        self.folder = self.project / 'operations' / 'o123456789abc'
        self.stage = 'director'
        self.task = 'Plan from these facts.\n' + json.dumps(dict(initial_brief='Keep the accepted goal unresolved. ' * 1500))
        self.base = dict(model='glm-4-6', temperature=.25, max_tokens=4096, enable_thinking=False, stream=True,
                         messages=[dict(role='system', content='You are a helpful assistant.'),
                                   dict(role='user', content=self.task)])

    def seed(self, *, custom=False, legacy=False, budgeted=False, status=400, error='too much context'):
        if custom:
            self.configure('Pinned')
        with patch('engine.context_budget.fit_request', side_effect=self.compact if budgeted else
                   lambda _w, _f, _s, request: copy.deepcopy(request)):
            accepted = prompt_management.prepare_request(self.workbench, self.folder, self.stage, self.base)
        save(self.folder / (self.stage + '_request.json'), accepted)
        if legacy:
            (self.folder / (self.stage + '_prompt_profile.json')).unlink()
        target = self.folder / self.stage
        save(target / 'result.json', dict(http_status=status, finish_reason='stop' if status == 200 else None))
        save(target / 'request.json', accepted)
        (target / 'error.txt').write_text(error, encoding='utf-8')
        (target / 'story.txt').write_text('{}' if status == 200 else '', encoding='utf-8')
        save(self.folder / (self.stage + '_attempt.json'), dict(status='started'))
        self.job = dict(id='j123456789abc', project_id=self.project.name, operation_id=self.folder.name,
                        stage=self.stage, stage_folder=target.relative_to(self.project).as_posix(), status='failed')
        return accepted

    def configure(self, label):
        save(Path(self.workbench.directory) / '_settings' / 'prompt_profiles.json',
             dict(version=prompt_management.VERSION, revision=7,
                  profiles={self.stage: dict(system_prompt=label + ' system', author_note=label + ' note',
                           task_template='{{task}}', before_task=label + ' before', after_task=label + ' after')}))

    @staticmethod
    def compact(_workbench, _folder, _stage, request):
        result = copy.deepcopy(request)
        result['messages'][-1]['content'] = 'Compact accepted story memory; its goal remains unresolved.'
        return result

    @property
    def pending(self):
        return self.folder / (self.stage + '_context_budget_retry.json')

    def files(self):
        return {name: (self.folder / name).read_bytes() for name in
                (self.stage + '_request.json', self.stage + '_prompt_profile.json')
                if (self.folder / name).exists()}

    def test_explicit_retry_archives_then_fits_pinned_request_outside_lock(self):
        accepted = self.seed(custom=True)
        originals = self.files()
        save(self.folder / 'story_request.json', {'earlier': 'accepted story'})
        previous_story = (self.folder / 'story_request.json').read_bytes()
        with patch('engine.context_budget.fit_request', side_effect=AssertionError('Archive must not summarize')) as fit:
            with patch('engine.iterative_comic.generate', side_effect=AssertionError('Archive must not generate')) as generate:
                archive_failed_stage(self.workbench, self.job)
        fit.assert_not_called()
        generate.assert_not_called()
        self.assertEqual(originals, self.files())
        archived = self.project / self.job['archived_response']
        for name, value in originals.items():
            self.assertEqual(value, (archived / name).read_bytes())
        self.assertEqual(prompt_management._digest(accepted), read(self.pending)['request_sha256'])
        self.configure('Changed after failure')

        def compact(workbench, folder, stage, request):
            acquired = []
            def take_lock():
                locked = prompt_management.LOCK.acquire(timeout=.5)
                acquired.append(locked)
                if locked:
                    prompt_management.LOCK.release()
            thread = threading.Thread(target=take_lock)
            thread.start()
            thread.join(1)
            self.assertEqual([True], acquired)
            self.assertEqual(accepted, request)
            return self.compact(workbench, folder, stage, request)

        def respond(request_path, target, *_args, **_kwargs):
            self.assertEqual('Pinned system', read(request_path)['messages'][0]['content'])
            save(target / 'result.json', dict(http_status=200, finish_reason='stop'))
            (target / 'story.txt').write_text('{"ok":true}', encoding='utf-8')

        with patch('engine.context_budget.fit_request', side_effect=compact) as fit:
            with patch('engine.iterative_comic.generate', side_effect=respond) as generate:
                self.assertEqual({'ok': True}, self.workbench.call(self.folder, self.stage, self.task))
                self.assertEqual(1, fit.call_count)
                self.assertEqual(1, generate.call_count)
        fitted = read(self.folder / (self.stage + '_request.json'))
        profile = read(self.folder / (self.stage + '_prompt_profile.json'))
        self.assertEqual(fitted, profile['request'])
        self.assertEqual(prompt_management._digest(fitted), profile['request_sha256'])
        self.assertEqual(self.base, profile['base_request'])
        self.assertEqual(1, profile['context_budget_version'])
        self.assertFalse(self.pending.exists())
        self.assertEqual(previous_story, (self.folder / 'story_request.json').read_bytes())
        with patch('engine.context_budget.fit_request', side_effect=AssertionError('Completed request must stay exact')):
            self.assertEqual(fitted, prompt_management.prepare_request(self.workbench, self.folder, self.stage, self.base))

    def test_legacy_request_is_pinned_before_summary_failure_and_resumes(self):
        accepted = self.seed(legacy=True)
        original = (self.folder / (self.stage + '_request.json')).read_bytes()
        archive_failed_stage(self.workbench, self.job)
        queued = self.pending.read_bytes()
        self.configure('Unrelated current profile')

        def fail(_workbench, _folder, _stage, request):
            self.assertEqual(accepted, request)
            profile = read(self.folder / (self.stage + '_prompt_profile.json'))
            self.assertTrue(profile['legacy_request'])
            self.assertFalse(profile['overridden'])
            self.assertEqual(self.base, profile['base_request'])
            raise RuntimeError('Summary failed')

        with patch('engine.context_budget.fit_request', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'Summary failed'):
                prompt_management.prepare_request(self.workbench, self.folder, self.stage, self.base)
        self.assertEqual(queued, self.pending.read_bytes())
        self.assertEqual(original, (self.folder / (self.stage + '_request.json')).read_bytes())
        with patch('engine.context_budget.fit_request', side_effect=self.compact):
            result = prompt_management.prepare_request(self.workbench, self.folder, self.stage, self.base)
        self.assertFalse(self.pending.exists())
        self.assertEqual('You are a helpful assistant.', result['messages'][0]['content'])
        self.assertEqual(result, read(self.folder / (self.stage + '_request.json')))

    def test_legitimately_budgeted_snapshot_is_not_rejected_by_profile_rerender(self):
        self.stage = 'state_resolution'
        self.base['messages'][-1]['content'] = (
            'Resolve visual state only for the supplied existing panel descriptions.\nInput:\n' +
            json.dumps(dict(state_context_version='compact-v1', cast={}, panels=[], accepted_prefix=[])))
        accepted = self.seed(custom=True, budgeted=True)
        profile = read(self.folder / (self.stage + '_prompt_profile.json'))
        self.assertNotEqual(accepted, prompt_management._apply(self.base, self.stage, profile['config']))
        archive_failed_stage(self.workbench, self.job)
        self.assertTrue(self.pending.exists())
        with patch('engine.context_budget.fit_request', side_effect=lambda _w, _f, _s, request: copy.deepcopy(request)):
            result = prompt_management.prepare_request(self.workbench, self.folder, self.stage, self.base)
        self.assertEqual(accepted, result)
        self.assertFalse(self.pending.exists())

    def test_all_registered_main_text_stages_can_queue_budget_retry(self):
        for index, stage in enumerate(prompt_management.STAGES):
            with self.subTest(stage=stage):
                self.stage = stage
                self.folder = self.project / 'operations' / f'o{index:012x}'
                self.seed()
                archive_failed_stage(self.workbench, self.job)
                self.assertEqual(stage, read(self.pending)['stage'])

    def test_automatic_and_non_context_retries_keep_saved_request_exact(self):
        for index, (automatic, status, error) in enumerate(((True, 400, 'too much context'),
                (False, 400, 'invalid schema'), (False, 503, 'too much context'), (False, 200, 'too much context'))):
            with self.subTest(automatic=automatic, status=status, error=error):
                self.folder = self.project / 'operations' / f'o{index:012x}'
                self.seed(status=status, error=error)
                originals = self.files()
                archive_failed_stage(self.workbench, self.job, automatic=automatic)
                self.assertFalse(self.pending.exists())
                self.assertEqual(originals, self.files())
                with patch('engine.context_budget.fit_request', side_effect=AssertionError('Must not fit')):
                    prompt_management.prepare_request(self.workbench, self.folder, self.stage, self.base)

    def test_request_tampering_is_rejected_before_archive(self):
        self.seed()
        path = self.folder / (self.stage + '_request.json')
        request = read(path)
        request['max_tokens'] = 4000
        save(path, request)
        with self.assertRaisesRegex(ValueError, '스냅샷'):
            archive_failed_stage(self.workbench, self.job)
        self.assertTrue((self.folder / self.stage).exists())
        self.assertFalse(self.pending.exists())

    def test_pending_marker_tampering_is_rejected_before_summary(self):
        self.seed()
        archive_failed_stage(self.workbench, self.job)
        marker = read(self.pending)
        marker['request_sha256'] = 'altered'
        save(self.pending, marker)
        with patch('engine.context_budget.fit_request', side_effect=AssertionError('Must not fit')):
            with self.assertRaisesRegex(ValueError, '재시도 기록'):
                prompt_management.prepare_request(self.workbench, self.folder, self.stage, self.base)


if __name__ == '__main__':
    unittest.main()
