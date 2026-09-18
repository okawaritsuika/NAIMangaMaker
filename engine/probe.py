"""Independent prose probe. No comic imports, validation, rewriting or retries."""
import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENDPOINT = 'https://text.novelai.net/oa/v1/chat/completions'


def generate(request_path, output_path, key_env, api_key=None):
    payload = json.loads(request_path.read_text(encoding='utf-8'))
    if not api_key:
        raise ValueError('설정에서 스토리 생성에 사용할 키를 선택해 주세요.')
    key = api_key
    output_path.mkdir(parents=True, exist_ok=False)
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
