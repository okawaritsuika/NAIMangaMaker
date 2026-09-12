"""Anlas eligibility checks for this workbench's single V5 text-to-image request."""
import json
import urllib.request

FREE_PIXELS=1024*1024
FREE_STEPS=28
SOURCE='https://docs.novelai.net/en/subscription/'

def subscription(api_key):
    request=urllib.request.Request('https://image.novelai.net/user/subscription',headers={
        'Authorization':'Bearer '+api_key,'User-Agent':'Mozilla/5.0',
        'Accept':'application/json','Cache-Control':'no-cache'})
    with urllib.request.urlopen(request,timeout=30) as response:
        raw=json.load(response)
    usage=raw.get('usage') or {}
    percent=usage.get('percent')
    return dict(active=raw.get('active'),tier=raw.get('tier'),
        remaining_percent=(0 if usage.get('isNegative') else percent) if type(percent) in (int,float) else None)

def assess(settings, account):
    width,height,steps=(settings[k] for k in ('width','height','steps'))
    reasons=[]
    if width*height>FREE_PIXELS:
        reasons.append(f'해상도 {width}×{height} = {width*height:,}픽셀로 Anlas 미사용 상한 1,048,576픽셀을 넘습니다.')
    if steps>FREE_STEPS:
        reasons.append(f'Steps {steps}로 Anlas 미사용 상한 28을 넘습니다.')
    unknown=account.get('active') is None or account.get('tier') is None
    if not unknown and (account['active'] is not True or account['tier']!=3):
        reasons.append('현재 계정에 활성 Opus의 Anlas 미사용 혜택이 없습니다.')
    remaining=account.get('remaining_percent')
    blocked=remaining is None or remaining<=5 or unknown
    if remaining is not None and remaining<=0:
        reasons.append('V5 이미지 사용량을 소진해 Anlas 미사용 혜택을 적용할 수 없습니다.')
    message=('계정 또는 V5 잔량을 확인하지 못했어요. 잠시 후 다시 확인해 주세요.' if unknown or remaining is None else
             '이미지 잔량이 5% 이하라 기존 잔량 보호 규칙에 따라 생성이 중지됩니다.' if blocked else
             '이 설정으로 생성하면 Anlas가 사용됩니다.' if reasons else
             '현재 계정과 설정은 Anlas 미사용 범위입니다. V5 이미지 사용량은 차감됩니다.')
    return dict(width=width,height=height,steps=steps,pixels=width*height,
        requires_confirmation=bool(reasons) and not blocked,can_generate=not blocked,
        anlas_status='unknown' if unknown else 'paid' if reasons else 'free',
        message=message,reasons=reasons,remaining_percent=remaining,
        exact_anlas=None,source=SOURCE)

def ensure_allowance(api_key, *, settings, confirmed=False):
    result=assess(settings,subscription(api_key))
    if not result['can_generate']:
        raise InterruptedError(result['message'])
    if result['requires_confirmation'] and confirmed is not True:
        raise InterruptedError('Anlas 사용 확인이 필요해요. 페이지의 생성 버튼 또는 이미지 편집실에서 비용 안내를 확인한 뒤 생성해 주세요.')
    return result

def preview(workbench,pid,gid,data):
    from .image_preferences import validate
    current=workbench.preview_prompt(pid,gid)
    settings={k:current[k] for k in ('width','height','steps')}
    override=data.get('image_settings',{})
    validate(override)
    settings.update({k:override[k] for k in settings if k in override})
    validate(settings)
    try:
        account=subscription(workbench.key_provider())
    except Exception:
        account={}
    return assess(settings,account)
