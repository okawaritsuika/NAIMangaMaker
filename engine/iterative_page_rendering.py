"""Prompt editing and immutable page render versions for the local workbench."""
import copy
import secrets
from pathlib import Path


def _page(project, page_id):
    page = next((p for p in project['pages'] if p['id'] == page_id), None)
    if page is None:
        raise ValueError('페이지를 찾을 수 없어요.')
    return page


def _automatic(project, page):
    from .iterative_comic_render import build_settings
    automatic = copy.deepcopy(page)
    automatic.pop('prompt_overrides', None)
    automatic.pop('prompt_overrides_source_sha256', None)
    return build_settings(project, automatic)


def _set_overrides(project, page, value):
    from .iterative_comic_render import validate_prompt_overrides
    settings, audit = _automatic(project, page)
    if value is None:
        page.pop('prompt_overrides', None)
        page.pop('prompt_overrides_source_sha256', None)
    else:
        page['prompt_overrides'] = validate_prompt_overrides(value,
            len(settings['v4_prompt']['caption']['char_captions']))
        page['prompt_overrides_source_sha256'] = audit['prompt_source_sha256']


class RenderingMixin:
    def preview_prompt(self, project_id, page_id):
        from .iterative_comic_render import build_settings, payload
        project = self.load(project_id)
        page = _page(project, page_id)
        settings, audit = _automatic(project, page)
        stale = (page.get('prompt_overrides') is not None and
                 page.get('prompt_overrides_source_sha256') != audit['prompt_source_sha256'])
        if page.get('prompt_overrides') is not None and not stale:
            settings, audit = build_settings(project, page)
        body = payload(settings)
        params = body['parameters']
        positives = params['v4_prompt']['caption']['char_captions']
        negatives = params['v4_negative_prompt']['caption']['char_captions']
        return dict(prompt=body['input'], negative_prompt=params['negative_prompt'],
            characters=[dict(index=i, panel_id=actor['panel_id'], actor=actor['actor'],
                prompt=positive['char_caption'], negative_prompt=negative['char_caption'],
                centers=copy.deepcopy(positive['centers']))
                for i, (positive, negative, actor) in enumerate(zip(positives, negatives, audit['actors']))],
            **{key: params[key] for key in ('seed', 'width', 'height', 'steps', 'scale', 'sampler')},
            model=body['model'], action=body['action'], n_samples=params['n_samples'],
            cfg_rescale=params.get('cfg_rescale'), noise_schedule=params.get('noise_schedule'),
            render_version=audit['render_version'],
            source_sha256=audit['prompt_source_sha256'],
            overrides_applied=bool(audit['prompt_overrides_applied']), overrides_stale=stale)

    def save_prompt(self, project_id, page_id, payload):
        if not isinstance(payload, dict):
            raise ValueError('프롬프트 요청은 객체여야 해요.')
        project = self.load(project_id)
        page = _page(project, page_id)
        value = payload.get('prompt_overrides') if 'prompt_overrides' in payload else payload
        _set_overrides(project, page, value)
        page['stale'] = bool(page.get('renders'))
        project['revision'] += 1
        self.commit(project)
        return project


