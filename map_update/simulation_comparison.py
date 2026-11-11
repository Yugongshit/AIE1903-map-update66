"""Engine-independent, registered before/after views of executed map patches.

Both sides use one world-coordinate viewport. A displacement vector is reported
only when every corresponding vertex has the same displacement within 1e-7 m;
an average displacement is never substituted for a deformation. Optional patch
attributes are checked against the prepared overlay before they are displayed.
"""
from __future__ import annotations

from copy import deepcopy
from html import escape
from math import ceil, dist, floor, isfinite, log10


SCHEMA = 'simulation-map-comparison-1.0'
TOLERANCE_M = 1e-7
ATTRIBUTE_FIELDS = ('type', 'entry_lanes', 'exit_lanes', 'left_neighbor',
                    'right_neighbor', 'speed_limit_kmh', 'speed_limit_mph')
TOPOLOGY_FIELDS = {'entry_lanes', 'exit_lanes', 'left_neighbor', 'right_neighbor'}
COLORS = {'previous': '#f9c65b', 'removed': '#ff777c', 'updated': '#32e8c7'}


def _point(value):
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        raise ValueError('Comparison points require XY coordinates')
    if any(type(v) not in (int, float) or not isfinite(v) for v in value[:2]):
        raise ValueError('Comparison coordinates must be finite numbers')
    return [float(value[0]), float(value[1])]


def _paths(paths, allowed_roles):
    result = []
    for path in paths:
        if not isinstance(path, dict) or path.get('role') not in allowed_roles:
            raise ValueError('Unknown comparison path role')
        points = [_point(point) for point in path.get('points', [])]
        if len(points) < 2:
            raise ValueError('Comparison paths require at least two points')
        result.append({'role': path['role'], 'points': points,
                       'dashed': bool(path.get('dashed', False))})
    return result


def _nice_length(maximum):
    exponent = 10 ** floor(log10(maximum))
    return max(v * exponent for v in (.1, .2, .5, 1, 2, 5) if v * exponent <= maximum + 1e-12)


def _viewport(paths, size):
    if len(size) != 2 or any(type(v) not in (int, float) or not isfinite(v) or v <= 0 for v in size):
        raise ValueError('Comparison panel dimensions must be positive')
    points = [point for path in paths for point in path['points']]
    if points:
        low = [min(point[i] for point in points) for i in (0, 1)]
        high = [max(point[i] for point in points) for i in (0, 1)]
    else:
        low, high = [-5., -5.], [5., 5.]
    center = [(low[i] + high[i]) / 2 for i in (0, 1)]
    raw_width, raw_height = high[0]-low[0], high[1]-low[1]
    margin = max(.75, max(raw_width, raw_height) * .12)
    width, height = max(raw_width + 2*margin, 2.), max(raw_height + 2*margin, 2.)
    scale = min(size[0] / width, size[1] / height)
    width, height = size[0] / scale, size[1] / scale
    return {'bounds_xyxy': [center[0]-width/2, center[1]-height/2,
                            center[0]+width/2, center[1]+height/2],
            'center_xy': center, 'panel_size': list(size), 'pixels_per_metre': scale,
            'scale_bar_m': _nice_length(width*.23), 'same_transform_for_both_sides': True,
            'axes': {'horizontal': '+X right', 'vertical': '+Y up', 'units': 'm'}}


def _midpoint(points):
    remaining = sum(dist(a, b) for a, b in zip(points, points[1:])) / 2
    for a, b in zip(points, points[1:]):
        length = dist(a, b)
        if length > 0 and length >= remaining:
            return [a[i] + remaining / length * (b[i]-a[i]) for i in (0, 1)]
        remaining -= length
    return list(points[-1])


