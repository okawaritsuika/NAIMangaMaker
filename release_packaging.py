"""Build from the reviewed resource allowlist, never whole runtime directories."""
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent


def resource_args(root=ROOT):
    manifest = json.loads((root / 'release_resources.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or not isinstance(manifest.get('files'), list):
        raise ValueError('Invalid release resource manifest')
    seen, args = set(), []
    for name in manifest['files']:
        path = PurePosixPath(name)
        if (name in seen or path.is_absolute() or '..' in path.parts or
                path.parts[0] not in ('assets', 'web', 'engine', 'licenses') or
                any(part in ('__pycache__', 'stories', '.history') for part in path.parts) or
                path.name in ('keys.json', 'workspace.json', 'preferences.json', 'characters.json', '.env')):
            raise ValueError('Unsafe release resource: ' + name)
        source = (root / name).resolve()
        if not source.is_relative_to(root.resolve()) or not source.is_file():
            raise ValueError('Missing or external release resource: ' + name)
        seen.add(name)
        args += ['--add-data', str(source) + ';' + str(path.parent)]
    return args


if __name__ == '__main__':
    import tkinter
    # Fail before packaging if the remote controller's GUI runtime is unavailable.
    tkinter.Tcl()
    from PyInstaller.__main__ import run
    run(['--noconfirm', '--clean', '--onefile', '--windowed', '--name', 'NAIMangaMaker',
         '--icon', 'assets/app.ico', '--hidden-import', 'server', '--collect-submodules', 'engine',
         *resource_args(), 'remote.py'])
