"""Explicit restarts recover failed shared summaries without losing evidence."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.iterative_comic import Workbench, read, save
from engine.iterative_comic_server import App
from engine.iterative_recovery import current_session


class BudgetWorkbench(Workbench):
    def create(self, payload, progress=lambda _: None):
        project_id = current_session(self).project_id
        folder = self.operation(project_id, payload)
        result = self.call(folder, 'story', 'Read this brief and return a title.\n' +
                           json.dumps(dict(seed=payload['seed'])))
        project = dict(id=project_id, revision=1, title=result['title'], panels=[], pages=[])
        self.commit(project)
        return project


class ContextSummaryRestartTests(unittest.TestCase):
    def wait_job(self, app, job_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with app.lock:
                job = app.get_job(job_id)
                if job['status'] not in ('queued', 'running'):
                    return job
            time.sleep(.01)
        self.fail('Offline summary job did not finish')

    def assert_restart_recovers(self, failure_kind):
        with tempfile.TemporaryDirectory() as temporary:
            workbench = BudgetWorkbench(temporary, key_provider=lambda: 'offline-fixture-key')
            app = App(workbench)
            summary_requests = []

            def generate(request_path, output_path, *_args, **_kwargs):
                if request_path.name == 'context_summary_request.json':
                    summary_requests.append(request_path)
                    if len(summary_requests) == 1:
                        if failure_kind == 'transport':
                            save(output_path / 'result.json', dict(http_status=503, finish_reason=None))
                            (output_path / 'story.txt').write_text('Partial failed response', encoding='utf-8')
                            (output_path / 'error.txt').write_text('Offline upstream failure', encoding='utf-8')
                            raise OSError('Offline upstream failure')
                        response = dict(memory=123)
                    else:
                        response = dict(memory='Aster must never wear red. The missing key remains unresolved.')
                else:
                    self.assertEqual('story_request.json', request_path.name)
                    response = dict(title='A Garden Promise')
                save(output_path / 'result.json', dict(http_status=200, finish_reason='stop'))
                (output_path / 'story.txt').write_text(json.dumps(response), encoding='utf-8')

            try:
                with patch('urllib.request.urlopen', side_effect=AssertionError('No live network')) as network:
                    with patch('engine.iterative_comic.generate', side_effect=generate):
                        failed_id = app.job('create', dict(seed='Aster must never wear red. ' * 1800))['job_id']
                        failed = self.wait_job(app, failed_id)
                        self.assertEqual('failed', failed['status'])
                        self.assertEqual('context_summary', failed['stage'])
                        self.assertTrue(failed['stage_folder'].startswith('_context_memory/'))
                        response_folder = app.response_folder(failed)
                        originals = {path.name: path.read_bytes() for path in response_folder.iterdir() if path.is_file()}
                        attempt = (response_folder.parent / 'context_summary_attempt.json').read_bytes()
                        request = summary_requests[0].read_bytes()
                        operation_id = failed['operation_id']

                        restarted_id = app.retry(failed_id, restart=True)['job_id']
                        restarted = self.wait_job(app, restarted_id)
                        self.assertEqual('complete', restarted['status'], restarted.get('message'))
                        self.assertNotEqual(operation_id, restarted['operation_id'])
                        self.assertEqual(failed['project_id'], restarted['project_id'])
                        self.assertEqual(2, summary_requests.count(summary_requests[0]))
                        self.assertEqual(request, summary_requests[0].read_bytes())
                        archived = app.workbench.folder(failed['project_id']) / failed['archived_response']
                        for name, content in originals.items():
                            self.assertEqual(content, (archived / name).read_bytes())
                        self.assertEqual(attempt, (archived / 'stage_attempt.json').read_bytes())
                        saved_job = read(Path(temporary) / '_jobs' / (failed_id + '.json'))
                        self.assertEqual(failed['archived_response'], saved_job['archived_response'])
                    network.assert_not_called()
            finally:
                app.pool.shutdown(wait=True)

    def test_explicit_restart_archives_failed_summary_and_generates_again(self):
        self.assert_restart_recovers('transport')

    def test_explicit_restart_archives_invalid_successful_summary(self):
        self.assert_restart_recovers('invalid_summary')


if __name__ == '__main__':
    unittest.main()