def _translation(before, after):
    result = {'kind': 'unpaired', 'dx_m': None, 'dy_m': None, 'distance_m': None,
              'arrow': None, 'tolerance_m': TOLERANCE_M,
              'method': 'all_corresponding_vertex_displacements; no averaging'}
    if not before or not after or len(before) != len(after):
        return result
    if any(len(a['points']) != len(b['points']) for a, b in zip(before, after)):
        return result
    displacements = [[q[i]-p[i] for i in (0, 1)]
                     for a, b in zip(before, after) for p, q in zip(a['points'], b['points'])]
    vector = displacements[0]
    if not all(dist(vector, value) <= TOLERANCE_M for value in displacements):
        result['kind'] = 'deformation'
        return result
    distance = dist([0, 0], vector)
    if distance <= TOLERANCE_M:
        result.update(kind='unchanged', dx_m=0., dy_m=0., distance_m=0.)
    else:
        start = _midpoint(before[0]['points'])
        result.update(kind='uniform_translation', dx_m=vector[0], dy_m=vector[1],
                      distance_m=distance,
                      arrow={'start': start, 'end': [start[i]+vector[i] for i in (0, 1)]})
    return result


def _feature_paths(feature, offset):
    if feature is None:
        return []
    result = []
    for key in ('polyline', 'polygon'):
        if key not in feature:
            continue
        points = [_point(point) for point in feature[key]]
        if key == 'polygon' and points and points[0] != points[-1]:
            points.append(list(points[0]))
        result.append([[point[i]+offset[i] for i in (0, 1)] for point in points])
    return result


def _same_paths(prepared, source):
    return (len(prepared) == len(source)
            and all(len(a['points']) == len(b)
                    and all(dist(p, q) <= TOLERANCE_M for p, q in zip(a['points'], b))
                    for a, b in zip(prepared, source)))


def _attributes(overlay, item, before, after, patch_log):
    if patch_log is None:
        changes = []
        if item.get('before_type') != item.get('after_type'):
            changes.append({'field': 'type', 'before': item.get('before_type'), 'after': item.get('after_type')})
        return changes, False
    if not isinstance(patch_log, list):
        raise ValueError('Comparison patch log must be a list')
    matched = [entry for entry in patch_log if isinstance(entry, dict) and entry.get('feature_id') == item['feature_id']]
    if len(matched) != 1 or matched[0].get('operation') != item['operation']:
        raise ValueError('Comparison patch attributes do not match the selected overlay')
    patch = matched[0]
    offset = _point(overlay.get('report', {}).get('source_to_runtime_offset_m', [0, 0]))
    for role, paths in (('before', before), ('after', after)):
        value = patch.get(role)
        if (value.get('type') if value else None) != item.get(role+'_type') or not _same_paths(paths, _feature_paths(value, offset)):
            raise ValueError('Comparison patch geometry differs from the prepared overlay')
    prior, current = patch.get('before') or {}, patch.get('after') or {}
    return [{'field': key, 'before': deepcopy(prior.get(key)), 'after': deepcopy(current.get(key))}
            for key in ATTRIBUTE_FIELDS if prior.get(key) != current.get(key)], True


def _brief(value):
    if value is None:
        return 'absent'
    text = str(value)
    return text.replace('ROAD_LINE_', '').replace('LANE_SURFACE_', '').replace('_', ' ').lower()


def _reference_ids(value):
    """Normalize explicit IDs only; unknown encodings have no inferred count."""
    if value is None:
        return set()
    if isinstance(value, str):
        return {value} if value else None
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return set(value)
    return None


