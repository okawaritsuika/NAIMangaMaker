"""Render a selected multi-panel page in one NovelAI request, preserving source."""
import copy
import re
import hashlib
import io
import json
import urllib.request
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from .forced_infill_delivery import payload, read, save, sha
from .export_people_page import layout_rows
from .framing_layout import rectangles
from .image_cost import ensure_allowance
from .single_page_five import verify_rendered
from .iterative_comic import actorless_panel


REFERENCE = Path(__file__).resolve().parent / 'reference_settings.json'
LAYOUTS = {'auto', 'top', 'middle', 'bottom'}
EXCLUSIONS = 'inset panel, extra panels, empty panel, split panel, zoom layer, duplicate panel'
TEXT_EXCLUSIONS = 'text, speech bubble, thought bubble, caption, lettering, sound effects'
RENDER_VERSION = 'single_view_v3'
PREVIOUS_RENDER_VERSION = 'panel_background_v2'
LEGACY_RENDER_VERSION = 'legacy_global_setting_v1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def validate_prompt_overrides(value, count):
    if not isinstance(value, dict):
        raise ValueError('Prompt overrides must be an object')
    rows = value.get('characters')
    if not isinstance(rows, list) or len(rows) > count:
        raise ValueError('인물 프롬프트 목록을 확인해 주세요.')
    indexed = all(isinstance(row, dict) and 'source_index' in row for row in rows)
    if not indexed and (len(rows) != count or any(isinstance(row, dict) and 'source_index' in row for row in rows)):
        raise ValueError('남길 인물의 원래 항목 번호가 필요해요.')
    indices = [row['source_index'] for row in rows] if indexed else list(range(count))
    if any(type(index) is not int or not 0 <= index < count for index in indices) or len(set(indices)) != len(indices):
        raise ValueError('인물 항목 번호가 중복되거나 올바르지 않아요.')
    def pair(row, positioned=False):
        if not isinstance(row, dict) or any(not isinstance(row.get(key), str) for key in ('prompt', 'negative_prompt')):
            raise ValueError('Each prompt and negative_prompt must be text')
        if not row['prompt'].strip():
            raise ValueError('Positive prompts must not be empty')
        result = {key: row[key] for key in ('prompt', 'negative_prompt')}
        if positioned and 'centers' in row:
            centers = row['centers']
            if not isinstance(centers, list) or not 1 <= len(centers) <= 16:
                raise ValueError('인물 위치 목록을 확인해 주세요.')
            if any(not isinstance(c, dict) or set(c) != {'x','y'} or
                   any(type(c[k]) not in (int,float) or not 0 <= c[k] <= 1 for k in ('x','y')) for c in centers):
                raise ValueError('인물 위치는 0~1 사이의 좌표여야 해요.')
            result['centers'] = copy.deepcopy(centers)
        return result
    return dict(**pair(value), characters=[dict(pair(row, True), source_index=index) for row, index in zip(rows, indices)])


def _prompt_basis(snapshot):
    value = copy.deepcopy(snapshot)
    value['page'].pop('seed', None)
    return _digest(value)


def _selected(project, page):
    ids = page.get('panel_ids')
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)):
        raise ValueError('A page must select one or more distinct panel IDs')
    all_panels = project.get('panels', [])
    indexed = {panel['id']: panel for panel in all_panels}
    if len(indexed) != len(all_panels) or any(identity not in indexed for identity in ids):
        raise ValueError('Selected panel IDs must uniquely exist in the project')
    panels = copy.deepcopy([indexed[identity] for identity in ids])
    for panel in panels:
        camera = panel.get('camera', {})
        if any(not isinstance(camera.get(key), str) or not camera[key].strip()
               for key in ('focus', 'framing', 'angle')):
            raise ValueError('Each panel needs the model focus, framing, and angle')
        subjects = camera.get('subjects', [])
        if not isinstance(subjects, list) or len(subjects) != len(set(subjects)):
            raise ValueError('Camera subjects must be a list of distinct actor IDs')
        actorless = actorless_panel(panel)
        visible = [] if actorless else subjects or [panel['actor']]
        if not actorless and (not 1 <= len(visible) <= 2 or panel['actor'] not in project['cast'] or
                any(actor not in project['cast'] for actor in visible)):
            raise ValueError('Each panel supports one or two known visible actors')
        if not isinstance(panel.get('description'), str) or not panel['description'].strip():
            raise ValueError('Each panel needs its model-authored visible moment')
        if not isinstance(panel.get('background', ''), str):
            raise ValueError('Panel background must be text')
        speeches = panel.get('dialogues_ko', [])
        if not isinstance(speeches, list) or any(not isinstance(s, str) or not s.strip() for s in speeches):
            raise ValueError('Korean dialogue must be a list of nonempty strings or an empty list')
        if actorless and speeches:
            raise ValueError('An actorless panel must also have no Korean dialogue')
        if speeches and panel['actor'] not in visible:
            raise ValueError('A dialogue owner must be among the visible actors')
    return panels


