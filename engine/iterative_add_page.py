"""Append one page through cached, atomic batches of the existing writer workflow."""
import copy
import hashlib

from .iterative_recovery import RecoveryMixin, conflict, current_session


def add_page(workbench, project_id, payload, progress=lambda _: None):
    """Create 1..5 new panels and one page inside the caller's RecoverySession.

    The caller alone flushes the completed project. Every retry starts from the
    saved job base, then replays completed stages using their original responses.
    """
    from .iterative_comic import read, save

    if not isinstance(payload, dict):
        raise ValueError('다음 페이지 요청을 확인해 주세요.')
    count = payload.get('count', 3)
    dialogue = payload.get('dialogue', 'auto')
    intent = payload.get('intent', 'story')
    instruction = payload.get('instruction', '')
    if type(count) is not int or not 1 <= count <= 5:
        raise ValueError('새 페이지의 컷 수는 1~5개로 선택해 주세요.')
    if dialogue not in ('auto', 'none', 'with') or intent not in ('story', 'action'):
        raise ValueError('새 페이지의 대사와 연결 방식을 확인해 주세요.')
    if not isinstance(instruction, str) or len(instruction) > 6000:
        raise ValueError('추가 지시는 6,000자 이내의 문장으로 입력해 주세요.')
    session = current_session(workbench)
    if session is None or not isinstance(workbench, RecoveryMixin):
        raise ValueError('다음 페이지 추가는 복구 가능한 작업 안에서 실행해 주세요.')
    if session.project_id != project_id or session.operation_scope is not None:
        raise ValueError('다음 페이지를 추가할 복구 작업을 확인해 주세요.')
    message = conflict(workbench, session.job)
    if message:
        raise ValueError(message)
    base = copy.deepcopy(session.job['base_project'])
    if not base or base.get('id') != project_id or not base.get('panels'):
        raise ValueError('이어서 만들 마지막 컷이 필요해요.')
    request = dict(operation='add_page', count=count, dialogue=dialogue,
                   intent=intent, instruction=instruction.strip())
    parent_folder = workbench.operation(project_id, request)
    before_path = parent_folder / 'add_page_before.json'
    if before_path.exists() and read(before_path) != base:
        raise ValueError('저장된 페이지 추가의 시작 작품이 달라요. 원문은 보존했어요.')
    if not before_path.exists():
        save(before_path, base)

    previous_pending = session.pending
    previous_scope = session.operation_scope
    session.pending = copy.deepcopy(base)
    new_ids, segments = [], []
    try:
        for index, offset in enumerate(range(0, count, 2)):
            batch_count = min(2, count - offset)
            project = workbench.load(project_id)
            previous_panels = copy.deepcopy(project['panels'])
            scope = 'o' + hashlib.sha256((session.job['operation_id'] + str(index)).encode('utf-8')).hexdigest()[:12]
            session.operation_scope = scope
            task = (
                f'Continue after the last actual panel to create ONE new page of {count} new panels in total. '
                f'This request is batch {index + 1}: create only new page panels {offset + 1} through {offset + batch_count}. '
                f'{offset} panels of this new page are already in the actual prefix; '
                f'{count - offset - batch_count} more will be requested after this batch. '
                'Continue the accepted action and state without replaying earlier events. '
                'Use the overall page instruction across the whole page; advance only this batch now. '
                'Choose the drawable moments, cameras and permitted dialogue for the current request. '
                'Do not force a story ending merely because this page ends.'
            )
            if request['instruction']:
                task += '\nOverall page instruction from the user:\n' + request['instruction']
            progress(f'새 페이지의 {offset + 1}~{offset + batch_count}번째 컷을 이어 만들고 있어요.')
            project = workbench.expand(project_id, dict(
                anchor_id=previous_panels[-1]['id'], position='after', intent=intent,
                count=batch_count, dialogue=dialogue, instruction=task), progress)
            if (project['panels'][:len(previous_panels)] != previous_panels or
                    len(project['panels']) != len(previous_panels) + batch_count or
                    project['pages'] != base['pages']):
                raise ValueError('새 컷을 추가하는 동안 기존 컷이나 페이지가 달라졌어요. 작품은 저장하지 않았어요.')
            ids = [row['id'] for row in project['panels'][len(previous_panels):]]
            all_ids = [row['id'] for row in project['panels']]
            if len(all_ids) != len(set(all_ids)):
                raise ValueError('새 컷 번호가 기존 컷과 중복돼요. 작품은 저장하지 않았어요.')
            new_ids.extend(ids)
            segments.append(dict(index=index, scope=scope, added_ids=ids))
        session.operation_scope = previous_scope
        progress('새로 만든 컷만 한 페이지로 묶고 있어요.')
        project = workbench.page(project_id, dict(panel_ids=new_ids, layout='auto'))
        if (project['pages'][:-1] != base['pages'] or
                project['pages'][-1]['panel_ids'] != new_ids or len(new_ids) != count):
            raise ValueError('새 페이지의 컷 구성을 확인하지 못했어요. 작품은 저장하지 않았어요.')
        save(parent_folder / 'add_page_result.json', dict(
            operation_id=session.job['operation_id'], new_page_id=project['pages'][-1]['id'],
            added_ids=new_ids, segments=segments, existing_panels_and_pages_unchanged=True))
        return project
    except BaseException:
        session.pending = previous_pending
        raise
    finally:
        session.operation_scope = previous_scope
