import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.iterative_comic import save
from engine.iterative_comic_server import App
from engine.iterative_director import validate_decision
from engine.iterative_recovery import archive_failed_stage, current_session, incomplete_response, run_stage
from test_story_fixture import DemoWorkbench


CONTEXT = dict(current_page_slots=2, closing_required=False, emphasis_allowed=True)
DECISION = dict(action='expand', count=1, intent='action', dialogue='none',
                instruction='Show the gardener reaching for the watering can.',
                reason_en='The watering can must be picked up before watering.')


class DirectorRecovery(unittest.TestCase):
    def test_missing_presentation_fields_do_not_change_story_decision(self):
        original = copy.deepcopy(DECISION)
        result = validate_decision(original, CONTEXT)
        self.assertEqual(original, DECISION)
        self.assertEqual(result['layout'], 'auto')
        self.assertIn(DECISION['reason_en'], result['reason_ko'])
        for key, value in DECISION.items(): self.assertEqual(result[key], value)
        for layout in ('auto', 'top', 'middle', 'bottom'):
            result = validate_decision(dict(DECISION, layout=layout, reason_ko='물을 주기 전에 물뿌리개를 들어야 해요.'), CONTEXT)
            self.assertEqual(result['layout'], layout)

    def test_generation_constraints_remain_required(self):
        for changes in (dict(layout='landscape'), dict(count=3), dict(instruction=''), dict(action='unknown')):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_decision(dict(DECISION, **changes), CONTEXT)

    def test_completed_saved_decision_reused_without_model_call(self):
        with tempfile.TemporaryDirectory() as temp:
            wb = DemoWorkbench(Path(temp))
            project = wb.create(dict(seed='A gardener plants flowers', count=2))
            folder = wb.folder(project['id']) / 'operations/o123456789abc'
            target = folder/'director'
            request = dict(model='glm-4-6', messages=[])
            save(folder/'director_request.json', request)
            save(folder/'director_context.json', CONTEXT)
            save(target/'result.json', dict(http_status=200, finish_reason='stop'))
            raw = json.dumps(DECISION)
            (target/'story.txt').write_text(raw, encoding='utf-8')
            job = dict(id='j123456789abc', project_id=project['id'], kind='direct', stage='director',
                       stage_folder='operations/o123456789abc/director', operation_id='o123456789abc', status='failed')
            archive_failed_stage(wb, job)
            with patch('engine.iterative_comic.generate', side_effect=AssertionError('Must reuse response')):
                result = run_stage(wb, folder, 'director', request)
            self.assertEqual(result, DECISION)
            self.assertEqual((target/'story.txt').read_text(encoding='utf-8'), raw)
            self.assertFalse((folder/'recovery_attempts').exists())
            save(target/'result.json', dict(http_status=200, finish_reason=None))
            archive_failed_stage(wb, job)
            self.assertFalse(target.exists())
            self.assertTrue((folder/'recovery_attempts'/job['id']/'director/story.txt').exists())


class InterruptedDirector(DemoWorkbench):
    failures = 0
    def call(self, folder, name, task, **kwargs):
        if name == 'director' and self.failures:
            self.failures -= 1
            target = Path(folder)/name
            session = current_session(self)
            if session: session.stage_started(name, target)
            save(target/'result.json', dict(http_status=200, finish_reason=None))
            (target/'story.txt').write_text('{"action":', encoding='utf-8')
            raise incomplete_response(dict(http_status=200, finish_reason=None))
        return super().call(folder, name, task, **kwargs)


class AutoResumeRecovery(unittest.TestCase):
    def wait_run(self, app, rid):
        deadline = time.monotonic()+15
        while time.monotonic() < deadline:
            with app.lock:
                run = app.auto.get(rid)
                if run['status'] in ('failed', 'complete'): return copy.deepcopy(run)
            time.sleep(.02)
        self.fail('Automatic run did not settle')

    def test_explicit_resume_restores_bounded_recovery_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            app = App(InterruptedDirector(Path(temp)))
            try:
                app.workbench.failures = 4
                rid = app.auto.start(dict(brief=dict(seed='A gardener plants flowers'), target_pages=3,
                                         panels_per_page=1, render_images=False))['run_id']
                failed = self.wait_run(app, rid)
                self.assertEqual(failed['status'], 'failed')
                before = app.workbench.load(failed['project_id'])
                self.assertEqual(sum(j['kind']=='direct' for j in app.jobs.values()), 3)
                app.auto.resume(rid)
                final = self.wait_run(app, rid)
                self.assertEqual(final['status'], 'complete', final['message'])
                after = app.workbench.load(final['project_id'])
                self.assertEqual(after['pages'][:len(before['pages'])], before['pages'])
                self.assertTrue(any(j['kind']=='direct' and j['restart'] and j['status']=='complete' for j in app.jobs.values()))
            finally: app.pool.shutdown(wait=True)


if __name__ == '__main__': unittest.main()
