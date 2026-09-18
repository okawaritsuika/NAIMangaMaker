"""Text transport with a bounded fallback for Xialong subscription denial."""
import argparse
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENDPOINT = 'https://text.novelai.net/oa/v1/chat/completions'
_DENIED_KEYS = {}
_DENIED_LOCK = threading.Lock()
_DENIAL_TTL = 600


def _tier_denial(error, folder):
    if error.code != 400:
        return False
    try:
        body = json.loads((folder / 'error.txt').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    message = body.get('message') if isinstance(body, dict) else None
    return isinstance(message, str) and bool(re.search(
        r"model\s+['\"]xialong-v1['\"]\s+not allowed for current user tier\s+['\"][A-Za-z]+['\"]",
        message, flags=re.I))


def generate(request_path, output_path, key_env, api_key=None):
    payload = json.loads(request_path.read_text(encoding='utf-8'))
    if not api_key:
        raise ValueError('설정에서 스토리 생성에 사용할 키를 선택해 주세요.')
    output_path.mkdir(parents=True, exist_ok=False)
    fingerprint = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
    with _DENIED_LOCK:
        for expired in [k for k, until in _DENIED_KEYS.items() if until <= time.monotonic()]:
            del _DENIED_KEYS[expired]
        cached_denial = payload['model'] == 'xialong-v1' and fingerprint in _DENIED_KEYS
    if not cached_denial:
        try:
            return _generate(payload, output_path, api_key)
        except urllib.error.HTTPError as error:
            if payload['model'] != 'xialong-v1' or not _tier_denial(error, output_path):
                raise
            # Keep the exact rejected request and response; recovery archives the
            # whole stage, including this first attempt, when GLM also fails.
            rejected = output_path / 'xialong_rejected'
            rejected.mkdir()
            for name in ('request.json', 'result.json', 'story.txt', 'error.txt', 'response.sse'):
                path = output_path / name
                if path.exists():
                    path.rename(rejected / name)
            with _DENIED_LOCK:
                _DENIED_KEYS[fingerprint] = time.monotonic() + _DENIAL_TTL
    # Do not change the saved stage snapshot: retry/continue relies on its hash.
    # User prompts and generation settings stay; replace only our default model
    # self-identification when it is present as a complete system message.
    (output_path / 'model_fallback.json').write_text(json.dumps(dict(
        requested_model='xialong-v1', actual_model='glm-4-6',
        reason='model_not_allowed_for_tier', cached_denial=cached_denial,
    ), indent=2), encoding='utf-8')
    (output_path / 'requested.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    fallback = dict(payload, model='glm-4-6')
    default_system = (ROOT / 'default_system.txt').read_text(encoding='utf-8').strip()
    fallback['messages'] = [dict(message, content='You are a creative writing assistant. ' + default_system.partition('. ')[2])
                            if message.get('role') == 'system' and message.get('content') == default_system
                            else message for message in payload.get('messages', [])]
    try:
        return _generate(fallback, output_path, api_key)
    finally:
        result_path = output_path / 'result.json'
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding='utf-8'))
            result.update(requested_model='xialong-v1', fallback_reason='model_not_allowed_for_tier')
            result_path.write_text(json.dumps(result, indent=2), encoding='utf-8')


def _generate(payload, output_path, key):
    (output_path / 'request.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    request = urllib.request.Request(ENDPOINT, data=json.dumps(payload).encode('utf-8'), headers={
        'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        'User-Agent': 'PromptServer-Xialong-Prose-Baseline/1.0',
        'Cache-Control': 'no-cache',
    })
    start = time.monotonic()
    parts = []
    finish_reason = None
    usage = None
    first_token = None
    error_type = None
    api_error = False
    status = None
    try:
        with urllib.request.urlopen(request, timeout=90) as response, (output_path / 'response.sse').open('wb') as raw:
            status = response.status
            for line in response:
                raw.write(line)
                raw.flush()
                if not line.startswith(b'data:'):
                    continue
                data = line[5:].strip()
                if data == b'[DONE]':
                    break
                event = json.loads(data)
                usage = event.get('usage') or usage
                if event.get('error'):
                    api_error = True
                    (output_path / 'error.txt').write_text(json.dumps(event['error'], ensure_ascii=False).replace(key, '[REDACTED]'), encoding='utf-8')
                    raise RuntimeError('API returned an error event; see raw response')
                for choice in event.get('choices', []):
                    piece = choice.get('delta', {}).get('content') or choice.get('text') or ''
                    if piece:
                        if first_token is None:
                            first_token = round(time.monotonic() - start, 2)
                        parts.append(piece)
                    finish_reason = choice.get('finish_reason') or finish_reason
    except Exception as exc:
        error_type = type(exc).__name__
        status = getattr(exc, 'code', status)
        if isinstance(exc, urllib.error.HTTPError):
            body = exc.read().decode('utf-8', errors='replace').replace(key, '[REDACTED]')
            (output_path / 'error.txt').write_text(body, encoding='utf-8')
        raise
    finally:
        text = ''.join(parts)
        (output_path / 'story.txt').write_text(text, encoding='utf-8')
        summary = dict(endpoint=ENDPOINT, model=payload['model'], http_status=status,
                       error_type=error_type, api_error=api_error, finish_reason=finish_reason, usage=usage,
                       seconds=round(time.monotonic() - start, 2), first_token_seconds=first_token,
                       characters=len(text), words=len(text.split()))
        (output_path / 'result.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(dict(trial=output_path.name, **summary), ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('request', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--key-env', default='NOVELAI_API_KEY')
    args = parser.parse_args()
    generate(args.request, args.output, args.key_env)


if __name__ == '__main__':
    main()
