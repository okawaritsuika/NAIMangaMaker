"""Masked V5 editing with immutable source, API artifact and applied composite."""
import base64
import copy
import io
import json
import math
import secrets
import urllib.request
import urllib.error
import zipfile

from PIL import Image, ImageChops, ImageFilter

from .forced_infill_delivery import payload, save, read, sha
from .image_studio import artifact, check_source
from .iterative_page_rendering import _page, _set_overrides
from .iterative_comic import identity, now


def png(image):
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def inputs(workbench, pid, page, value):
    required={'render_id','image_sha256','mask','strength'}
    if not isinstance(value, dict) or not required <= set(value) or set(value)-required-{'feather'}:
        raise ValueError('인페인팅 원본과 마스크를 확인해 주세요.')
    strength = value['strength']
    if type(strength) not in (int,float) or not math.isfinite(strength) or not 0.01 <= strength <= 1:
        raise ValueError('변경 강도는 0.01~1 사이여야 해요.')
    if type(value.get('feather',8)) is not int or not 0 <= value.get('feather',8) <= 32:
        raise ValueError('경계 부드럽게는 0~32픽셀 사이여야 해요.')
    row = next((r for r in page.get('renders',[]) if r['id']==value['render_id']),None)
    if not row or row.get('status')!='rendered' or not row.get('verified'):
        raise ValueError('완료된 그림을 인페인팅 원본으로 선택해 주세요.')
    path = artifact(workbench,pid,row['folder']+'/page.png')
    checksum = sha(path)
    if not value['image_sha256'] or checksum != value['image_sha256'] or checksum != row.get('image_sha256'):
        raise ValueError('인페인팅 원본이 달라졌어요. 그림을 다시 선택해 주세요.')
    with Image.open(path) as original:
        width,height = original.size
        if width % 64 or height % 64 or not 256 <= width <= 2048 or not 256 <= height <= 2048 or width*height>3_145_728:
            raise ValueError('인페인팅 원본은 64배수, 한 변 256~2048, 전체 3,145,728픽셀 이하여야 해요.')
        source = original.convert('RGBA')
    try:
        encoded=value['mask']
        if not isinstance(encoded,str) or len(encoded)>5_500_000: raise ValueError()
        raw=base64.b64decode(encoded,validate=True)
        with Image.open(io.BytesIO(raw)) as image:
            if image.format!='PNG' or image.size!=source.size: raise ValueError()
            # Transparent pixels cannot accidentally become editable white.
            rgba=image.convert('RGBA')
            opaque=Image.new('RGBA',image.size,(0,0,0,255))
            opaque.alpha_composite(rgba)
            mask=opaque.convert('L').point(lambda x:255 if x>=128 else 0)
    except Exception as error:
        raise ValueError('원본과 같은 크기의 PNG 마스크가 필요해요.') from error
    if mask.getbbox() is None:
        raise ValueError('수정할 부분을 먼저 칠해 주세요.')
    return row,source,mask


def blend_mask(mask, radius):
    """Fade inward only, keeping every unselected pixel exactly unchanged."""
    if not radius: return mask
    distance=Image.new('L',mask.size)
    inner=mask
    for _ in range(radius+1):
        distance=ImageChops.add(distance,inner.point(lambda x:1 if x else 0))
        inner=inner.filter(ImageFilter.MinFilter(3))
    # A small isolated brush mark must still have a fully editable center.
    maximum=distance.getextrema()[1]
    return distance.point(lambda x:round(255*x/maximum) if maximum else 0)


