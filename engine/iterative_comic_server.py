"""Loopback comic UI with persisted, explicitly retryable operations."""
import argparse
import copy
import json
import mimetypes
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

from .iterative_comic import Workbench, ROOT, identity, save, read, now
from .iterative_recovery import RecoverySession, archive_failed_stage, conflict, digest, disk_project, recoverable

API_VERSION = 'editing-v1'


def validate_job_payload(kind, payload):
    if not isinstance(payload, dict):
        raise ValueError('요청 형식이 올바르지 않아요.')
    if 'generation_options' in payload:
        from .translation_modes import options
        options(payload['generation_options'])
    for field in ('seed', 'direction', 'place', 'instruction', 'action_hint', 'state_change',
                  'anchor_id', 'mode', 'dialogue', 'position', 'intent', 'description_ko', 'background_ko', 'state_ko'):
        if field == 'seed' and kind in ('render','studio_render'):
            continue
        if field in payload and not isinstance(payload[field], str):
            raise ValueError(field + ' 값은 문자열이어야 해요.')
    if 'count' in payload and (type(payload['count']) is not int or (payload['count'] < 1 if kind == 'expand' else payload['count'] not in (range(1,6) if kind=='add_page' else (1, 2)))):
        raise ValueError('추가 컷 수는 1 이상의 정수로 입력해 주세요.' if kind=='expand' else '새 페이지는 1~5컷을 선택해 주세요.' if kind=='add_page' else '한 번에 만들 컷은 1개 또는 2개를 선택해 주세요.')
    if 'characters' in payload and (not isinstance(payload['characters'], list) or
                                   any(not isinstance(c, dict) for c in payload['characters'])):
        raise ValueError('인물 설정 형식을 확인해 주세요.')
    if 'camera_ko' in payload and not isinstance(payload['camera_ko'], dict):
        raise ValueError('시점 정보는 객체여야 해요.')
    if 'dialogues_ko' in payload and (not isinstance(payload['dialogues_ko'], list) or
                                     any(not isinstance(s, str) for s in payload['dialogues_ko'])):
        raise ValueError('대사는 문자열 목록이어야 해요.')
    if kind == 'create' and not payload.get('seed', '').strip():
        raise ValueError('단어나 짧은 문장을 입력해 주세요.')
    if kind == 'render':
        if type(payload.get('reroll', False)) is not bool:
            raise ValueError('다시 그리기 여부는 true 또는 false여야 해요.')
        if 'seed' in payload and (type(payload['seed']) is not int or not 0 <= payload['seed'] < 2**32):
            raise ValueError('그림 시드는 0~4294967295 정수여야 해요.')
        if payload.get('prompt_overrides') is not None and not isinstance(payload['prompt_overrides'], dict):
            raise ValueError('수정 프롬프트는 객체여야 해요.')