def _layout(panels, requested, width, height):
    count = len(panels)
    chosen, reason = requested, 'explicit page layout'
    if requested == 'auto':
        last = panels[-1]
        marked = last.get('emphasis') == 'main' or last.get('importance') in {'main', 'large', 'dominant'}
        close = {'portrait', 'close-up', 'closeup', 'close up', 'close shot'}
        wide = {'wide shot', 'full body', 'medium-wide shot', 'long shot'}
        reveal = (count >= 3 and last['camera']['framing'].strip().lower() in wide and
                  sum(p['camera']['framing'].strip().lower() in close for p in panels[:-1]) >= (count - 1) / 2)
        if count > 1 and (marked or reveal):
            chosen = 'bottom'
            reason = 'last panel explicitly emphasized' if marked else 'last wide view follows mostly close views'
        elif count > 5 or (count >= 3 and any(actorless_panel(panel) for panel in panels)):
            chosen, reason = 'bottom', 'actorless scene uses existing row allocator; person-only framing search is inapplicable'
        elif count >= 3:
            boxes, words, search = rectangles([dict(p, entry_id=p['id']) for p in panels], width, height)
            slots = {i: ((b[0]+b[2])/2, (b[1]+b[3])/2, b[2]-b[0]) for i, b in boxes.items()}
            if count != 5 or len({round(s[0], 5) for s in slots.values()}) > 1:
                return words, slots, dict(requested=requested, selected='framing',
                    source_function='framing_layout.rectangles', heuristic='existing pixel aspect search', search=search)
            chosen, reason = 'bottom', 'avoid five uniform horizontal strips'
        else:
            chosen, reason = 'middle', 'one or two panels use existing row layout'
    # Reuse the existing row allocator, including its unequal row weights.
    # Small adjacent moments share a row; a main moment gets its own row.
    main = 0 if chosen == 'top' else count - 1 if chosen == 'bottom' else count // 2
    sizes = ['small'] * count
    sizes[main] = 'large'
    words, slots = layout_rows([dict(size=size) for size in sizes])
    if count == 5 and chosen == 'bottom':
        words = 'a 2 by 2 panel grid above a single full-width bottom panel'
    elif count == 5 and chosen == 'top':
        words = 'a single full-width top panel above a 2 by 2 panel grid'
    return words, slots, dict(requested=requested, selected=chosen,
        source_function='export_people_page.layout_rows', heuristic=reason, sizes=sizes,
        layout_text_serialization='strict grid wording for the existing five-panel top/bottom rows')


