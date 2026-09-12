"""User edits and reversible page operations for the incremental workbench."""
import copy
import json

from .iterative_page_rendering import RenderingMixin


def _text(value, label, *, empty=False, limit=6000):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError(label + '을 올바르게 입력해 주세요.')
    return value.strip()


def _panel(project, panel_id):
    for index, panel in enumerate(project['panels']):
        if panel['id'] == panel_id:
            return index, panel
    raise ValueError('수정할 컷을 찾을 수 없어요.')


def _page(project, page_id, *, deleted=False):
    for page in project.get('deleted_pages' if deleted else 'pages', []):
        if page['id'] == page_id:
            return page
    raise ValueError('페이지를 찾을 수 없어요.')


def _clear_prompt(page):
    page.pop('prompt_overrides', None)
    page.pop('prompt_overrides_source_sha256', None)


def _stale(project, panel_id):
    for page in project['pages'] + project.get('deleted_pages', []):
        if panel_id in page['panel_ids']:
            page['stale'] = True
            _clear_prompt(page)


class EditMixin(RenderingMixin):
    def _replace_panel(self, project, index, candidate, folder, reason):
        from .iterative_comic import save, now
        original = project['panels'][index]
        candidate['id'] = original['id']
        self.validate({'panels': [candidate]}, project['cast'], 1)
        subjects = candidate['camera']['subjects']
        if len(subjects) > 2 or len(set(subjects)) != len(subjects):
            raise ValueError('한 컷에는 서로 다른 인물을 최대 두 명까지 지정해 주세요.')
        if candidate.get('dialogues') and candidate.get('actor') not in subjects:
            raise ValueError('대사를 말하는 인물이 화면에 있어야 해요.')
        versions = project.setdefault('panel_versions', {}).setdefault(original['id'], [])
        versions.append(dict(panel=copy.deepcopy(original), saved_at=now(), reason=reason))
        project['panels'][index] = candidate
        _stale(project, original['id'])
        project['revision'] += 1
        project.setdefault('operations', []).append(dict(id=folder.name, intent=reason, edited_id=original['id']))
        save(folder / 'accepted_english.json', [candidate])
        self.commit(project)
        return project

    def edit_panel(self, project_id, panel_id, payload, progress=lambda _: None):
        from .iterative_comic import save
        project = self.load(project_id)
        index, original = _panel(project, panel_id)
        changes = {}
        for field in ('description_ko', 'background_ko', 'state_ko'):
            if field in payload:
                value = _text(payload[field], field, empty=field == 'state_ko')
                if value != original.get(field, ''):
                    changes[field] = value
        if 'dialogues_ko' in payload:
            lines = payload['dialogues_ko']
            if not isinstance(lines, list) or len(lines) > 1:
                raise ValueError('대사는 비워 두거나 한 줄만 입력해 주세요.')
            lines = [_text(line, '대사', limit=1000) for line in lines]
            if lines != original.get('dialogues_ko', []):
                changes['dialogues_ko'] = lines
        if 'camera_ko' in payload:
            if not isinstance(payload['camera_ko'], dict):
                raise ValueError('시점 정보를 확인해 주세요.')
            camera = {}
            for field in ('focus', 'framing', 'angle'):
                if field in payload['camera_ko']:
                    value = _text(payload['camera_ko'][field], '시점', limit=2000)
                    if value != original.get('camera_ko', {}).get(field, ''):
                        camera[field] = value
            if camera:
                changes['camera_ko'] = camera
        if not changes:
            return project
        if original['actor'] is None and changes.get('dialogues_ko'):
            raise ValueError('인물이 없는 컷에는 인물 대사를 넣을 수 없어요.')
        folder = self.operation(project_id, dict(operation='edit_panel', panel_id=panel_id, **changes))
        save(folder / 'before.json', project)
        progress('변경한 내용만 영어 요청에 반영하고 있어요.')
        en = self.english(folder, changes)
        candidate = copy.deepcopy(original)
        for ko, english in (('description_ko', 'description'), ('background_ko', 'background')):
            if ko in changes:
                candidate[english] = _text(en.get(ko), '번역된 설명')
                candidate[ko] = changes[ko]
        if 'dialogues_ko' in changes:
            lines = en.get('dialogues_ko')
            if not isinstance(lines, list) or len(lines) != len(changes['dialogues_ko']):
                raise ValueError('번역된 대사 수가 달라요. 수정 입력은 보존했어요.')
            candidate['dialogues'] = [_text(line, '번역된 대사', limit=2000) for line in lines]
            candidate['dialogues_ko'] = changes['dialogues_ko']
        if 'camera_ko' in changes:
            if not isinstance(en.get('camera_ko'), dict):
                raise ValueError('번역된 시점 형식이 잘못됐어요. 입력은 보존했어요.')
            for field, value in changes['camera_ko'].items():
                candidate['camera'][field] = _text(en['camera_ko'].get(field), '번역된 시점')
                candidate.setdefault('camera_ko', {})[field] = value
        if 'description_ko' in changes or 'state_ko' in changes:
            progress('수정된 순간에 맞게 자세와 현재 상태를 정리하고 있어요.')
            state_change = _text(en.get('state_ko', ''), '상태 변경', empty=True)
            save(folder / 'input_en.json', dict(en, state_change=state_change))
            prepared = self.prepare(folder, {'panels': [candidate]}, project['cast'], prefix=project['panels'][:index + 1])
            candidate = prepared['panels'][0]
            # Translate only the new state summary; user-authored Korean wording stays exact.
            from .translation_modes import options, state_summary
            if options(project.get('generation_options'))['translation_mode'] == 'full':
                summary = self.call(folder, 'state_display', 'Translate these active visual states into one concise Korean description. Do not add events. Return JSON {"state_ko":"Korean"}.\n' + json.dumps(candidate['visual_states'], ensure_ascii=False))
                candidate['state_ko'] = _text(summary.get('state_ko'), '번역된 상태', empty=candidate['actor'] is None)
            else:
                candidate['state_ko'] = state_summary(candidate)
            if state_change:
                candidate['origin'] = dict(candidate.get('origin', {}), operation=folder.name, state_change=state_change)
        # User wording may deliberately mix languages; do not label it as a fresh translation.
        if any(k in changes for k in ('description_ko', 'background_ko', 'state_ko', 'camera_ko')):
            candidate.pop('display_language', None)
        if 'dialogues_ko' in changes:
            candidate.pop('dialogue_language', None)
        return self._replace_panel(project, index, candidate, folder, 'edit')

    def reroll_panel(self, project_id, panel_id, payload, progress=lambda _: None):
        from .iterative_comic import save, SCHEMA, DIALOGUE
        project = self.load(project_id)
        index, original = _panel(project, panel_id)
        mode = payload.get('mode', 'camera')
        dialogue = payload.get('dialogue', 'auto')
        if mode not in ('camera', 'variation') or dialogue not in DIALOGUE:
            raise ValueError('장면을 다시 만드는 방식과 대사 방식을 선택해 주세요.')
        if original['actor'] is None and mode == 'camera' and dialogue == 'with':
            raise ValueError('인물이 없는 같은 순간에는 인물 대사를 넣을 수 없어요. 무대사 또는 자동을 선택해 주세요.')
        instruction = _text(payload.get('instruction', ''), '추가 요청', empty=True)
        folder = self.operation(project_id, dict(operation='reroll_panel', panel_id=panel_id, mode=mode, dialogue=dialogue, instruction=instruction))
        save(folder / 'before.json', project)
        en = self.english(folder, {'instruction': instruction})
        canonical = lambda row: {k: copy.deepcopy(v) for k, v in row.items() if not k.endswith('_ko') and k not in ('origin', 'display_language', 'dialogue_language')}
        context = dict(anchor=canonical(original), previous=canonical(project['panels'][index - 1]) if index else None,
                       next=canonical(project['panels'][index + 1]) if index + 1 < len(project['panels']) else None,
                       instruction=en.get('instruction', ''))
        progress('선택한 장면의 새 시점을 만들고 있어요.' if mode == 'camera' else '같은 장면을 요청에 맞게 다시 구성하고 있어요.')
        if mode == 'camera':
            raw = self.call(folder, 'story', '''Choose a genuinely different camera angle or framing for the EXACT SAME frozen instant in anchor. Do not advance time or perform the action again. No changes to action, background, pose, outfit or held objects. Keep camera.subjects EXACTLY the anchor subjects so active states are preserved. Follow the user's camera request. Return JSON {"camera":{"subjects":["unchanged actor IDs"],"focus":"English focal point","framing":"English shot size","angle":"English viewpoint"},"dialogues":["zero or one short English line"]}. For an actorless anchor return dialogues=[].\n''' + DIALOGUE[dialogue] + '\n' + json.dumps(context, ensure_ascii=False), memory=self.facts(project), model='xialong-v1')
            camera = raw.get('camera')
            if not isinstance(camera, dict):
                raise ValueError('새 시점을 확인하지 못했어요. 원문은 보존했어요.')
            # Visible cast and active state are application-owned in same-instant mode.
            camera = dict(camera, subjects=copy.deepcopy(original['camera']['subjects']))
            if all(str(camera.get(key, '')).strip().casefold() == original['camera'][key].strip().casefold()
                   for key in ('focus', 'framing', 'angle')):
                raise ValueError('원래와 같은 시점이 돌아왔어요. 실패 단계 재시도 또는 구체적인 시점 요청을 사용해 주세요.')
            candidate = dict(copy.deepcopy(original), camera=camera, dialogues=raw.get('dialogues'))
        else:
            task = '''Replace only the selected anchor with ONE alternative depiction of the same story beat. Follow the user's changes to acting, expression or staging; preserve identity, active outfit, held objects and location unless explicitly changed by the instruction. Neither previous nor next is rewritten; next has not happened yet. Do not add a new scene or advance to the next event. Return one panel, same cast IDs.\n''' + DIALOGUE[dialogue] + '\n' + json.dumps(context, ensure_ascii=False) + '\n' + SCHEMA
            from .translation_modes import concise_instruction
            task += concise_instruction(project.get('generation_options'))
            raw = self.call(folder, 'story', task, memory=self.facts(project), model='xialong-v1')
            if raw.get('conflict'):
                raise ValueError('앞뒤 장면과 충돌하는 요청이에요. 원문을 확인하거나 요청을 바꿔 주세요.')
            save(folder / 'input_en.json', dict(en, state_change=en.get('instruction', '')))
            prepared = self.prepare(folder, raw, project['cast'], prefix=project['panels'][:index + 1])
            candidate = copy.deepcopy(self.validate(prepared, project['cast'], 1)[0])
            candidate.update(id=panel_id, origin=dict(original.get('origin', {}), operation=folder.name,
                                                     state_change=en.get('instruction', '')))
        self.validate({'panels': [candidate]}, project['cast'], 1)
        if len(candidate['dialogues']) > 1 or (dialogue == 'none' and candidate['dialogues']) or (dialogue == 'with' and len(candidate['dialogues']) != 1):
            raise ValueError('새 장면의 대사 방식이 요청과 달라요. 원문은 보존했어요.')
        progress('수정된 장면을 한국어로 보여드릴 준비를 하고 있어요.')
        self.korean(folder, project, [candidate])
        if mode == 'camera':
            for field in ('description_ko', 'background_ko', 'state_ko'):
                candidate[field] = original.get(field, '')
        return self._replace_panel(project, index, candidate, folder, mode)

    def restore_panel(self, project_id, panel_id, payload):
        from .iterative_comic import save
        project = self.load(project_id)
        index, original = _panel(project, panel_id)
        version = payload.get('version')
        versions = project.get('panel_versions', {}).get(panel_id, [])
        if type(version) is not int or not 0 <= version < len(versions):
            raise ValueError('복원할 컷 버전을 선택해 주세요.')
        folder = self.operation(project_id, dict(operation='restore_panel', panel_id=panel_id, version=version))
        save(folder / 'before.json', project)
        return self._replace_panel(project, index, copy.deepcopy(versions[version]['panel']), folder, 'restore')

    def edit_page(self, project_id, page_id, payload):
        project = self.load(project_id)
        page = _page(project, page_id)
        ids = payload.get('panel_ids', page['panel_ids'])
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('페이지에 넣을 서로 다른 컷을 한 개 이상 선택해 주세요.')
        if set(ids) - {p['id'] for p in project['panels']}:
            raise ValueError('존재하지 않는 컷이 포함되어 있어요.')
        layout = payload.get('layout', page['layout'])
        if layout not in ('auto', 'top', 'middle', 'bottom'):
            raise ValueError('컷 배치를 선택해 주세요.')
        if ids == page['panel_ids'] and layout == page['layout']:
            return project
        from .iterative_comic import now
        page.setdefault('composition_versions', []).append(dict(panel_ids=page['panel_ids'][:], layout=page['layout'], saved_at=now(), prompt_overrides=copy.deepcopy(page.get('prompt_overrides'))))
        page.update(panel_ids=ids[:], layout=layout, stale=True)
        _clear_prompt(page)
        project['revision'] += 1
        self.commit(project)
        return project

    def delete_page(self, project_id, page_id, payload=None):
        from .iterative_comic import now
        project = self.load(project_id)
        page = _page(project, page_id)
        page['deleted_index'] = project['pages'].index(page)
        page['deleted_at'] = now()
        project['pages'].remove(page)
        project.setdefault('deleted_pages', []).append(page)
        for index, row in enumerate(project['pages'], 1):
            row['title'] = f'{index}페이지'
        project['revision'] += 1
        self.commit(project)
        return project

    def restore_page(self, project_id, page_id, payload=None):
        project = self.load(project_id)
        page = _page(project, page_id, deleted=True)
        at = min(page.pop('deleted_index', len(project['pages'])), len(project['pages']))
        page.pop('deleted_at', None)
        project['deleted_pages'].remove(page)
        project['pages'].insert(at, page)
        for index, row in enumerate(project['pages'], 1):
            row['title'] = f'{index}페이지'
        project['revision'] += 1
        self.commit(project)
        return project

    def select_render(self, project_id, page_id, payload):
        project = self.load(project_id)
        page = _page(project, page_id)
        index = payload.get('index')
        versions = page.get('renders', [])
        if type(index) is not int or not 0 <= index < len(versions):
            raise ValueError('복원할 그림을 선택해 주세요.')
        chosen = versions[index]
        if chosen.get('engine') == 'gpt_image_edit':
            from .image_studio import apply, source_hash
            return apply(self, project_id, page_id,
                         dict(render_id=chosen['id'], source_sha256=source_hash(project, page)))
        if not chosen.get('image_url') or chosen.get('verified') is False:
            raise ValueError('완료가 확인된 그림만 선택할 수 있어요. 새 버전을 요청해 주세요.')
        if page.get('selected_render_index') == index and page.get('image_url') == chosen['image_url']:
            return project
        # Compare actual content, so changing the selected version does not itself make it stale.
        if chosen.get('source_sha256') and chosen.get('request_page'):
            from .iterative_comic_render import build_settings, _digest
            requested = copy.deepcopy(page)
            requested['seed'] = chosen['seed']
            try:
                settings, audit = build_settings(project, requested, render_version=chosen['render_version'])
                stale = audit['source_sha256'] != chosen['source_sha256'] or _digest(settings) != chosen['settings_sha256']
            except ValueError:
                stale = True
        else:
            stale = bool(page.get('stale')) or chosen.get('source_revision') != project['revision']
        page.update(image_url=chosen['image_url'], selected_render_index=index, status='rendered', error=None, stale=stale)
        project['revision'] += 1
        self.commit(project)
        return project