def prepare_comparison(prepared_overlay, selected_index=0, *, patch_log=None,
                       context_paths=None, panel_size=(480, 330)):
    """Prepare one selected edit with a single registered XY transform.

``context_paths`` are already-centered ``[{points: [[x,y], ...]}]`` paths shared
by both views. They never enlarge the viewport and are explicitly unchanged
context. ``patch_log`` enables checked type/topology attribute summaries, because
the existing overlay deliberately carries only geometry and feature types.
"""
    if not isinstance(prepared_overlay, dict) or not isinstance(prepared_overlay.get('items'), list):
        raise ValueError('Expected a prepared update overlay')
    items = prepared_overlay['items']
    if type(selected_index) is not int or selected_index < 0 or (items and selected_index >= len(items)) or (not items and selected_index != 0):
        raise ValueError('Comparison selection is outside the changed-feature list')
    context = []
    for path in context_paths or []:
        points = [_point(point) for point in path.get('points', [])]
        if len(points) < 2:
            raise ValueError('Context paths require two or more points')
        context.append({'role': 'unchanged_context', 'points': points, 'dashed': False})
    synthetic = bool(prepared_overlay.get('report', {}).get('synthetic'))
    result = {'schema': SCHEMA, 'empty': not items, 'selected_index': selected_index,
              'changed_features': len(items), 'feature_id': None, 'label': None, 'operation': None,
              'synthetic': synthetic, 'context_paths': context,
              'coordinate_frame': prepared_overlay.get('report', {}).get('coordinate_frame', 'metadrive_centered_m'),
              'affects_physics_or_planning': False, 'source': 'executed_map_patch_log',
              'state_events_presented_as_map_edits': False}
    if not items:
        if patch_log is not None and patch_log != []:
            raise ValueError('An empty comparison cannot omit supplied map patches')
        absent = {'present': False, 'paths': [], 'type': None, 'missing_label': 'NO MAP CHANGES APPLIED'}
        result.update(before=deepcopy(absent), after=deepcopy(absent),
                      viewport=_viewport([], panel_size), translation=_translation([], []),
                      attribute_changes=[], topology_available=patch_log is not None,
                      summary_lines=['NO MAP CHANGES APPLIED', 'State observations are separate from structural map edits.'])
        return result
    item = items[selected_index]
    all_paths = _paths(item.get('paths', []), {'previous', 'removed', 'updated'})
    before = [path for path in all_paths if path['role'] != 'updated']
    after = [path for path in all_paths if path['role'] == 'updated']
    operation = item.get('operation')
    expected = {'add': (False, True), 'replace': (True, True), 'delete': (True, False)}
    if operation not in expected or (bool(before), bool(after)) != expected[operation]:
        raise ValueError('Comparison geometry does not match the applied operation')
    attributes, topology_available = _attributes(prepared_overlay, item, before, after, patch_log)
    translation = _translation(before, after)
    kind = translation['kind']
    summary = []
    if operation == 'add':
        summary.append('ADDED: the object is absent in the original map.')
    elif operation == 'delete':
        summary.append('REMOVED: the object is absent in the updated map.')
    elif kind == 'uniform_translation':
        summary.append(f"Uniform translation: {translation['distance_m']:.3f} m")
        summary.append(f"Delta X {translation['dx_m']:+.3f} m; delta Y {translation['dy_m']:+.3f} m")
    elif kind == 'unchanged':
        summary.append('Geometry unchanged; compare feature attributes below.')
    elif kind == 'deformation':
        summary.append('Non-uniform geometry change; no single displacement vector.')
    else:
        summary.append('Geometry representations differ; no paired-vertex displacement reported.')
    for change in attributes:
        name, old, new = change['field'], change['before'], change['after']
        if name in TOPOLOGY_FIELDS:
            old_ids, new_ids = _reference_ids(old), _reference_ids(new)
            if old_ids is None or new_ids is None:
                summary.append(f"{name.replace('_', ' ').title()}: changed; reference encoding unavailable for counts")
            else:
                summary.append(f"{name.replace('_', ' ').title()}: {len(old_ids)} -> {len(new_ids)} (+{len(new_ids-old_ids)} / -{len(old_ids-new_ids)})")
        else:
            summary.append(f"{name.replace('_', ' ').title()}: {_brief(old)} -> {_brief(new)}")
    if before and after and item.get('before_type') == item.get('after_type'):
        summary.append('Feature type unchanged: ' + _brief(item.get('before_type')))
    if not topology_available:
        summary.append('Topology attributes were not supplied to this view.')
    result.update(feature_id=item.get('feature_id'), label=item.get('label'), operation=operation,
                  synthetic=bool(item.get('synthetic', synthetic)),
                  before={'present': bool(before), 'paths': before, 'type': item.get('before_type'),
                          'missing_label': 'NOT PRESENT BEFORE' if not before else None},
                  after={'present': bool(after), 'paths': after, 'type': item.get('after_type'),
                         'missing_label': 'REMOVED FROM UPDATED MAP' if not after else None},
                  viewport=_viewport(before+after, panel_size), translation=translation,
                  attribute_changes=attributes, topology_available=topology_available,
                  summary_lines=summary)
    return result


