"""Replay delivered, labelled map changes on an isolated reference-map copy.

This module is deliberately separate from observation-based prediction. Its
inputs contain the answer (the supplied M8 prior/current object states), so its
outputs are a trusted-reference replay, never detector success or accuracy.
All geometry remains in the supplied local City XY metric frame, not WGS84.
"""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json

from .geometry import validate_geometry


LAYER_KEYS = {'laneSegments': 'lane_segments', 'pedCrossings': 'pedestrian_crossings'}


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                             allow_nan=False).encode()).hexdigest()


def _identity(value):
    return isinstance(value, str) and bool(value.strip())


def _closed(points):
    result = deepcopy(points)
    if result and result[0] != result[-1]:
        result.append(deepcopy(result[0]))
    return result


def _normalize_state(layer, state):
    """Convert the supplied crossing edge representation to the M2 map schema."""
    if state is None:
        return None
    if not isinstance(state, dict) or not _identity(state.get('id')):
        raise ValueError('A map object requires a nonempty string identity')
    result = deepcopy(state)
    if layer == 'pedCrossings':
        points = result.get('points')
        if points is None:
            edges = result.pop('edges', None)
            if not isinstance(edges, list):
                raise ValueError('A crossing requires supplied points or edge vertices')
            points = [[point['x'], point['y']] for point in edges]
        elif 'edges' in result:
            raise ValueError('Ambiguous crossing geometry representation')
        result['points'] = _closed(points)
    _object_geometry(layer, result)
    return result


def _object_geometry(layer, state):
    if state is None:
        return None
    if layer == 'laneSegments':
        sides = []
        for side in ('left', 'right'):
            line = [[point['x'], point['y']] for point in state[side + '_lane_boundary']]
            validate_geometry({'type': 'LineString', 'coordinates': line}, line_only=True)
            sides.append(line)
        geometry = {'type': 'Polygon', 'coordinates': [_closed(sides[0] + sides[1][::-1])]}
    elif layer == 'pedCrossings':
        geometry = {'type': 'Polygon', 'coordinates': [_closed(state['points'])]}
    else:
        raise ValueError('Unsupported reference geometry layer: ' + str(layer))
    validate_geometry(geometry)
    return geometry


def _indices(map_data):
    result = {}
    for layer, key in LAYER_KEYS.items():
        rows = map_data.get(key)
        if not isinstance(rows, list):
            raise ValueError('Reference map is missing layer: ' + key)
        found = {}
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or not _identity(row.get('id')) or row['id'] in found:
                raise ValueError('Missing or duplicate map object identity in ' + key)
            _normalize_state(layer, row)
            found[row['id']] = index
        result[layer] = found
    return result


def reference_map_context(map_data, provenance=None):
    """Convert a reference-copy map to the existing presentation-context shape."""
    _indices(map_data)
    elements, boundaries = [], {}
    for lane in map_data['lane_segments']:
        eid = lane['id']
        elements.append({'element_id': eid, 'layer': 'lane',
                         'geometry_city': _object_geometry('laneSegments', lane),
                         'source_ids': [eid],
                         'geometry_origin': 'display_fill_from_supplied_lane_boundaries',
                         'properties': {key: deepcopy(lane[key]) for key in
                                        ('lane_type', 'is_intersection', 'predecessors', 'successors')
                                        if key in lane}})
        for side in ('left', 'right'):
            points = [[p['x'], p['y']] for p in lane[side + '_lane_boundary']]
            ordered = tuple(map(tuple, points))
            key = min(ordered, ordered[::-1])
            if key not in boundaries:
                boundaries[key] = {'element_id': 'boundary:' + _digest(key)[:20],
                                   'layer': 'lane_boundary',
                                   'geometry_city': {'type': 'LineString', 'coordinates': points},
                                   'source_ids': [], 'map_aliases': [],
                                   'geometry_origin': 'supplied_boundary_vertices'}
            boundary = boundaries[key]
            if eid not in boundary['source_ids']:
                boundary['source_ids'].append(eid)
            boundary['map_aliases'].append({'map_element_id': eid, 'boundary_side': side,
                                            'marking_type': lane.get(side + '_lane_mark_type', 'UNKNOWN')})
    elements.extend(boundaries.values())
    for crossing in map_data['pedestrian_crossings']:
        normalized = _normalize_state('pedCrossings', crossing)
        elements.append({'element_id': crossing['id'], 'layer': 'ped_crossing',
                         'geometry_city': _object_geometry('pedCrossings', normalized),
                         'source_ids': [crossing['id']], 'geometry_origin': 'supplied_crossing_polygon'})
    points = []
    for element in elements:
        geometry = element['geometry_city']
        points.extend(geometry['coordinates'] if geometry['type'] == 'LineString' else geometry['coordinates'][0])
    bounds = [min(p[0] for p in points), min(p[1] for p in points),
              max(p[0] for p in points), max(p[1] for p in points)] if points else None
    return {'dataset_id': 'argoverse', 'scene_id': map_data['scene_id'],
            'map_id': map_data['scene_id'] + ':trusted_reference_replay',
            'map_version': 'reference-' + _digest(map_data)[:12], 'map_sha256': _digest(map_data),
            'coordinate_reference': {'frame': 'Argoverse supplied city XY',
                                     'scene_id': map_data['scene_id'], 'units': 'm'},
            'elements': elements, 'bounds_xyxy': bounds,
            'summary': {'elements': len(elements), 'layers': dict(Counter(e['layer'] for e in elements)),
                        'source_lanes': len(map_data['lane_segments']),
                        'source_crossings': len(map_data['pedestrian_crossings'])},
            'provenance': {'source': 'delivered_ground_truth', 'gt_current_map_used': True,
                           'prediction_input': False, 'source_base_map_mutated': False,
                           'supplied_provenance': deepcopy(provenance)}}


