"""Read NovelAI stealth metadata without modifying an image or repairing JSON.

This is a structural inspection utility. CLI output omits prompt contents.
The payload layout matches NAIA's image_info.py alpha-channel reader.
"""
import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image


def read_alpha(path):
    with Image.open(path) as image:
        if image.mode != 'RGBA':
            raise ValueError('Original image has no RGBA alpha carrier')
        alpha = np.asarray(image)[:, :, 3]
    packed = np.packbits(alpha.T.reshape(-1) & 1).tobytes()
    if packed[:15] != b'stealth_pngcomp':
        raise ValueError('Compressed alpha signature absent')
    bit_length = int.from_bytes(packed[15:19], 'big')
    if bit_length % 8 or bit_length <= 0 or bit_length > (len(packed)-19)*8:
        raise ValueError('Invalid embedded payload length')
    compressed = packed[19:19+bit_length//8]
    with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
        raw = stream.read(16*1024*1024+1)
    if len(raw) > 16*1024*1024:
        raise ValueError('Metadata exceeds inspection limit')
    metadata = json.loads(raw)
    settings = metadata.get('Comment')
    if isinstance(settings, str):
        settings = json.loads(settings)
    if not isinstance(settings, dict):
        raise ValueError('NovelAI Comment settings missing')
    return metadata, settings, dict(
        method='alpha_lsb_x_then_y_gzip',
        signature='stealth_pngcomp', compressed_bytes=len(compressed),
        payload_bytes=len(raw), payload_sha256=hashlib.sha256(raw).hexdigest())


def structure(path):
    metadata, settings, carrier = read_alpha(path)
    with Image.open(path) as image:
        image_info = dict(format=image.format, mode=image.mode, size=list(image.size),
                          container_metadata_keys=sorted(image.info))
    v4 = settings.get('v4_prompt') or {}
    caption = v4.get('caption') or {}
    chars = caption.get('char_captions') or []
    return dict(path=str(Path(path).resolve()), file_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                image=image_info, carrier=carrier, metadata_keys=list(metadata),
                setting_keys=list(settings),
                generation={key:settings.get(key) for key in ('width','height','steps','scale','sampler','seed')},
                flags={key:v4.get(key) for key in ('use_coords','use_order','legacy_uc')},
                character_captions=len(chars),
                caption_centers=[char.get('centers',[]) for char in chars],
                prompt_contents_exported=False)


if __name__ == '__main__':
    cli=argparse.ArgumentParser()
    cli.add_argument('image',type=Path)
    args=cli.parse_args()
    print(json.dumps(structure(args.image),ensure_ascii=False,indent=2))
