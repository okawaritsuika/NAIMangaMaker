"""Page-bound image editing: immutable candidates, explicit apply, exact settings."""
import copy
import hashlib

from .iterative_page_rendering import _page, _automatic
from .iterative_comic import read
from .iterative_recovery import digest
from . import image_preferences as preferences


def scene_source(project, page):
    ids = set(page['panel_ids'])
    return dict(project_id=project['id'], cast=project['cast'], setting=project.get('setting', ''),
        direction=project.get('direction', ''), panels=[p for p in project['panels'] if p['id'] in ids],
        page={key: page[key] for key in ('id', 'panel_ids', 'layout') if key in page})


def source_hash(project, page):
    return digest(scene_source(project, page))


def artifact(workbench, pid, relative):
    if not isinstance(relative, str):
        raise ValueError('저장된 이미지 설정 경로를 확인해 주세요.')
    base = workbench.folder(pid).resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        raise ValueError('작품 밖의 설정은 불러올 수 없어요.')
    return path


def form(settings, audit):
    from .iterative_comic_render import payload
    body = payload(settings)
    params = body['parameters']
    positives = params['v4_prompt']['caption']['char_captions']
    negatives = params['v4_negative_prompt']['caption']['char_captions']
    actors = audit['actors']
    return dict(prompt=body['input'], negative_prompt=params['negative_prompt'], model=body['model'], color_mode=settings.get('color_mode','prompt'),
        **{key: params[key] for key in ('seed','width','height','steps','scale','cfg_rescale','sampler','noise_schedule')},
        characters=[dict(index=i, source_index=actor.get('source_index', i), panel_id=actor['panel_id'], actor=actor['actor'],
            prompt=positive['char_caption'], negative_prompt=negative['char_caption'], centers=positive['centers'])
            for i,(positive,negative,actor) in enumerate(zip(positives,negatives,actors))])


def saved_form(workbench, pid, row):
    if not row.get('settings_path') or not row.get('audit_path'):
        return None
    settings = artifact(workbench, pid, row['settings_path'])
    audit = artifact(workbench, pid, row['audit_path'])
    return form(read(settings), read(audit)) if settings.exists() and audit.exists() else None


def get(workbench, pid, gid):
    from .iterative_page_rendering import _automatic
    project = workbench.load(pid)
    page = _page(project, gid)
    preview = workbench.preview_prompt(pid, gid)
    history = []
    for index, row in enumerate(page.get('renders', [])):
        selected = page.get('selected_render_index') == index and page.get('image_url') == row.get('image_url')
        item = dict(index=index, id=row['id'], image_url=row.get('image_url'), status=row['status'],
            selected=selected, seed=row.get('seed'), created_at=row.get('created_at'),
            prompt=saved_form(workbench, pid, row), verified=row.get('verified', False),
            candidate=bool(row.get('studio_candidate')),
            engine=row.get('engine') or ('novelai' if row.get('original_api_png') else 'unknown'),
            original_api_png=bool(row.get('original_api_png')),
            verification_kind=row.get('verification_kind') or ('nai_api_artifact' if row.get('original_api_png') else None),
            actual_image_dimensions=copy.deepcopy(row.get('actual_image_dimensions')),
            provenance=copy.deepcopy(row.get('provenance')))
        item['image_sha256'] = row.get('image_sha256')
        history.append(item)
        if selected and item['prompt'] and not page.get('stale'):
            preview = item['prompt']
    for value in [preview, *[item['prompt'] for item in history if item.get('prompt')]]:
        for row in value.get('characters', []):
            actor = project['cast'].get(row.get('actor'), {})
            row['panel_number'] = page['panel_ids'].index(row['panel_id'])+1 if row.get('panel_id') in page['panel_ids'] else None
            row['name'] = actor.get('name') or row.get('actor') or '배경'
    return dict(project_id=pid, page_id=gid, title=page.get('title') or gid,
        image_url=page.get('image_url'), source_sha256=source_hash(project,page),
        prompt=preview, character_slots=form(*_automatic(project, page))['characters'],
        history=history, defaults=preferences.public(workbench), stale=bool(page.get('stale')))


def check_source(project, page, data):
    if data.get('source_sha256') != source_hash(project,page):
        raise ValueError('페이지 내용이 바뀌었어요. 입력 초안을 보존하고 페이지를 다시 불러와 주세요.')