def render_project_page(workbench, project_id, page_id, progress=lambda _: None, *, payload=None):
    from .iterative_comic import identity, now
    from .iterative_comic_render import build_settings, render_page, read, _digest, RENDER_VERSION, LEGACY_RENDER_VERSION
    if payload is None:
        payload = {}
    if not isinstance(payload, dict) or type(payload.get('reroll', False)) is not bool:
        raise ValueError('Render payload must be an object with a boolean reroll')
    project = workbench.load(project_id)
    page = _page(project, page_id)
    apply_result = payload.get('apply_result', True)
    if type(apply_result) is not bool:
        raise ValueError('그림 적용 여부는 true 또는 false여야 해요.')
    try:
        from .iterative_recovery import current_session
    except ModuleNotFoundError as error:
        if error.name != 'iterative_recovery':
            raise
        session = None
    else:
        session = current_session(workbench)
    folder = (Path(session.artifact_folder('image', owner=page_id)) if session is not None else
              workbench.folder(project_id) / 'renders' / page_id / identity('r'))
    relative = folder.relative_to(workbench.folder(project_id)).as_posix()
    record = next((row for row in page.get('renders', []) if row.get('folder') == relative), None)
    # A process can stop after the PNG arrives but before the deferred project commit.
    # Recover the request from its preserved audit, never invent another seed.
    saved_audit = (read(folder/'audit.json') if session is not None and record is None and
                   (folder/'audit.json').exists() else None)
    recovering = record is not None or saved_audit is not None
    if record is not None:
        request_page = copy.deepcopy(record['request_page'])
        version = record['render_version']
    elif saved_audit is not None:
        request_page = copy.deepcopy(saved_audit['source']['page'])
        version = saved_audit.get('render_version', LEGACY_RENDER_VERSION)
    else:
        request_page = copy.deepcopy(page)
        version = RENDER_VERSION
    if recovering and any(page.get(key) != request_page.get(key)
                          for key in (('id', 'panel_ids', 'layout', 'width', 'height') if apply_result
                                      else ('id', 'panel_ids', 'layout'))):
        raise ValueError('The saved render page composition changed; its artifacts were preserved')
    if not recovering and 'image_settings' in payload:
        from .image_preferences import validate
        prefs = validate(payload['image_settings'], base=request_page.get('image_settings', {}))
        request_page['image_settings'] = prefs
        for key in ('width', 'height'):
            if key in prefs:
                request_page[key] = prefs[key]
    if not recovering and 'prompt_overrides' in payload:
        _set_overrides(project, request_page, payload['prompt_overrides'])
    if not recovering and 'seed' in payload:
        seed = payload['seed']
        if type(seed) is not int or not 0 <= seed < 2**32:
            raise ValueError('Seed must be an unsigned 32-bit integer')
        request_page['seed'] = seed
    elif not recovering and (payload.get('reroll') or page.get('renders') or page.get('status') in {'rendered', 'failed'}):
        previous = {row.get('seed') for row in page.get('renders', [])}
        previous.add(_automatic(project, page)[0]['seed'])
        seed = secrets.randbits(32)
        while seed in previous:
            seed = secrets.randbits(32)
        request_page['seed'] = seed
    settings, audit = build_settings(project, request_page, render_version=version)
    request_page['seed'] = settings['seed']
    # Bind the saved page's explicit seed to the exact request snapshot.
    settings, audit = build_settings(project, request_page, render_version=version)
    if saved_audit is not None:
        if saved_audit['source_sha256'] != audit['source_sha256'] or read(folder/'settings.json') != settings:
            raise ValueError('The interrupted render source or settings changed; its image was preserved')
    if record is not None:
        if record['source_sha256'] != audit['source_sha256'] or record['settings_sha256'] != _digest(settings):
            raise ValueError('The saved render operation no longer matches its source')
    else:
        record = dict(id=folder.name, folder=relative, status='rendering', seed=settings['seed'],
            created_at=now(), source_revision=project['revision'], render_version=audit['render_version'],
            source_sha256=audit['source_sha256'], settings_sha256=_digest(settings),
            request_page=copy.deepcopy(audit['source']['page']), recovered_from_existing_artifacts=saved_audit is not None,
            settings_path=relative + '/settings.json', audit_path=relative + '/audit.json',
            request_path=relative + '/request.json', verified=False, original_api_png=False, composited=False)
        if not apply_result:
            record.update(studio_candidate=True, studio_source_sha256=payload.get('source_sha256'))
        page.setdefault('renders', []).append(record)
    if apply_result:
        page['seed'] = settings['seed']
        for key in ('prompt_overrides', 'prompt_overrides_source_sha256', 'image_settings', 'width', 'height'):
            if key in request_page:
                page[key] = copy.deepcopy(request_page[key])
            else:
                page.pop(key, None)
        page.update(status='rendering', error=None)
    workbench.commit(project)
    progress('전체 페이지를 한 번의 이미지 요청으로 그리고 있어요.')
    try:
        if session is not None:
            session.stage_started('image', folder)
        key = '' if (folder/'page.png').exists() else workbench.image_key_provider()
        result = render_page(project, request_page, folder, key, progress=progress,
                             anlas_confirmed=payload.get('anlas_confirmed') is True)
        if session is not None:
            session.stage_completed('image', folder)
    except Exception as error:
        record.update(status='failed', error=type(error).__name__,
                      response_zip_exists=(folder/'response.zip').exists(), png_exists=(folder/'page.png').exists())
        if record['png_exists']:
            record['image_url'] = f'/files/{project_id}/{relative}/page.png'
            record['original_api_png'] = record['response_zip_exists']
        if apply_result:
            page.update(status='failed', error='그림 요청의 완료를 확인하지 못했어요. 받은 파일과 버전을 보존했어요. 자동 재요청은 하지 않아요.')
        workbench.commit(project)
        raise
    image = Path(result['image']).resolve()
    image_relative = image.relative_to(workbench.folder(project_id).resolve()).as_posix()
    record.update(status='rendered', image_url=f'/files/{project_id}/{image_relative}', verified=True,
                  original_api_png=True, response_zip_exists=True, png_exists=True,
                  settings_sha256=_digest(result['settings']), image_sha256=result.get('image_sha256'))
    if apply_result:
        page.update(status='rendered', image_url=record['image_url'], error=None, stale=False,
                    selected_render_index=page['renders'].index(record))
    workbench.commit(project)
    return project
