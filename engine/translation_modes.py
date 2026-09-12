"""Lab-only translation choices. English canon is never replaced by display text."""
import copy
import json
import re

MODES = ('full', 'dialogue', 'off')

def state_summary(panel):
    return '; '.join(actor + ': ' + ', '.join(state.get('appearance', [])) +
        '; ' + state.get('pose', '') + '; holding: ' + ', '.join(state.get('held_objects', []))
        for actor, state in panel.get('visual_states', {}).items())

def options(value=None, previous=None):
    result = dict(previous or {'translation_mode': 'full'})
    if value is not None:
        if not isinstance(value, dict) or set(value) - {'translation_mode', 'concise_prompts'}:
            raise ValueError('번역 옵션 형식을 확인해 주세요.')
        result.update(value)
    if result.get('translation_mode') not in MODES:
        raise ValueError('전체 한글, 대사만 한글, 번역 안 함 중 선택해 주세요.')
    if 'concise_prompts' in result and type(result['concise_prompts']) is not bool:
        raise ValueError('짧은 묘사 설정은 켜기 또는 끄기로 선택해 주세요.')
    return result

def concise_instruction(value=None):
    if not options(value).get('concise_prompts', False):
        return ''
    return '''\nSHORT DESCRIPTION MODE: Write compact English directly; do not draft a long version or add a compression step.
For description, use one short drawable sentence: subject + visible action + target + essential expression. Prefer roughly 15-30 words, but keep facts over a word limit. No inner thoughts, backstory, decorative prose or invented props. Keep each pose a brief physical phrase and background a short visible-setting phrase. Keep complete identity, outfit and held-object state in their existing fields; never drop required state to save words.
Choose framing and angle to reveal this beat's action or reaction. Use recent shots as context; vary distance or viewpoint when it improves clarity, including hands, feet or the action target when relevant. Do not mechanically cycle angles or default every panel to a face portrait. Preserve the requested dialogue/silence, chronology and same-instant constraints. Return the existing JSON schema only.\n'''

def english_display(project, panels, include_cast):
    """Legacy *_ko fields hold the selected display language, explicitly labelled."""
    for panel in panels:
        panel.update(description_ko=panel['description'], background_ko=panel['background'],
                     camera_ko=copy.deepcopy(panel['camera']), dialogues_ko=list(panel['dialogues']),
                     state_ko=state_summary(panel),
                     display_language='en', dialogue_language='en')
    if include_cast:
        project['title'] = project.get('title_en') or 'New story'
        project['place_ko'] = project.get('place_ko') or project.get('setting', '')
        provided = {c['id']: c for c in project.get('characters_ko', [])}
        project['characters_ko'] = [provided.get(actor) or dict(id=actor, name=c['name'],
            personality=c.get('personality', ''), appearance=', '.join(c.get('appearance', [])))
            for actor, c in project['cast'].items()]

def prefix_for_translation(project, panels):
    prior = project.get('panels', [])
    ids = [p['id'] for p in prior]
    existing = [ids.index(p['id']) for p in panels if p['id'] in ids]
    if existing:
        prior = prior[:min(existing)]
    elif panels:
        origin = panels[0].get('origin', {})
        if origin.get('anchor_id') in ids:
            at = ids.index(origin['anchor_id']) + (origin.get('position') == 'after')
            prior = prior[:at]
        elif origin.get('intent') == 'start':
            prior = []
    return [dict(actor=p['actor'], english=p['dialogues'], korean=p.get('dialogues_ko', []))
            for p in prior[-4:] if p.get('dialogues') and p.get('dialogue_language', 'ko') == 'ko'
            and any(isinstance(line, str) and re.search('[가-힣]', line) for line in p.get('dialogues_ko', []))]

def dialogue_context(project, panels):
    names = {c['id']: c.get('name', '') for c in project.get('characters_ko', [])}
    return dict(direction=project.get('direction', ''),
        speakers={actor: {k: c.get(k, '') for k in ('name', 'personality')} for actor, c in project['cast'].items()},
        established_display_names=names,
        preceding_lines=prefix_for_translation(project, panels),
        panels=[{k: p.get(k) for k in ('id', 'actor', 'description', 'dialogues')} for p in panels])

def translate_dialogue(wb, folder, project, panels, rules):
    # Silent batches bypass the API entirely; they remain silent in rendering/export.
    if not any(p['dialogues'] for p in panels):
        return
    task = rules + '\nReturn JSON {"panels":[{"id":"exact ID","dialogues_ko":["Korean line"]}]}. Return every supplied panel in order. Preserve IDs and line counts; [] stays []. Return no descriptions or commentary.\n'
    context = dialogue_context(project, panels)
    obj = wb.call(folder, 'output_translation', task + json.dumps(context, ensure_ascii=False))
    rows = obj.get('panels')
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows) or [r.get('id') for r in rows] != [p['id'] for p in panels]:
        raise ValueError('번역된 컷 순서가 달라요. 원문을 보존했어요.')
    for panel, row in zip(panels, rows):
        lines = row.get('dialogues_ko')
        if not isinstance(lines, list) or len(lines) != len(panel['dialogues']) or any(not isinstance(s, str) or not s.strip() for s in lines):
            raise ValueError('번역된 대사 수가 달라요. 원문을 보존했어요.')
        panel['dialogues_ko'] = lines
        panel['dialogue_language'] = 'ko'

# Keep this narrow: a statement's intention is interpreted only from supplied context.
NATURAL_DIALOGUE = '''Translate the supplied English comic dialogue into natural spoken Korean. Preserve each speaker, addressee, meaning, uncertainty, negation and speech act. Use the depicted action and surrounding exchange to interpret short or idiomatic replies. Do not copy English syntax, unnecessary pronouns or a mechanical dictionary meaning. A phrase about a plan working is not necessarily a machine operating. Omit subjects only when Korean naturally permits it without changing who acts.
Use established Korean names consistently, with particles and vocative endings appropriate to their Korean pronunciation. Use the established character relationship and previous Korean lines to keep speech level consistent. For explicitly close adult friends in a casual scene, use conversational banmal unless their established voice says otherwise. Do not infer age hierarchy, gender, romance or kinship from names. If their relationship is unspecified, use neutral conversational Korean. Keep jokes and emotional intent without inventing a punchline. No explanatory prose, new facts or new dialogue.'''
