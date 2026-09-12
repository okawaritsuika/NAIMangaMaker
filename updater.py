"""Public GitHub release updater. Replaces one EXE, never user data."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from app_version import VERSION, GITHUB_REPOSITORY, ASSET_NAME

RELEASES_URL = 'https://github.com/' + GITHUB_REPOSITORY + '/releases'
MAX_BYTES = 512 * 1024 * 1024


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('지원하지 않는 릴리스 버전 형식입니다.')
    return tuple(map(int, value.lstrip('v').split('.')))


def parse_release(data):
    if data.get('draft') or data.get('prerelease'):
        return None
    version = data.get('tag_name', '')
    if version_tuple(version) <= version_tuple(VERSION):
        return None
    asset = next((a for a in data.get('assets', []) if a.get('name') == ASSET_NAME), None)
    if not asset:
        raise ValueError('이 릴리스에 Windows EXE가 없습니다.')
    digest = asset.get('digest', '')
    if not isinstance(digest, str) or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('업데이트 파일의 SHA-256 검증값을 확인할 수 없습니다.')
    expected = RELEASES_URL + '/download/' + version + '/' + ASSET_NAME
    if asset.get('browser_download_url') != expected:
        raise ValueError('업데이트 다운로드 주소가 공식 저장소와 다릅니다.')
    size = asset.get('size')
    if type(size) is not int or not 0 < size <= MAX_BYTES:
        raise ValueError('업데이트 파일 크기를 확인할 수 없습니다.')
    return dict(version=version, url=expected, sha256=digest[7:], size=size,
                notes=str(data.get('body') or '')[:12000])


def latest():
    request = urllib.request.Request('https://api.github.com/repos/' + GITHUB_REPOSITORY + '/releases/latest',
        headers={'Accept':'application/vnd.github+json', 'X-GitHub-Api-Version':'2022-11-28',
                 'User-Agent':'NAIMangaMaker/' + VERSION})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError('릴리스 응답이 너무 큽니다.')
        return parse_release(json.loads(body))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        if error.code in (403, 429):
            raise ValueError('GitHub 조회 제한입니다. 잠시 후 다시 확인해 주세요.') from error
        raise


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def download(release, data_dir, progress=lambda value: None):
    # Validate even when called independently of latest().
    if release['url'] != RELEASES_URL+'/download/'+release['version']+'/'+ASSET_NAME:
        raise ValueError('공식 다운로드 주소가 아닙니다.')
    version_tuple(release['version'])
    if not re.fullmatch('[0-9a-f]{64}', release['sha256']):
        raise ValueError('SHA-256 값이 올바르지 않습니다.')
    if not 0 < release['size'] <= MAX_BYTES:
        raise ValueError('파일 크기가 올바르지 않습니다.')
    folder = Path(data_dir) / 'updates' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    partial = folder / 'download.part'
    request = urllib.request.Request(release['url'], headers={'User-Agent':'NAIMangaMaker/'+VERSION})
    count = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response, partial.open('wb') as output:
            while chunk := response.read(256*1024):
                count += len(chunk)
                if count > release['size'] or count > MAX_BYTES:
                    raise ValueError('예상보다 큰 업데이트 파일입니다.')
                output.write(chunk)
                progress(int(count*100/release['size']))
        if count != release['size'] or sha256(partial) != release['sha256']:
            raise ValueError('업데이트 파일 검증에 실패했습니다. 기존 버전은 유지됩니다.')
        with partial.open('rb') as stream:
            if stream.read(2) != b'MZ':
                raise ValueError('Windows 실행 파일이 아닙니다.')
        target = folder / ASSET_NAME
        partial.replace(target)
        return target
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def launch_installer(downloaded, digest):
    if not getattr(sys, 'frozen', False):
        raise ValueError('자동 교체는 배포 EXE에서 사용할 수 있습니다.')
    downloaded = Path(downloaded).resolve()
    if sha256(downloaded) != digest:
        raise ValueError('다운로드 후 파일이 변경되었습니다.')
    helper = downloaded.parent / 'update-helper.exe'
    shutil.copy2(sys.executable, helper)
    plan = downloaded.parent / 'install.json'
    plan.write_text(json.dumps(dict(target=str(Path(sys.executable).resolve()),
        downloaded=str(downloaded), sha256=digest, parent_pid=os.getpid()), ensure_ascii=False), encoding='utf-8')
    subprocess.Popen([str(helper), '--apply-update', str(plan)], cwd=str(helper.parent),
        env={**os.environ,'PYINSTALLER_RESET_ENVIRONMENT':'1'},
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def replace_executable(target, downloaded, digest):
    """Atomic replacement with rollback. Only sibling EXE files are modified."""
    target, downloaded = Path(target).resolve(), Path(downloaded).resolve()
    if target.suffix.lower() != '.exe' or not target.is_file() or target == downloaded:
        raise ValueError('교체할 EXE 경로를 확인해 주세요.')
    if sha256(downloaded) != digest:
        raise ValueError('업데이트 파일 해시가 다릅니다.')
    staged = target.with_name(target.name + '.update-new')
    backup = target.with_name(target.stem + '.previous.exe')
    shutil.copy2(downloaded, staged)
    if sha256(staged) != digest:
        staged.unlink(missing_ok=True)
        raise ValueError('복사한 파일의 해시가 다릅니다.')
    moved = False
    try:
        # The onefile parent may hold the original EXE briefly after the UI exits.
        for attempt in range(100):
            try:
                os.replace(target, backup)
                moved = True
                break
            except PermissionError:
                if attempt == 99:
                    raise
                time.sleep(.2)
        os.replace(staged, target)
    except Exception:
        if moved and not target.exists():
            os.replace(backup, target)
        staged.unlink(missing_ok=True)
        raise
    return target


def apply_update(plan_path):
    import ctypes
    from ctypes import wintypes
    plan_path = Path(plan_path).resolve()
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    downloaded = Path(plan['downloaded']).resolve()
    if downloaded.parent != plan_path.parent or downloaded.name != ASSET_NAME:
        raise ValueError('업데이트 파일 위치가 올바르지 않습니다.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000,False,plan['parent_pid'])
    if handle:
        try:
            if kernel.WaitForSingleObject(handle,60000) != 0:
                raise ValueError('기존 리모콘이 종료되지 않았습니다. 다시 시도해 주세요.')
        finally:
            kernel.CloseHandle(handle)
    target = replace_executable(plan['target'], downloaded, plan['sha256'])
    subprocess.Popen([str(target)], cwd=str(target.parent),
        env={**os.environ,'PYINSTALLER_RESET_ENVIRONMENT':'1'})
    # Helper and verified download stay in updates for troubleshooting; no data-folder cleanup.
    plan_path.write_text(json.dumps({**plan,'completed':True},ensure_ascii=False),encoding='utf-8')
