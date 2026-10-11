import copy
import secrets

from .image_preferences import DEFAULT_MODEL, MEDIUM_MODEL

# NovelAI's V5 Full Medium uses the fixed Heavy UC preset.
# https://docs.novelai.net/en/image/effort/
MEDIUM_NEGATIVE = ('lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, '
    'jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone, screentone, '
    'multiple views, logo, too many watermarks, negative space, blank page')


def image_payload(settings,seed=None):
    p=copy.deepcopy(settings)
    prompt=p.pop('prompt')
    p.pop('color_mode',None)
    model=p.pop('model',DEFAULT_MODEL)
    if model not in (DEFAULT_MODEL,MEDIUM_MODEL):
        raise ValueError('지원하는 NovelAI 이미지 모델을 선택해 주세요.')
    for name in ['model_name','model_hash','request_type','version','stream','tag_hint_transparent_background','tag_hint_uc_preset','tag_hint_qt']:
        p.pop(name,None)
    p.update(seed=p.get('seed',secrets.randbelow(2**32)) if seed is None else seed,n_samples=1,steps=14)
    p['negative_prompt']=p.pop('uc','')
    if model==MEDIUM_MODEL:
        negative=('' if 'nsfw' in prompt.lower() else 'nsfw, ')+MEDIUM_NEGATIVE
        p.update(steps=14,sampler='k_euler_ancestral',noise_schedule='karras',
                 negative_prompt=negative,tag_hint_uc_preset=2,dynamic_thresholding=False,
                 deliberate_euler_ancestral_bug=False,prefer_brownian=True)
        for name in ('cfg_rescale','skip_cfg_above_sigma','sm','sm_dyn'):
            p.pop(name,None)
        if 'v4_negative_prompt' in p:
            caption=p['v4_negative_prompt']['caption']
            caption['base_caption']=negative
            for character in caption['char_captions']:
                character['char_caption']=''
    return dict(input=prompt,model=model,action='generate',parameters=p)
