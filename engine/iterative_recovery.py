"""Explicit, persisted stage recovery for the loopback comic workbench."""
import copy
import hashlib
import json
import re
from contextvars import ContextVar
from pathlib import Path

_SESSION = ContextVar('comic_recovery_session', default=None)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def current_session(workbench=None):
    value = _SESSION.get()
    return value if value is not None and (workbench is None or value.workbench is workbench) else None


def disk_project(workbench, project_id):
    from .iterative_comic import read
    path = workbench.folder(project_id) / 'project.json'
    return read(path) if path.exists() else None


def conflict(workbench, job):
    actual = disk_project(workbench, job['project_id'])
    if digest(actual) != job['base_sha256']:
        return '요청 이후 작품이 수정되어 이 작업을 다시 적용할 수 없어요. 현재 작품에서 새 요청을 시작해 주세요.'
    return None


class RecoverySession:
    def __init__(self, workbench, job, changed):
        self.workbench, self.job, self.changed = workbench, job, changed
        self.project_id = job['project_id']
        self.pending = copy.deepcopy(job.get('resume_project'))
        self.flushing = False
        self.operation_scope = None

    def __enter__(self):
        self.token = _SESSION.set(self)
        return self

    def __exit__(self, *_):
        _SESSION.reset(self.token)

    def operation(self, project_id, payload):
        from .iterative_comic import read, save
        if project_id != self.project_id:
            raise ValueError('복구 작업의 작품 번호가 달라요.')
        folder = self.workbench.folder(project_id) / 'operations' / self.job['operation_id']
        if self.operation_scope is not None:
            if not isinstance(self.operation_scope, str) or not re.fullmatch(r'o[0-9a-f]{12}', self.operation_scope):
                raise ValueError('복구 작업의 하위 단계 번호가 올바르지 않아요.')
            folder = folder / 'segments' / self.operation_scope
        path = folder / 'input_ko.json'
        if path.exists() and read(path) != payload:
            raise ValueError('저장된 요청과 복구 요청이 달라요. 기존 응답은 보존했어요.')
        save(path, payload)
        return folder

    def artifact_folder(self, stage, owner=None):
        if stage != 'image' or not isinstance(owner, str) or not re.fullmatch(r'g[0-9a-f]{12}', owner):
            raise ValueError('복구 파일의 단계를 확인해 주세요.')
        return self.workbench.folder(self.project_id) / 'renders' / owner / self.job['operation_id']

    def stage_started(self, name, folder):
        base = self.workbench.folder(self.project_id).resolve()
        path = Path(folder).resolve()
        if not path.is_relative_to(base):
            raise ValueError('복구 응답 경로가 작품 폴더를 벗어났어요.')
        self.job.update(stage=name, stage_folder=path.relative_to(base).as_posix(), stage_complete=False)
        if name == 'image' and self.pending is not None:
            # Persist the chosen seed/request page before HTTP, without committing the
            # unfinished render into the live project. Needed even for interrupted retries.
            self.job['resume_project'] = copy.deepcopy(self.pending)
        self.changed()

    def stage_completed(self, name, folder):
        self.job.update(stage=name, stage_complete=True)
        self.changed()

    def flush(self, *, success):
        from .iterative_comic import now
        message = conflict(self.workbench, self.job)
        if message:
            raise ValueError(message)
        if self.pending is None:
            return
        project = copy.deepcopy(self.pending)
        if success:
            project['_last_recovery_operation'] = self.job['operation_id']
        project['updated'] = now()
        self.flushing = True
        try:
            self.workbench.commit(project)
        finally:
            self.flushing = False
        self.pending = project
        if not success:
            # Failed render metadata is a deliberate commit, and becomes the retry base.
            self.job.update(base_project=copy.deepcopy(project), base_sha256=digest(project),
                            base_revision=project.get('revision'))
            self.job['resume_project'] = copy.deepcopy(project)


