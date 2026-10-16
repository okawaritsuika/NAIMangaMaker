"""Local prompt profiles and exact request previews for Workbench.call stages.

Profiles affect new stage snapshots only. Recovery keeps the saved profile;
an explicit retry may compact a known rejected context. No API key is read.
"""
import ast
import copy
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

VERSION = 'prompt-profiles-v1'
AUTHOR_NOTE = 'Perform only the current task. Established facts and accepted story remain authoritative.'
FIELDS = ('system_prompt', 'author_note', 'task_template', 'before_task', 'after_task')
ROOT = Path(__file__).resolve().parent
LOCK = threading.RLock()
STAGES = {
    'input_translation': ('입력 영어 번역', 'glm-4-6', '사용자가 입력한 문장을 영어 작업 지시로 변환합니다.', '입력에 영어 이외의 문자가 있을 때. ID·선택값·빈값은 유지합니다.'),
    'cast_plan': ('자동 시작 인물 계획', 'glm-4-6', '요청한 관계를 만족하는 최소 인물 설정을 준비합니다.', '자동 작품을 시작하며 사용자가 인물 설정을 지정하지 않았을 때.'),
    'story': ('이야기와 컷 작성', 'xialong-v1', '시작 컷, 이어쓰기·삽입, 같은 순간의 다른 구도, 선택 컷의 대체 표현을 작성합니다.', '작품 시작, 컷 추가, 강조, 카메라 리롤, 장면 변형에 공통 적용됩니다. 각 경우의 원형 지시와 JSON 응답 계약은 다릅니다.'),
    'state_resolution': ('인물의 현재 상태 확정', 'glm-4-6', '실제 묘사와 이전 상태를 읽어 등장인물의 전체 외형·소지품·자세를 확정합니다.', '새 이야기 컷을 검증하기 전. 원문 사건과 대사를 다시 쓰지 않습니다.'),
    'output_translation': ('표시용 한국어 번역', 'glm-4-6', '실제 영어 본문과 인물 관계를 바탕으로 한국어 표시 내용을 만듭니다.', '전체 번역 또는 대사만 번역 설정에 따라 요청과 응답 형식이 달라집니다. 영어 표시와 무대사 묶음에서는 생략될 수 있습니다.'),
    'conflict_translation': ('충돌 안내 번역', 'glm-4-6', '작성 모델이 반환한 앞뒤 장면 충돌 사유를 짧은 한국어로 옮깁니다.', '컷 추가 요청에 대해 모델이 conflict를 반환했을 때.'),
    'state_display': ('수정한 상태의 한국어 표시', 'glm-4-6', '현재 외형·소지품·자세를 한국어 한 문장으로 표시합니다.', '컷 직접 수정 과정에서 상태 표시를 갱신해야 할 때.'),
    'director': ('자동 페이지 편집 판단', 'glm-4-6', '실제 이야기와 목표 분량을 읽어 다음 연결 동작·진전·대사·강조·마무리를 고릅니다.', '자동 제작의 다음 요청을 선택할 때. 이미 작성된 결말을 근거로만 중단합니다.'),
}


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def defaults(stage, model=None):
    model = model or STAGES[stage][1]
    from .basic_expansion import SYSTEM
    return dict(system_prompt=SYSTEM if model == 'xialong-v1' else 'You are a helpful assistant.',
                author_note=AUTHOR_NOTE,
                task_template='{{task}}', before_task='', after_task='')


def _settings_path(workbench):
    return Path(workbench.directory) / '_settings' / 'prompt_profiles.json'


def _history_path(workbench):
    return Path(workbench.directory) / '_settings' / 'prompt_profile_history'


