"""Independent app integration using persisted, recoverable engine operations."""
import copy
from pathlib import Path

try:
    from .engine.iterative_comic import Workbench, identity, now, read, save
    from .engine.iterative_comic_server import App, validate_job_payload
    from .engine.iterative_autobook import AutoBooks
    from .engine.iterative_recovery import digest, disk_project
    from .engine.translation_modes import options
except ImportError:
    from engine.iterative_comic import Workbench, identity, now, read, save
    from engine.iterative_comic_server import App, validate_job_payload
    from engine.iterative_autobook import AutoBooks
    from engine.iterative_recovery import digest, disk_project
    from engine.translation_modes import options


class StoryStarts(AutoBooks):
    """Create exactly the requested opening cuts in preserved 1–2-cut jobs."""
    def __init__(self, app):
        self.app, self.runs, self.threads = app, {}, {}
        for path in (app.workbench.directory / '_starts').glob('s*.json'):
            run = read(path)
            if run['status'] == 'running':
                run.update(status='paused', pause_requested=False,
                           message='서버가 중단되었어요. 저장된 컷부터 재개할 수 있어요.')
                self.persist(run)
            self.runs[run['id']] = run

    def persist(self, run):
        run['updated'] = now()
        save(self.app.workbench.directory / '_starts' / (run['id'] + '.json'), run)

    def public(self, run):
        with self.app.lock:
            project = disk_project(self.app.workbench, run['project_id']) or {}
            result = {key: copy.deepcopy(run.get(key)) for key in (
                'id', 'status', 'message', 'project_id', 'current_job_id', 'created',
                'updated', 'pause_requested', 'target_panels', 'generation_options')}
            result['progress'] = dict(panels=len(project.get('panels', [])), target_panels=run['target_panels'])
            return result

    def start(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('이야기 설정을 확인해 주세요.')
        count = payload.get('count', 2)
        if type(count) is not int or count < 1:
            raise ValueError('시작 컷 수는 1 이상의 정수로 입력해 주세요.')
        if payload.get('project_id'):
            raise ValueError('새 이야기는 새 작품으로 시작해 주세요.')
        with self.app.lock:
            if any(run['status'] == 'running' for run in self.runs.values()):
                raise ValueError('진행 중인 새 이야기 생성이 끝나거나 일시정지한 뒤 시작해 주세요.')
            brief = copy.deepcopy(payload)
            brief['count'] = min(2, count)
            brief['generation_options'] = options(dict(brief.get('generation_options') or {}, concise_prompts=True))
            validate_job_payload('create', brief)
            run = dict(id=identity('s'), status='running', created=now(), project_id=identity('p'),
                target_panels=count, brief=brief, generation_options=brief['generation_options'],
                # AutoBooks.queue's original bounded-operation guard allows every two-cut step.
                max_pages=count, panels_per_page=2, message='새 이야기를 준비하고 있어요.',
                current_job_id=None, step=None, sequence=0, pause_requested=False,
                last_project_sha256=digest(None), stop=False)
            self.runs[run['id']] = run
            self.persist(run)
            self.launch(run)
            return dict(run_id=run['id'])

    def plan(self, run, project):
        if project is None:
            return self.queue(run, 'create', run['brief'])
        remaining = run['target_panels'] - len(project['panels'])
        if remaining <= 0:
            run.update(status='complete', current_job_id=None, message='시작 컷 생성을 마쳤어요.')
            self.persist(run)
            return
        self.queue(run, 'expand', dict(anchor_id=project['panels'][-1]['id'], position='after',
            intent='story', dialogue=run['brief'].get('dialogue', 'auto'), count=min(2, remaining),
            instruction=''))

    def accepted(self, run, job):
        project = self.app.workbench.load(run['project_id'])
        run.update(last_project_sha256=digest(project), step=None, current_job_id=None)
        self.persist(run)


class RemoteApp(App):
    def __init__(self, workbench):
        super().__init__(workbench)
        self.starts = StoryStarts(self)

    def start_story(self, payload):
        return self.starts.start(payload)

    def delete_project(self, pid, payload):
        import re
        import shutil
        if payload != {'confirmed':True} or type(payload.get('confirmed')) is not bool:
            raise ValueError('작품과 모든 그림·이력을 삭제할지 확인해 주세요.')
        with self.lock:
            self.starts.guard(pid);self.auto.guard(pid)
            if pid in self.active:
                raise ValueError('생성 중인 작품은 삭제할 수 없어요. 작업이 끝난 뒤 삭제해 주세요.')
            root=self.workbench.directory.resolve()
            folder=self.workbench.folder(pid)
            if folder.is_symlink() or folder.is_junction() or folder.resolve().parent!=root:
                raise ValueError('작품 저장 경로를 확인해 주세요.')
            if not folder.is_dir(): raise FileNotFoundError(pid)
            groups=[(self.jobs,'_jobs','j'),(self.auto.runs,'_auto','a'),(self.starts.runs,'_starts','s')]
            cleanup=[]
            for entries,sub,prefix in groups:
                for rid,record in entries.items():
                    if record.get('project_id')!=pid: continue
                    if not re.fullmatch(prefix+r'[0-9a-f]{12}',rid): raise ValueError('작업 기록 번호를 확인해 주세요.')
                    path=root/sub/(rid+'.json')
                    if not path.resolve().is_relative_to(root): raise ValueError('작업 기록 경로를 확인해 주세요.')
                    cleanup.append((entries,rid,path))
            for manager in (self.auto,self.starts):
                if any(r.get('project_id')==pid and manager.threads.get(rid) and manager.threads[rid].is_alive()
                       for rid,r in manager.runs.items()):
                    raise ValueError('자동 작업이 종료되는 중이에요. 잠시 뒤 삭제해 주세요.')
            # All targets are checked before removing the explicitly confirmed project.
            shutil.rmtree(folder)
            for entries,rid,path in cleanup:
                path.unlink(missing_ok=True);entries.pop(rid,None)
            return dict(deleted=True,project_id=pid)

    def job(self, kind, payload, project_id=None, target_id=None, **kwargs):
        if hasattr(self, 'starts') and project_id:
            self.starts.guard(project_id, kwargs.get('auto_run_id'))
        return super().job(kind, payload, project_id, target_id, **kwargs)

    def mutation(self, pid, method, *args):
        with self.lock:
            self.starts.guard(pid)
            return super().mutation(pid, method, *args)


def make_app(key_store, data_dir):
    """Construct without reading/decrypting credentials or contacting any service."""
    wb = Workbench(directory=Path(data_dir), key_provider=lambda: key_store.credential_for('story')[1])
    wb.image_key_provider = lambda: key_store.credential_for('image')[1]
    return RemoteApp(wb)
