"""Validated local image defaults, snapshotted into newly composed pages."""
import copy
import math
import re
import secrets
import unicodedata

SAMPLERS = ['k_euler_ancestral', 'k_euler', 'k_dpmpp_2s_ancestral', 'k_dpmpp_2m', 'k_dpmpp_sde', 'ddim_v3']
NOISE_SCHEDULES = ['native', 'karras', 'exponential', 'polyexponential']
LIMITS = dict(steps=dict(min=1, max=50), scale=dict(min=0, max=10), cfg_rescale=dict(min=0, max=1),
              width=dict(min=256, max=2048, step=64), height=dict(min=256, max=2048, step=64))
MAX_PIXELS = 3145728
NUMERIC = ('steps', 'scale', 'cfg_rescale', 'width', 'height')
FIELDS = (*NUMERIC, 'sampler', 'noise_schedule', 'style_prompt', 'negative_prompt', 'color_mode')


def original_defaults():
    from .iterative_comic_render import REFERENCE, read
    ref = read(REFERENCE)
    style = ref['prompt'].split(', color, fully clothed,', 1)[0] + ', color, fully clothed'
    style = style.replace('detailed fantasy background', 'detailed background')
    return dict(**{k: ref[k] for k in (*NUMERIC, 'sampler', 'noise_schedule')},
                style_prompt=style, negative_prompt=ref['uc'], color_mode='prompt')


def validate(value, *, base=None):
    if not isinstance(value, dict) or set(value) - set(FIELDS):
        raise ValueError('이미지 설정 항목을 확인해 주세요.')
    result = dict(copy.deepcopy(base) if base is not None else {}, **copy.deepcopy(value))
    if result.get('color_mode','prompt') not in ('prompt','monochrome'):
        raise ValueError('색상 모드는 프롬프트대로 또는 흑백을 선택해 주세요.')
    for key, limit in LIMITS.items():
        if key not in result:
            continue
        number = result[key]
        if (type(number) not in (int, float) or not math.isfinite(number)
                or not limit['min'] <= number <= limit['max']
                or (key in ('steps', 'width', 'height') and type(number) is not int)
                or ('step' in limit and number % limit['step'])):
            raise ValueError(f'{key}: {limit["min"]}~{limit["max"]}' + (' 범위의 64배수 정수로 입력해 주세요.' if 'step' in limit else ' 범위로 입력해 주세요.'))
    if result.get('width', 256) * result.get('height', 256) > MAX_PIXELS:
        raise ValueError('가로×세로는 3,145,728 픽셀 이하로 설정해 주세요.')
    for key, choices in (('sampler', SAMPLERS), ('noise_schedule', NOISE_SCHEDULES)):
        if key in result and result[key] not in choices:
            raise ValueError(key + ' 설정을 확인해 주세요.')
    for key in ('style_prompt', 'negative_prompt'):
        if key in result and (not isinstance(result[key], str) or len(result[key]) > 30000):
            raise ValueError('프롬프트는 30,000자 이내의 문자열이어야 해요.')
    return result


def path(workbench):
    return workbench.directory / '_settings' / 'image_defaults.json'


def load(workbench):
    from .iterative_comic import read
    saved = read(path(workbench))['settings'] if path(workbench).exists() else {}
    return validate(saved, base=original_defaults())


def public(workbench):
    return dict(settings=load(workbench), model='nai-diffusion-5-full', limits=copy.deepcopy(LIMITS),
                max_pixels=MAX_PIXELS, samplers=SAMPLERS, noise_schedules=NOISE_SCHEDULES,
                styles=styles(workbench))


def store(workbench, payload):
    from .iterative_comic import save, now
    if not isinstance(payload, dict) or set(payload) != {'settings'}:
        raise ValueError('settings 객체로 기본값을 저장해 주세요.')
    value = validate(payload['settings'], base=load(workbench))
    save(path(workbench), dict(settings=value, updated=now()))
    return public(workbench)


STYLE_FIELDS = ('style_prompt','negative_prompt','color_mode')


def styles(workbench):
    from .iterative_comic import read
    file=path(workbench).with_name('style_presets.json')
    if not file.exists(): return []
    rows=read(file)['styles']
    if not isinstance(rows,list): raise ValueError('저장한 그림체 목록을 확인해 주세요.')
    for row in rows:
        if (not isinstance(row,dict) or not re.fullmatch(r'st[0-9a-f]{12}',str(row.get('id','')))
                or not isinstance(row.get('name'),str) or not row['name'].strip()
                or not isinstance(row.get('settings'),dict) or set(row['settings'])!=set(STYLE_FIELDS)):
            raise ValueError('저장한 그림체 항목을 확인해 주세요.')
        validate(row['settings'])
    if len({row['id'] for row in rows})!=len(rows): raise ValueError('그림체 번호가 중복되었어요.')
    return rows


def save_style(workbench, payload):
    from .iterative_comic import save, now
    if not isinstance(payload,dict) or set(payload)-{'id','name','settings'}:
        raise ValueError('그림체 저장 항목을 확인해 주세요.')
    name=payload.get('name');value=payload.get('settings')
    if not isinstance(name,str) or not 1<=len(name.strip())<=80:
        raise ValueError('그림체 이름을 1~80자로 입력해 주세요.')
    if not isinstance(value,dict) or set(value)!=set(STYLE_FIELDS):
        raise ValueError('그림체·네거티브·색상 설정이 필요해요.')
    value=validate(value);rows=styles(workbench);rid=payload.get('id')
    existing=next((row for row in rows if row['id']==rid),None)
    if rid is not None and existing is None: raise ValueError('덮어쓸 그림체를 다시 선택해 주세요.')
    name=name.strip();normalized=unicodedata.normalize('NFKC',name).casefold()
    if any(row is not existing and unicodedata.normalize('NFKC',row['name']).casefold()==normalized for row in rows):
        raise ValueError('같은 이름이 있어요. 다른 이름을 쓰거나 해당 그림체를 선택해 덮어써 주세요.')
    row=dict(id=rid or 'st'+secrets.token_hex(6),name=name,settings=value,updated=now())
    if existing is None: rows.append(row)
    else: rows[rows.index(existing)]=row
    save(path(workbench).with_name('style_presets.json'),dict(version=1,styles=rows))
    return dict(styles=rows,saved_id=row['id'])


def delete_style(workbench, rid):
    from .iterative_comic import save
    rows=styles(workbench)
    if not any(row['id']==rid for row in rows): raise ValueError('삭제할 그림체를 다시 선택해 주세요.')
    rows=[row for row in rows if row['id']!=rid]
    save(path(workbench).with_name('style_presets.json'),dict(version=1,styles=rows))
    return dict(styles=rows)