def _profiles(workbench):
    path = _settings_path(workbench)
    data = _read(path) if path.exists() else dict(version=VERSION, revision=0, profiles={})
    if (not isinstance(data, dict) or data.get('version') != VERSION or
            type(data.get('revision')) is not int or not isinstance(data.get('profiles'), dict)):
        raise ValueError('저장된 프롬프트 설정 형식을 확인해 주세요.')
    data.setdefault('presets', {})
    data.setdefault('bindings', {})
    return data


def _stage(value):
    if not isinstance(value, str) or value not in STAGES:
        raise ValueError('알려진 프롬프트 단계를 선택해 주세요.')
    return value


def _config(value, base):
    if not isinstance(value, dict) or set(value) - set(FIELDS):
        raise ValueError('프롬프트 설정에는 system_prompt, author_note, task_template, before_task, after_task만 사용할 수 있어요.')
    if any(not isinstance(text, str) or len(text) > 80000 for text in value.values()):
        raise ValueError('각 프롬프트 입력은 80,000자 이내 문자열이어야 해요.')
    result = dict(base, **value)
    if sum(map(len, result.values())) > 200000:
        raise ValueError('프롬프트 설정 전체는 200,000자 이내여야 해요.')
    return result


def _effective(data, stage, model=None):
    selected = data.get('presets', {}).get(data.get('bindings', {}).get(stage))
    return _config(selected['config'] if selected else data['profiles'].get(stage, {}), defaults(stage, model))


def _warnings(config, *, missing_example=False):
    warnings = [
        '{{task}}는 기존의 동적 지시 전체로 바뀝니다. 여기에는 응답 형식과 현재 컷·인물·요청 문맥이 함께 들어갈 수 있습니다.',
        '예시 전체를 고정 문장으로 붙여 넣으면 그 작품의 인물·장면·ID도 다음 요청에 고정될 수 있습니다. Memory와 이전 본문은 별도 읽기 전용 문맥입니다.',
        '기본 작가노트는 원래 Memory/이전 본문이 있는 요청에만 쓰입니다. 기본과 다른 작가노트는 문맥이 없는 요청에도 별도 system 메시지로 적용됩니다.',
        '변경은 아직 요청을 저장하지 않은 새 단계부터 적용됩니다. 복구·재시도에는 저장된 프롬프트 설정을 사용합니다.',
    ]
    if '{{task}}' not in config['task_template']:
        warnings.append('현재 템플릿에는 {{task}}가 없습니다. 기존 동적 지시와 그 안의 응답 형식·현재 작업 문맥을 자동으로 포함하지 않습니다.')
    if missing_example:
        warnings.append('이 단계의 실제 요청 예시가 아직 없습니다. 동적 지시가 빈 상태인 구성 미리보기이며 실제 호출을 만들지 않습니다.')
    return warnings