def request_body(settings, source, mask, strength, legacy=False):
    body=payload(settings)
    body.update(model='nai-diffusion-5-full-inpainting',action='infill')
    params=body['parameters']
    params.update(image=base64.b64encode(png(source.convert('RGB'))).decode(),mask=base64.b64encode(png(mask)).decode(),
                  strength=strength,noise=0,extra_noise_seed=params['seed'],add_original_image=True,
                  params_version=4,noise_schedule='karras')
    for key in ('sm','sm_dyn','autoSmea'):
        params.pop(key,None)
    if not legacy:
        # NAIA's small-mask convention expands to full resolution on the wire.
        small=mask.resize((source.width//8,source.height//8)).point(lambda x:255 if x>127 else 0)
        wire_mask=small.resize(source.size,Image.Resampling.NEAREST).convert('RGB')
        if wire_mask.getbbox() is None:
            raise ValueError('선택 영역이 너무 작아요. 브러시로 조금 더 넓게 칠해 주세요.')
        params['mask']=base64.b64encode(png(wire_mask)).decode()
        params.pop('strength',None)
        params.update(inpaintImg2ImgStrength=strength,img2img=dict(strength=strength,color_correct=True),
                      request_type='NativeInfillingRequest')
    return body


def render(workbench,pid,gid,request,value,progress):
    from .iterative_comic_render import build_settings
    from .iterative_recovery import current_session, digest
    from .image_cost import ensure_allowance
    project=workbench.load(pid)
    page=_page(project,gid)
    check_source(project,page,request)
    parent,source,mask=inputs(workbench,pid,page,value)
    session=current_session(workbench)
    folder=(session.artifact_folder('image',owner=gid) if session else
            workbench.folder(pid)/'renders'/gid/identity('r'))
    from pathlib import Path
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    relative=folder.relative_to(workbench.folder(pid)).as_posix()
    record=next((r for r in page.get('renders',[]) if r.get('folder')==relative),None)
    if record:
        requested=copy.deepcopy(record['request_page'])
    else:
        requested=copy.deepcopy(page)
        requested['image_settings']=copy.deepcopy(request['image_settings'])
        requested['image_settings'].update(width=source.width,height=source.height,noise_schedule='karras')
        requested.update(width=source.width,height=source.height,seed=request.get('seed',secrets.randbits(32)))
        _set_overrides(project,requested,request['prompt_overrides'])
    settings,audit=build_settings(project,requested)
    legacy=record is not None and record.get('provenance',{}).get('pipeline')!='native_strength_soft_edge_v2'
    feather=0 if legacy else value.get('feather',8)
    body=request_body(settings,source,mask,value['strength'],legacy=legacy)
    binding=dict(request_sha256=digest(body),source_sha256=request['source_sha256'],parent_sha256=sha(artifact(workbench,pid,parent['folder']+'/page.png')))
    if not legacy: binding['feather']=feather
    if (folder/'binding.json').exists():
        if read(folder/'binding.json')!=binding: raise ValueError('보존한 인페인팅 요청과 현재 입력이 달라요.')
    else:
        save(folder/'binding.json',binding)
        save(folder/'settings.json',settings)
        save(folder/'audit.json',audit)
        save(folder/'request.json',body)
        (folder/'source.png').write_bytes(png(source))
        (folder/'mask.png').write_bytes(png(mask))
    if record is None:
        record=dict(id=folder.name,folder=relative,status='rendering',seed=settings['seed'],created_at=now(),
            request_page=copy.deepcopy(audit['source']['page']),settings_path=relative+'/settings.json',
            audit_path=relative+'/audit.json',request_path=relative+'/request.json',studio_candidate=True,
            studio_source_sha256=request['source_sha256'],engine='novelai_inpaint',verified=False,
            original_api_png=False,composited=True,provenance=dict(parent_render_id=parent['id'],
            parent_sha256=binding['parent_sha256'],mask_sha256=sha(folder/'mask.png'),strength=value['strength'],
            feather=feather,pipeline='native_strength_soft_edge_v2'))
        page.setdefault('renders',[]).append(record)
        workbench.commit(project)
    attempt=folder/'attempt.json'
    try:
        if session: session.stage_started('image',folder)
        if not (folder/'response.zip').exists():
            if attempt.exists(): raise RuntimeError('이전 인페인팅 요청의 완료 여부가 불확실해요. 이력 확인 후 새 생성으로 요청해 주세요.')
            key=workbench.image_key_provider()
            save(folder/'allowance.json',ensure_allowance(key,settings=settings,confirmed=request.get('anlas_confirmed') is True))
            with attempt.open('x',encoding='utf-8') as stream: json.dump(dict(status='started',automatic_retry=False),stream)
            progress('칠한 영역을 NovelAI로 다시 그리고 있어요. 원본은 보존됩니다.')
            req=urllib.request.Request('https://image.novelai.net/ai/generate-image',data=json.dumps(body).encode(),
                headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Accept':'application/zip',
                         'User-Agent':'PromptServer-IterativeComic/1.0'})
            try:
                with urllib.request.urlopen(req,timeout=240) as response: raw=response.read()
            except urllib.error.HTTPError as error:
                save(folder/'http_error.json',dict(status=error.code,body=error.read(12000).decode('utf-8',errors='replace')))
                raise ValueError(f'NovelAI 인페인팅 요청이 거절되었어요 (HTTP {error.code}). 원문과 마스크는 보존했습니다.') from error
            (folder/'response.zip').write_bytes(raw)
        with zipfile.ZipFile(folder/'response.zip') as archive:
            images=[r for r in archive.infolist() if r.filename.lower().endswith('.png')]
            if len(images)!=1 or images[0].file_size>40_000_000: raise ValueError('인페인팅 PNG 응답을 확인하지 못했어요.')
            raw=archive.read(images[0])
        (folder/'api.png').write_bytes(raw)
        with Image.open(io.BytesIO(raw)) as generated:
            if generated.format!='PNG' or generated.size!=source.size: raise ValueError('인페인팅 응답의 크기가 원본과 달라요.')
            output=Image.composite(generated.convert('RGBA'),source,blend_mask(mask,feather))
        from .image_color import monochrome, grayscale
        if monochrome(settings): output=grayscale(output)
        (folder/'page.png').write_bytes(png(output))
        record.update(status='rendered',verified=True,verification_kind='mask_composite_sha256',
            image_url=f'/files/{pid}/{relative}/page.png',image_sha256=sha(folder/'page.png'),
            actual_image_dimensions=dict(width=source.width,height=source.height),error=None)
        record['color_mode']=settings.get('color_mode','prompt')
        outside=ImageChops.multiply(ImageChops.difference(output,source).convert('RGB'),ImageChops.invert(mask).convert('RGB'))
        save(attempt,dict(status='complete',api_png_sha256=sha(folder/'api.png'),image_sha256=record['image_sha256'],unmasked_pixels_preserved=outside.getbbox() is None))
        if session: session.stage_completed('image',folder)
        workbench.commit(project)
        progress('인페인팅 미리보기를 저장했어요. 확인한 뒤 페이지에 적용해 주세요.')
        return project
    except Exception as error:
        record.update(status='failed',error=type(error).__name__)
        workbench.commit(project)
        raise
