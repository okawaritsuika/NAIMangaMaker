import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from app_version import VERSION
from engine.iterative_comic import save
from engine.iterative_comic_server import App
from engine.iterative_recovery import current_session
from engine.job_diagnostics import diagnose, failure_message
from engine.probe import generate
from test_story_fixture import DemoWorkbench


class FailedWorkbench(DemoWorkbench):
    failure = None

    def call(self, folder, name, task, **kwargs):
        if self.failure and name == 'story':
            current_session(self).stage_started(name, Path(folder) / name)
            raise self.failure
        return super().call(folder, name, task, **kwargs)


class JobDiagnostics(unittest.TestCase):
    def test_classification_and_safe_report(self):
        cases = [(HTTPError('https://example.test/?token=PRIVATE', code, 'PRIVATE', {}, None), 'http')
                 for code in (400, 401, 403, 429, 500, 503)]
        cases += [(TimeoutError('PRIVATE'), 'timeout'), (URLError(TimeoutError('PRIVATE')), 'timeout'),
                  (URLError('PRIVATE'), 'network'), (KeyError('PRIVATE'), 'internal'),
                  (PermissionError('PRIVATE'), 'file'), (ValueError('PRIVATE'), 'validation')]
        for error, category in cases:
            with self.subTest(error=error):
                d = diagnose(dict(id='j0123456789ab', kind='expand', stage='story', payload='PRIVATE'), exc=error)
                self.assertEqual(d['report']['category'], category)
                self.assertNotIn('PRIVATE', json.dumps(d['report']))
                self.assertNotIn('PRIVATE', failure_message(d, error))
                self.assertIn('이야기와 컷 작성', failure_message(d, error))
                self.assertEqual(d['report']['version'], VERSION)

    def test_long_validation_detail_stays_out_of_summary_and_copy(self):
        error = ValueError('상태 확인: ' + 'private_input' * 2000)
        d = diagnose(dict(stage='story'), exc=error)
        self.assertEqual(d['detail'], str(error))
        self.assertLess(len(failure_message(d, error)), 200)
        self.assertNotIn('private_input', json.dumps(d['report']))

    def test_json_error_reports_position_without_the_document(self):
        error = json.JSONDecodeError('Invalid response', 'PRIVATE\nresponse', 9)
        d = diagnose(dict(stage='story'), exc=error)
        self.assertEqual(d['report']['json_position'], dict(line=2, column=2, character=9))
        self.assertNotIn('PRIVATE', json.dumps(d['report']))

    def test_existing_jobs_get_available_metadata_without_fabricated_version(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            save(folder / 'result.json', dict(http_status=429, error_type='HTTPError', finish_reason=None,
                                            prompt='PRIVATE', endpoint='PRIVATE'))
            d = diagnose(dict(stage='director'), folder)
            self.assertEqual(d['report']['http_status'], 429)
            self.assertEqual(d['report']['error_type'], 'HTTPError')
            self.assertEqual(d['report']['version'], 'unknown (older job)')
            self.assertNotIn('PRIVATE', json.dumps(d))

    def test_failed_job_persists_diagnostics_and_remains_retryable(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(FailedWorkbench(Path(temp)))
            try:
                app.workbench.failure = KeyError('PRIVATE story and key')
                jid = app.job('create', dict(seed='A small garden', count=1))['job_id']
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with app.lock:
                        if app.get_job(jid)['status'] == 'failed':
                            break
                    time.sleep(.02)
                public = app.public_job(app.get_job(jid))
                self.assertEqual(public['status'], 'failed')
                self.assertIn('KeyError', public['message'])
                self.assertIn('이야기와 컷 작성', public['message'])
                self.assertTrue(public['retryable'])
                self.assertNotIn('PRIVATE', json.dumps(public))
                self.assertTrue(public['diagnostic']['report']['locations'])
                saved = json.loads((Path(temp) / '_jobs' / (jid + '.json')).read_text(encoding='utf-8'))
                self.assertEqual(saved['diagnostic'], public['diagnostic'])
                app.workbench.failure = None
                retry = app.retry(jid)['job_id']
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    with app.lock:
                        if app.get_job(retry)['status'] in ('failed', 'complete'):
                            break
                    time.sleep(.02)
                self.assertEqual(app.get_job(retry)['status'], 'complete')
            finally:
                app.pool.shutdown(wait=True)

    def test_http_and_stream_errors_preserve_private_raw_separately(self):
        for http in (True, False):
            with self.subTest(http=http), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                request = root / 'request.json'
                save(request, dict(model='glm-4-6', messages=[]))
                target = root / 'story'
                if http:
                    error = HTTPError('https://example.test', 401, 'Unauthorized', {}, io.BytesIO(b'PRIVATE-KEY'))
                    mocked = patch('engine.probe.urllib.request.urlopen', side_effect=error)
                else:
                    response = io.BytesIO(b'data: {"error": {"message": "PRIVATE-KEY"}}\n')
                    response.status = 200
                    mocked = patch('engine.probe.urllib.request.urlopen', return_value=response)
                with mocked, self.assertRaises((HTTPError, RuntimeError)):
                    generate(request, target, 'UNUSED', api_key='PRIVATE-KEY')
                d = diagnose(dict(stage='story'), target)
                self.assertEqual(d['report']['category'], 'http' if http else 'api_event')
                self.assertNotIn('PRIVATE-KEY', (target / 'error.txt').read_text())
                self.assertNotIn('PRIVATE-KEY', json.dumps(d))


if __name__ == '__main__':
    unittest.main()
