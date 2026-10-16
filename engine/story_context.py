"""Explicit direct-HTTP context; not NovelAI editor storage or a full context builder."""
import copy
import json


def compact_state_context(context):
    """Keep local scene detail and earlier outfit/possession changes, not every pose."""
    if context.get('state_context_version') == 'compact-v1':
        return copy.deepcopy(context)
    result = copy.deepcopy(context)
    prefix = result.get('accepted_prefix', [])
    keys = ('id', 'actor', 'description', 'background', 'camera', 'visual_states')
    start = max(0, len(prefix) - 8)
    recent = [dict({key: panel[key] for key in keys if key in panel}, prefix_index=index + 1)
              for index, panel in enumerate(prefix[start:], start)]
    # A count limit alone still admits eight unusually verbose panels. Keep the
    # nearest panel intact, with room for the task, cast and latest actor states.
    while len(recent) > 1 and len(json.dumps(recent, ensure_ascii=False).encode('utf-8')) > 16000:
        start += 1
        recent.pop(0)
    sources, latest_changes, previous_states = {}, {}, {}
    for index, panel in enumerate(prefix):
        for actor, state in panel.get('visual_states', {}).items():
            for key in ('appearance', 'held_objects'):
                slot = (actor, key)
                values = state.get(key)
                if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                    continue
                active = set(values)
                prior = sources.get(slot, {})
                sources[slot] = {value: prior.get(value, index) for value in active}
                if previous_states.get(slot) != active:
                    latest_changes[slot] = index
                    previous_states[slot] = active
    # Retain the introduction of each still-active attribute, and the latest
    # transition for each field (including dropping the final held object).
    # Repeated prop swaps and differently worded outfits cannot accumulate one
    # history record per panel when those attributes are no longer active.
    evidence = {}
    for slot, active in sources.items():
        actor, key = slot
        for index in set(active.values()) | {latest_changes[slot]}:
            if index < start:
                evidence.setdefault(index, {}).setdefault(actor, {})[key] = prefix[index]['visual_states'][actor][key]
    history = [dict(id=prefix[index].get('id'), prefix_index=index + 1, description=prefix[index].get('description'),
                    visual_states=states) for index, states in sorted(evidence.items())]
    positions = {panel['id']: index + 1 for index, panel in enumerate(prefix) if 'id' in panel}
    for request in result.get('persistent_state_requests', []):
        if request.get('panel_id') in positions:
            request['prefix_index'] = positions[request['panel_id']]
    result['accepted_prefix'] = recent
    result['earlier_state_changes'] = history
    result['state_context_version'] = 'compact-v1'
    result['context_note'] = ('accepted_prefix contains only the most recent accepted panels. '
        'earlier_state_changes contains selected evidence for currently active attributes '
        'and the latest outfit/held-object transitions, in story order, with original descriptions. '
        'It is not a complete event history; missing events do not imply a reset. '
        'prefix_index is the 1-based story position shared by evidence, recent panels and user requests. '
        'A changed state alone does not revoke a persistent user request. '
        'latest_active_states and persistent_state_requests cover the entire actual prefix, '
        'including actors absent from recent panels.')
    return result


def with_context(request, *, memory, author_note='', story=''):
    """Resend persistent facts; put local guidance near the actual story tail.

    Memory is plain text in the first user message. A/N is a system message,
    and already generated story is assistant content. No invented HTTP fields.
    Request-specific instructions remain user content; default system is kept.
    """
    result = copy.deepcopy(request)
    messages = result['messages']
    if len(messages) != 2 or [m['role'] for m in messages] != ['system', 'user']:
        raise ValueError('Expected an uncontextualized system/user request')
    facts = 'Memory:\n' + memory.strip()
    if not author_note and not story:
        messages[1]['content'] = facts + '\n\n' + messages[1]['content']
        return result
    task = messages.pop()
    messages.append(dict(role='user', content=facts))
    if author_note:
        messages.append(dict(role='system', content=author_note.strip()))
    if story:
        messages.append(dict(role='assistant', content=story))
    messages.append(task)
    return result
