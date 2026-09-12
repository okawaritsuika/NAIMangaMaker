import json
import hashlib
from pathlib import Path
from .render_novelai import image_payload

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def payload(settings):
    result = image_payload(settings)
    # The existing backend forces 14. This experiment explicitly uses the
    # attached image's recorded setting, without changing the shared backend.
    result['parameters']['steps'] = settings['steps']
    return result
