"""Shareable failure metadata; never include prompts, exception text or paths."""
import json
import re
import traceback
from urllib.error import URLError

from app_version import VERSION
from .prompt_management import STAGES


def identifier(value, fallback='unknown'):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', value) else fallback


def diagnose(job, folder=None, exc=None):
    result = {}
    if folder:
        for name in ('result.json', 'attempt.json'):
            try:
                value = json.loads((folder / name).read_text(encoding='utf-8'))
                if isinstance(value, dict):
                    result.update({key: value[key] for key in ('http_status', 'finish_reason', 'error_type', 'api_error', 'model', 'requested_model', 'fallback_reason') if key in value})
            except (OSError, ValueError):
                pass
    error_type = identifier(type(exc).__name__ if exc else job.get('error_type') or result.get('error_type'))
    status = getattr(exc, 'code', None) or result.get('http_status')
    status = status if type(status) is int and 100 <= status <= 599 else None
    finish = result.get('finish_reason')
    finish = finish if finish in ('stop', 'length', 'content_filter', 'tool_calls') else None
    retry = '작업 기록에서 실패 단계 재시도를 선택해 주세요.'
    category, summary, action = 'internal', '프로그램 내부 처리 중 오류가 발생했어요.', '문의용 정보를 복사해 전달해 주세요.'
    if status and status >= 400:
        category = 'http'
        if status in (401, 403):
            summary, action = 'API가 인증 또는 접근을 거부했어요.', '설정에서 이 작업에 사용하는 키와 구독 상태를 확인해 주세요.'
        elif status == 429:
            summary, action = 'API 요청 제한 응답을 받았어요.', '잠시 기다린 뒤 재시도해 주세요. 반복되면 계정의 사용량을 확인해 주세요.'
        elif status >= 500:
            summary, action = 'API 서버에서 오류 응답을 보냈어요.', '잠시 기다린 뒤 실패 단계 재시도를 선택해 주세요.'
        else:
            summary, action = 'API가 요청을 거부했어요.', '요청 설정과 받은 원문을 확인해 주세요. 반복되면 문의용 정보를 전달해 주세요.'
    elif error_type in ('TimeoutError', 'timeout') or isinstance(getattr(exc, 'reason', None), TimeoutError):
        category, summary, action = 'timeout', 'API 응답을 기다리다 제한 시간을 넘겼어요.', retry
    elif isinstance(exc, URLError) or error_type in ('URLError', 'ConnectionError', 'ConnectionResetError', 'ConnectionAbortedError', 'RemoteDisconnected', 'IncompleteRead', 'SSLError'):
        category, summary, action = 'network', 'API와 통신 중 연결 오류가 발생했어요.', '인터넷 연결을 확인한 뒤 실패 단계 재시도를 선택해 주세요.'
    elif result.get('api_error') is True:
        category, summary, action = 'api_event', 'API가 응답 도중 오류 이벤트를 보냈어요.', retry
    elif error_type == 'IncompleteResponseError':
        category, summary, action = 'incomplete', '응답의 정상 완료를 확인하지 못했어요.', retry
        if finish == 'length':
            summary, action = '응답이 출력 길이 제한에 도달했어요.', '요청 컷 수나 설명을 줄여 다시 요청해 주세요.'
    elif error_type == 'JSONDecodeError':
        category, summary, action = 'json', '받은 응답의 JSON 형식을 읽지 못했어요.', retry
    elif error_type == 'FileNotFoundError':
        category, summary, action = 'file', '작업에 필요한 파일을 찾지 못했어요.', '파일이 이동·삭제되었는지 확인하고 문의용 정보를 전달해 주세요.'
    elif error_type in ('PermissionError', 'OSError'):
        category, summary, action = 'file', '파일을 읽거나 저장하지 못했어요.', '저장 공간과 폴더 접근 권한을 확인해 주세요.'
    elif error_type == 'ValueError':
        category, summary, action = 'validation', '요청 또는 생성 결과가 해당 단계의 조건을 충족하지 못했어요.', '아래 오류 안내와 받은 원문을 확인해 주세요.'
    elif error_type == 'InterruptedError':
        category, summary, action = 'interrupted', '작업이 중단되었어요.', retry
    elif error_type == 'unknown':
        category, summary = 'unknown', '남아 있는 기록만으로 오류 종류를 확인하지 못했어요.'
    stage = identifier(job.get('stage'), 'preparation')
    label = STAGES[stage][0] if stage in STAGES else '이미지 생성' if stage == 'image' else '작업 처리'
    if job.get('stage_complete'):
        label += ' · 응답 처리'
    if job.get('kind') in ('render', 'studio_render'):
        action += ' 이미지 요청은 처리됐을 수 있으므로 결과와 사용량을 확인한 뒤 재시도해 주세요.'
    locations = []
    if exc:
        # Module names work in a frozen EXE too; source paths may refer to the
        # build machine and must not appear in a shareable report.
        for frame, line in traceback.walk_tb(exc.__traceback__):
            module = frame.f_globals.get('__name__', '')
            if module.startswith('engine.') or module in ('engine_bridge', 'server'):
                locations.append(f'{identifier(module)}:{line} ({identifier(frame.f_code.co_name)})')
    # Only explicitly selected metadata is copied; no str(exc), story, key or raw response.
    report = dict(version=VERSION if exc else 'unknown (older job)', job_id=identifier(job.get('id')),
                  operation=identifier(job.get('kind')), stage=stage,
                  phase='response_processing' if job.get('stage_complete') else 'request_or_response',
                  error_type=error_type, http_status=status, finish_reason=finish,
                  category=category, locations=locations[-6:],
                  automatic_retries=job.get('automatic_retries', 0) if type(job.get('automatic_retries', 0)) is int else 0)
    if isinstance(exc, json.JSONDecodeError):
        report['json_position'] = dict(line=exc.lineno, column=exc.colno, character=exc.pos)
    for key in ('model', 'requested_model'):
        if result.get(key) in ('glm-4-6', 'xialong-v1'):
            report[key] = result[key]
    if result.get('fallback_reason') == 'model_not_allowed_for_tier':
        report['fallback_reason'] = result['fallback_reason']
    if type(getattr(exc, 'errno', None)) is int:
        report['os_error_number'] = exc.errno
    # The existing validation explanation is local-only, separate from copying.
    detail = str(exc) if isinstance(exc, (ValueError, InterruptedError, FileNotFoundError)) else ''
    if exc is None and isinstance(job.get('error'), str):
        detail = job['error']
    return dict(stage_label=label, summary=summary, next_action=action, report=report, detail=detail)


def failure_message(diagnostic, exc):
    report = diagnostic['report']
    code = 'HTTP ' + str(report['http_status']) if report['http_status'] and report['http_status'] >= 400 else report['error_type']
    return f"{diagnostic['stage_label']} 실패 ({code}) · {diagnostic['summary']}"