def rollback_reference_replay(updated_map, patch_log, prior_metadata, expected_updated_sha256=None):
    """Invert the executed object patches; reject changed targets or versions."""
    if expected_updated_sha256 is not None and _digest(updated_map) != expected_updated_sha256:
        raise ValueError('Updated reference-map version differs from the replay version')
    restored = deepcopy(updated_map)
    for patch in reversed(patch_log):
        key = LAYER_KEYS[patch['layer']]
        index = _indices(restored)[patch['layer']].get(patch['element_id'])
        current = restored[key][index] if index is not None else None
        if current != patch['after_state']:
            raise ValueError('Rollback target differs from the executed patch')
        if patch['operation'] == 'add':
            restored[key].pop(index)
        elif patch['operation'] == 'delete':
            if index is not None:
                raise ValueError('Deleted rollback target unexpectedly exists')
            restored[key].insert(patch['before_index'], deepcopy(patch['before_state']))
        elif patch['operation'] == 'replace':
            restored[key][index] = deepcopy(patch['before_state'])
        else:
            raise ValueError('Unsupported rollback operation')
    for key in list(restored):
        if key not in LAYER_KEYS.values():
            del restored[key]
    restored.update(deepcopy(prior_metadata))
    return restored


def build_reference_replay(prior, changed_rows, inventory, provenance=None):
    """Apply complete labelled changes without using or altering predictions.

    The caller must load these evaluation-only inputs after prediction freeze.
    Returns ``changes`` and a GeoJSON ``change_layer`` (one feature per changed
    object), ``updated_map`` (isolated, executed copy), exact ``rollback_map``,
    reversible ``patch_log``, ``updated_map_context``, provenance and counts.
    Unsupported inventory layers are reported; missing supported change records,
    mismatched prior states, duplicate identities and invalid geometry fail the
    entire transaction. Source inputs are never modified.
    """
    if not isinstance(prior, dict) or not _identity(prior.get('scene_id')):
        raise ValueError('A scene-identified prior map is required')
    _indices(prior)
    scene = prior['scene_id']
    inventory_lookup = {}
    for row in inventory:
        key = (row.get('layer'), row.get('element_id'))
        if row.get('scene_id') != scene or not _identity(key[1]) or key in inventory_lookup:
            raise ValueError('Inventory scene or object identity is invalid or duplicated')
        inventory_lookup[key] = row
    expected = {key for key, row in inventory_lookup.items()
                if row.get('official_changed') is True and row.get('state_comparison_supported') is True
                and key[0] in LAYER_KEYS}
    supported, unsupported, seen = [], [], set()
    for row in changed_rows:
        key = (row.get('layer'), row.get('element_id'))
        if key in seen or row.get('scene_id') != scene or key not in inventory_lookup:
            raise ValueError('Change row scene or object identity is invalid or duplicated')
        seen.add(key)
        label = inventory_lookup[key]
        for field in ('official_changed', 'state_comparison_supported', 'prior_present',
                      'current_present', 'state_delta_types', 'differences'):
            if row.get(field) != label.get(field):
                raise ValueError('Change row disagrees with inventory: ' + field)
        if row.get('official_changed') is not True:
            raise ValueError('A replay change must be explicitly labelled changed')
        if key[0] not in LAYER_KEYS or row.get('state_comparison_supported') is not True:
            unsupported.append({'element_id': key[1], 'layer': key[0],
                                'reason': 'UNSUPPORTED_REFERENCE_STATE_COMPARISON'})
            continue
        if not row.get('state_delta_types'):
            raise ValueError('A changed object requires explicit change types')
        before, after = row.get('prior_state'), row.get('current_state')
        if ((before is not None) != row.get('prior_present')
                or (after is not None) != row.get('current_present') or before is None and after is None):
            raise ValueError('Presence flags disagree with supplied states')
        for state in (before, after):
            if state is not None and state.get('id') != key[1]:
                raise ValueError('Change state object identity differs from its label')
        supported.append(row)
    if {(r['layer'], r['element_id']) for r in supported} != expected:
        raise ValueError('Missing supported changed objects from the supplied inventory')

    updated = deepcopy(prior)
    prior_metadata = {key: deepcopy(value) for key, value in prior.items() if key not in LAYER_KEYS.values()}
    changes, patches, features = [], [], []
    for row in sorted(supported, key=lambda r: (r['layer'], r['element_id'])):
        layer, eid = row['layer'], row['element_id']
        map_key = LAYER_KEYS[layer]
        index = _indices(updated)[layer].get(eid)
        stored_before = updated[map_key][index] if index is not None else None
        before = _normalize_state(layer, row['prior_state'])
        after = _normalize_state(layer, row['current_state'])
        if _normalize_state(layer, stored_before) != before:
            raise ValueError('Supplied prior state differs from the actual map object: ' + eid)
        if before == after:
            raise ValueError('A labelled change has identical before and after states: ' + eid)
        operation = 'add' if before is None else 'delete' if after is None else 'replace'
        if (operation == 'add' and 'added' not in row['state_delta_types']
                or operation == 'delete' and 'removed' not in row['state_delta_types']):
            raise ValueError('Presence operation disagrees with change types')
        patch = {'patch_id': 'reference-patch-' + str(len(patches) + 1), 'element_id': eid,
                 'layer': layer, 'operation': operation, 'before_index': index,
                 'before_state': deepcopy(stored_before), 'after_state': deepcopy(after),
                 'before_sha256': _digest(stored_before), 'after_sha256': _digest(after),
                 'executed': True, 'execution_target': 'isolated_reference_copy',
                 'confirmation_source': 'reference', 'source': 'delivered_ground_truth'}
        if operation == 'add':
            updated[map_key].append(deepcopy(after))
        elif operation == 'delete':
            updated[map_key].pop(index)
        else:
            updated[map_key][index] = deepcopy(after)
        patches.append(patch)
        before_geometry, after_geometry = _object_geometry(layer, before), _object_geometry(layer, after)
        change_id = row.get('gt_change_id') or 'reference:' + scene + ':' + layer + ':' + eid
        properties = {'change_id': change_id, 'element_id': eid, 'layer': layer,
                      'change_types': deepcopy(row['state_delta_types']), 'operation': operation,
                      'confirmed': True, 'confirmation_source': 'reference',
                      'source': 'delivered_ground_truth', 'map_patch_executed': True,
                      'execution_target': 'isolated_reference_copy', 'model_prediction': False}
        change = {**deepcopy(properties), 'scene_id': scene, 'before_geometry': before_geometry,
                  'after_geometry': after_geometry, 'geometry_city': deepcopy(after_geometry or before_geometry),
                  'prior_state': deepcopy(row['prior_state']), 'current_state': deepcopy(row['current_state']),
                  'differences': deepcopy(row.get('differences', {})),
                  'geometry_origin': 'derived_lane_footprint' if layer == 'laneSegments' else 'supplied_crossing_polygon'}
        changes.append(change)
        features.append({'type': 'Feature', 'id': change_id,
                         'geometry': deepcopy(change['geometry_city']), 'properties': properties})
    updated['role'] = 'trusted_reference_replay_copy_from_delivered_ground_truth'
    before_hash, after_hash = _digest(prior), _digest(updated)
    restored = rollback_reference_replay(updated, patches, prior_metadata, after_hash)
    if restored != prior:
        raise ValueError('Reference replay rollback did not restore the complete original map')
    unsupported_inventory = [row for row in inventory if row.get('layer') not in LAYER_KEYS
                             or row.get('state_comparison_supported') is not True]
    summary = {'confirmed_changes': len(changes), 'executed_patches': len(patches),
               **{name: sum(p['operation'] == op for p in patches)
                  for name, op in [('added', 'add'), ('replaced', 'replace'), ('deleted', 'delete')]},
               'layers': dict(Counter(c['layer'] for c in changes)),
               'change_types': dict(Counter(kind for c in changes for kind in c['change_types'])),
               'unsupported_changed_objects': len(unsupported),
               'unsupported_inventory_objects': len(unsupported_inventory),
               'updated_lanes': len(updated['lane_segments']),
               'updated_crossings': len(updated['pedestrian_crossings']),
               'rollback_verified': True, 'source_base_map_mutated': False,
               'detector_successes_claimed': False}
    return {'schema': 'trusted-reference-map-replay-v1', 'scene_id': scene,
            'source': 'delivered_ground_truth', 'confirmation_source': 'reference',
            'prediction_input': False, 'source_base_map_mutated': False,
            'changes': changes,
            'change_layer': {'type': 'FeatureCollection', 'features': features,
                             'coordinate_reference': {'frame': 'Argoverse supplied city XY', 'units': 'm'},
                             'source': 'delivered_ground_truth', 'prediction_input': False},
            'updated_map': updated, 'rollback_map': restored, 'patch_log': patches,
            'prior_metadata': prior_metadata, 'summary': summary,
            'version': {'prior_map_sha256': before_hash, 'updated_map_sha256': after_hash,
                        'rollback_map_sha256': _digest(restored),
                        'hash_format': 'canonical JSON sorted keys; compact separators'},
            'unsupported_changes': unsupported, 'provenance': deepcopy(provenance),
            'updated_map_context': reference_map_context(updated, provenance)}