def run_stage(workbench, folder, name, request_body):
    """A stage is never resent implicitly, even when its HTTP result is incomplete."""
    from .iterative_comic import generate, json_object, now, read, save
    folder = Path(folder)
    if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
        raise ValueError('생성 단계 이름이 올바르지 않아요.')
    request_path = folder / (name + '_request.json')
    target = folder / name
    marker = folder / (name + '_attempt.json')
    session = current_session(workbench)
    if session:
        session.stage_started(name, target)
    if request_path.exists() and read(request_path) != request_body:
        raise ValueError('이 단계의 저장된 요청과 현재 요청이 달라요. 응답을 덮어쓰지 않았어요.')
    save(request_path, request_body)
    result_path, raw_path = target / 'result.json', target / 'story.txt'
    if result_path.exists() and raw_path.exists():
        result = read(result_path)
        if result.get('http_status') == 200 and result.get('finish_reason') == 'stop':
            value = json_object(raw_path.read_text(encoding='utf-8'), audit_folder=target)
            save(target / 'parsed.json', value)
            if session:
                session.stage_completed(name, target)
            return value
    if marker.exists() or target.exists():
        raise ValueError('이 단계의 미완료 응답을 보존했어요. 작업 목록에서 실패 단계 재시도를 선택해 주세요.')
    save(marker, dict(status='started', started=now(), request_sha256=digest(request_body)))
    generate(request_path, target, 'NOVELAI_API_KEY', api_key=workbench.key_provider())
    result = read(result_path)
    if result.get('http_status') != 200 or result.get('finish_reason') != 'stop':
        raise ValueError('응답의 완료를 확인하지 못했어요. 받은 원문은 보존했어요.')
    value = json_object(raw_path.read_text(encoding='utf-8'), audit_folder=target)
    save(target / 'parsed.json', value)
    save(marker, dict(status='complete', completed=now(), request_sha256=digest(request_body)))
    if session:
        session.stage_completed(name, target)
    return value


class RecoveryMixin:
    """Context-local snapshots keep generated candidates out of the live project."""
    def load(self, project_id):
        session = current_session(self)
        if session and project_id == session.project_id:
            project = session.pending if session.pending is not None else session.job['base_project']
            if project is None:
                raise FileNotFoundError('작품이 아직 생성되지 않았어요.')
            return copy.deepcopy(project)
        return super().load(project_id)

    def commit(self, project):
        session = current_session(self)
        if session and not session.flushing:
            if project['id'] != session.project_id:
                raise ValueError('다른 작품에 결과를 저장할 수 없어요.')
            session.pending = copy.deepcopy(project)
            return
        return super().commit(project)

    def korean(self, folder, project, panels, include_cast=False):
        from .iterative_comic import read, save
        if current_session(self):
            path = Path(folder) / 'recovery_panel_ids.json'
            if path.exists():
                ids = read(path)
                if len(ids) != len(panels):
                    raise ValueError('재개할 번역의 컷 수가 달라요.')
                for panel, panel_id in zip(panels, ids):
                    panel['id'] = panel_id
            else:
                save(path, [p['id'] for p in panels])
            save(Path(folder) / 'accepted_english.json', panels)
        return super().korean(folder, project, panels, include_cast=include_cast)


def recoverable(workbench):
    """Wrap only this instance; never patch methods on the global Workbench class."""
    if isinstance(workbench, RecoveryMixin):
        return workbench
    cls = type('Recoverable' + type(workbench).__name__, (RecoveryMixin, type(workbench)), {})
    wrapped = object.__new__(cls)
    wrapped.__dict__.update(workbench.__dict__)
    return wrapped


def archive_failed_stage(workbench, job, *, automatic=False):
    """The retry button preserves the prior attempt before allowing one new call."""
    from .iterative_comic import read, save
    stage = job.get('stage')
    if not stage or not job.get('stage_folder'):
        return
    base = workbench.folder(job['project_id']).resolve()
    folder = (base / job['stage_folder']).resolve()
    if not folder.is_relative_to(base):
        raise ValueError('응답 보존 경로가 올바르지 않아요.')
    # A process interruption after receiving a complete response needs no new request.
    if job['status'] == 'interrupted':
        if stage == 'image' and (folder / 'page.png').exists():
            return
        if (folder / 'result.json').exists() and (folder / 'story.txt').exists():
            result = read(folder / 'result.json')
            if result.get('http_status') == 200 and result.get('finish_reason') == 'stop':
                return
    archive = base / 'operations' / job['operation_id'] / 'recovery_attempts' / job['id']
    destination = (archive / stage).resolve()
    if not destination.is_relative_to(base) or destination.exists():
        raise ValueError('이미 보존한 재시도 기록을 덮어쓸 수 없어요.')
    files = []
    if folder.exists():
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                files.append(dict(path=path.relative_to(folder).as_posix(),
                                  sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        destination.parent.mkdir(parents=True, exist_ok=True)
        folder.rename(destination)
    marker = folder.parent / (stage + '_attempt.json')
    if marker.exists() and stage != 'image':
        destination.mkdir(parents=True, exist_ok=True)
        marker.rename(destination / 'stage_attempt.json')
    save(archive / 'decision.json', dict(automatic_retry=automatic, stage=stage, job_id=job['id'],
        preserved_files=files, original_folder=job['stage_folder']))
    job['stage_folder'] = destination.relative_to(base).as_posix()
    job['archived_response'] = job['stage_folder']
