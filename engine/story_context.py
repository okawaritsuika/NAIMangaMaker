"""Explicit direct-HTTP context; not NovelAI editor storage or a full context builder."""
import copy


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
