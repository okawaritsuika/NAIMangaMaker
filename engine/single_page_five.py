import copy
from PIL import Image
from .forced_infill_delivery import payload, read, sha
from .reference_metadata import read_alpha
from .studio_render import _contains

def verify_rendered(image_path, settings, request_path):
    expected = payload(settings)
    assert read(request_path) == expected
    with Image.open(image_path) as image:
        assert image.format == 'PNG' and image.size == (settings['width'], settings['height'])
    _, actual, carrier = read_alpha(image_path)
    normalized = copy.deepcopy(actual)
    rounding = []
    # NAI stores the requested center coordinates at float32 precision.
    # Tolerate only that measured coordinate rounding; all other fields stay exact.
    for field in ('v4_prompt', 'v4_negative_prompt'):
        wanted = expected['parameters'][field]['caption']['char_captions']
        observed = normalized[field]['caption']['char_captions']
        assert len(wanted) == len(observed)
        for index, (left, right) in enumerate(zip(wanted, observed)):
            assert len(left['centers']) == len(right['centers'])
            for lcenter, rcenter in zip(left['centers'], right['centers']):
                for axis in ('x', 'y'):
                    a, b = lcenter[axis], rcenter[axis]
                    if a != b:
                        assert abs(a-b) < 1e-6
                        rounding.append(dict(field=field, index=index, axis=axis, requested=a, returned=b))
                        rcenter[axis] = a
    _contains(expected['input'], actual['prompt'], 'prompt')
    for key, value in expected['parameters'].items():
        field = 'uc' if key == 'negative_prompt' else key
        _contains(value, normalized[field], field)
    return dict(image_sha256=sha(image_path), settings_verified=True, alpha_metadata=carrier,
                coordinate_rounding=rounding, semantic_quality_verified=False)
