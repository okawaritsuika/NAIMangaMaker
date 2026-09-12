"""Local reusable character presets; no generation, translation, or project mutation."""
import copy
import json
import re
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path


FIELDS = ('gender', 'name', 'personality', 'appearance', 'aliases')
LIMITS = dict(name=200, personality=5000, appearance=5000, alias=200, aliases=30)
ID = re.compile(r'ch[0-9a-f]{12}')


def path(workbench):
    return Path(workbench.directory) / '_settings' / 'character_library.json'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _fold(value):
    return unicodedata.normalize('NFKC', value).casefold().strip()


def _load(workbench):
    file = path(workbench)
    if not file.exists():
        return dict(version=1, characters=[])
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise ValueError('저장된 캐릭터 목록을 읽지 못했어요. 원본 파일은 보존했습니다.') from error
    if (not isinstance(data, dict) or data.get('version') != 1
            or not isinstance(data.get('characters'), list)):
        raise ValueError('캐릭터 저장 파일의 형식을 확인해 주세요. 원본 파일은 보존했습니다.')
    seen = set()
    for row in data['characters']:
        if (not isinstance(row, dict) or not isinstance(row.get('id'), str)
                or not ID.fullmatch(row['id']) or row['id'] in seen
                or any(not isinstance(row.get(key), str) for key in ('name', 'personality', 'appearance'))
                or not isinstance(row.get('aliases'), list)
                or any(not isinstance(alias, str) for alias in row['aliases'])
                or not isinstance(row.get('history'), list)
                or any(not isinstance(row.get(key), str) for key in ('created', 'updated'))
                or type(row.get('revision')) is not int or row['revision'] < 1
                or type(row.get('archived')) is not bool):
            raise ValueError('캐릭터 저장 항목의 형식을 확인해 주세요. 원본 파일은 보존했습니다.')
        row.setdefault('gender', 'auto')
        if row['gender'] not in ('auto','girl','boy'):raise ValueError('저장된 성별을 확인해 주세요.')
        seen.add(row['id'])
    return data


def _write(workbench, data):
    file = path(workbench)
    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.with_name(file.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(file)
    finally:
        if temporary.exists():
            temporary.unlink()


def _public(row):
    return {key: copy.deepcopy(row[key]) for key in ('id', *FIELDS, 'created', 'updated', 'archived', 'revision')}


def _listed(data, query=''):
    if not isinstance(query, str) or len(query) > 2000:
        raise ValueError('검색어는 2,000자 이내의 문자열로 입력해 주세요.')
    needle = _fold(query)
    rows = [row for row in data['characters'] if not row['archived']]
    if needle:
        rows = [row for row in rows if any(needle in _fold(value)
            for value in (row['name'], *row['aliases'], row['personality'], row['appearance']))]
    return [_public(row) for row in sorted(rows, key=lambda row: (row['updated'], row['id']), reverse=True)]


def public(workbench, query=''):
    """Return active presets. Search is local; archived records remain on disk."""
    return dict(characters=_listed(_load(workbench), query))


def _validate(payload, previous=None):
    if not isinstance(payload, dict) or set(payload) - set(('id', *FIELDS)):
        raise ValueError('캐릭터 이름·성격·외형·별칭 항목을 확인해 주세요.')
    value = {key: copy.deepcopy(previous[key]) if previous else ([] if key == 'aliases' else 'auto' if key == 'gender' else '') for key in FIELDS}
    value.update({key: copy.deepcopy(payload[key]) for key in FIELDS if key in payload})
    if value['gender'] not in ('auto','girl','boy'):raise ValueError('성별을 확인해 주세요.')
    for key in ('name', 'personality', 'appearance'):
        if not isinstance(value[key], str) or len(value[key]) > LIMITS[key]:
            raise ValueError(f'{key} 항목은 {LIMITS[key]:,}자 이내의 문자열로 입력해 주세요.')
    value['name'] = value['name'].strip()
    if not value['name']:
        raise ValueError('캐릭터 이름을 입력해 주세요.')
    aliases = value['aliases']
    if not isinstance(aliases, list) or len(aliases) > LIMITS['aliases']:
        raise ValueError('별칭은 최대 30개의 문자열 목록으로 입력해 주세요.')
    cleaned, seen = [], set()
    for alias in aliases:
        if not isinstance(alias, str) or not alias.strip() or len(alias) > LIMITS['alias']:
            raise ValueError('각 별칭은 1~200자의 문자열로 입력해 주세요.')
        alias = alias.strip()
        normalized = _fold(alias)
        if normalized not in seen:
            cleaned.append(alias)
            seen.add(normalized)
    value['aliases'] = cleaned
    return value


def _find(data, character_id):
    if not isinstance(character_id, str) or not ID.fullmatch(character_id):
        raise ValueError('캐릭터 프리셋 번호가 올바르지 않아요.')
    row = next((row for row in data['characters'] if row['id'] == character_id), None)
    if row is None:
        raise ValueError('저장된 캐릭터 프리셋을 찾을 수 없어요.')
    return row


def _record(row, action):
    row['history'].append(dict(action=action, at=row['updated'], snapshot=_public(row)))


def store(workbench, payload):
    """Create or update a preset, preserving all earlier snapshots in history."""
    if not isinstance(payload, dict):
        raise ValueError('캐릭터 입력은 객체여야 해요.')
    data = _load(workbench)
    previous = _find(data, payload['id']) if 'id' in payload else None
    if previous and previous['archived']:
        raise ValueError('목록에서 삭제된 프리셋입니다. 새 캐릭터로 저장해 주세요.')
    value = _validate(payload, previous)
    if previous and all(previous[key] == value[key] for key in FIELDS):
        return dict(character=_public(previous), characters=_listed(data))
    stamp = _now()
    if previous:
        previous.update(value, updated=stamp, revision=previous['revision'] + 1)
        row = previous
        _record(row, 'updated')
    else:
        used = {row['id'] for row in data['characters']}
        character_id = 'ch' + uuid.uuid4().hex[:12]
        while character_id in used:
            character_id = 'ch' + uuid.uuid4().hex[:12]
        row = dict(id=character_id, **value, created=stamp, updated=stamp,
                   archived=False, revision=1, history=[])
        _record(row, 'created')
        data['characters'].append(row)
    _write(workbench, data)
    return dict(character=_public(row), characters=_listed(data))


def archive(workbench, character_id):
    """Hide a preset from selection while keeping its data and revision history."""
    data = _load(workbench)
    row = _find(data, character_id)
    if not row['archived']:
        row.update(archived=True, updated=_now(), revision=row['revision'] + 1)
        _record(row, 'archived')
        _write(workbench, data)
    return dict(character=_public(row), characters=_listed(data))
