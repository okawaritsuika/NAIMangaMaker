

def _contains(expected, actual, field):
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            raise ValueError(f'Rendered metadata differs at {field}')
        for key, value in expected.items():
            if key not in actual:
                raise ValueError(f'Rendered metadata is missing {field}.{key}')
            _contains(value, actual[key], field + '.' + key)
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            raise ValueError(f'Rendered metadata differs at {field}')
        for index, (left, right) in enumerate(zip(expected, actual)):
            _contains(left, right, f'{field}[{index}]')
    elif expected != actual:
        raise ValueError(f'Rendered metadata differs at {field}')
