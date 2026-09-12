"""Experimental framing-aware boxes for an existing, ordered 3--5 entry page.

Pixel aspect targets are hypotheses to test on rendered pages, not measured
guarantees. The exporter derives character center hints from these boxes; its
image request does not enforce panel rectangles. No source, camera, dialogue,
caption, pagination, exporter default, or image request is changed here.
"""

import math
from copy import deepcopy


def _partitions(count):
    if not count:
        yield ()
    for size in range(1, min(3, count) + 1):
        for tail in _partitions(count - size):
            yield (size, *tail)


def _target(entry, index):
    if not isinstance(entry, dict) or not isinstance(entry.get('camera'), dict):
        raise ValueError('Each entry needs its original camera mapping')
    camera = entry['camera']
    actor = entry.get('actor')
    subjects = camera.get('subjects', []) or []
    if not isinstance(actor, str) or not actor or not isinstance(subjects, (list, tuple)) or any(
            not isinstance(subject, str) or not subject for subject in subjects):
        raise ValueError('Actor and camera subjects must be actor IDs')
    framing = camera.get('framing')
    if not isinstance(framing, str):
        raise ValueError('Camera framing must be a string')
    label = framing.strip().casefold()
    people = list(dict.fromkeys([*subjects, actor]))
    ratio, weight, reason = {
        'full body': (.7, 1.0, 'solo_full_body'),
        'portrait': (.85, 1.0, 'solo_portrait'),
        'upper body': (1.0, 1.0, 'solo_upper_body'),
        'cowboy shot': (1.0, 1.0, 'solo_cowboy_shot'),
        'close-up': (1.0, .25, 'weak_closeup_preference'),
        'closeup': (1.0, .25, 'weak_closeup_preference'),
        'close up': (1.0, .25, 'weak_closeup_preference'),
    }.get(label, (1.0, .25, 'weak_unknown_framing_preference'))
    if len(people) > 1:
        ratio = 1.4 + .2 * min(len(people) - 2, 3)
        weight, reason = 1.0, 'multiple_subjects_wider'
    return dict(index=index, entry_id=deepcopy(entry.get('entry_id', index)),
                framing=framing, subjects=people, target_pixel_aspect=ratio,
                error_weight=weight, reason=reason)


def _candidate(counts, targets, width, height, margin, gap, min_width, min_height, closeup_aspect):
    rows, cursor = [], 0
    for count in counts:
        rows.append(list(range(cursor, cursor + count)))
        cursor += count
    usable_height = height - 2 * margin - gap * (len(rows) - 1)
    available_widths = [width - 2 * margin - gap * (len(row) - 1) for row in rows]
    # Give closeup width a small search range. A smaller score weight alone
    # would cancel out when every actual/target ratio shares one row scale.
    layout_aspects = [closeup_aspect if target['reason'] == 'weak_closeup_preference'
                      else target['target_pixel_aspect'] for target in targets]
    result = dict(row_counts=list(counts), rows=rows, closeup_layout_aspect=closeup_aspect,
                  layout_target_pixel_aspects=layout_aspects, valid=False)
    if usable_height <= 0 or min(available_widths) <= 0:
        return dict(result, rejection='margins_or_gaps_exhaust_canvas')
    ideal_heights = [available / sum(layout_aspects[index] for index in row)
                     for row, available in zip(rows, available_widths)]
    scale = usable_height / sum(ideal_heights)
    row_heights = [ideal * scale for ideal in ideal_heights]
    boxes, panels = {}, []
    top = margin
    for row_number, (row, available, row_height) in enumerate(zip(rows, available_widths, row_heights)):
        if row_height + 1e-9 < min_height:
            return dict(result, rejection='row_below_minimum_pixel_height')
        row_total = sum(layout_aspects[index] for index in row)
        left = margin
        for column, index in enumerate(row):
            panel_width = available * layout_aspects[index] / row_total
            if panel_width + 1e-9 < min_width:
                return dict(result, rejection='panel_below_minimum_pixel_width')
            right, bottom = left + panel_width, top + row_height
            boxes[index] = [left / width, top / height, right / width, bottom / height]
            actual = panel_width / row_height
            log_error = math.log(actual / targets[index]['target_pixel_aspect'])
            panels.append(dict(index=index, row=row_number, column=column,
                               pixel_box=[left, top, right, bottom],
                               pixel_width=panel_width, pixel_height=row_height,
                               actual_pixel_aspect=actual, log_aspect_error=log_error))
            left = right + gap
        top += row_height + gap
    error = sum(targets[panel['index']]['error_weight'] * panel['log_aspect_error'] ** 2
                for panel in panels) / sum(target['error_weight'] for target in targets)
    return dict(result, valid=True, boxes=boxes, panels=panels, aspect_error=error,
                ideal_row_heights_px=ideal_heights, row_height_scale=scale,
                row_heights_px=row_heights)


def _layout_text(counts):
    numbers = {1: 'one', 2: 'two', 3: 'three'}
    labels = []
    for index, count in enumerate(counts):
        position = 'only' if len(counts) == 1 else 'top' if index == 0 else 'bottom' if index == len(counts) - 1 else 'middle'
        labels.append(f'{numbers[count]} ' + ('panel' if count == 1 else 'panels') + f' in {position} row')
    return ', '.join(labels)


