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
from engine.iterative_autobook import writer_instruction
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

    def test_korean_and_mixed_decisions_preserve_content(self):
        for instruction in ('Show 민수 reaching for the watering can.', '물뿌리개를 집어 드는 장면을 보여 주세요.'):
            raw = dict(DECISION, instruction=instruction, reason_en='물을 주기 전에 집어 들어야 해요.',
                       editorial=dict(camera_goal='손과 물뿌리개를 함께 보여 주세요.'))
            original = copy.deepcopy(raw)
            result = validate_decision(raw, CONTEXT)
            self.assertEqual(raw, original)
            self.assertEqual(result['instruction'], instruction)
            self.assertEqual(result['reason_en'], raw['reason_en'])
            self.assertEqual(result['editorial'], raw['editorial'])
        result = validate_decision(dict(DECISION, action='stop', instruction='',
                                       reason_en='정원 가꾸기가 끝났어요.'), CONTEXT)
        self.assertTrue(result['should_end'])

    def test_korean_director_instruction_uses_existing_writer_translation(self):
        with tempfile.TemporaryDirectory() as temp:
            wb = DemoWorkbench(Path(temp))
            project = wb.create(dict(seed='A gardener plants flowers', count=1))
            decision = validate_decision(dict(DECISION, instruction='물뿌리개를 들어 주세요.',
                editorial=dict(camera_goal='손과 물뿌리개를 함께 보여 주세요.')), CONTEXT)
            wb.demo_calls.clear()
            after = wb.expand(project['id'], dict(anchor_id=project['panels'][-1]['id'],
                count=1, intent=decision['intent'], dialogue=decision['dialogue'],
                instruction=writer_instruction(decision)))
            stages = [call['stage'] for call in wb.demo_calls]
            self.assertLess(stages.index('input_translation'), stages.index('story'))
            translation = next(call for call in wb.demo_calls if call['stage'] == 'input_translation')
            self.assertIn(decision['instruction'], translation['task'])
            self.assertIn(decision['editorial']['camera_goal'], translation['task'])
            story = next(call for call in wb.demo_calls if call['stage'] == 'story')
            self.assertIn('Tend a small garden', story['task'])
            self.assertNotIn(decision['instruction'], story['task'])
            self.assertEqual(len(after['panels']), 2)

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
            saved_decision = dict(DECISION, instruction='Show 민수 picking up the can.',
                                  reason_en='물을 주기 전에 집어 들어야 해요.')
            raw = json.dumps(saved_decision, ensure_ascii=False)
            (target/'story.txt').write_text(raw, encoding='utf-8')
            job = dict(id='j123456789abc', project_id=project['id'], kind='direct', stage='director',
                       stage_folder='operations/o123456789abc/director', operation_id='o123456789abc', status='failed')
            archive_failed_stage(wb, job)
            with patch('engine.iterative_comic.generate', side_effect=AssertionError('Must reuse response')):
                result = run_stage(wb, folder, 'director', request)
            self.assertEqual(result, saved_decision)
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