def with_defaults(workbench, pid, gid, data):
    from .iterative_comic_render import build_settings
    project = workbench.load(pid)
    page = copy.deepcopy(_page(project,gid))
    check_source(project,page,data)
    current = get(workbench,pid,gid)['prompt']
    page.pop('prompt_overrides',None)
    page.pop('prompt_overrides_source_sha256',None)
    page['image_settings'] = preferences.load(workbench)
    page.update(width=page['image_settings']['width'],height=page['image_settings']['height'])
    settings,audit = build_settings(project,page)
    result = form(settings,audit)
    # Loading the global style must not discard edited panel/actor prompt text.
    result['characters'] = copy.deepcopy(current['characters'])
    return dict(prompt=result,source_sha256=source_hash(project,_page(project,gid)))


def render(workbench,pid,gid,data,progress=lambda _:None):
    from .iterative_page_rendering import render_project_page
    project = workbench.load(pid)
    page = _page(project,gid)
    check_source(project,page,data)
    if set(data)-{'source_sha256','prompt_overrides','image_settings','seed','anlas_confirmed','inpaint'}:
        raise ValueError('이미지 편집 요청 항목을 확인해 주세요.')
    if 'prompt_overrides' not in data or not isinstance(data.get('image_settings'),dict):
        raise ValueError('편집한 프롬프트와 이미지 설정이 필요해요.')
    from .iterative_comic_render import validate_prompt_overrides
    current = workbench.preview_prompt(pid,gid)
    from .iterative_page_rendering import _automatic
    automatic_settings, _ = _automatic(project, page)
    prompts = validate_prompt_overrides(data['prompt_overrides'],len(automatic_settings['v4_prompt']['caption']['char_captions']))
    if set(data['image_settings'])-set((*preferences.NUMERIC,'sampler','noise_schedule','color_mode')):
        raise ValueError('편집실 수치 설정 항목을 확인해 주세요.')
    defaults = {key:current[key] for key in (*preferences.NUMERIC,'sampler','noise_schedule')}
    defaults['color_mode']=current.get('color_mode','prompt')
    settings = preferences.validate(data['image_settings'],base=defaults)
    request = dict(prompt_overrides=prompts,image_settings=settings,apply_result=False,
                   source_sha256=data['source_sha256'],reroll=True)
    if data.get('anlas_confirmed') is True:
        request['anlas_confirmed']=True
    if data.get('seed') is not None:
        seed = data['seed']
        if type(seed) is not int or not 0<=seed<2**32:
            raise ValueError('시드는 0~4294967295 정수여야 해요.')
        request['seed']=seed
    if 'inpaint' in data:
        from .image_inpaint import render
        return render(workbench,pid,gid,request,data['inpaint'],progress)
    return render_project_page(workbench,pid,gid,progress,payload=request)


def apply(workbench,pid,gid,data):
    from .iterative_comic import now
    project = workbench.load(pid)
    page = _page(project,gid)
    check_source(project,page,data)
    match = next(((i,r) for i,r in enumerate(page.get('renders',[])) if r['id']==data.get('render_id')),None)
    if not match:
        raise ValueError('적용할 생성 결과를 선택해 주세요.')
    index,row = match
    if row.get('status')!='rendered' or not row.get('verified') or not row.get('image_url'):
        raise ValueError('완료와 원본 검증을 마친 그림만 적용할 수 있어요.')
    expected = row.get('studio_source_sha256')
    if expected is None:
        audit = read(artifact(workbench,pid,row['audit_path']))
        source = copy.deepcopy(audit['source'])
        source['page'] = {k:source['page'][k] for k in ('id','panel_ids','layout') if k in source['page']}
        expected = digest(source)
    if expected!=source_hash(project,page):
        raise ValueError('이 그림을 생성한 뒤 페이지 내용이 달라졌어요. 현재 페이지로 새 후보를 만들어 주세요.')
    image = artifact(workbench,pid,row['folder']+'/page.png')
    if not image.exists() or (row.get('image_sha256') and hashlib.sha256(image.read_bytes()).hexdigest()!=row['image_sha256']):
        raise ValueError('보존한 원본 그림을 확인하지 못했어요.')
    if page.get('selected_render_index') == index and page.get('image_url') == row['image_url']:
        return project
    requested = row['request_page']
    for key in ('seed','width','height','image_settings','prompt_overrides','prompt_overrides_source_sha256'):
        if key in requested: page[key]=copy.deepcopy(requested[key])
        else: page.pop(key,None)
    page.setdefault('image_applications',[]).append(dict(at=now(),previous_index=page.get('selected_render_index'),render_id=row['id']))
    page.update(image_url=row['image_url'],selected_render_index=index,status='rendered',stale=False,error=None)
    project['revision']+=1
    workbench.commit(project)
    return project
