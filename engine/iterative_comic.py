"""Persistent, incremental comic editor: English canon, Korean presentation."""
import copy
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from .basic_expansion import SYSTEM, configured_key
from .iterative_json import json_object
from .probe import generate
from .story_context import with_context
from .iterative_editing import EditMixin
from .translation_modes import options, english_display, translate_dialogue, dialogue_context, NATURAL_DIALOGUE, concise_instruction

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'iterative_comic_data'
SCHEMA = '''Return JSON only:
{"title":"short English title","cast":{"actor1":{"name":"name","personality":"stable personality","appearance":["complete baseline identity and outfit in English"]}},"setting":"initial location","panels":[{"actor":"stable actor ID","description":"one drawable instant in concise English","background":"only this shot's visible setting","dialogues":["zero or one short English line"],"camera":{"subjects":["visible actor IDs"],"focus":"visible focal subject/action","framing":"shot size","angle":"viewpoint"},"visual_states":{"actor1":{"appearance":["complete active identity and outfit"],"held_objects":["objects currently held"],"pose":"pose in this frame"}},"importance":"normal or main"}],"ended":false,"conflict":null}.
Existing cast IDs and definitions are fixed. Add no new actor unless the task calls for one.
Choose at most two visible cast actors in a panel; the cast may contain more people who appear in other shots.
camera.subjects contains only visible cast actor IDs, never props, food, scenery or abstract events. Keep non-person focal objects in camera.focus and description.
Each panel is ONE instant. State appearance is the complete active identity/outfit, independent of framing. Off-frame clothing remains unchanged. Accepted panel state overrides the cast baseline; never restore removed clothing from that baseline. Pose describes this frame only. Carry state from the first new panel into the second.
Write every active appearance attribute explicitly; never return only 'same outfit', 'unchanged', or a change relative to another panel. Record explicit removals as attributes such as 'no shoes' or 'barefoot', retaining the other clothing, rather than only omitting the old item. held_objects lists only objects actually held at this instant; an object resting beside the actor is not held.
Choose camera and focal subject to communicate the action. Do not make every shot a face portrait. Do not put sequential actions in one panel. The next two panels are not a complete long story. Set ended only for an actual resolution.'''
DIALOGUE = {
    'auto': 'Decide whether each panel benefits from dialogue. Prefer silence for visual actions, discoveries and emphasis. Add a line only if it communicates something the picture cannot. Empty dialogues [] are valid.',
    'with': 'Give each requested new panel one short, useful line of dialogue.',
    'none': 'Every requested new panel must be silent: dialogues=[]. No narration, thought balloons, captions or sound-effect lettering.',
}
INTENT = {
    'story': 'Advance the story by a small, causally connected visible event.',
    'dialogue': 'Develop the exchange or reaction through dialogue; keep the ongoing physical situation coherent.',
    'action': 'Show the connecting physical action or preparation between established moments. Do not skip directly to a distant outcome.',
    'emphasis': 'Emphasize the anchor beat from a materially different viewing angle AND a useful framing. Show a tiny adjacent phase of the same continuous action: just BEFORE the anchor when inserting before, just AFTER when inserting after. Change the visible hand/body pose or expression accordingly; do not replay a completed action, copy the frozen pose, introduce a new event, or jump past the fixed suffix. For example, after a catch show the fingers securing the caught object, not another catch. Preserve identity, outfit, location and object continuity. Return the actual new description and pose, not only camera changes. When requesting multiple panels, show distinct adjacent phases in chronological order with useful viewpoints; do not repeat the anchor or the same pose across those panels.',
}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(prefix):
    return prefix + uuid.uuid4().hex[:12]


def text(value):
    return str(value or '').strip()


def actorless_panel(panel):
    return (panel.get('actor', ...) is None and isinstance(panel.get('camera'), dict) and
            panel['camera'].get('subjects') == [] and panel.get('dialogues') == [] and
            panel.get('visual_states') == {})