def _safe(value):
    """Redact credential-shaped data in the inspection API, never in live requests."""
    if isinstance(value, dict):
        return {key: ('[REDACTED]' if re.sub('[^a-z]', '', str(key).lower()) in
                     {'apikey', 'authorization', 'accesstoken', 'refreshtoken', 'password', 'secret'} else _safe(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r'\bpst-[A-Za-z0-9_-]{12,}', '[REDACTED]', value)
        return re.sub(r'\bBearer\s+[A-Za-z0-9_.-]{12,}', 'Bearer [REDACTED]', value, flags=re.I)
    return value


def _case(path, root, stage):
    for folder in path.parents:
        if folder == root or folder.name == 'operations':
            break
        source = folder / 'input_ko.json'
        if not source.exists():
            continue
        try:
            data = _read(source)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if data.get('operation') == 'reroll_panel':
            return '카메라 다시 선택' if data.get('mode') == 'camera' else '장면 변형'
        if data.get('operation') == 'edit_panel':
            return '컷 직접 수정'
        if 'anchor_id' in data:
            return {'action': '동작 연결', 'emphasis': '같은 순간 강조', 'dialogue': '대사 전개'}.get(data.get('intent'), '이어쓰기·삽입')
        if 'seed' in data:
            return '새 작품 시작'
    return STAGES[stage][0]


def _examples(workbench):
    root = Path(workbench.directory).resolve()
    result = {stage: [] for stage in STAGES}
    for path in root.glob('p*/operations/**/*_request.json'):
        stage = path.name.removesuffix('_request.json')
        if stage not in STAGES:
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            continue
        relative = resolved.relative_to(root).as_posix()
        try:
            stamp = path.stat().st_mtime
        except OSError:
            continue
        parts = Path(relative).parts
        result[stage].append(dict(id=hashlib.sha256(relative.encode()).hexdigest()[:24],
            project_id=parts[0], operation_id=parts[2], request_path=relative,
            updated=datetime.fromtimestamp(stamp, timezone.utc).isoformat(), model=STAGES[stage][1],
            case=_case(path, root, stage)))
    for rows in result.values():
        rows.sort(key=lambda row: (row['updated'], row['request_path']), reverse=True)
    return result


def _representative_examples(rows):
    selected = rows[:12]
    cases = {row['case'] for row in selected}
    for row in rows[12:]:
        if row['case'] not in cases:
            selected.append(row)
            cases.add(row['case'])
    return selected


@lru_cache(maxsize=1)
def _sources():
    """Show the live call sites alongside the separately displayed generated task."""
    result = {stage: [] for stage in STAGES}
    for name in ('iterative_comic.py', 'iterative_editing.py', 'translation_modes.py', 'iterative_director.py'):
        source = (ROOT / name).read_text(encoding='utf-8')
        tree = ast.parse(source)
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if (not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or
                    node.func.attr != 'call' or len(node.args) < 3 or not isinstance(node.args[1], ast.Constant)):
                continue
            stage = node.args[1].value
            if stage not in result:
                continue
            owner = node
            while owner in parents and not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                owner = parents[owner]
            result[stage].append(dict(path=name, line=node.lineno, function=getattr(owner, 'name', ''),
                                     code=ast.get_source_segment(source, node)))
    return result


def catalog(workbench):
    with LOCK:
        data = _profiles(workbench)
        history = {stage: [] for stage in STAGES}
        for path in sorted(_history_path(workbench).glob('*.json'), reverse=True):
            row = _read(path)
            if row.get('stage') in history and len(history[row['stage']]) < 20:
                history[row['stage']].append({key: row[key] for key in ('at', 'config', 'action', 'revision')})
    examples, sources = _examples(workbench), _sources()
    stages = []
    for stage, (title, model, purpose, when) in STAGES.items():
        config = _effective(data, stage)
        stages.append(dict(stage=stage, title=title, model=model, purpose=purpose, when=when,
            defaults=defaults(stage), config=config, overridden=stage in data['profiles'] or stage in data['bindings'], selected_preset=data['bindings'].get(stage),
            examples=_representative_examples(examples[stage]), sources=sources[stage], history=history[stage], warnings=_warnings(config)))
    return _safe(dict(version=VERSION, revision=data['revision'], stages=stages, presets=list(data['presets'].values()),
                      warnings=['이 화면은 Workbench.call의 텍스트 생성 8단계를 관리합니다. 이미지 프롬프트는 이미지 편집실에서 관리합니다.']))


def save_profile(workbench, payload):
    if not isinstance(payload, dict):
        raise ValueError('프롬프트 설정 요청은 객체여야 해요.')
    stage = _stage(payload.get('stage'))
    fields = {key: value for key, value in payload.items() if key != 'stage'}
    with LOCK:
        data = _profiles(workbench)
        previous = _effective(data, stage)
        config = _config(fields, previous)
        data['bindings'].pop(stage, None)
        data['profiles'][stage] = config
        data['revision'] += 1
        at = _now()
        _save(_history_path(workbench) / f'{data["revision"]:08d}_{stage}.json',
              dict(stage=stage, revision=data['revision'], at=at, action='save', config=config, previous=previous))
        _save(_settings_path(workbench), data)
    return catalog(workbench)


def reset_profile(workbench, payload):
    if not isinstance(payload, dict) or set(payload) != {'stage'}:
        raise ValueError('초기화할 stage만 지정해 주세요.')
    stage = _stage(payload.get('stage'))
    with LOCK:
        data = _profiles(workbench)
        previous = _effective(data, stage)
        data['bindings'].pop(stage, None)
        data['profiles'].pop(stage, None)
        data['revision'] += 1
        _save(_history_path(workbench) / f'{data["revision"]:08d}_{stage}.json',
              dict(stage=stage, revision=data['revision'], at=_now(), action='reset', config=defaults(stage), previous=previous))
        _save(_settings_path(workbench), data)
    return catalog(workbench)


def _parts(request):
    messages = request['messages']
    task = messages[-1]['content']
    memory = next((row['content'][8:] for row in messages[1:-1]
                   if row.get('role') == 'user' and row.get('content', '').startswith('Memory:\n')), '')
    history = '\n'.join(row['content'] for row in messages if row.get('role') == 'assistant')
    author_note = '\n'.join(row['content'] for row in messages[1:-1] if row.get('role') == 'system')
    return dict(generated_task=task, memory=memory, history=history, author_note=author_note)


def _apply(base_request, stage, config):
    request = copy.deepcopy(base_request)
    messages = request['messages']
    parts = _parts(base_request)
    messages[0]['content'] = config['system_prompt']
    task = config['task_template'].replace('{{task}}', parts['generated_task'])
    messages[-1]['content'] = '\n\n'.join(part for part in (config['before_task'], task, config['after_task']) if part)
    note = config['author_note'].strip()
    has_context = bool(parts['memory'] or parts['history'] or parts['author_note'])
    custom_note = config['author_note'] != defaults(stage, request['model'])['author_note']
    # Keep Memory/assistant history verbatim; replace only the distinct A/N message.
    messages[:] = [row for index, row in enumerate(messages) if index == 0 or row.get('role') != 'system']
    if note and (has_context or custom_note):
        position = next((i for i, row in enumerate(messages[1:], 1) if row['role'] == 'assistant'), len(messages) - 1)
        messages.insert(position, dict(role='system', content=note))
    return request


def _validate_retry_snapshot(stage, accepted, preserved):
    if not preserved:
        return
    base = preserved['base_request']
    if preserved['base_request_sha256'] != _digest(base):
        raise ValueError('저장된 원본 프롬프트 스냅샷이 변경되었어요. 원문을 보존했어요.')
    if (preserved['request_sha256'] != _digest(accepted) or
            preserved['request_sha256'] != _digest(preserved['request'])):
        raise ValueError('저장된 프롬프트 요청이 최초 스냅샷과 달라요. 원문을 보존했어요.')
    # A fitted request intentionally differs from applying the pinned profile to
    # the original base. Its stored hashes, not re-rendering, identify that input.
    if preserved.get('context_budget_version') != 1:
        original = _apply(base, stage, preserved['config']) if preserved['overridden'] else base
        if original != accepted:
            raise ValueError('저장된 프롬프트 설정과 요청이 달라요. 원문을 보존했어요.')


def context_budget_retry_marker(folder, stage, updates):
    """Validate and queue a known rejection without making any summary request."""
    folder = Path(folder)
    request_name, profile_name = stage + '_request.json', stage + '_prompt_profile.json'
    if not (folder / request_name).exists():
        return None
    with LOCK:
        accepted = updates[request_name] if request_name in updates else _read(folder / request_name)
        preserved = (updates[profile_name] if profile_name in updates else
                     _read(folder / profile_name) if (folder / profile_name).exists() else None)
        _validate_retry_snapshot(stage, accepted, preserved)
        base = preserved['base_request'] if preserved else accepted
        return dict(version=1, stage=stage, queued_at=_now(), request_sha256=_digest(accepted),
                    base_request_sha256=_digest(base))


def state_context_retry_updates(folder):
    """Prepare a smaller known state request, retaining its pinned prompt profile."""
    from .story_context import compact_state_context
    folder = Path(folder)
    request_path = folder / 'state_resolution_request.json'
    profile_path = folder / 'state_resolution_prompt_profile.json'
    if not request_path.exists():
        return {}
    with LOCK:
        accepted = _read(request_path)
        preserved = _read(profile_path) if profile_path.exists() else None
        base = preserved['base_request'] if preserved else accepted
        _validate_retry_snapshot('state_resolution', accepted, preserved)
        messages = base.get('messages', [])
        if not messages or messages[-1].get('role') != 'user':
            return {}
        task = messages[-1].get('content')
        if not isinstance(task, str) or not task.startswith('Resolve visual state only for the supplied existing panel descriptions.'):
            return {}
        instruction, separator, source = task.rpartition('\nInput:\n')
        if not separator:
            return {}
        try:
            context = json.loads(source)
        except ValueError:
            return {}
        if (not isinstance(context, dict) or context.get('state_context_version') == 'compact-v1' or
                not isinstance(context.get('accepted_prefix'), list) or
                not isinstance(context.get('cast'), dict) or not isinstance(context.get('panels'), list)):
            return {}
        compact = compact_state_context(context)
        updated_base = copy.deepcopy(base)
        updated_base['messages'][-1]['content'] = instruction + separator + json.dumps(compact, ensure_ascii=False)
        updated = (_apply(updated_base, 'state_resolution', preserved['config'])
                   if preserved and preserved['overridden'] else copy.deepcopy(updated_base))
        if len(json.dumps(updated, ensure_ascii=False)) >= len(json.dumps(accepted, ensure_ascii=False)):
            return {}
        updates = {'state_resolution_request.json': updated, 'state_context.json': compact}
        if preserved:
            profile = copy.deepcopy(preserved)
            profile.update(base_request=updated_base, request=updated,
                           base_request_sha256=_digest(updated_base), request_sha256=_digest(updated))
            updates['state_resolution_prompt_profile.json'] = profile
        return updates


def prepare_request(workbench, folder, stage, base_request):
    """Called only by Workbench.call, before its existing recovery stage runner."""
    if not isinstance(stage, str) or not re.fullmatch('[a-z][a-z0-9_]*', stage):
        raise ValueError('생성 단계 이름이 올바르지 않아요.')
    folder = Path(folder)
    saved = folder / (stage + '_request.json')
    snapshot = folder / (stage + '_prompt_profile.json')
    retry_path = folder / (stage + '_context_budget_retry.json')
    retry = None
    with LOCK:
        if saved.exists():
            accepted = _read(saved)
            if snapshot.exists():
                preserved = _read(snapshot)
                if preserved['base_request_sha256'] != _digest(base_request):
                    raise ValueError('현재 동적 지시 또는 문맥이 최초 요청과 달라요. 원문을 보존했어요.')
                if (preserved['request_sha256'] != _digest(accepted) or
                        preserved['request_sha256'] != _digest(preserved['request'])):
                    raise ValueError('저장된 프롬프트 요청이 최초 스냅샷과 달라요. 원문을 보존했어요.')
            elif accepted != base_request:
                raise ValueError('이 단계의 저장된 요청과 현재 요청이 달라요. 원문을 보존했어요.')
            if not retry_path.exists():
                return accepted
            retry = _read(retry_path)
            if (retry.get('version') != 1 or retry.get('stage') != stage or
                    retry.get('request_sha256') != _digest(accepted) or
                    retry.get('base_request_sha256') != _digest(base_request)):
                raise ValueError('문맥 재시도 기록과 저장된 요청이 달라요. 원문을 보존했어요.')
            if not snapshot.exists():
                # Legacy requests need an original base after fitting, too. Pin
                # it before the summary can fail or global profiles can change.
                preserved = dict(version=VERSION, stage=stage, at=_now(), profile_revision=0,
                    overridden=False, config=defaults(stage, accepted['model']), legacy_request=True,
                    base_request=copy.deepcopy(base_request), request=copy.deepcopy(accepted),
                    base_request_sha256=_digest(base_request), request_sha256=_digest(accepted))
                _save(snapshot, preserved)
            _validate_retry_snapshot(stage, accepted, preserved)
        elif retry_path.exists():
            raise ValueError('문맥 재시도에 필요한 저장된 요청을 찾을 수 없어요.')
        elif snapshot.exists():
            preserved = _read(snapshot)
            if preserved['base_request_sha256'] != _digest(base_request):
                raise ValueError('현재 동적 지시 또는 문맥이 최초 스냅샷과 달라요.')
            if preserved['request_sha256'] != _digest(preserved['request']):
                raise ValueError('저장된 프롬프트 스냅샷이 변경되었어요.')
        else:
            data = _profiles(workbench)
            config = _effective(data, stage, base_request['model'])
            custom = folder / 'story_profile_override.json'
            overridden = stage in data['profiles'] or stage in data['bindings']
            if stage == 'story' and custom.exists():
                config = _config(_read(custom)['config'], defaults(stage, base_request['model']))
                overridden = True
            request = _apply(base_request, stage, config) if overridden else copy.deepcopy(base_request)
            preserved = dict(version=VERSION, stage=stage, at=_now(), profile_revision=data['revision'],
                overridden=overridden, config=config, base_request=base_request, request=request,
                base_request_sha256=_digest(base_request), request_sha256=_digest(request))
            # Pin the profile before any memory-generation stages can fail.
            _save(snapshot, preserved)
    from .context_budget import fit_request
    request = fit_request(workbench, folder, stage, preserved['request'])
    if retry is not None:
        with LOCK:
            if (_read(retry_path) != retry or _digest(_read(saved)) != retry['request_sha256'] or
                    _read(snapshot) != preserved):
                raise ValueError('기억 요약 중 저장된 요청이 변경되었어요. 원문을 보존했어요.')
            preserved.update(request=request, request_sha256=_digest(request), context_budget_version=1)
            _save(snapshot, preserved)
            _save(saved, request)
            retry_path.unlink()
    elif request != preserved['request']:
        preserved.update(request=request, request_sha256=_digest(request), context_budget_version=1)
        with LOCK:
            _save(snapshot, preserved)
    return request


def preview(workbench, payload):
    if not isinstance(payload, dict) or set(payload) - {'stage', 'example_id', 'config'}:
        raise ValueError('미리보기에는 stage, example_id, config만 지정해 주세요.')
    stage = _stage(payload.get('stage'))
    data = _profiles(workbench)
    config = _config(payload.get('config', {}), _effective(data, stage))
    rows = _examples(workbench)[stage]
    wanted = payload.get('example_id')
    if wanted is not None and (not isinstance(wanted, str) or not re.fullmatch('[0-9a-f]{24}', wanted)):
        raise ValueError('요청 예시 번호가 올바르지 않아요.')
    example = next((row for row in rows if row['id'] == wanted), None) if wanted else next(iter(rows), None)
    if wanted and example is None:
        raise ValueError('이 단계의 요청 예시를 찾을 수 없어요.')
    if example:
        path = Path(workbench.directory) / example['request_path']
        original = _read(path)
        snapshot = path.with_name(stage + '_prompt_profile.json')
        preserved = _read(snapshot) if snapshot.exists() else None
        base = preserved['base_request'] if preserved else copy.deepcopy(original)
        example = dict(example, model=original.get('model'), provenance='profile_snapshot' if preserved else 'legacy_request',
                       profile_revision=preserved['profile_revision'] if preserved else None)
    else:
        base = dict(model=STAGES[stage][1], temperature=0.75 if STAGES[stage][1] == 'xialong-v1' else 0.25,
                    max_tokens=4096, enable_thinking=False, stream=True,
                    messages=[dict(role='system', content=defaults(stage)['system_prompt']), dict(role='user', content='')])
        original = None
    rendered = _apply(base, stage, config)
    parts, applied = _parts(base), _parts(rendered)
    return _safe(dict(version=VERSION, stage=stage, model=base['model'], example=example, config=config,
        generated_task=parts['generated_task'], memory=parts['memory'], history=parts['history'],
        original_author_note=parts['author_note'], author_note=applied['author_note'], author_note_applied=bool(applied['author_note']),
        original_request=original, base_request=base, preview_request=rendered,
        warnings=_warnings(config, missing_example=example is None)))


def save_preset(workbench, payload):
    stage = _stage(payload.get('stage'))
    title = payload.get('title', '')
    if not isinstance(title,str):
        raise ValueError('프롬프트 이름은 문자열이어야 해요.')
    title=title.strip()
    if not title or len(title)>80:
        raise ValueError('프롬프트 이름을 80자 이내로 입력해 주세요.')
    expand = payload.get('expand', False)
    if type(expand) is not bool or (expand and stage!='story'):
        raise ValueError('늘리기 메뉴에는 이야기 작성용 프롬프트만 추가할 수 있어요.')
    intent = payload.get('base_intent', 'story')
    if intent not in ('story','dialogue','action','emphasis'):
        raise ValueError('늘리기의 기본 동작을 선택해 주세요.')
    config = _config(payload.get('config', {}), defaults(stage))
    with LOCK:
        data = _profiles(workbench)
        pid = payload.get('id') or 'pr'+uuid.uuid4().hex[:12]
        if payload.get('id') and pid not in data['presets']:
            raise ValueError('저장된 프롬프트를 찾을 수 없어요.')
        previous = data['presets'].get(pid)
        if previous and previous['stage']!=stage:
            raise ValueError('다른 단계용은 새 프롬프트로 저장해 주세요.')
        data['revision']+=1
        row=dict(id=pid,title=title,stage=stage,config=config,expand=expand,base_intent=intent,updated=_now())
        data['presets'][pid]=row
        _save(Path(workbench.directory)/'_settings'/'prompt_preset_history'/f"{data['revision']:08d}.json",dict(previous=previous,current=row))
        _save(_settings_path(workbench),data)
    return dict(**catalog(workbench),saved_preset_id=pid)


def assign_preset(workbench,payload):
    stage=_stage(payload.get('stage'))
    with LOCK:
        data=_profiles(workbench)
        pid=payload.get('id')
        if pid:
            row=data['presets'].get(pid)
            if not row or row['stage']!=stage:
                raise ValueError('이 단계에 맞는 프롬프트를 선택해 주세요.')
            data['bindings'][stage]=pid
        else:
            data['bindings'].pop(stage,None)
        data['revision']+=1
        _save(_settings_path(workbench),data)
    return catalog(workbench)


def expansion_choices(workbench):
    with LOCK:
        return [dict(id=r['id'],title=r['title'],base_intent=r['base_intent']) for r in _profiles(workbench)['presets'].values() if r['stage']=='story' and r['expand']]


def capture_expansion_profile(workbench, payload):
    result=copy.deepcopy(payload)
    result.pop('_custom_story_profile',None)
    pid=result.get('custom_profile_id')
    if pid:
        with LOCK:
            row=_profiles(workbench)['presets'].get(pid)
            if not row or row['stage']!='story' or not row['expand']:
                raise ValueError('선택한 늘리기 프롬프트가 없거나 사용 해제되어 있어요. 다시 선택해 주세요.')
            result['_custom_story_profile']=copy.deepcopy(row)
            result['intent']=row['base_intent']
    return result
