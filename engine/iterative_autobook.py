"""Checkpointed editorial loop; each model operation commits independently."""
import copy
import random
import threading
import time

from .iterative_comic import identity, now, read, save
from .iterative_recovery import digest, disk_project


def limits(target):
    delta = min(2, max(1, round(target * .1)))
    return max(1, target - delta), target + delta


def writer_instruction(decision):
    """Use editorial evidence in the next request, never as accepted story state."""
    instruction = decision['instruction']
    if decision.get('should_end'):
        return instruction
    notes = decision.get('editorial', {})
    for field, label in (('visible_evidence', 'The next image must make visible'),
                         ('camera_goal', 'Frame the evidence this way')):
        value = notes.get(field) if isinstance(notes, dict) else None
        if isinstance(value, str) and value.strip() and value.strip() not in instruction:
            instruction += '\n' + label + ': ' + value.strip()
    if decision['action'] == 'finish':
        instruction += '\nAdvance the established resolution through the requested visible step. No new conflict or sequel hook. Set ended=true only when the actual panels show an earned resolution and final image.'
    return instruction


class AutoBooks:
    def __init__(self, app):
        self.app = app
        self.runs = {}
        self.threads = {}
        for path in (app.workbench.directory / '_auto').glob('a*.json'):
            run = read(path)
            if run['status'] == 'running':
                run.update(status='paused', pause_requested=False,
                           message='서버가 중단되었어요. 저장된 단계부터 재개할 수 있어요.')
                self.persist(run)
            self.runs[run['id']] = run

    def persist(self, run):
        run['updated'] = now()
        save(self.app.workbench.directory / '_auto' / (run['id'] + '.json'), run)

    def get(self, rid):
        if rid not in self.runs:
            raise FileNotFoundError(rid)
        return self.runs[rid]

    def public(self, run):
        with self.app.lock:
            value = {k: copy.deepcopy(run.get(k)) for k in (
                'id', 'status', 'message', 'project_id', 'current_job_id', 'reason',
                'reason_ko', 'updated', 'created', 'pause_requested', 'adjusted_target_pages',
                'render_images', 'generation_options', 'panels_per_page', 'min_panels_per_page', 'max_panels_per_page', 'min_pages', 'max_pages')}
            project = disk_project(self.app.workbench, run['project_id']) or {}
            value['last_decision'] = copy.deepcopy(run.get('decisions', [])[-1]) if run.get('decisions') else None
            value['progress'] = dict(pages=len(project.get('pages', [])),
                panels=len(project.get('panels', [])), target_pages=run['target_pages'],
                rendered_pages=sum(bool(p.get('image_url')) and p.get('status') == 'rendered' and not p.get('stale')
                                   for p in project.get('pages', [])))
            return value

    def list(self):
        with self.app.lock:
            return [self.public(r) for r in sorted(self.runs.values(), key=lambda r: r['created'], reverse=True)]

    def guard(self, pid, run_id=None):
        if any(r['project_id'] == pid and r['status'] == 'running' and r['id'] != run_id
               for r in self.runs.values()):
            raise ValueError('자동 완성이 진행 중이에요. 일시정지가 완료된 뒤 수정해 주세요.')

    def start(self, payload):
        from .iterative_comic_server import validate_job_payload
        with self.app.lock:
            target = payload.get('target_pages')
            low = payload.get('min_panels_per_page', payload.get('panels_per_page'))
            count = payload.get('max_panels_per_page', payload.get('panels_per_page', low))
            if type(low) is not int or type(count) is not int or not 1 <= low <= count <= 5:
                raise ValueError('페이지당 최소·최대 컷 수는 1~5이며 최소가 최대보다 클 수 없어요.')
            if type(target) is not int or not 1 <= target <= 30 or type(count) is not int or not 1 <= count <= 5:
                raise ValueError('목표 페이지는 1~30, 페이지당 컷 수는 1~5로 지정해 주세요.')
            if type(payload.get('render_images', True)) is not bool:
                raise ValueError('그림 생성 여부를 확인해 주세요.')
            if any(r['status'] == 'running' for r in self.runs.values()):
                raise ValueError('다른 자동 완성이 진행 중이에요. 완료하거나 일시정지한 뒤 시작해 주세요.')
            pid = payload.get('project_id') or identity('p')
            self.app.workbench.folder(pid)
            if pid in self.app.active:
                raise ValueError('이 작품의 진행 중 요청이 끝난 뒤 시작해 주세요.')
            project = disk_project(self.app.workbench, pid)
            brief = copy.deepcopy(payload.get('brief', {}))
            if payload.get('project_id') and project is None:
                raise FileNotFoundError(pid)
            if not project:
                if not isinstance(brief, dict):
                    raise ValueError('이야기 설정을 확인해 주세요.')
                brief['count'] = min(2, low)
                brief['_auto_prepare_cast'] = True
                validate_job_payload('create', brief)
            from .translation_modes import options
            generation_options = options(payload.get('generation_options'), options((project or brief).get('generation_options')))
            if not project:
                brief['generation_options'] = generation_options
            minimum, maximum = limits(target)
            if project and len(project.get('pages', [])) > maximum:
                raise ValueError('이미 만든 페이지보다 큰 전체 목표를 지정해 주세요.')
            run = dict(id=identity('a'), status='running', created=now(), project_id=pid,
                target_pages=target, panels_per_page=count, min_panels_per_page=low, max_panels_per_page=count, page_targets={}, min_pages=minimum, max_pages=maximum,
                render_images=payload.get('render_images', True), brief=brief, generation_options=generation_options,
                message='자동 완성을 준비하고 있어요.', reason='', reason_ko='',
                current_job_id=None, step=None, sequence=0, decisions=[], stop=False,
                pause_requested=False, last_project_sha256=digest(project))
            self.runs[run['id']] = run
            self.persist(run)
            self.launch(run)
            return dict(run_id=run['id'])

    def launch(self, run):
        thread = threading.Thread(target=self.loop, args=(run['id'],), daemon=True)
        self.threads[run['id']] = thread
        thread.start()

    def pause(self, rid):
        with self.app.lock:
            run = self.get(rid)
            if run['status'] == 'running':
                run.update(pause_requested=True, message='현재 요청을 보존한 뒤 일시정지해요.')
                self.persist(run)
            return self.public(run)

    def resume(self, rid):
        with self.app.lock:
            run = self.get(rid)
            if run['status'] == 'complete' or run['status'] == 'running':
                return self.public(run)
            if any(r['status'] == 'running' for r in self.runs.values()) or run['project_id'] in self.app.active:
                raise ValueError('진행 중인 요청이 끝난 뒤 재개해 주세요.')
            project = disk_project(self.app.workbench, run['project_id'])
            step = run.get('step')
            if step and not step.get('job_id'):
                matches = [j for j in self.app.jobs.values() if j.get('auto_run_id') == rid and j.get('auto_step_key') == step['key']]
                if matches:
                    step['job_id'] = max(matches, key=lambda j: j['created'])['id']
            if digest(project) != run.get('last_project_sha256') and not (step and step.get('job_id')):
                # User edits while paused become the new accepted story, never a stale decision.
                run.update(step=None, stop=False, last_project_sha256=digest(project))
            if step and step.get('job_id'):
                job = self.app.get_job(step['job_id'])
                while job.get('retry_job_id'):
                    job = self.app.get_job(job['retry_job_id'])
                if job['status'] in ('failed', 'interrupted'):
                    # This explicit resume permits one attempt even for an image failure.
                    step['attempt_offset'] = sum(j.get('auto_run_id') == rid and j.get('auto_step_key') == step['key'] for j in self.app.jobs.values())
                    result = self.app.retry(job['id'], auto_run_id=rid)
                    step['job_id'] = result['job_id']
                    step.update(retries=1, restarted=False)
                    run['current_job_id'] = result['job_id']
                else:
                    step['job_id'] = job['id']
            run.update(status='running', pause_requested=False, message='저장한 단계부터 이어가고 있어요.')
            self.persist(run)
            self.launch(run)
            return self.public(run)

    def queue(self, run, kind, payload, target=None):
        if kind == 'expand' and 'generation_options' in run:
            payload = dict(payload, generation_options=copy.deepcopy(run['generation_options']))
        run['sequence'] += 1
        # Bounded accepted steps; each failed text step gets at most one automatic retry.
        if run['sequence'] > run['max_pages'] * (2 * run['panels_per_page'] + 2) + 6:
            raise ValueError('자동 요청 한도에 도달했어요. 현재 결과와 판단을 확인해 주세요.')
        run['step'] = dict(key=str(run['sequence']), kind=kind, payload=payload,
                           target=target, job_id=None, retries=0)
        self.persist(run)

    def plan(self, run, project):
        if project is None:
            return self.queue(run, 'create', run['brief'])
        pages, panels = project['pages'], project['panels']
        assigned = {cid for page in pages for cid in page['panel_ids']}
        pending = [p['id'] for p in panels if p['id'] not in assigned]
        page_key = str(len(pages))
        targets = run.setdefault('page_targets', {})
        if page_key not in targets:
            targets[page_key] = random.randint(run.get('min_panels_per_page', run['panels_per_page']), run.get('max_panels_per_page', run['panels_per_page']))
            self.persist(run)
        per_page = targets[page_key]
        if pending and (len(pending) >= per_page or run['stop']):
            if len(pages) >= run['max_pages']:
                raise ValueError('소폭 조정 가능한 최대 페이지에 도달했어요. 남은 컷을 확인해 주세요.')
            layout = run['decisions'][-1]['layout'] if run['decisions'] else 'auto'
            return self.queue(run, 'compose', dict(panel_ids=pending[:per_page], layout=layout))
        if run['render_images']:
            missing = next((p for p in pages if not p.get('image_url') or p.get('status') != 'rendered' or p.get('stale')), None)
            if missing:
                return self.queue(run, 'render', {}, missing['id'])
        if run['stop']:
            run.update(status='complete', message='이야기와 페이지 생성을 마쳤어요.' if run['render_images'] else '이야기와 페이지 구성을 마쳤어요.',
                adjusted_target_pages=len(pages), current_job_id=None)
            self.persist(run)
            return
        self.queue(run, 'direct', dict(target_pages=run['target_pages'], panels_per_page=per_page,
            completed_pages=len(pages), current_page_slots=max(1, per_page - len(pending)),
            remaining_pages=max(0, run['target_pages'] - len(pages)),
            min_pages=run['min_pages'], max_pages=run['max_pages'], page_number=len(pages) + 1,
            is_last_request=(run['max_pages'] - len(pages)) * per_page - len(pending) <= 2,
            last_decisions=run['decisions'][-8:]))

    def accepted(self, run, job):
        project = self.app.workbench.load(run['project_id'])
        run.update(last_project_sha256=digest(project), step=None, current_job_id=None)
        if job.get('result_project_sha256') and job['result_project_sha256'] != digest(project):
            run.update(stop=False, message='수정한 이야기를 기준으로 다음 컷을 다시 판단해요.')
            self.persist(run)
            return
        if job['kind'] == 'direct':
            decision = copy.deepcopy(project['auto_decision'])
            run['decisions'].append(decision)
            run.update(reason=decision['reason_ko'], reason_ko=decision['reason_ko'], stop=decision['should_end'])
            if not run['stop']:
                if len(project['pages']) >= run['max_pages']:
                    raise ValueError('최대 분량까지 만들었지만 결말 확인이 되지 않았어요. 현재 결과를 확인해 주세요.')
                instruction = writer_instruction(decision)
                self.queue(run, 'expand', dict(anchor_id=project['panels'][-1]['id'], position='after',
                    intent=decision['intent'], dialogue=decision['dialogue'], count=decision['count'],
                    instruction=instruction))
        self.persist(run)

    def loop(self, rid):
        try:
            while True:
                with self.app.lock:
                    run = self.get(rid)
                    if run['status'] != 'running':
                        return
                    step = run.get('step')
                    if step and step.get('job_id'):
                        job = self.app.get_job(step['job_id'])
                        while job.get('retry_job_id'):
                            job = self.app.get_job(job['retry_job_id'])
                            step['job_id'] = job['id']
                        run['current_job_id'] = job['id']
                        if job['status'] == 'complete':
                            self.accepted(run, job)
                            continue
                        if job['status'] in ('failed', 'interrupted'):
                            attempts = sum(j.get('auto_run_id') == rid and j.get('auto_step_key') == step['key'] for j in self.app.jobs.values()) - step.get('attempt_offset', 0)
                            public = self.app.public_job(job)
                            restart = step.get('retries', 0) >= 1 or not public['retryable']
                            can_retry = public['restartable' if restart else 'retryable']
                            if run['pause_requested']:
                                run.update(status='paused', pause_requested=False, message='실패 결과를 보존하고 일시정지했어요.')
                                self.persist(run)
                                return
                            if attempts < 3 and not step.get('restarted') and can_retry:
                                result = self.app.retry(job['id'], auto_run_id=rid, automatic=True, restart=restart)
                                step.update(job_id=result['job_id'], retries=step.get('retries', 0) + 1, restarted=restart)
                                run.update(current_job_id=result['job_id'], message='이번 요청을 처음부터 한 번 다시 생성하고 있어요. 완료된 페이지는 유지합니다.' if restart else '실패 단계만 한 번 자동 재시도하고 있어요.')
                                self.persist(run)
                            else:
                                run.update(status='failed', pause_requested=False,
                                    message=job['message'] + ' 자동 복구를 완료하지 못했어요. 완료 결과와 원문은 보존했습니다. 요청을 수정하거나 다시 진행할지 확인해 주세요.')
                                self.persist(run)
                                return
                        elif not run['pause_requested'] and run['message'] != job['message']:
                            run['message'] = job['message']
                            self.persist(run)
                    else:
                        if run['pause_requested']:
                            run.update(status='paused', pause_requested=False, message='일시정지했어요. 저장된 결과부터 재개할 수 있어요.')
                            self.persist(run)
                            return
                        if not step:
                            project = disk_project(self.app.workbench, run['project_id'])
                            if digest(project) != run['last_project_sha256']:
                                raise ValueError('자동 진행 중 작품이 바뀌었어요. 저장된 내용을 확인해 주세요.')
                            self.plan(run, project)
                            continue
                        result = self.app.job(step['kind'], step['payload'], run['project_id'], step['target'],
                                              auto_run_id=rid, auto_step_key=step['key'])
                        step['job_id'] = result['job_id']
                        run['current_job_id'] = result['job_id']
                        self.persist(run)
                time.sleep(.2)
        except Exception as exc:
            with self.app.lock:
                run = self.get(rid)
                run.update(status='failed', pause_requested=False,
                           message=str(exc) if isinstance(exc, (ValueError, FileNotFoundError)) else '자동 진행을 중단했어요. 완료 결과와 원문은 보존했어요.')
                self.persist(run)