def build_settings(project, page, layout='auto', *, render_version=RENDER_VERSION):
    if render_version not in {RENDER_VERSION, PREVIOUS_RENDER_VERSION, LEGACY_RENDER_VERSION}:
        raise ValueError('Unsupported saved render version')
    panels = _selected(project, page)
    requested = page.get('layout', 'auto') if layout == 'auto' else layout
    if requested not in LAYOUTS:
        raise ValueError('Layout must be auto, top, middle, or bottom')
    reference = read(REFERENCE)
    settings = copy.deepcopy(reference)
    for key in ('signed_hash', 'extra_passthrough_testing'):
        settings.pop(key, None)
    image_settings = page.get('image_settings')
    if image_settings is not None:
        from .image_preferences import validate
        image_settings = validate(image_settings)
        settings.update({key: value for key, value in image_settings.items()
                         if key not in ('style_prompt', 'negative_prompt', 'width', 'height')})
    width, height = page.get('width', reference['width']), page.get('height', reference['height'])
    if any(type(value) is not int or value < 256 or value % 64 for value in (width, height)):
        raise ValueError('Canvas dimensions must be multiples of 64 and at least 256')
    words, slots, layout_audit = _layout(panels, requested, width, height)
    separator = ', color, fully clothed,'
    if separator not in reference['prompt']:
        raise ValueError('The preserved reference style boundary is missing')
    style = reference['prompt'].split(separator, 1)[0] + ', color, fully clothed'
    style = style.replace('detailed fantasy background', 'detailed background')
    if image_settings is not None and 'style_prompt' in image_settings:
        style = image_settings['style_prompt']
    count = len(panels)
    structure = (f'2::comic page, exactly {count} ' + ('panel' if count == 1 else 'panels') + ', ' + words + ', clear panel borders::')
    single_view = count == 1 and render_version == RENDER_VERSION
    if single_view:
        structure = '2::single illustration, one continuous scene, undivided composition::'
    base = structure + ', ' + style
    if render_version == LEGACY_RENDER_VERSION:
        base += ', ' + str(project.get('setting', ''))
    if not single_view:
        base += (', read left to right within each row, then top to bottom; each character prompt belongs only to its assigned panel; '
             'all people, actions, and props described for a panel share one continuous view inside that panel')
    silent = not any(panel.get('dialogues_ko') for panel in panels)
    if silent:
        base += ', wordless illustration' if single_view else ', silent comic, wordless visual storytelling'
    negative_base = image_settings.get('negative_prompt', reference['uc']) if image_settings is not None else reference['uc']
    negative = negative_base + ', ' + EXCLUSIONS + (', ' + TEXT_EXCLUSIONS if silent else '')
    positives, negatives, actor_audit = [], [], []
    for index, panel in enumerate(panels):
        camera = panel['camera']
        actorless = actorless_panel(panel)
        visible = [] if actorless else camera['subjects'] or [panel['actor']]
        x, y, zone_width = slots[index]
        camera_text = '2::focus ' + ', '.join(camera[key] for key in ('focus', 'framing', 'angle')) + '::'
        if actorless:
            center = [dict(x=x, y=y)]
            parts = ['still life, objects and setting only', camera_text, panel['background'], panel['description'],
                     'one continuous scene' if single_view else f'one continuous view in panel {index + 1}; all visible props stay inside this same panel']
            local_negative = TEXT_EXCLUSIONS + ', people, person, human, character'
            positives.append(dict(char_caption=', '.join(parts), centers=center))
            negatives.append(dict(char_caption=local_negative, centers=center))
            actor_audit.append(dict(panel_id=panel['id'], actor=None, actorless_panel=True,
                camera=copy.deepcopy(camera), description=panel['description'], appearance_source=None,
                appearance=[], visual_state={}, dialogue_count=0, center=center,
                single_visible_actor_constraint=False, local_negative=local_negative))
            continue
        for position, actor in enumerate(visible):
            state = panel.get('visual_states', {}).get(actor, {})
            overridden = 'appearance' in state
            appearance = state['appearance'] if overridden else project['cast'][actor]['appearance']
            if not isinstance(appearance, list) or not appearance or any(not isinstance(s, str) or not s.strip() for s in appearance):
                raise ValueError('Visible appearance must be a complete nonempty list of attributes')
            pose, held = state.get('pose', ''), state.get('held_objects', [])
            if not isinstance(pose, str) or not isinstance(held, list) or any(not isinstance(s, str) or not s.strip() for s in held):
                raise ValueError('Visual-state pose and held objects must be text')
            identity = project['cast'][actor]
            gender = identity.get('gender', 'auto')
            label = str(identity.get('name') or '')
            if re.fullmatch(r'(?:actor[0-9]+|Character [0-9]+)', label):
                label = ''
            parts = ([gender] if gender in ('girl', 'boy') else []) + ([label] if label else []) + list(appearance) + [camera_text]
            if len(visible) == 1:
                parts.append('solo' if single_view else 'solo, one person in this panel')
            if panel.get('background'):
                parts.append(panel['background'])
            if pose:
                parts.append(pose)
            parts.extend('holding ' + item for item in held)
            if actor == panel['actor']:
                parts.append(panel['description'])
            parts.append('one continuous scene' if single_view else f'one continuous view in panel {index + 1}; any visible props stay inside this same panel')
            speech = panel.get('dialogues_ko', []) if actor == panel['actor'] else []
            if speech:
                parts.extend(['speech bubble', *[json.dumps(s, ensure_ascii=False) for s in speech]])
            cx = x if len(visible) == 1 else x + (-.16 if position == 0 else .16) * zone_width
            center = [dict(x=cx, y=y)]
            positives.append(dict(char_caption=', '.join(parts), centers=center))
            local_negative = '' if speech else TEXT_EXCLUSIONS
            if len(visible) == 1:
                local_negative = ', '.join(filter(None, [local_negative, 'multiple people, duplicate person']))
            negatives.append(dict(char_caption=local_negative, centers=center))
            actor_audit.append(dict(panel_id=panel['id'], actor=actor, camera=copy.deepcopy(camera),
                description=panel['description'], appearance_source='panel.visual_states' if overridden else 'project.cast',
                appearance=copy.deepcopy(appearance), visual_state=copy.deepcopy(state),
                dialogue_count=len(speech), center=center,
                single_visible_actor_constraint=len(visible) == 1,
                local_negative=local_negative))
    seed = page.get('seed', (reference['seed'] + zlib.crc32(
        (str(project.get('id', '')) + '|' + str(page.get('id', ''))).encode('utf-8'))) % 2**32)
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError('Seed must be an unsigned 32-bit integer')
    settings.update(prompt=base, uc=negative, width=width, height=height, seed=seed, n_samples=1)
    settings['v4_prompt'] = dict(caption=dict(base_caption=base, char_captions=positives), use_coords=True, use_order=True)
    settings['v4_negative_prompt'] = dict(caption=dict(base_caption=negative, char_captions=negatives), legacy_uc=False)
    snapshot = dict(project_id=project.get('id'), cast=copy.deepcopy(project['cast']),
        setting=project.get('setting', ''), direction=project.get('direction', ''), panels=panels,
        page={key: copy.deepcopy(page[key]) for key in ('id', 'panel_ids', 'layout', 'width', 'height', 'seed', 'image_settings') if key in page})
    prompt_source_sha256 = _prompt_basis(snapshot)
    overrides = page.get('prompt_overrides')
    for index, actor in enumerate(actor_audit):
        actor['source_index'] = index
    if overrides is not None:
        if page.get('prompt_overrides_source_sha256') != prompt_source_sha256:
            raise ValueError('Saved prompts are stale after page changes; preview and save them again or reset them')
        overrides = validate_prompt_overrides(overrides, len(positives))
        settings['prompt'] = settings['v4_prompt']['caption']['base_caption'] = overrides['prompt']
        settings['uc'] = settings['v4_negative_prompt']['caption']['base_caption'] = overrides['negative_prompt']
        indices = [row['source_index'] for row in overrides['characters']]
        positives[:] = [positives[index] for index in indices]
        negatives[:] = [negatives[index] for index in indices]
        actor_audit[:] = [actor_audit[index] for index in indices]
        for index, row in enumerate(overrides['characters']):
            positives[index]['char_caption'] = row['prompt']
            negatives[index]['char_caption'] = row['negative_prompt']
            if 'centers' in row:
                positives[index]['centers'] = copy.deepcopy(row['centers'])
                negatives[index]['centers'] = copy.deepcopy(row['centers'])
                actor_audit[index]['center'] = copy.deepcopy(row['centers'])
        snapshot['page'].update(prompt_overrides=copy.deepcopy(overrides),
                                prompt_overrides_source_sha256=prompt_source_sha256)
    audit = dict(source=snapshot, source_sha256=_digest(snapshot), reference_sha256=sha(REFERENCE),
        prompt_source_sha256=prompt_source_sha256, prompt_overrides_applied=overrides is not None,
        prompt_override_fields=['prompt', 'negative_prompt', 'character caption text'] if overrides is not None else [],
        render_version=render_version,
        background_policy=dict(panel_backgrounds_in_local_captions=True,
            initial_setting_in_global_prompt=render_version == LEGACY_RENDER_VERSION),
        panel_order=[p['id'] for p in panels], layout=dict(**layout_audit, prompt=words, centers=slots),
        actors=actor_audit, n_samples=1, image_calls=1, composited=False,
        reference_style_replacements={'detailed fantasy background': 'detailed background'},
        model_camera_text_preserved=overrides is None, page_global_text_block=False,
        panel_boundaries_enforced=False, semantic_quality_verified=False)
    return settings, audit