class App:
    def __init__(self, workbench=None):
        self.workbench = recoverable(workbench or Workbench())
        self.jobs, self.active = {}, set()
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1)
        for path in (self.workbench.directory / '_jobs').glob('j*.json'):
            try:
                job = read(path)
                job['automatic_retry_pending'] = False
                if job.get('status') in ('queued', 'running'):
                    project = disk_project(self.workbench, job['project_id']) if job.get('project_id') else None
                    committed = project and job.get('operation_id') and project.get('_last_recovery_operation') == job['operation_id']
                    job.update(status='complete' if committed else 'interrupted',
                               message='완료했어요.' if committed else '서버가 중단되었어요. 완료한 단계는 재사용할 수 있어요.', updated=now())
                    if committed:
                        job['result_project_sha256'] = digest(project)
                    save(path, job)
                self.jobs[job['id']] = job
            except (ValueError, KeyError, OSError):
                continue
        from .iterative_autobook import AutoBooks
        self.auto = AutoBooks(self)

    def persist(self, job):
        job['updated'] = now()
        save(self.workbench.directory / '_jobs' / (job['id'] + '.json'), job)

    def get_job(self, jid):
        if jid not in self.jobs:
            raise FileNotFoundError(jid)
        return self.jobs[jid]

    def public_job(self, job):
        with self.lock:
            fields = ('id', 'status', 'kind', 'project_id', 'operation_id', 'stage', 'message',
                      'error', 'created', 'updated', 'retry_of', 'retry_job_id', 'target_id', 'automatic_retry_pending')
            value = {key: job.get(key) for key in fields}
            candidate = (job.get('status') in ('failed', 'interrupted') and
                         all(key in job for key in ('operation_id', 'base_sha256', 'payload', 'kind')))
            reason = conflict(self.workbench, job) if candidate else None
            if job.get('retry_job_id'):
                reason = '이 작업의 재시도가 이미 등록되었어요. 새 작업을 확인해 주세요.'
            value['retry_conflict'] = reason
            value['restartable'] = bool(candidate and not reason)
            value['retryable'] = value['restartable']
            folder = self.response_folder(job)
            raw = (folder / 'story.txt') if folder else None
            available = bool(folder and folder.exists() and any(folder.iterdir()))
            value['raw'] = dict(available=available, stage=job.get('stage'),
                characters=len(raw.read_text(encoding='utf-8')) if raw and raw.exists() else 0,
                response_url='/api/jobs/' + job['id'] + '/response' if available else None)
            return value

    def list_jobs(self):
        with self.lock:
            return [self.public_job(j) for j in sorted(self.jobs.values(), key=lambda j: j.get('created', j.get('updated', '')), reverse=True)]

    def response_folder(self, job):
        if not job.get('project_id') or not job.get('stage_folder'):
            return None
        base = self.workbench.folder(job['project_id']).resolve()
        folder = (base / job['stage_folder']).resolve()
        if not folder.is_relative_to(base):
            raise ValueError('응답 경로가 올바르지 않아요.')
        return folder

    def response(self, jid):
        with self.lock:
            job = self.get_job(jid)
            def item(value):
                folder = self.response_folder(value)
                raw = (folder / 'story.txt').read_text(encoding='utf-8') if folder and (folder / 'story.txt').exists() else ''
                result = {}
                if folder:
                    for name in ('result.json', 'attempt.json'):
                        if (folder / name).exists():
                            result[name.removesuffix('.json')] = read(folder / name)
                    if not raw:
                        for name in ('response.txt', 'response.json'):
                            if (folder / name).exists():
                                raw = (folder / name).read_text(encoding='utf-8', errors='replace')
                                break
                return dict(job_id=value['id'], stage=value.get('stage'), text=raw, result=result,
                            files=[p.name for p in folder.iterdir() if p.is_file()] if folder and folder.exists() else [])
            result = item(job)
            previous, seen = job.get('retry_of'), set()
            result['attempts'] = []
            while previous and previous not in seen and previous in self.jobs:
                seen.add(previous)
                ancestor = self.jobs[previous]
                result['attempts'].append(item(ancestor))
                previous = ancestor.get('retry_of')
            return result

    def job(self, kind, payload, project_id=None, target_id=None, *, retry=None, restart=False,
            auto_run_id=None, auto_step_key=None, automatic_retries=0):
        with self.lock:
            validate_job_payload(kind, payload)
            if kind not in ('create', 'expand', 'settings', 'edit_panel', 'reroll_panel', 'render', 'direct', 'compose','studio_render','add_page'):
                raise ValueError('작업 종류를 확인해 주세요.')
            pid = project_id or identity('p')
            self.workbench.folder(pid)
            self.auto.guard(pid, auto_run_id)
            if auto_run_id and auto_step_key and not retry:
                matches = [j for j in self.jobs.values() if j.get('auto_run_id') == auto_run_id and j.get('auto_step_key') == auto_step_key]
                if matches:
                    previous = max(matches, key=lambda j: j['created'])
                    if (previous['kind'], previous['payload'], previous['target_id'], previous['project_id']) != (kind, payload, target_id, pid):
                        raise ValueError('저장된 자동 단계와 요청이 달라요.')
                    return dict(job_id=previous['id'])
            if pid in self.active:
                raise ValueError('이 작품의 이전 요청이 진행 중이에요. 완료 후 다시 시도해 주세요.')
            base = disk_project(self.workbench, pid)
            if kind != 'create' and base is None:
                raise FileNotFoundError(pid)
            jid = identity('j')
            job = dict(id=jid, kind=kind, payload=copy.deepcopy(payload), target_id=target_id,
                project_id=pid, operation_id=retry['operation_id'] if retry and not restart else identity('o'),
                status='queued', message='요청을 준비하고 있어요.', error=None, stage=None,
                created=now(), updated=now(), retry_of=retry['id'] if retry else None, restart=restart,
                base_project=copy.deepcopy(base), base_revision=base.get('revision') if base else None,
                base_sha256=digest(base), automatic_retries=automatic_retries)
            if retry and not restart and retry.get('resume_project') is not None:
                job['resume_project'] = copy.deepcopy(retry['resume_project'])
            if auto_run_id:
                job.update(auto_run_id=auto_run_id, auto_step_key=auto_step_key)
            self.jobs[jid] = job
            self.active.add(pid)
            self.persist(job)
            if retry:
                retry['retry_job_id'] = jid
                self.persist(retry)
            self.pool.submit(self.work, jid)
            return dict(job_id=jid)

    def retry(self, jid, *, restart=False, auto_run_id=None, automatic=False):
        with self.lock:
            job = self.get_job(jid)
            self.auto.guard(job['project_id'], auto_run_id)
            public = self.public_job(job)
            if not public['restartable' if restart else 'retryable']:
                raise ValueError(public['retry_conflict'] or '실패하거나 중단된 생성 단계만 재시도할 수 있어요.')
            if job['project_id'] in self.active:
                raise ValueError('이 작품의 이전 요청이 진행 중이에요.')
            if not restart:
                archive_failed_stage(self.workbench, job, automatic=automatic)
                self.persist(job)
            return self.job(job['kind'], job['payload'], job['project_id'], job.get('target_id'), retry=job, restart=restart,
                            auto_run_id=auto_run_id or job.get('auto_run_id'), auto_step_key=job.get('auto_step_key'),
                            automatic_retries=job.get('automatic_retries', 0)+1 if automatic else 0)

    def work(self, jid):
        job = self.jobs[jid]
        def changed():
            with self.lock:
                self.persist(job)
        def progress(message):
            with self.lock:
                job['message'] = str(message)
                self.persist(job)
        with RecoverySession(self.workbench, job, changed) as session:
            try:
                with self.lock:
                    job['status'] = 'running'
                    self.persist(job)
                    message = conflict(self.workbench, job)
                    if message:
                        raise ValueError(message)
                wb, kind, pid = self.workbench, job['kind'], job['project_id']
                if kind == 'create':
                    project = wb.create(job['payload'], progress)
                elif kind == 'render':
                    project = wb.render(pid, job['target_id'], progress, payload=job['payload'])
                elif kind == 'studio_render':
                    from .image_studio import render
                    project = render(wb,pid,job['target_id'],job['payload'],progress)
                elif kind == 'add_page':
                    from .iterative_add_page import add_page
                    project = add_page(wb,pid,job['payload'],progress)
                elif kind in ('edit_panel', 'reroll_panel'):
                    project = getattr(wb, kind)(pid, job['target_id'], job['payload'], progress)
                elif kind == 'compose':
                    project = wb.page(pid, job['payload'])
                elif kind == 'direct':
                    from .iterative_director import plan_next
                    project = wb.load(pid)
                    folder = wb.operation(pid, dict(operation='direct', **job['payload']))
                    progress('실제 전개를 읽고 다음 연결·강조·결말을 판단하고 있어요.')
                    decision = plan_next(wb, project, job['payload'], folder)
                    project['auto_decision'] = decision
                    project['operations'].append(dict(id=folder.name, intent='direct', decision=decision))
                    if decision['should_end']:
                        project['ended'] = True
                    wb.commit(project)
                else:
                    project = getattr(wb, kind)(pid, job['payload'], progress)
                with self.lock:
                    session.flush(success=True)
                    job.update(status='complete', message='완료했어요.', project_id=project['id'],
                               result_project_sha256=digest(disk_project(wb, pid)))
            except Exception as exc:
                message = ('응답 JSON 형식이 올바르지 않아요. 원문을 보존했어요. ' + str(exc)) if isinstance(exc, json.JSONDecodeError) else str(exc) if isinstance(exc, (ValueError, InterruptedError, FileNotFoundError)) else '요청을 완료하지 못했어요. 받은 원문은 보존했어요.'
                with self.lock:
                    if job['kind'] in ('render','studio_render') and session.pending is not None:
                        try:
                            session.flush(success=False)
                        except ValueError as guard_error:
                            message = str(guard_error)
                    job.update(status='failed', message=message, error=message, error_type=type(exc).__name__)
                    job['automatic_retry_pending'] = bool(isinstance(exc,json.JSONDecodeError)
                        and not job.get('auto_run_id') and job['kind'] not in ('render','studio_render')
                        and job.get('automatic_retries',0)<2)
            finally:
                with self.lock:
                    self.active.discard(job['project_id'])
                    self.persist(job)

        # AutoBooks/StoryStarts already own retries for their steps. Manual text
        # jobs get one stage retry and one fresh operation, never image retries.
        if (job['status'] == 'failed' and not job.get('auto_run_id')
                and job['kind'] not in ('render', 'studio_render')
                and job.get('error_type') == 'JSONDecodeError'
                and job.get('automatic_retries', 0) < 2):
            try:
                with self.lock:
                    restart = job.get('automatic_retries', 0) >= 1
                    result = self.retry(jid, automatic=True, restart=restart)
                    child = self.get_job(result['job_id'])
                    child['message'] = 'JSON 오류로 이 요청을 처음부터 한 번 다시 시도합니다.' if restart else 'JSON 오류가 난 단계만 자동 재시도합니다.'
                    self.persist(child)
            except (ValueError, OSError) as exc:
                with self.lock:
                    job['message'] += ' 자동 재시도를 시작하지 못했어요: ' + str(exc)
                    self.persist(job)
            finally:
                with self.lock:
                    job['automatic_retry_pending'] = False
                    self.persist(job)

    def mutation(self, pid, method, *args):
        with self.lock:
            self.auto.guard(pid)
            if pid in self.active:
                raise ValueError('진행 중인 요청이 완료된 뒤 수정해 주세요.')
            return getattr(self.workbench, method)(pid, *args)