def project_point(prepared, point):
    """Map one world XY point to panel-local pixels, using the shared transform."""
    x, y = _point(point)
    viewport = prepared['viewport']
    left, _, _, top = viewport['bounds_xyxy']
    scale = viewport['pixels_per_metre']
    return [(x-left)*scale, (top-y)*scale]


def render_comparison_svg(prepared, *, width=1440, height=760):
    """Render BEFORE / AFTER / REGISTERED CHANGE to a standalone SVG string."""
    if type(width) not in (int, float) or type(height) not in (int, float) or not isfinite(width) or not isfinite(height) or width < 960 or height < 620:
        raise ValueError('Comparison SVG requires at least 960 x 620 pixels')
    if prepared.get('schema') != SCHEMA:
        prepared = prepare_comparison(prepared)
    model = deepcopy(prepared)
    margin, gap = 28., 18.
    card_width = (width - 2*margin - 2*gap) / 3
    card_top, card_height = 142., height-318.
    plot_top, inset = card_top+63., 18.
    plot_size = (card_width-2*inset, card_height-100.)
    # Refit the UNION once for all three panels when the SVG size changes.
    model['viewport'] = _viewport(model['before']['paths'] + model['after']['paths'], plot_size)
    feature_id = model.get('feature_id') or 'No structural edits'
    title = 'MAP UPDATE  /  LOCAL COMPARISON'
    badge = ('NO STRUCTURAL EDITS' if model['empty'] else
             'CONTROLLED DEMO' if model['synthetic'] else 'APPLIED MAP PATCH')
    subtitle = ('No structural map edits were exported; no differences are drawn.' if model['empty'] else
                'Controlled demonstration; not a detected change.' if model['synthetic'] else
                'Actual exported patch geometry. No inferred edits are drawn.')
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:g}" height="{height:g}" viewBox="0 0 {width:g} {height:g}" role="img" aria-labelledby="title desc">',
           f'<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle)} Before and after use identical scale and coordinates.</desc>',
           '<defs><marker id="arrowhead" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0 0L8 4L0 8Z" fill="#ffffff"/></marker></defs>',
           f'<rect width="{width:g}" height="{height:g}" fill="#0a111c"/>',
           '<g font-family="Inter,Segoe UI,Arial,sans-serif">',
           f'<text x="{margin:g}" y="41" fill="#92a7be" font-size="12" font-weight="700" letter-spacing="2">{title}</text>',
           f'<text x="{margin:g}" y="79" fill="#f4f8fc" font-size="26" font-weight="700">{escape((model.get("label") or "No changes") + "  /  " + (model.get("operation") or "unchanged").upper())}</text>',
           f'<text x="{margin:g}" y="104" fill="#9fb3c8" font-size="13">{escape(feature_id)}</text>',
           f'<rect x="{width-236:g}" y="31" width="208" height="32" rx="8" fill="{"#4a3413" if model["synthetic"] else "#113b38"}"/>',
           f'<text x="{width-132:g}" y="52" text-anchor="middle" fill="{"#ffd575" if model["synthetic"] else "#74f4dc"}" font-size="12" font-weight="700">{badge}</text>']
    bounds = model['viewport']['bounds_xyxy']
    tick = _nice_length((bounds[2]-bounds[0])/4)
    common = model['context_paths']
    for index, (name, color, paths, missing) in enumerate([
        ('BEFORE', COLORS['previous'], model['before']['paths'], model['before']['missing_label']),
        ('AFTER', COLORS['updated'], model['after']['paths'], model['after']['missing_label']),
        ('REGISTERED CHANGE', '#d5e0ec', model['before']['paths']+model['after']['paths'],
         'NO MAP CHANGES APPLIED' if model['empty'] else None),
    ]):
        x = margin + index*(card_width+gap)
        px = x+inset
        svg += [f'<rect x="{x:g}" y="{card_top:g}" width="{card_width:g}" height="{card_height:g}" rx="14" fill="#111d2c" stroke="#26374c"/>',
                f'<text x="{px:g}" y="{card_top+29:g}" fill="{color}" font-size="14" font-weight="700" letter-spacing="1.3">{name}</text>',
                f'<text x="{px:g}" y="{card_top+47:g}" fill="#8399af" font-size="11">SAME VIEWPORT  /  XY METRES</text>',
                f'<defs><clipPath id="clip{index}"><rect x="{px:g}" y="{plot_top:g}" width="{plot_size[0]:g}" height="{plot_size[1]:g}"/></clipPath></defs>',
                f'<g clip-path="url(#clip{index})">']
        for axis in (0, 1):
            low, high = bounds[axis], bounds[axis+2]
            for step in range(int(ceil(low/tick)), int(floor(high/tick))+1):
                value = step*tick
                a = [value, bounds[1]] if axis == 0 else [bounds[0], value]
                b = [value, bounds[3]] if axis == 0 else [bounds[2], value]
                q, r = project_point(model, a), project_point(model, b)
                svg.append(f'<line x1="{px+q[0]:.3f}" y1="{plot_top+q[1]:.3f}" x2="{px+r[0]:.3f}" y2="{plot_top+r[1]:.3f}" stroke="#223349" stroke-width="1"/>')
        for path in common+paths:
            points = [project_point(model, p) for p in path['points']]
            data = ' '.join(f'{px+p[0]:.3f},{plot_top+p[1]:.3f}' for p in points)
            context = path['role'] == 'unchanged_context'
            stroke = '#4a596b' if context else COLORS[path['role']]
            dash = ' stroke-dasharray="8 5"' if path.get('dashed') else ''
            svg.append(f'<polyline points="{data}" fill="none" stroke="{stroke}" stroke-width="{2 if context else 4}" stroke-linejoin="round" stroke-linecap="round"{dash}/>')
        arrow = model['translation']['arrow']
        if index == 2 and arrow:
            a, b = project_point(model, arrow['start']), project_point(model, arrow['end'])
            svg += [f'<line x1="{px+a[0]:.3f}" y1="{plot_top+a[1]:.3f}" x2="{px+b[0]:.3f}" y2="{plot_top+b[1]:.3f}" stroke="#0a111c" stroke-width="7"/>',
                    f'<line x1="{px+a[0]:.3f}" y1="{plot_top+a[1]:.3f}" x2="{px+b[0]:.3f}" y2="{plot_top+b[1]:.3f}" stroke="#ffffff" stroke-width="2.5" marker-end="url(#arrowhead)"/>']
        if missing:
            svg.append(f'<text x="{x+card_width/2:g}" y="{plot_top+plot_size[1]/2:g}" text-anchor="middle" fill="#91a6bd" font-size="12" font-weight="700">{escape(missing)}</text>')
        svg.append('</g>')
        scale_m = model['viewport']['scale_bar_m']
        bar_px = scale_m*model['viewport']['pixels_per_metre']
        bottom = card_top+card_height-20
        svg += [f'<path d="M{px:g},{bottom-5:g}v5h{bar_px:.3f}v-5" fill="none" stroke="#c9d6e4" stroke-width="2"/>',
                f'<text x="{px+bar_px+8:.3f}" y="{bottom+3:g}" fill="#c9d6e4" font-size="11">{scale_m:g} m</text>',
                f'<text x="{x+card_width-inset:g}" y="{bottom+3:g}" text-anchor="end" fill="#91a6bd" font-size="11">+X →   +Y ↑</text>']
    footer = card_top+card_height+28
    summary = model['summary_lines']
    for index, line in enumerate(summary[:4]):
        svg.append(f'<text x="{margin:g}" y="{footer+index*22:g}" fill="{"#e7f0f9" if index == 0 else "#a6bad0"}" font-size="{15 if index == 0 else 12}" font-weight="{700 if index == 0 else 400}">{escape(line)}</text>')
    if len(summary) > 4:
        svg.append(f'<text x="{width-margin:g}" y="{footer+66:g}" text-anchor="end" fill="#91a6bd" font-size="11">+ {len(summary)-4} additional attribute details in the comparison model</text>')
    svg += [f'<text x="{margin:g}" y="{height-24:g}" fill="#8399af" font-size="11">{escape(subtitle)}  |  Amber: prior  ·  Cyan: updated  ·  Red: removed' + ('  ·  Grey: unchanged context' if common else '') + '</text>',
            '</g></svg>']
    return '\n'.join(svg)
