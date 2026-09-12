import copy
import secrets

def image_payload(settings,seed=None):
    p=copy.deepcopy(settings)
    prompt=p.pop('prompt')
    p.pop('color_mode',None)
    for name in ['model_name','model_hash','request_type','version','stream','tag_hint_transparent_background','tag_hint_uc_preset','tag_hint_qt']:
        p.pop(name,None)
    p.update(seed=p.get('seed',secrets.randbelow(2**32)) if seed is None else seed,n_samples=1,steps=14)
    p['negative_prompt']=p.pop('uc','')
    return dict(input=prompt,model='nai-diffusion-5-full',action='generate',parameters=p)
