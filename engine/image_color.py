"""Explicit monochrome guidance; preserve the API original separately."""
from PIL import Image, ImageOps

POSITIVE = '3::monochrome, greyscale::'
NEGATIVE = 'color, full color, spot color, partially colored'


def monochrome(settings):
    return settings.get('color_mode') == 'monochrome'


def guide(settings):
    if not monochrome(settings): return
    def positive(text):
        return text if text.startswith(POSITIVE) else POSITIVE + ', ' + text
    def negative(text):
        return text if NEGATIVE in text else ', '.join(filter(None, [text, NEGATIVE]))
    settings['prompt'] = settings['v4_prompt']['caption']['base_caption'] = positive(settings['prompt'])
    settings['uc'] = settings['v4_negative_prompt']['caption']['base_caption'] = negative(settings['uc'])
    for field, transform in [('v4_prompt',positive),('v4_negative_prompt',negative)]:
        for row in settings[field]['caption']['char_captions']:
            row['char_caption'] = transform(row['char_caption'])


def grayscale(image):
    rgba=image.convert('RGBA')
    result=ImageOps.grayscale(rgba).convert('RGBA')
    result.putalpha(rgba.getchannel('A'))
    return result


def finish(folder, settings, request_path):
    """Verify the API response before creating/verifying its monochrome derivative."""
    from .single_page_five import verify_rendered
    from .forced_infill_delivery import sha
    import io
    original, target = folder/'api.png', folder/'page.png'
    verified=verify_rendered(original,settings,request_path)
    with Image.open(original) as image:
        buffer=io.BytesIO();grayscale(image).save(buffer,format='PNG')
    expected=buffer.getvalue()
    if target.exists() and target.read_bytes()!=expected:
        raise ValueError('저장된 흑백 그림이 원본으로부터 만든 결과와 달라요.')
    if not target.exists(): target.write_bytes(expected)
    return dict(verified,image_sha256=sha(target),api_image_sha256=sha(original),
                original_api_png=False,verification_kind='grayscale_from_verified_api')
