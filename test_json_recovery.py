import json
import tempfile
import time
import unittest
from pathlib import Path
from engine.iterative_json import json_object, parse_object
from engine.iterative_comic import save
from engine.iterative_comic_server import App
from engine.iterative_recovery import current_session
from test_story_fixture import DemoWorkbench


class SyntaxRecovery(unittest.TestCase):
    def test_missing_panel_closer_preserves_values_and_audit(self):
        raw='{"panels":[{"visual_states":{"actor1":{"pose":"resting"}}],"ended":false}'
        with tempfile.TemporaryDirectory() as folder:
            result=json_object(raw,audit_folder=folder)
            self.assertEqual(result,{'panels':[{'visual_states':{'actor1':{'pose':'resting'}}}],'ended':False})
            audit=json.loads((Path(folder)/'syntax_repair.json').read_text())
            self.assertEqual(audit['insertions'][0]['insert'],'}')

    def test_commas_still_work_and_truncation_is_rejected(self):
        self.assertEqual(parse_object('{"a":1 "b":2}')[0],{'a':1,'b':2})
        for raw in ['{"a":"unfinished', '{"a":1', '{"a":1]', '{"a":1,"a":2}', '{"a":}']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):parse_object(raw)


class BrokenResponses(DemoWorkbench):
    failures=0
    def call(self,folder,name,task,**kwargs):
        if name=='story' and self.failures:
            self.failures-=1
            target=Path(folder)/name;target.mkdir(parents=True,exist_ok=True)
            session=current_session(self)
            if session:session.stage_started(name,target)
            (target/'story.txt').write_text('{"a":"truncated',encoding='utf-8')
            save(target/'result.json',{'http_status':200,'finish_reason':'stop'})
            raise json.JSONDecodeError('Unterminated string','{"a":"truncated',5)
        return super().call(folder,name,task,**kwargs)


class ManualRetry(unittest.TestCase):
    def wait(self,app,jid):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            with app.lock:
                job=app.get_job(jid)
                while job.get('retry_job_id'):job=app.get_job(job['retry_job_id'])
                if job['status']=='complete' or (job['status']=='failed' and job.get('automatic_retries')==2):return job
            time.sleep(.02)
        self.fail('Retry chain did not settle')

    def test_manual_json_retry_is_bounded_and_remains_retryable(self):
        with tempfile.TemporaryDirectory() as folder:
            wb=BrokenResponses(Path(folder));project=wb.create({'seed':'A gardener plants flowers','count':2})
            app=App(wb)
            try:
                app.workbench.failures=3
                result=app.job('expand',dict(anchor_id=project['panels'][-1]['id'],count=1,intent='action'),project['id'])
                last=self.wait(app,result['job_id'])
                self.assertEqual(len(app.jobs),3)
                self.assertTrue(last['restart'])
                self.assertTrue(app.public_job(last)['retryable'])
                self.assertEqual(len(app.workbench.load(project['id'])['panels']),2)
                self.assertTrue(list(Path(folder).glob('*/operations/*/recovery_attempts/*/story/story.txt')))
                retry=app.retry(last['id'])
                self.assertEqual(self.wait(app,retry['job_id'])['status'],'complete')
                self.assertEqual(len(app.workbench.load(project['id'])['panels']),3)
            finally:app.pool.shutdown(wait=True)

    def test_one_bad_response_recovers_automatically(self):
        with tempfile.TemporaryDirectory() as folder:
            wb=BrokenResponses(Path(folder));project=wb.create({'seed':'A gardener plants flowers','count':2})
            app=App(wb)
            try:
                app.workbench.failures=1
                result=app.job('expand',dict(anchor_id=project['panels'][-1]['id'],count=1,intent='action'),project['id'])
                self.assertEqual(self.wait(app,result['job_id'])['status'],'complete')
                self.assertEqual(len(app.jobs),2)
            finally:app.pool.shutdown(wait=True)


if __name__=='__main__':unittest.main()
