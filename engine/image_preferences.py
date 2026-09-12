"""Validated local image defaults, snapshotted into newly composed pages."""
import copy
import math

SAMPLERS = ['k_euler_ancestral', 'k_euler', 'k_dpmpp_2s_ancestral', 'k_dpmpp_2m', 'k_dpmpp_sde', 'ddim_v3']
NOISE_SCHEDULES = ['native', 'karras', 'exponential', 'polyexponential']
LIMITS = dict(steps=dict(min=1, max=50), scale=dict(min=0, max=10), cfg_rescale=dict(min=0, max=1),
              width=dict(min=256, max=2048, step=64), height=dict(min=256, max=2048, step=64))
MAX_PIXELS = 3145728
NUMERIC = ('steps', 'scale', 'cfg_rescale', 'width', 'height')
FIELDS = (*NUMERIC, 'sampler', 'noise_schedule', 'style_prompt', 'negative_prompt')


def original_defaults():
    from .iterative_comic_render import REFERENCE, read
    ref = read(REFERENCE)
    style = ref['prompt'].split(', color, fully clothed,', 1)[0] + ', color, fully clothed'
    style = style.replace('detailed fantasy background', 'detailed background')
    return dict(**{k: ref[k] for k in (*NUMERIC, 'sampler', 'noise_schedule')},
                style_prompt=style, negative_prompt=ref['uc'])


def validate(value, *, base=None):
    if not isinstance(value, dict) or set(value) - set(FIELDS):
        raise ValueError('이미지 설정 항목을 확인해 주세요.')
    result = dict(copy.deepcopy(base) if base is not None else {}, **copy.deepcopy(value))
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
                max_pixels=MAX_PIXELS, samplers=SAMPLERS, noise_schedules=NOISE_SCHEDULES)


def store(workbench, payload):
    from .iterative_comic import save, now
    if not isinstance(payload, dict) or set(payload) != {'settings'}:
        raise ValueError('settings 객체로 기본값을 저장해 주세요.')
    value = validate(payload['settings'], base=load(workbench))
    save(path(workbench), dict(settings=value, updated=now()))
    return public(workbench)
