"""Offline regression coverage for explicitly retrying old context-limit failures."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.iterative_comic import Workbench, read, save
from engine.iterative_recovery import archive_failed_stage
from engine import prompt_management
from engine.story_context import compact_state_context


class StateContextRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(patch('urllib.request.urlopen', side_effect=AssertionError('No network in recovery tests')))
        self.workbench = Workbench(temporary.name, key_provider=lambda: 'offline-key')
        self.base = self.workbench.folder('p123456789abc')
        self.operation = self.base / 'operations' / 'o123456789abc'
        self.response = self.operation / 'state_resolution'
        state = dict(appearance=['adult gardener', 'green shirt', 'blue trousers'],
                     held_objects=['flowerpot'], pose='standing')
        panel = dict(id='c000000000000', actor='actor1', description='Alex tends the garden.',
                     background='A sunny garden', dialogues=['A long repeated line. ' * 30],
                     camera=dict(subjects=['actor1'], focus='Alex and the flowerpot',
                                 framing='wide shot', angle='side view'),
                     visual_states={'actor1': state}, origin={'operation': 'o000000000000'})
        self.context = dict(cast={'actor1': dict(name='Alex', appearance=state['appearance'])},
                            accepted_prefix=[dict(copy.deepcopy(panel), id=f'c{i:012x}') for i in range(100)],
                            latest_active_states={'actor1': state}, persistent_state_requests=[],
                            current_state_change='', panels=[dict(index=1, **panel)])
        self.job = dict(id='j123456789abc', project_id=self.base.name, operation_id=self.operation.name,
                        stage='state_resolution', stage_folder='operations/o123456789abc/state_resolution',
                        status='failed')

    def seed(self, *, custom=False, status=400, error='bad request: invalid prompt, too much context',
             context=None, task=None, legacy=False):
        context = self.context if context is None else context
        if custom:
            save(Path(self.workbench.directory) / '_settings' / 'prompt_profiles.json',
                 dict(version=prompt_management.VERSION, revision=7, profiles={'state_resolution':
                      dict(system_prompt='Pinned system', author_note='Pinned note',
                           task_template='Pinned task:\n{{task}}', before_task='Pinned before', after_task='Pinned after')}))
        base = dict(model='glm-4-6', temperature=0.25, max_tokens=4096, enable_thinking=False, stream=True,
                    messages=[dict(role='system', content='You are a helpful assistant.'),
                              dict(role='user', content=task or
                                   'Resolve visual state only for the supplied existing panel descriptions. Keep original text.\nInput:\n' +
                                   json.dumps(context, ensure_ascii=False))])
        # Seed the unbounded request from the pre-fix version; production now fits
        # new requests before saving them.
        with patch('engine.context_budget.fit_request', side_effect=lambda _w, _f, _s, request: request):
            accepted = prompt_management.prepare_request(self.workbench, self.operation, 'state_resolution', base)
        save(self.operation / 'state_resolution_request.json', accepted)
        if legacy:
            (self.operation / 'state_resolution_prompt_profile.json').unlink()
        save(self.operation / 'state_context.json', context)
        save(self.operation / 'state_resolution_attempt.json', dict(status='started'))
        save(self.response / 'result.json', dict(http_status=status, finish_reason='stop' if status == 200 else None))
        save(self.response / 'request.json', accepted)
        (self.response / 'error.txt').write_text(error, encoding='utf-8')
        (self.response / 'story.txt').write_text('{}' if status == 200 else '', encoding='utf-8')
        return base, accepted

    def saved_inputs(self):
        return {name: (self.operation / name).read_bytes() for name in
                ('state_resolution_request.json', 'state_resolution_prompt_profile.json', 'state_context.json')
                if (self.operation / name).exists()}

    def assert_compacted(self, originals):
        archived = self.base / self.job['archived_response']
        for name, content in originals.items():
            self.assertEqual(content, (archived / name).read_bytes())
        context = read(self.operation / 'state_context.json')
        self.assertEqual('compact-v1', context['state_context_version'])
        self.assertEqual(8, len(context['accepted_prefix']))
        self.assertEqual(self.context['latest_active_states'], context['latest_active_states'])
        new_request = read(self.operation / 'state_resolution_request.json')
        self.assertLess(len(json.dumps(new_request)), len(originals['state_resolution_request.json']) // 3)
        return new_request

    def test_explicit_retry_compacts_old_request_and_preserves_prior_stage(self):
        self.seed()
        originals = self.saved_inputs()
        save(self.operation / 'story_request.json', {'original': 'accepted story request'})
        save(self.operation / 'story' / 'result.json', {'http_status': 200, 'finish_reason': 'stop'})
        (self.operation / 'story' / 'story.txt').write_text('{"panels":[]}', encoding='utf-8')
        story_files = {path: path.read_bytes() for path in self.operation.glob('story/**/*') if path.is_file()}
        story_request = (self.operation / 'story_request.json').read_bytes()
        with patch('engine.iterative_comic.generate') as generate:
            archive_failed_stage(self.workbench, self.job)
            generate.assert_not_called()
        new_request = self.assert_compacted(originals)
        profile = read(self.operation / 'state_resolution_prompt_profile.json')
        self.assertEqual(new_request, prompt_management.prepare_request(
            self.workbench, self.operation, 'state_resolution', profile['base_request']))
        self.assertEqual(story_request, (self.operation / 'story_request.json').read_bytes())
        for path, content in story_files.items():
            self.assertEqual(content, path.read_bytes())

        def respond(request_path, output_path, *_args, **_kwargs):
            self.assertEqual(new_request, read(request_path))
            save(output_path / 'result.json', dict(http_status=200, finish_reason='stop'))
            (output_path / 'story.txt').write_text(json.dumps(dict(panels=[dict(index=1,
                visual_states=self.context['latest_active_states'])])), encoding='utf-8')

        with patch('engine.iterative_comic.generate', side_effect=respond) as generate:
            panel = copy.deepcopy(self.context['panels'][0])
            prepared = self.workbench.prepare(self.operation, dict(panels=[panel]), self.context['cast'],
                                              prefix=self.context['accepted_prefix'])
            self.assertEqual(self.context['latest_active_states'], prepared['panels'][0]['visual_states'])
            self.assertEqual(panel['description'], prepared['panels'][0]['description'])
            self.assertEqual(1, generate.call_count)
            self.assertEqual(self.operation / 'state_resolution_request.json', generate.call_args.args[0])
        for path, content in story_files.items():
            self.assertEqual(content, path.read_bytes())

    def test_custom_profile_stays_pinned_after_settings_change(self):
        self.seed(custom=True)
        originals = self.saved_inputs()
        old_profile = read(self.operation / 'state_resolution_prompt_profile.json')
        save(Path(self.workbench.directory) / '_settings' / 'prompt_profiles.json',
             dict(version=prompt_management.VERSION, revision=8, profiles={'state_resolution':
                  dict(system_prompt='New unrelated system', task_template='Discard the original task')}))
        archive_failed_stage(self.workbench, self.job)
        updated = self.assert_compacted(originals)
        profile = read(self.operation / 'state_resolution_prompt_profile.json')
        self.assertEqual(old_profile['config'], profile['config'])
        self.assertEqual(7, profile['profile_revision'])
        self.assertEqual('Pinned system', updated['messages'][0]['content'])
        self.assertIn('Pinned note', [row['content'] for row in updated['messages']])
        self.assertTrue(updated['messages'][-1]['content'].startswith('Pinned before\n\nPinned task:\n'))
        self.assertTrue(updated['messages'][-1]['content'].endswith('\n\nPinned after'))
        self.assertEqual(updated, prompt_management.prepare_request(
            self.workbench, self.operation, 'state_resolution', profile['base_request']))

    def test_legacy_request_without_profile_can_retry(self):
        self.seed(legacy=True)
        originals = self.saved_inputs()
        archive_failed_stage(self.workbench, self.job)
        updated = self.assert_compacted(originals)
        self.assertFalse((self.operation / 'state_resolution_prompt_profile.json').exists())
        self.assertEqual(updated, prompt_management.prepare_request(
            self.workbench, self.operation, 'state_resolution', updated))

    def test_non_context_error_does_not_change_saved_inputs(self):
        self.seed(error='bad request: invalid prompt')
        originals = self.saved_inputs()
        archive_failed_stage(self.workbench, self.job)
        self.assertEqual(originals, self.saved_inputs())

    def test_completed_interrupted_stage_is_left_untouched(self):
        self.seed(status=200)
        self.job['status'] = 'interrupted'
        originals = self.saved_inputs()
        archive_failed_stage(self.workbench, self.job)
        self.assertEqual(originals, self.saved_inputs())
        self.assertTrue(self.response.exists())
        self.assertNotIn('archived_response', self.job)

    def test_wrong_status_does_not_compact(self):
        self.seed(status=503)
        originals = self.saved_inputs()
        archive_failed_stage(self.workbench, self.job)
        self.assertEqual(originals, self.saved_inputs())

    def test_automatic_retry_does_not_compact(self):
        self.seed()
        originals = self.saved_inputs()
        archive_failed_stage(self.workbench, self.job, automatic=True)
        self.assertEqual(originals, self.saved_inputs())

    def test_already_compact_request_is_left_unchanged(self):
        self.seed(context=compact_state_context(self.context))
        originals = self.saved_inputs()
        archive_failed_stage(self.workbench, self.job)
        self.assertEqual(originals, self.saved_inputs())

    def test_unrecognized_task_is_left_unchanged(self):
        self.seed(task='A custom state task.\nInput:\n' + json.dumps(self.context))
        originals = self.saved_inputs()
        archive_failed_stage(self.workbench, self.job)
        self.assertEqual(originals, self.saved_inputs())

    def test_tampered_request_is_rejected_before_archiving(self):
        self.seed()
        request_path = self.operation / 'state_resolution_request.json'
        request = read(request_path)
        request['max_tokens'] = 4000
        save(request_path, request)
        with self.assertRaises(ValueError):
            archive_failed_stage(self.workbench, self.job)
        self.assertTrue(self.response.exists())
        self.assertNotIn('archived_response', self.job)

    def test_tampered_base_is_rejected_before_archiving(self):
        self.seed()
        profile_path = self.operation / 'state_resolution_prompt_profile.json'
        profile = read(profile_path)
        profile['base_request']['max_tokens'] = 4000
        save(profile_path, profile)
        with self.assertRaises(ValueError):
            archive_failed_stage(self.workbench, self.job)
        self.assertTrue(self.response.exists())

    def test_tampered_pinned_config_is_rejected_before_archiving(self):
        self.seed(custom=True)
        profile_path = self.operation / 'state_resolution_prompt_profile.json'
        profile = read(profile_path)
        profile['config']['system_prompt'] = 'Changed pinned system'
        save(profile_path, profile)
        with self.assertRaises(ValueError):
            archive_failed_stage(self.workbench, self.job)
        self.assertTrue(self.response.exists())


if __name__ == '__main__':
    unittest.main()
