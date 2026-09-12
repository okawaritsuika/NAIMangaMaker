"""Conservative JSON syntax recovery; never invent values or close truncated text."""
import json
import hashlib
from pathlib import Path


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def parse_object(raw):
    """Return (object, comma insertions, candidate), keeping the raw input intact."""
    candidate = raw.strip()
    if candidate.startswith('```') and candidate.endswith('```') and '\n' in candidate:
        candidate = candidate.split('\n', 1)[1].rsplit('```', 1)[0].strip()
    decoder = json.JSONDecoder(object_pairs_hook=_pairs)
    try:
        obj = decoder.decode(candidate)
        edits = []
    except json.JSONDecodeError as original:
        edits = []

        def space(i):
            while i < len(candidate) and candidate[i] in ' \t\r\n':
                i += 1
            return i

        def string(i):
            if i >= len(candidate) or candidate[i] != '"':
                raise original
            value, end = decoder.raw_decode(candidate, i)
            if not isinstance(value, str):
                raise original
            return end

        def value(i, depth=0):
            if depth > 100 or i >= len(candidate):
                raise original
            if candidate[i] not in '{[':
                _, end = decoder.raw_decode(candidate, i)
                return end
            is_object = candidate[i] == '{'
            closing = '}' if is_object else ']'
            i = space(i + 1)
            if i < len(candidate) and candidate[i] == closing:
                return i + 1
            while True:
                if is_object:
                    i = space(string(i))
                    if i >= len(candidate) or candidate[i] != ':':
                        raise original
                    i = space(i + 1)
                end = value(i, depth + 1)
                i = space(end)
                if i >= len(candidate):
                    raise original
                if candidate[i] == closing:
                    return i + 1
                if candidate[i] == ',':
                    i = space(i + 1)
                    continue
                # Only add a comma when another complete member/value starts.
                # Adjacent string/number fragments without whitespace are ambiguous.
                if is_object:
                    next_end = space(string(i))
                    if next_end >= len(candidate) or candidate[next_end] != ':':
                        raise original
                elif candidate[i] not in '{["-0123456789tfn' or (
                        i == end and candidate[end - 1] not in '}]'):
                    raise original
                if len(edits) >= 32:
                    raise original
                edits.append(dict(offset=i, insert=','))

        try:
            end = space(value(space(0)))
            if end != len(candidate) or not edits:
                raise original
            repaired = candidate
            for edit in sorted(edits, key=lambda row: row['offset'], reverse=True):
                repaired = repaired[:edit['offset']] + ',' + repaired[edit['offset']:]
            obj = decoder.decode(repaired)
            candidate = repaired
        except (json.JSONDecodeError, RecursionError):
            raise original from None
    if not isinstance(obj, dict):
        raise ValueError('Expected one JSON object.')
    return obj, edits, candidate


def json_object(raw, *, audit_folder=None):
    obj, edits, candidate = parse_object(raw)
    if edits and audit_folder is not None:
        folder = Path(audit_folder)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'syntax_repaired.txt').write_text(candidate, encoding='utf-8')
        audit = dict(kind='missing_json_commas', source_sha256=hashlib.sha256(raw.encode('utf-8')).hexdigest(),
                     offsets_relative_to='trimmed JSON after optional Markdown fence removal',
                     insertions=edits, repaired_file='syntax_repaired.txt')
        (folder / 'syntax_repair.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')
    return obj