class Workbench(EditMixin):
    def __init__(self, directory=DATA, key_provider=configured_key):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.key_provider = key_provider

    def folder(self, project_id):
        if not re.fullmatch(r'p[0-9a-f]{12}', project_id):
            raise ValueError('작품 번호가 올바르지 않아요.')
        return self.directory / project_id

    def load(self, project_id):
        return read(self.folder(project_id) / 'project.json')

    def commit(self, project):
        project['updated'] = now()
        save(self.folder(project['id']) / 'project.json', project)

    def list_projects(self):
        values = []
        for path in self.directory.glob('p*/project.json'):
            p = read(path)
            values.append({k: p[k] for k in ('id', 'title', 'updated')})
        return sorted(values, key=lambda p: p['updated'], reverse=True)

    def public(self, project):
        p = copy.deepcopy(project)
        return {k: p[k] for k in ('id', 'title', 'seed_ko', 'direction_ko', 'place_ko',
            'characters_ko', 'revision', 'updated', 'ended')} | dict(
            panels=[{k: row.get(k) for k in ('id', 'description_ko', 'background_ko', 'dialogues_ko', 'camera_ko', 'state_ko', 'origin', 'display_language', 'dialogue_language')} | dict(
                history=[{k: v['panel'].get(k) for k in ('description_ko', 'background_ko', 'dialogues_ko', 'camera_ko', 'state_ko', 'display_language', 'dialogue_language')} | dict(saved_at=v['saved_at'], reason=v['reason'])
                         for v in p.get('panel_versions', {}).get(row['id'], [])]) for row in p['panels']],
            pages=p['pages'], deleted_pages=p.get('deleted_pages', []), generation_options=options(p.get('generation_options')))

    def operation(self, project_id, payload):
        from .iterative_recovery import current_session
        session = current_session(self)
        if session:
            return session.operation(project_id, payload)
        folder = self.folder(project_id) / 'operations' / identity('o')
        save(folder / 'input_ko.json', payload)
        return folder

    def call(self, folder, name, task, *, memory='', history='', model='glm-4-6'):
        req = dict(model=model, temperature=0.75 if model == 'xialong-v1' else 0.25,
                   max_tokens=4096, enable_thinking=False, stream=True,
                   messages=[dict(role='system', content=SYSTEM if model == 'xialong-v1' else 'You are a helpful assistant.'),
                             dict(role='user', content=task)])
        if memory or history:
            req = with_context(req, memory=memory, author_note='Perform only the current task. Established facts and accepted story remain authoritative.', story=history)
        from .prompt_management import prepare_request
        req = prepare_request(self, folder, name, req)
        from .iterative_recovery import run_stage
        return run_stage(self, folder, name, req)

    def english(self, folder, payload):
        if json.dumps(payload, ensure_ascii=False).isascii():
            save(folder / 'input_en.json', payload)
            return copy.deepcopy(payload)
        result = self.call(folder, 'input_translation',
            'Translate the human-written string values in this JSON into clear English. Preserve names, IDs, keys, arrays, enums and numeric values. Do not create a story, add attributes or turn a state request into an invented action. Empty values stay empty. Return the same JSON structure, English string values only.\n' + json.dumps(payload, ensure_ascii=False))
        # Identity and operation controls are not translator-owned.
        for key in ('count', 'position', 'intent', 'dialogue', 'anchor_id'):
            if key in payload:
                result[key] = payload[key]
        if 'characters' in payload:
            if len(result.get('characters', [])) != len(payload['characters']):
                raise ValueError('인물 설정의 번역 수가 맞지 않아요. 원문은 보존했어요.')
            for old, new in zip(payload['characters'], result['characters']):
                new['id'] = old['id']
                new['gender'] = old.get('gender', 'auto')
        save(folder / 'input_en.json', result)
        return result

    def controls(self, data, unlimited=False):
        count = data.get('count', 1)
        if type(count) is not int or count < 1 or (not unlimited and count not in (1, 2)):
            raise ValueError('추가 컷 수는 1 이상의 정수로 입력해 주세요.' if unlimited else '한 번에 만들 컷은 1개 또는 2개를 선택해 주세요.')
        mode = data.get('dialogue', 'auto')
        if mode not in DIALOGUE:
            raise ValueError('대사 방식을 선택해 주세요.')
        return count, mode

    def characters(self, values, existing=None):
        if not isinstance(values, list) or len(values) > 4:
            raise ValueError('인물은 최대 4명까지 지정할 수 있어요.')
        rows, used = [], set()
        old_ids = set((existing or {}).keys())
        for index, row in enumerate(values):
            if not isinstance(row, dict):
                raise ValueError('인물 설정은 객체여야 합니다.')
            gender = row.get('gender', 'auto')
            if gender not in ('auto', 'girl', 'boy'):
                raise ValueError('성별을 자동·여성·남성 중 선택해 주세요.')
            if gender == 'auto' and not any(text(row.get(k)) for k in ('name', 'personality', 'appearance')):
                continue
            actor = row.get('id') or 'actor' + str(index + 1)
            if not re.fullmatch(r'actor[1-9][0-9]*', actor) or actor in used:
                raise ValueError('인물 번호가 중복되거나 잘못되었어요.')
            used.add(actor)
            rows.append(dict(id=actor, gender=gender, **{k: text(row.get(k)) for k in ('name', 'personality', 'appearance')}))
        if old_ids - used:
            raise ValueError('기존 컷에서 사용하는 인물은 삭제할 수 없어요. 설정은 수정할 수 있어요.')
        return rows

    def facts(self, project):
        return json.dumps(dict(direction=project['direction'], initial_setting=project['setting'],
                               cast=project['cast'], latest_user_setting_edits=project.get('latest_setting_edits'),
                               note='Cast is baseline identity. Actual prefix supplies active outfit and held objects. Stable baseline edits do not undo active transient states. To change current state, honor an explicit task state_change.'), ensure_ascii=False)

    def prepare(self, folder, raw, cast, prefix=None):
        """Resolve visual state without rewriting the writer's accepted scene text."""
        save(folder / 'state_resolution_source.json', raw)
        prepared = copy.deepcopy(raw)
        if prepared.get('conflict'):
            return prepared
        panels = prepared.get('panels')
        if panels is None and isinstance(prepared.get('panel'), dict):
            panels = [prepared['panel']]
        if not isinstance(panels, list) or not panels or not all(isinstance(p, dict) for p in panels):
            raise ValueError('상태를 정리할 컷을 확인하지 못했어요. 원문은 보존했어요.')
        aliases = {}
        for actor, details in cast.items():
            name = details.get('name')
            if isinstance(name, str) and name and sum(c.get('name') == name for c in cast.values()) == 1:
                aliases[name] = actor
        alias_repairs = []
        for p in panels:
            if p.get('actor') not in cast and p.get('actor') in aliases:
                alias_repairs.append(dict(field='actor', before=p['actor'], after=aliases[p['actor']]))
                p['actor'] = aliases[p['actor']]
            camera = p.get('camera')
            if not isinstance(camera, dict):
                raise ValueError('구도 설명을 확인하지 못했어요. 원문은 보존했어요.')
            if p.get('actor', ...) is None:
                if not actorless_panel(p):
                    raise ValueError('무인 컷은 인물, 대사와 인물 상태가 모두 비어 있어야 해요.')
                continue
            subjects = camera.get('subjects', [])
            if isinstance(subjects, list):
                for i, subject in enumerate(subjects):
                    if isinstance(subject, str) and subject not in cast and subject in aliases:
                        alias_repairs.append(dict(field='camera.subjects', before=subject, after=aliases[subject]))
                        subjects[i] = aliases[subject]
            known = list(dict.fromkeys(a for a in subjects if isinstance(a, str) and a in cast)) if isinstance(subjects, list) else []
            if not known and isinstance(p.get('actor'), str) and p['actor'] in cast:
                known = [p['actor']]
            if not known:
                raise ValueError('화면에 등장하는 기존 인물을 확인하지 못했어요. 원문은 보존했어요.')
            camera['subjects'] = known
        if alias_repairs:
            save(folder / 'cast_alias_repairs.json', alias_repairs)
        clean = lambda rows: [{k: v for k, v in p.items() if not k.endswith('_ko') and k not in ('display_language', 'dialogue_language')} for p in rows]
        latest_states, persistent_requests, seen_operations = {}, [], set()
        for previous in prefix or []:
            for actor, state in previous.get('visual_states', {}).items():
                if actor in cast:
                    latest_states[actor] = copy.deepcopy(state)
            origin = previous.get('origin', {})
            operation = text(origin.get('operation'))
            state_change = text(origin.get('state_change'))
            if 'state_change' not in origin and re.fullmatch(r'o[0-9a-f]{12}', operation):
                source = folder.parent / operation / 'input_en.json'
                if source.is_file():
                    state_change = text(read(source).get('state_change'))
            request_key = operation or previous.get('id')
            if state_change and request_key not in seen_operations:
                persistent_requests.append(dict(panel_id=previous.get('id'), operation=operation,
                                                actor=previous.get('actor'), state_change=state_change))
                seen_operations.add(request_key)
        input_path = folder / 'input_en.json'
        current_state_change = text(read(input_path).get('state_change')) if input_path.is_file() else ''
        context = dict(cast=cast, accepted_prefix=clean(prefix or []), latest_active_states=latest_states,
                       persistent_state_requests=persistent_requests, current_state_change=current_state_change,
                       panels=[dict(index=index, **{k: p.get(k) for k in
                           ('actor', 'description', 'background', 'camera', 'visual_states')})
                           for index, p in enumerate(panels, 1)])
        save(folder / 'state_context.json', context)
        task = '''Resolve visual state only for the supplied existing panel descriptions. Do not write, rewrite, expand or regenerate any story, dialogue, background or camera choice.
Return JSON {"panels":[{"index":1,"visual_states":{"actor1":{"appearance":["every active identity and outfit attribute explicitly in English"],"held_objects":["objects actually held now"],"pose":"the pose at this depicted instant"}}}]}.
Return each supplied 1-based index exactly once, in order. Use only supplied cast actor IDs and include every actor in that panel's camera.subjects. Return only index and visual_states, with appearance, held_objects and pose for each actor.
Resolve panels in chronological order, carrying the first resolved state into the second. The accepted prefix has already happened. Its latest established active state takes precedence over incompatible cast defaults: do not put baseline shoes back onto an actor who is barefoot. When a new description explicitly depicts a change, apply only that change. Non-mention or an off-frame body part is not a removal or a reset. Use cast defaults only for attributes that have not been established.
Start from latest_active_states, the separately extracted latest state for each actor, rather than an older matching outfit elsewhere in the prefix. persistent_state_requests contains explicit user changes in story order, only up to this insertion point; those requests remain authoritative until a later explicit request or a described actual change supersedes them. An accidental reappearance in a draft state, or the cast baseline, does not revoke a persistent request. Apply current_state_change to the requested new panels while retaining unrelated state. Do not apply any future state change before its story position.
When a request removes an item, make that absence explicit in the complete appearance: for example, 'no shoes' or 'barefoot' as appropriate while keeping specified leggings and other clothing. Do not merely omit 'Brown shoes' and then infer them again from the baseline. Persistent requests also correct accidental omissions or restorations in latest_active_states; do not invent a dressing action to reconcile them.
Draft visual_states may be incomplete or contradict the description. Treat them as hints, not authority over the described visible facts or accepted active state. In particular, an object resting beside an actor, on the ground, or in a holder is not held. If no change is depicted, carry established held objects forward. Empty held_objects=[] is valid.
appearance is the COMPLETE active identity and outfit independent of framing, not a delta or a reference such as 'same outfit' or 'unchanged'. Expand inherited details explicitly while retaining compatible new visible details such as frosting on clothing. State only what is established by these inputs; do not invent a new action to explain a discrepancy. Pose describes this frame, not a sequence of actions.
Input:\n''' + json.dumps(context, ensure_ascii=False)
        result = self.call(folder, 'state_resolution', task, model='glm-4-6')
        resolved = result.get('panels')
        if not isinstance(resolved, list) or len(resolved) != len(panels) or any(
                not isinstance(row, dict) or type(row.get('index')) is not int or row['index'] != index
                for index, row in enumerate(resolved, 1)):
            raise ValueError('정리된 상태의 컷 순서가 맞지 않아요. 원문은 보존했어요.')
        for p, row in zip(panels, resolved):
            states = row.get('visual_states')
            if p.get('actor', ...) is None and states != {}:
                raise ValueError('무인 컷에 인물 상태가 추가됐어요. 원문은 보존했어요.')
            if not isinstance(states, dict) or any(actor not in cast for actor in states) or any(
                    actor not in states for actor in p['camera']['subjects']) or not all(isinstance(s, dict) for s in states.values()):
                raise ValueError('정리된 인물 상태를 확인하지 못했어요. 원문은 보존했어요.')
            p['visual_states'] = {actor: {k: copy.deepcopy(state.get(k)) for k in ('appearance', 'held_objects', 'pose')}
                                  for actor, state in states.items()}
        self.validate(prepared, cast, len(panels))
        save(folder / 'prepared_english.json', prepared)
        return prepared

    def validate(self, raw, cast, count):
        if raw.get('conflict'):
            raise ValueError('지정한 변화가 고정된 앞뒤 컷과 충돌해요. 새 컷은 반영하지 않았어요. ' + text(raw['conflict']))
        panels = raw.get('panels')
        if panels is None and isinstance(raw.get('panel'), dict):
            panels = [raw['panel']]
        if not isinstance(panels, list) or len(panels) != count:
            raise ValueError('요청한 컷 수와 응답이 달라요. 원문은 보존했어요.')
        for p in panels:
            actorless = actorless_panel(p)
            if (p.get('actor') not in cast and not actorless) or not all(isinstance(p.get(k), str) and p[k].strip() for k in ('description', 'background')):
                raise ValueError('장면 설명 또는 인물을 확인할 수 없어요. 원문은 보존했어요.')
            if not isinstance(p.get('dialogues'), list) or not all(isinstance(s, str) and s.strip() for s in p['dialogues']):
                raise ValueError('대사 목록을 확인할 수 없어요. 원문은 보존했어요.')
            camera = p.get('camera', {})
            if not all(isinstance(camera.get(k), str) and camera[k].strip() for k in ('focus', 'framing', 'angle')):
                raise ValueError('구도 설명이 빠졌어요. 원문은 보존했어요.')
            if actorless:
                continue
            if not isinstance(camera.get('subjects'), list) or not camera['subjects'] or any(a not in cast for a in camera['subjects']):
                raise ValueError('화면에 등장하는 인물을 확인해 주세요.')
            states = p.get('visual_states', {})
            for actor in camera['subjects']:
                state = states.get(actor, {})
                if not isinstance(state.get('appearance'), list) or not state['appearance'] or not all(isinstance(s, str) for s in state['appearance']):
                    raise ValueError('현재 외형 상태가 빠졌어요. 원문은 보존했어요.')
                if not isinstance(state.get('held_objects'), list) or not all(isinstance(s, str) and s.strip() for s in state['held_objects']) or not isinstance(state.get('pose'), str) or not state['pose'].strip():
                    raise ValueError('소지품 또는 자세 상태 형식을 확인할 수 없어요. 원문은 보존했어요.')
        return panels

    def korean(self, folder, project, panels, include_cast=False):
        mode = options(project.get('generation_options'))['translation_mode']
        save(folder / 'translation_choice.json', dict(mode=mode, english_canon_preserved=True))
        if mode != 'full':
            english_display(project, panels, include_cast)
            if mode == 'dialogue':
                translate_dialogue(self, folder, project, panels, NATURAL_DIALOGUE)
            return
        task = '''Translate these actual English comic results into natural Korean for display. No new events, interpretation, critique or extra dialogue. Return JSON {"title":"Korean title","characters_ko":[{"id":"unchanged ID","name":"Korean name","personality":"Korean","appearance":"Korean readable description"}],"place_ko":"Korean setting","panels":[{"id":"unchanged ID","description_ko":"brief faithful Korean description","background_ko":"Korean","dialogues_ko":["translate each line in the same count/order; [] stays []"],"camera_ko":{"focus":"Korean","framing":"Korean","angle":"Korean"},"state_ko":"brief current outfit/held-object description in Korean"}]}. Include every supplied panel once, in order.\n'''
        # Reframed panels may still carry the anchor's old display translation.
        english_panels = [{k: v for k, v in panel.items()
                           if not k.endswith('_ko') and k not in ('display_language', 'dialogue_language')}
                          for panel in panels]
        obj = self.call(folder, 'output_translation', NATURAL_DIALOGUE + '\n' + task + json.dumps(dict(title=project.get('title_en', ''), cast=project['cast'] if include_cast else {}, setting=project['setting'], panels=english_panels, dialogue_context=dialogue_context(project, panels)), ensure_ascii=False))
        rows = obj.get('panels', [])
        if not isinstance(rows, list) or [p.get('id') for p in rows] != [p['id'] for p in panels]:
            raise ValueError('번역된 컷 순서를 확인하지 못했어요. 영어 원문은 보존했어요.')
        for panel, translated in zip(panels, rows):
            lines = translated.get('dialogues_ko', [])
            if isinstance(lines, str):
                try:
                    decoded = json.loads(lines)
                    lines = decoded if isinstance(decoded, list) else [lines]
                except json.JSONDecodeError:
                    lines = [lines]
            if len(lines) != len(panel['dialogues']) or not all(isinstance(s, str) for s in lines):
                raise ValueError('번역된 대사 수가 달라요. 영어 원문은 보존했어요.')
            for field in ('description_ko', 'background_ko', 'state_ko'):
                panel[field] = text(translated.get(field))
            panel['dialogues_ko'] = lines
            panel['camera_ko'] = translated.get('camera_ko', {})
            panel.update(display_language='ko', dialogue_language='ko')
        if include_cast:
            project['title'] = text(obj.get('title')) or '새 이야기'
            project['place_ko'] = project['place_ko'] or text(obj.get('place_ko'))
            translated_cast = {c['id']: c for c in obj.get('characters_ko', [])}
            provided = {c['id']: c for c in project['characters_ko']}
            project['characters_ko'] = [provided.get(actor) or translated_cast.get(actor) or dict(id=actor, name=c['name'], personality='', appearance='') for actor, c in project['cast'].items()]

    def create(self, payload, progress=lambda _: None):
        generation_options = options(payload.get('generation_options'))
        count, mode = self.controls(payload)
        seed = text(payload.get('seed'))
        if not seed or len(seed) > 6000:
            raise ValueError('단어나 짧은 문장을 입력해 주세요. 6,000자까지 가능해요.')
        data = dict(seed=seed, direction=text(payload.get('direction')), place=text(payload.get('place')),
                    characters=self.characters(payload.get('characters', [])), count=count, dialogue=mode,
                    action_hint=text(payload.get('action_hint')), state_change=text(payload.get('state_change')))
        from .iterative_recovery import current_session
        session = current_session(self)
        pid = session.project_id if session else identity('p')
        folder = self.operation(pid, data)
        progress('입력한 설정을 영어로 옮기고 있어요.')
        en = self.english(folder, data)
        auto_cast = payload.get('_auto_prepare_cast') is True and not data['characters']
        if auto_cast:
            progress('이야기 설정에 필요한 최소 인물과 관계를 정리하고 있어요.')
            brief = {key: en.get(key, '') for key in ('seed', 'direction', 'place', 'action_hint', 'state_change')}
            planned = self.call(folder, 'cast_plan', '''Plan only the minimum stable cast required by this English comic brief. Return JSON {"characters":[{"id":"actor1","name":"English name","personality":"stable personality and any explicitly requested relationship","appearance":"concise complete baseline identity and outfit in English"}]}.
Use 1 to 4 distinct characters with stable actor1, actor2, etc IDs. Respect every explicitly requested person count, age, role and relationship. If the brief requests two adult friends, register both distinct adults and their friendship even if one is offscreen in the opening shot. Do not replace a requested friend with an anonymous stranger. Invent names or stable visual distinctions only where unspecified. Do not add unnecessary supporting people.
Write no plot, panels, actions, dialogue, future event or future outcome. A requested temporary prop condition or action belongs to the story/state request, not to a new cast member or permanent identity. Return every name/personality/appearance as a nonempty English string; appearance is one comma-separated string, not an array.
English brief:\n''' + json.dumps(brief, ensure_ascii=False), model='glm-4-6')
            planned_rows = planned.get('characters') if isinstance(planned, dict) else None
            if (not isinstance(planned_rows, list) or not planned_rows or
                    any(not isinstance(row, dict) or not all(isinstance(row.get(key), str) and row[key].strip()
                            for key in ('id', 'name', 'personality', 'appearance')) for row in planned_rows)):
                raise ValueError('자동 시작에 필요한 인물 설정을 확인하지 못했어요. 원문은 보존했어요.')
            en['characters'] = self.characters(planned_rows)
            save(folder / 'cast_plan_characters.json', en['characters'])
            save(folder / 'input_en.json', en)
        for character in en['characters']:
            if not character.get('name'):
                character['name'] = 'Character ' + character['id'].removeprefix('actor')
            if character.get('gender') in ('girl', 'boy'):
                character['appearance'] = character['gender'] + ', ' + character.get('appearance', '')
        save(folder / 'input_en.json', en)
        progress('NAI가 첫 장면과 대사 유무를 정하고 있어요.')
        task = f'Create only the first {count} panel(s) of a comic inspired by this brief. Do not write a full outline or finish a long story. User character personality/appearance and place are authoritative. Register ALL supplied character IDs in cast, including offscreen characters. Never renumber IDs. Names like Character 1 are identity labels for unnamed people, not reasons to omit them. Gender is authoritative; girl/boy is an image tag, not an age instruction. Preserve any specified age. Invent only unspecified details. Use actor1 etc as stable IDs. Honor the requested action and state change.\n' + json.dumps(en, ensure_ascii=False) + '\n' + DIALOGUE[mode] + '\n' + SCHEMA
        if auto_cast:
            task += '''\nRegister ALL supplied characters in cast using their exact IDs, names, personalities, relationships and appearances, including characters offscreen in these first panels. Add no other cast member, anonymous stranger or unregistered speaking person. An offscreen registered character can appear in a later panel; not every character must appear in every shot.
Keep each panel's primary actor, described acting person, dialogue speaker and camera.subjects consistent. Any person visibly acting or speaking in a panel must be a registered actor in camera.subjects with their own visual_states. panel.actor owns that panel's dialogue; never attach another person's response to the protagonist. Use a silent panel or a separate shot when a response cannot be assigned to a visible registered speaker. Preserve explicitly requested relationships rather than inventing a stranger for an exchange.'''
        task += concise_instruction(generation_options)
        raw = self.call(folder, 'story', task, model='xialong-v1')
        cast = raw.get('cast')
        if not isinstance(cast, dict) or not cast or any(not re.fullmatch(r'actor[1-9][0-9]*', a) for a in cast):
            raise ValueError('인물 설정을 읽을 수 없어요. 응답은 보존했어요.')
        required_cast = {c['id'] for c in (en['characters'] if auto_cast else data['characters'])}
        restored=[]
        supplied={c['id']:c for c in en['characters']}
        for actor in required_cast - set(cast):
            c=supplied[actor]
            # Register explicit user identity even if the first shot leaves them offscreen.
            # Do not rename or reassign model actors or invent their actions.
            cast[actor]=dict(name=c['name'], personality=c.get('personality',''), appearance=[c['appearance']] if c.get('appearance','').strip() else [])
            restored.append(actor)
        for actor,c in supplied.items():
            cast[actor]['gender']=c.get('gender','auto')
        if restored:save(folder / 'registered_user_cast.json',dict(restored_ids=sorted(restored),cast=cast))
        if auto_cast and set(cast) != required_cast:
            raise ValueError('자동 인물 계획에 없는 인물이 응답에 추가됐어요. 원문은 보존했어요.')
        progress('장면 원문을 유지하며 현재 착장과 소지품을 정리하고 있어요.')
        initial_panels = raw.get('panels', [raw['panel']] if isinstance(raw.get('panel'), dict) else None)
        if not isinstance(initial_panels, list) or len(initial_panels) != count:
            raise ValueError('요청한 컷 수와 응답이 달라요. 이야기 생성 단계부터 다시 요청할 수 있어요. 원문은 보존했어요.')
        raw = self.prepare(folder, raw, cast)
        panels = self.validate(raw, cast, count)
        if auto_cast and any(p.get('actor') is not None and p['actor'] not in p['camera']['subjects'] for p in panels):
            raise ValueError('자동 시작 장면의 행동·대사 주체와 화면 인물이 달라요. 원문은 보존했어요.')
        if mode == 'none' and any(p['dialogues'] for p in panels):
            raise ValueError('무대사 요청에 대사가 포함됐어요. 원문은 보존했어요.')
        if mode == 'with' and any(len(p['dialogues']) != 1 for p in panels):
            raise ValueError('대사 넣기 요청에 필요한 한 줄 대사가 빠졌어요. 원문은 보존했어요.')
        project = dict(id=pid, title_en=text(raw.get('title')), title='새 이야기', seed_ko=seed,
                       direction=en['direction'], direction_ko=data['direction'], setting=en['place'] or text(raw.get('setting')),
                       place_ko=data['place'], cast=cast, characters_ko=data['characters'], panels=[], pages=[],
                       revision=1, updated=now(), ended=bool(raw.get('ended')), operations=[], generation_options=generation_options)
        for p in panels:
            p.update(id=identity('c'), origin=dict(intent='start', position='start', operation=folder.name,
                                                 state_change=text(en.get('state_change'))))
        save(folder / 'accepted_english.json', panels)
        progress('선택한 언어로 결과를 준비하고 있어요.')
        self.korean(folder, project, panels, include_cast=True)
        project['panels'] = panels
        project['operations'].append(dict(id=folder.name, intent='start', added_ids=[p['id'] for p in panels]))
        self.commit(project)
        return project

    def settings(self, project_id, payload, progress=lambda _: None):
        project = self.load(project_id)
        generation_options = options(payload.get('generation_options'), options(project.get('generation_options')))
        data = dict(direction=text(payload.get('direction')), place=text(payload.get('place')),
                    characters=self.characters(payload.get('characters', []), project['cast']))
        folder = self.operation(project_id, dict(operation='settings', **data))
        # Changing only the display mode does not require retranslating character settings.
        if (data['direction'] == project['direction_ko'] and data['place'] == project['place_ko'] and
                data['characters'] == project['characters_ko']):
            save(folder / 'before.json', project)
            project['generation_options'] = generation_options
            project['revision'] += 1
            self.commit(project)
            return project
        progress('수정한 설정을 영어로 옮기고 있어요.')
        en = self.english(folder, data)
        save(folder / 'before.json', project)
        old_ko = {c['id']: c for c in project['characters_ko']}
        new_ko = {c['id']: c for c in data['characters']}
        edits = {}
        for row in en['characters']:
            previous = project['cast'].get(row['id'], {})
            appearance = text(row.get('appearance'))
            candidate = dict(gender=row.get('gender',previous.get('gender','auto')), name=row.get('name') or previous.get('name', row['id']),
                personality=row.get('personality') or previous.get('personality', ''),
                appearance=[s.strip() for s in appearance.split(',') if s.strip()] if appearance else previous.get('appearance', []))
            changes = {k: candidate[k] for k in ('gender', 'name', 'personality', 'appearance') if new_ko[row['id']].get(k) != old_ko.get(row['id'], {}).get(k)}
            project['cast'][row['id']] = dict(previous, **changes)
            if changes:
                edits[row['id']] = changes
        project.update(direction=en['direction'] if data['direction'] != project['direction_ko'] else project['direction'],
                       direction_ko=data['direction'], setting=(en['place'] or project['setting']) if data['place'] != project['place_ko'] else project['setting'],
                       place_ko=data['place'] or project['place_ko'], characters_ko=data['characters'],
                       latest_setting_edits=dict(cast=edits), generation_options=generation_options)
        project['revision'] += 1
        self.commit(project)
        return project

    def page(self, project_id, payload):
        project = self.load(project_id)
        ids = payload.get('panel_ids')
        if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
            raise ValueError('페이지로 만들 컷을 한 개 이상 선택해 주세요.')
        ordered = [p['id'] for p in project['panels'] if p['id'] in ids]
        if set(ordered) != set(ids):
            raise ValueError('존재하지 않는 컷이 포함됐어요.')
        layout = payload.get('layout', 'auto')
        if layout not in ('auto', 'top', 'middle', 'bottom'):
            raise ValueError('컷 배치를 선택해 주세요.')
        before_id = payload.get('before_page_id')
        at = len(project['pages'])
        if before_id is not None:
            at = next((i for i, page in enumerate(project['pages']) if page['id'] == before_id), None)
            if at is None:
                raise ValueError('앞에 추가할 기준 페이지를 찾을 수 없어요. 페이지를 다시 선택해 주세요.')
        new_page = dict(id=identity('g'), title=f'{at+1}페이지',
                        panel_ids=ordered, layout=layout, status='draft', renders=[])
        project['pages'].insert(at, new_page)
        for index, page in enumerate(project['pages'], 1):
            page['title'] = f'{index}페이지'
        from .image_preferences import path as image_defaults_path, load as image_defaults
        if image_defaults_path(self).exists():
            prefs = image_defaults(self)
            new_page.update(image_settings=prefs, width=prefs['width'], height=prefs['height'])
        project['revision'] += 1
        self.commit(project)
        return project

    def render(self, project_id, page_id, progress=lambda _: None, *, payload=None):
        from .iterative_page_rendering import render_project_page
        return render_project_page(self, project_id, page_id, progress, payload=payload)

    def export(self, project_id):
        project = self.load(project_id)
        folder = self.folder(project_id)
        target = folder / 'comic.zip'
        import html
        sections = []
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('project.json', json.dumps(project, ensure_ascii=False, indent=2))
            for index, page in enumerate(project['pages'], 1):
                if not page.get('image_url'):
                    continue
                relative = page['image_url'].split('/files/' + project_id + '/', 1)[1]
                image = folder / relative
                filename = f'page_{index:02}.png'
                archive.write(image, filename)
                selected = [p for p in project['panels'] if p['id'] in page['panel_ids']]
                words = ''.join('<p>' + html.escape(line) + '</p>' for p in selected for line in p['dialogues_ko'])
                sections.append(f'<section><h2>{index}페이지</h2><img src="{filename}">{words}</section>')
            archive.writestr('index.html', '<!doctype html><meta charset="utf-8"><title>' + html.escape(project['title']) + '</title><style>body{background:#142033;color:#fff;font:18px/1.7 sans-serif;max-width:900px;margin:auto;padding:24px}img{width:100%}section{margin:40px 0}</style><h1>' + html.escape(project['title']) + '</h1>' + ''.join(sections))
        return target

    def expand(self, project_id, payload, progress=lambda _: None):
        project = self.load(project_id)
        project['generation_options'] = options(payload.get('generation_options'), options(project.get('generation_options')))
        count, mode = self.controls(payload, unlimited=True)
        intent, position = payload.get('intent', 'story'), payload.get('position', 'after')
        if intent not in INTENT or position not in ('before', 'after'):
            raise ValueError('추가 위치와 방식을 선택해 주세요.')
        try:
            anchor_index = next(i for i, p in enumerate(project['panels']) if p['id'] == payload.get('anchor_id'))
        except StopIteration:
            raise ValueError('기준 컷을 선택해 주세요.') from None
        at = anchor_index + (position == 'after')
        anchor = project['panels'][anchor_index]
        data = {k: text(payload.get(k)) for k in ('instruction', 'action_hint', 'state_change')}
        data.update(count=count, dialogue=mode, intent=intent, position=position, anchor_id=anchor['id'])
        folder = self.operation(project_id, data)
        if payload.get('_custom_story_profile'):
            save(folder / 'story_profile_override.json', payload['_custom_story_profile'])
        progress('이번 요청을 영어로 옮기고 있어요.')
        en = self.english(folder, data)
        prefix, suffix = project['panels'][:at], project['panels'][at:]
        clean = lambda rows: [{k: v for k, v in p.items() if not k.endswith('_ko') and k not in ('display_language', 'dialogue_language')} for p in rows]
        save(folder / 'before.json', project)
        task = f'Generate exactly {count} new panel(s) in the indicated gap. ' + INTENT[intent] + '\n' + DIALOGUE[mode] + '''
Actual prefix has already happened; fixed suffix is FUTURE and has not happened at this point. Do not rewrite either side. Honor the user action/state request, and reach the fixed right starting state naturally. Mere non-mention of a prop is not a contradiction. If a requested persistent state change contradicts an explicit fixed future fact, return conflict with a brief reason and panels=[]; do not silently restore clothing or modify the original panels.
For an ending request, finish through a visible resolution. For an empty suffix, continue the last actual state. For an empty prefix, write a preceding moment that leads into the first fixed panel.
''' + 'This operation:\n' + json.dumps(en, ensure_ascii=False) + '\nFixed suffix:\n' + json.dumps(clean(suffix), ensure_ascii=False)
        if intent == 'emphasis':
            task += '\nFor emphasis, interpret state_change by its meaning, not by whether the field is populated. Expression, gaze, framing, and adjacent pose changes are compatible with emphasis. Preserve established outfit and held-object continuity. If the request requires an actual outfit or persistent possession change, return conflict explaining that action connection is needed; do not silently discard the request.\n'
            task += '\nREFERENCE anchor (show the adjacent action phase on the requested side, from a different angle):\n' + json.dumps(clean([anchor]), ensure_ascii=False)
        task += '\n' + SCHEMA + concise_instruction(project.get('generation_options'))
        progress('NAI가 앞뒤 연결과 새 컷을 만들고 있어요.')
        raw = self.call(folder, 'story', task, memory=self.facts(project), history=json.dumps(clean(prefix), ensure_ascii=False), model='xialong-v1')
        if raw.get('conflict'):
            reason = self.call(folder, 'conflict_translation', 'Translate this conflict into brief Korean. Return {"reason":"Korean"}.\n' + text(raw['conflict']))
            raise ValueError(text(reason.get('reason')) or '고정된 앞뒤 컷과 상태 변화가 충돌해요.')
        returned = raw.get('panels')
        if isinstance(returned, list) and len(returned) > count:
            from hashlib import sha256
            # Only remove complete, exact echoes of the context sent to this request.
            # Canonical JSON keeps bool/number/string distinctions; do not normalize text.
            exact = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            original_hash = sha256(exact(raw).encode('utf-8')).hexdigest()
            preserved = folder / 'echo_originals' / (original_hash + '.json')
            if not preserved.exists():
                save(preserved, raw)
            left, right = clean(prefix), clean(suffix)
            stripped = None
            removed_suffix = 0
            for tail in ([right, []] if right else [[]]):
                if (len(returned) == len(left) + count + len(tail)
                        and exact(returned[:len(left)]) == exact(left)
                        and (not tail or exact(returned[-len(tail):]) == exact(tail))):
                    stripped = copy.deepcopy(returned[len(left):len(left) + count])
                    removed_suffix = len(tail)
                    break
            audit = dict(version='exact-context-echo-v1', applied=stripped is not None,
                         returned_count=len(returned), requested_count=count,
                         removed_prefix_count=len(left) if stripped is not None else 0,
                         removed_suffix_count=removed_suffix,
                         original_value_sha256=original_hash,
                         preserved_raw_path=preserved.relative_to(folder).as_posix(),
                         preserved_file_sha256=sha256(preserved.read_bytes()).hexdigest(),
                         match_policy='complete clean(prefix) + requested count + optional complete clean(suffix); exact JSON values and types')
            api_story = folder / 'story' / 'story.txt'
            if api_story.exists():
                audit['api_story_sha256'] = sha256(api_story.read_bytes()).hexdigest()
            if stripped is not None:
                audit['kept_panels_value_sha256'] = sha256(exact(stripped).encode('utf-8')).hexdigest()
                raw = dict(copy.deepcopy(raw), panels=stripped)
            else:
                audit['reason'] = 'No exact complete context boundary matched; response unchanged.'
            save(folder / 'echo_strip_audit.json', audit)
        returned_panels = raw.get('panels')
        if returned_panels is None and isinstance(raw.get('panel'), dict):
            returned_panels = [raw['panel']]
        if not isinstance(returned_panels, list) or len(returned_panels) != count:
            raise ValueError('요청한 컷 수와 응답이 달라요. 이야기 생성 단계부터 다시 요청할 수 있어요. 원문은 보존했어요.')
        if intent == 'emphasis':
            candidates = raw.get('panels')
            if candidates is None and isinstance(raw.get('panel'), dict):
                candidates = [raw['panel']]
            for candidate in candidates if isinstance(candidates, list) else []:
                camera = candidate.get('camera') if isinstance(candidate, dict) else None
                if isinstance(camera, dict) and all(isinstance(camera.get(key), str) and
                        camera[key].strip().casefold() == anchor['camera'][key].strip().casefold()
                        for key in ('framing', 'angle')):
                    raise ValueError('원래와 같은 시점이 돌아왔어요. 구체적인 다른 시점을 요청해 주세요.')
        progress('장면 원문을 유지하며 현재 착장과 소지품을 정리하고 있어요.')
        raw = self.prepare(folder, raw, project['cast'], prefix=prefix)
        panels = self.validate(raw, project['cast'], count)
        if mode == 'none' and any(p['dialogues'] for p in panels):
            raise ValueError('무대사 요청에 대사가 포함됐어요. 원문은 보존했어요.')
        if mode == 'with' and any(len(p['dialogues']) != 1 for p in panels):
            raise ValueError('대사 넣기 요청에 필요한 한 줄 대사가 빠졌어요. 원문은 보존했어요.')
        for p in panels:
            p.update(id=identity('c'), origin=dict(intent=intent, position=position, anchor_id=anchor['id'],
                                                 operation=folder.name, state_change=text(en.get('state_change'))))
        save(folder / 'accepted_english.json', panels)
        progress('새 컷을 선택한 언어로 준비하고 있어요.')
        self.korean(folder, project, panels)
        project['panels'][at:at] = panels
        assert [p for p in project['panels'] if p['id'] not in {n['id'] for n in panels}] == read(folder / 'before.json')['panels']
        project['revision'] += 1
        if not suffix and intent != 'emphasis':
            project['ended'] = bool(raw.get('ended'))
        project['operations'].append(dict(id=folder.name, intent=intent, position=position,
                                         anchor_id=anchor['id'], added_ids=[p['id'] for p in panels], existing_panels_unchanged=True))
        self.commit(project)
        return project