def rectangles(entries, width=832, height=1216, *, margin_px=24, gap_px=24,
               min_width_px=144, min_height_px=160, fallback=None, area_balance_strength=0):
    """Return (index->normalized box, layout text, audit) without changing entries.

    Enumerate every consecutive row partition with one to three entries per
    row. Widths follow target aspects; ideal row heights then scale uniformly
    to the available page height. Minimize weighted squared log pixel-aspect
    error. Closeups can use .85, 1.0, or 1.2 for width allocation while retaining
    a weak scoring preference for 1.0. Each candidate uses one common closeup
    target, keeping the search bounded to at most 39 candidates for five cuts.
    Scores tie by fewer rows, lexicographic row counts, then closeup target.

    Only 3--5 entry pages use this experiment. Unsupported page sizes or no
    valid candidate call the existing exporter's rectangles, unless a caller
    injects its identical-contract fallback. Fallback geometry is unchanged
    and is not claimed to satisfy this experiment's minimum dimensions.
    """
    if not isinstance(entries, (list, tuple)):
        raise ValueError('entries must be an ordered list')
    if (isinstance(area_balance_strength, bool) or not isinstance(area_balance_strength, (int, float))
            or not math.isfinite(area_balance_strength) or area_balance_strength < 0):
        raise ValueError('area_balance_strength must be finite and nonnegative')
    values = dict(width=width, height=height, margin_px=margin_px, gap_px=gap_px,
                  min_width_px=min_width_px, min_height_px=min_height_px)
    for name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{name} must be finite numeric pixels')
        if value < 0 or name not in ('margin_px', 'gap_px') and value == 0:
            raise ValueError(f'{name} has an invalid pixel size')
    targets = [_target(entry, index) for index, entry in enumerate(entries)]
    audit = dict(method='framing_pixel_aspect_search', heuristic_version=1,
                 heuristic_quality_verified=False, source_entry_ids=[target['entry_id'] for target in targets],
                 canvas_pixels=[width, height], margin_px=margin_px, gap_px=gap_px,
                 min_width_px=min_width_px, min_height_px=min_height_px, targets=targets,
                 reading_order='left_to_right_then_top_to_bottom',
                 image_model_receives='layout text and character center hints derived from boxes, not enforced rectangles',
                 rectangle_constraints_enforced_by_image_model=False,
                 objective='weighted mean squared log(actual pixel aspect / target pixel aspect)',
                 tie_break='score rounded to 12 decimals, fewer rows, lexicographic row counts, closeup target nearest 1 then smaller')
    candidates = []
    closeup_aspects = (1.0, .85, 1.2) if any(target['reason'] == 'weak_closeup_preference' for target in targets) else (1.0,)
    audit['closeup_width_candidates'] = list(closeup_aspects)
    if 3 <= len(entries) <= 5:
        candidates = [_candidate(counts, targets, width, height, margin_px, gap_px,
                                 min_width_px, min_height_px, closeup_aspect)
                      for counts in _partitions(len(entries)) for closeup_aspect in closeup_aspects]
    valid = [candidate for candidate in candidates if candidate['valid']]
    if area_balance_strength:
        # This discourages a geometrically convenient oversized panel; it does
        # not infer narrative importance or impose a hard area limit.
        audit.update(heuristic_version=2, area_balance_strength=area_balance_strength,
                     area_ratio_threshold=1.5,
                     objective='aspect_error + strength * max(0, max_panel_area / mean_panel_area - 1.5)^2')
        for candidate in valid:
            areas = [panel['pixel_width'] * panel['pixel_height'] for panel in candidate['panels']]
            ratio = max(areas) / (sum(areas) / len(areas))
            penalty = area_balance_strength * max(0, ratio - 1.5) ** 2
            candidate.update(max_area_to_mean=ratio, panel_area_shares=[area / sum(areas) for area in areas],
                             area_penalty=penalty, combined_score=candidate['aspect_error'] + penalty)
    audit['candidates'] = [{key: deepcopy(value) for key, value in candidate.items()
                            if key not in ('boxes', 'panels')} for candidate in candidates]
    audit['candidate_count'] = len(candidates)
    audit['valid_candidate_count'] = len(valid)
    if not valid:
        if fallback is None:
            from .export_suite_webp import rectangles as fallback
        boxes, layout = fallback(deepcopy(entries))
        audit.update(fallback_used=True,
                     fallback_reason='unsupported_entry_count' if not 3 <= len(entries) <= 5 else 'no_valid_candidate',
                     selected=None)
        return deepcopy(boxes), layout, deepcopy(audit)
    selected = min(valid, key=lambda candidate: (round(candidate.get('combined_score', candidate['aspect_error']), 12),
                                                len(candidate['rows']), tuple(candidate['row_counts']),
                                                abs(candidate['closeup_layout_aspect'] - 1),
                                                candidate['closeup_layout_aspect']))
    audit.update(fallback_used=False, fallback_reason=None,
                 selected={key: deepcopy(value) for key, value in selected.items() if key != 'boxes'})
    return deepcopy(selected['boxes']), _layout_text(selected['row_counts']), deepcopy(audit)
