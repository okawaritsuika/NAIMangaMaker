import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from engine import probe
from engine.basic_expansion import SYSTEM
from engine.iterative_comic import Workbench, read, save
from engine.iterative_recovery import run_stage, archive_failed_stage
from engine.job_diagnostics import diagnose


DENIED = "bad request: model 'xialong-v1' not allowed for current user tier 'Scroll'"


def http_error(message=DENIED, status=400):
    body = json.dumps(dict(statusCode=status, message=message)).encode()
    return HTTPError(probe.ENDPOINT, status, 'Bad Request', {}, io.BytesIO(body))


def response(content='{"ok":true}', finish='stop'):
    event = dict(choices=[dict(delta=dict(content=content), finish_reason=finish)])
    stream = io.BytesIO(('data: ' + json.dumps(event) + '\n\ndata: [DONE]\n').encode())
    stream.status = 200
    return stream


class ModelFallback(unittest.TestCase):
    def setUp(self):
        probe._DENIED_KEYS.clear()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(probe._DENIED_KEYS.clear)
        self.root = Path(self.temp.name)
        self.request = self.root / 'stage_request.json'
        self.payload = dict(model='xialong-v1', max_tokens=4096, temperature=.75,
                            stream=True, enable_thinking=False,
                            messages=[dict(role='system', content=SYSTEM),
                                      dict(role='user', content='Return JSON for a quiet garden scene.')])
        save(self.request, self.payload)

    def generate(self, name='story', key='TEST-KEY'):
        return probe.generate(self.request, self.root / name, 'UNUSED', api_key=key)

    def test_denial_switches_once_and_preserves_both_requests(self):
        with patch('engine.probe.urllib.request.urlopen', side_effect=[http_error(), response()]) as send:
            self.generate()
        sent = [json.loads(call.args[0].data) for call in send.call_args_list]
        self.assertEqual([p['model'] for p in sent], ['xialong-v1', 'glm-4-6'])
        self.assertEqual(sent[1]['messages'][1:], self.payload['messages'][1:])
        self.assertNotIn('You are Xialong', sent[1]['messages'][0]['content'])
        for k in ('temperature', 'max_tokens', 'enable_thinking', 'stream'):
            self.assertEqual(sent[1][k], self.payload[k])
        self.assertEqual(read(self.request), self.payload)
        out = self.root / 'story'
        self.assertEqual(read(out / 'request.json'), sent[1])
        self.assertEqual(read(out / 'xialong_rejected/request.json'), self.payload)
        self.assertEqual(read(out / 'xialong_rejected/result.json')['http_status'], 400)
        self.assertEqual(read(out / 'result.json')['model'], 'glm-4-6')
        self.assertEqual(read(out / 'result.json')['requested_model'], 'xialong-v1')
        self.assertEqual((out / 'story.txt').read_text(), '{"ok":true}')
        self.assertFalse((out / 'error.txt').exists())
        self.assertNotIn('TEST-KEY', '\n'.join(p.read_text() for p in out.rglob('*') if p.is_file()))

    def test_custom_system_is_not_rewritten(self):
        self.payload['messages'][0]['content'] = 'User custom instructions.'
        save(self.request, self.payload)
        with patch('engine.probe.urllib.request.urlopen', side_effect=[http_error(), response()]) as send:
            self.generate()
        self.assertEqual(json.loads(send.call_args.args[0].data)['messages'], self.payload['messages'])

    def test_success_does_not_fallback(self):
        with patch('engine.probe.urllib.request.urlopen', return_value=response()) as send:
            self.generate()
        self.assertEqual(send.call_count, 1)
        self.assertFalse((self.root / 'story/model_fallback.json').exists())

    def test_other_errors_never_trigger_model_fallback(self):
        cases = [(400, 'maximum context length exceeded'), (400, 'invalid max_tokens'),
                 (400, "model 'glm-4-6' not allowed for current user tier 'Scroll'"),
                 (401, DENIED), (403, DENIED), (429, DENIED), (503, DENIED),
                 (400, 'model xialong-v1 does not exist')]
        for index, (status, message) in enumerate(cases):
            with self.subTest(status=status, message=message):
                with patch('engine.probe.urllib.request.urlopen', side_effect=http_error(message, status)) as send:
                    with self.assertRaises(HTTPError):
                        self.generate('failure' + str(index))
                self.assertEqual(send.call_count, 1)
        self.assertFalse(probe._DENIED_KEYS)

    def test_glm_failure_is_reported_without_a_loop(self):
        with patch('engine.probe.urllib.request.urlopen', side_effect=[http_error(), http_error('context too long')]) as send:
            with self.assertRaises(HTTPError):
                self.generate()
        self.assertEqual(send.call_count, 2)
        out = self.root / 'story'
        self.assertIn(DENIED, (out / 'xialong_rejected/error.txt').read_text())
        self.assertIn('context too long', (out / 'error.txt').read_text())
        report = diagnose(dict(stage='story'), out)['report']
        self.assertEqual(report['model'], 'glm-4-6')
        self.assertEqual(report['requested_model'], 'xialong-v1')
        self.assertEqual(report['http_status'], 400)

    def test_cache_is_per_key_expires_and_does_not_touch_glm_requests(self):
        with patch('engine.probe.urllib.request.urlopen', side_effect=[http_error(), response()]):
            self.generate()
        with patch('engine.probe.urllib.request.urlopen', side_effect=lambda *a, **k: response()) as send:
            self.generate('cached')
            self.generate('other_account', key='OTHER-KEY')
            self.payload['model'] = 'glm-4-6'
            save(self.request, self.payload)
            self.generate('translation')
            self.payload['model'] = 'xialong-v1'
            save(self.request, self.payload)
            with probe._DENIED_LOCK:
                for key in probe._DENIED_KEYS:
                    probe._DENIED_KEYS[key] = 0
            self.generate('expired')
        self.assertEqual([json.loads(c.args[0].data)['model'] for c in send.call_args_list],
                         ['glm-4-6', 'xialong-v1', 'glm-4-6', 'xialong-v1'])
        self.assertTrue(read(self.root / 'cached/model_fallback.json')['cached_denial'])

    def test_complete_fallback_result_is_reused_without_resending(self):
        workbench = Workbench(self.root / 'data', key_provider=lambda: 'TEST-KEY')
        operation = self.root / 'operation'
        with patch('engine.probe.urllib.request.urlopen', side_effect=[http_error(), response()]) as send:
            first = run_stage(workbench, operation, 'story', self.payload)
            second = run_stage(workbench, operation, 'story', self.payload)
        self.assertEqual(first, {'ok': True})
        self.assertEqual(second, first)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(read(operation / 'story_request.json'), self.payload)

    def test_retry_of_old_saved_denial_uses_fallback_and_preserves_old_error(self):
        workbench = Workbench(self.root / 'data', key_provider=lambda: 'TEST-KEY')
        project = 'p0123456789ab'
        operation_id = 'o0123456789ab'
        folder = workbench.folder(project) / 'operations' / operation_id
        save(folder / 'story_request.json', self.payload)
        save(folder / 'story_attempt.json', dict(status='started'))
        save(folder / 'story/result.json', dict(http_status=400, finish_reason=None))
        (folder / 'story/story.txt').write_text('')
        (folder / 'story/error.txt').write_text(DENIED)
        job = dict(id='j0123456789ab', project_id=project, operation_id=operation_id,
                   kind='create', stage='story', stage_folder=f'operations/{operation_id}/story', status='failed')
        archive_failed_stage(workbench, job)
        with patch('engine.probe.urllib.request.urlopen', side_effect=[http_error(), response()]):
            self.assertEqual(run_stage(workbench, folder, 'story', self.payload), {'ok': True})
        self.assertEqual((workbench.folder(project) / job['archived_response'] / 'error.txt').read_text(), DENIED)

    def test_uncertain_network_error_does_not_fallback(self):
        with patch('engine.probe.urllib.request.urlopen', side_effect=URLError('disconnected')) as send:
            with self.assertRaises(URLError):
                self.generate()
        self.assertEqual(send.call_count, 1)


if __name__ == '__main__':
    unittest.main()