def render_page(project, page, folder, api_key, progress=None, *, anlas_confirmed=False):
    folder = Path(folder).resolve()
    settings_path, audit_path = folder / 'settings.json', folder / 'audit.json'
    image_path, request_path, attempt = folder / 'page.png', folder / 'request.json', folder / 'attempt.json'
    saved_audit = read(audit_path) if audit_path.exists() else None
    version = saved_audit.get('render_version', LEGACY_RENDER_VERSION) if saved_audit is not None else RENDER_VERSION
    settings, audit = build_settings(project, page, render_version=version)
    folder.mkdir(parents=True, exist_ok=True)
    binding = dict(source_sha256=audit['source_sha256'], settings_sha256=_digest(settings))
    binding_path = folder / 'binding.json'
    if binding_path.exists():
        if read(binding_path) != binding:
            raise ValueError('Page inputs changed; provide a distinct render folder')
        if read(settings_path) != settings:
            raise ValueError('Saved render settings changed')
        audit = saved_audit
    elif any(path.exists() for path in (settings_path, audit_path, image_path, request_path, attempt)):
        raise ValueError('Unbound prior output exists; provide a distinct render folder')
    else:
        save(settings_path, settings)
        save(audit_path, audit)
        save(binding_path, binding)
    def result(reused, verified):
        return dict(image=str(image_path), settings=settings, audit=audit,
            settings_path=str(settings_path), audit_path=str(audit_path), request_path=str(request_path),
            reused=reused, composited=False, **verified)
    if image_path.exists():
        verified = verify_rendered(image_path, settings, request_path)
        if progress:
            progress('기존 페이지 원본과 생성 설정을 확인했습니다.')
        return result(True, verified)
    if attempt.exists() or request_path.exists():
        raise RuntimeError('An uncertain prior image request exists; automatic retry is disabled')
    if version != RENDER_VERSION:
        raise ValueError('Unrendered legacy settings require a distinct render folder')
    if not api_key:
        raise ValueError('NovelAI API key is required')
    save(folder / 'allowance.json', ensure_allowance(api_key, settings=settings, confirmed=anlas_confirmed))
    with attempt.open('x', encoding='utf-8') as stream:
        json.dump(dict(status='started', automatic_retry=False, at=datetime.now(timezone.utc).isoformat()), stream)
    body = payload(settings)
    save(request_path, body)
    request = urllib.request.Request('https://image.novelai.net/ai/generate-image',
        data=json.dumps(body).encode('utf-8'), headers={'Authorization': 'Bearer ' + api_key,
            'Content-Type': 'application/json', 'Accept': 'application/zip', 'User-Agent': 'PromptServer-IterativeComic/1.0'})
    if progress:
        progress(f'{len(page["panel_ids"])}컷을 한 장의 페이지로 생성하고 있습니다.')
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            raw = response.read()
        (folder / 'response.zip').write_bytes(raw)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith('.png')]
            if len(names) != 1:
                raise ValueError('Expected exactly one original PNG in the response')
            image_path.write_bytes(archive.read(names[0]))
        verified = verify_rendered(image_path, settings, request_path)
        save(attempt, dict(status='complete', original_png_bytes_preserved=True, **verified))
    except Exception as error:
        save(attempt, dict(status='uncertain', automatic_retry=False, error_type=type(error).__name__))
        raise
    if progress:
        progress('페이지 원본과 생성 설정을 저장하고 확인했습니다.')
    return result(False, verified)
