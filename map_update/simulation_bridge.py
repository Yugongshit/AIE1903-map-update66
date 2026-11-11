"""Export a versioned map into a separate ScenarioNet simulation database.

Patches contain explicit feature identities and complete lane semantics. City XY
is retained; MetaDrive centralizes the exported scenario when it loads it. State
events retain their observation semantics. A separate explicit simulation policy
keeps barriers/cones at their first observed pose by default. This module needs
NumPy, but does not import MetaDrive.
"""

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import pickle
import shutil
import tempfile

from .geometry import point_segment_distance, validate_geometry


LINE_TYPES = {
    'UNKNOWN_LINE', 'ROAD_LINE_BROKEN_SINGLE_WHITE', 'ROAD_LINE_SOLID_SINGLE_WHITE',
    'ROAD_LINE_SOLID_DOUBLE_WHITE', 'ROAD_LINE_BROKEN_SINGLE_YELLOW',
    'ROAD_LINE_BROKEN_DOUBLE_YELLOW', 'ROAD_LINE_SOLID_SINGLE_YELLOW',
    'ROAD_LINE_SOLID_DOUBLE_YELLOW', 'ROAD_LINE_PASSING_DOUBLE_YELLOW',
    'ROAD_EDGE_BOUNDARY', 'ROAD_EDGE_MEDIAN',
}
LANE_TYPES = {'LANE_SURFACE_STREET', 'LANE_SURFACE_UNSTRUCTURE', 'LANE_UNKNOWN',
              'LANE_FREEWAY', 'LANE_BIKE_LANE'}
PATCH_SCHEMA = 'simulation-map-patch-1.0'


def _numpy():
    try:
        import numpy
    except ImportError as error:
        raise ValueError('Simulation export requires NumPy; install the simulation dependencies') from error
    return numpy


class _ScenarioUnpickler(pickle.Unpickler):
    """Permit plain containers and numeric NumPy arrays, not arbitrary globals."""

    def find_class(self, module, name):
        np = _numpy()
        core = np._core if hasattr(np, '_core') else np.core
        allowed = {
            ('numpy', 'ndarray'): np.ndarray,
            ('numpy', 'dtype'): np.dtype,
            ('collections', 'defaultdict'): defaultdict,
            ('builtins', 'int'): int,
        }
        for prefix in ('numpy.core', 'numpy._core'):
            allowed[(prefix + '.multiarray', '_reconstruct')] = core.multiarray._reconstruct
            allowed[(prefix + '.multiarray', 'scalar')] = core.multiarray.scalar
            allowed[(prefix + '.numeric', '_frombuffer')] = core.numeric._frombuffer
        if (module, name) not in allowed:
            raise ValueError(f'Unsupported pickle global: {module}.{name}')
        return allowed[module, name]


def load_scenario(path):
    """Load supplied ScenarioNet numeric/container data with a global allowlist."""
    with Path(path).open('rb') as stream:
        return _ScenarioUnpickler(stream).load()


def json_value(value):
    """Convert NumPy values and sets without changing their numeric coordinates."""
    np = _numpy()
    if isinstance(value, np.ndarray):
        return json_value(value.tolist())
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, set):
        return sorted(json_value(v) for v in value)
    return value


def feature_sha256(feature):
    """Canonical digest used by the patch's per-feature preconditions."""
    return sha256(json.dumps(json_value(feature), sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _read_json(path):
    def invalid(value):
        raise ValueError('Nonfinite JSON constant: ' + value)
    return json.loads(Path(path).read_text(encoding='utf-8'), parse_constant=invalid)


def _write_json(path, value):
    Path(path).write_text(json.dumps(json_value(value), ensure_ascii=False, indent=2,
                                    allow_nan=False) + '\n', encoding='utf-8')


def _nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty string')
    return value


def _points(value, name, polygon=False):
    if not isinstance(value, list) or not value:
        raise ValueError(name + ' must be a coordinate array')
    dimension = len(value[0]) if isinstance(value[0], list) else 0
    if dimension not in (2, 3) or any(not isinstance(p, list) or len(p) != dimension for p in value):
        raise ValueError(name + ' requires consistent XY or XYZ coordinates')
    if any(type(v) not in (int, float) or not isfinite(v) for p in value for v in p):
        raise ValueError(name + ' coordinates must be finite numbers')
    xy = [p[:2] for p in value]
    geometry = {'type': 'Polygon' if polygon else 'LineString',
                'coordinates': [xy] if polygon else xy}
    validate_geometry(geometry, line_only=not polygon)
    return _numpy().asarray(value, dtype=float)


def _ids(value, name):
    if not isinstance(value, list) or any(not isinstance(v, str) or not v for v in value):
        raise ValueError(name + ' must be an explicit list of lane IDs')
    if len(set(value)) != len(value):
        raise ValueError(name + ' contains duplicate lane IDs')
    return list(value)


def _inside_or_on(point, polygon):
    if any(point_segment_distance(point, a, b) <= 1e-7 for a, b in zip(polygon, polygon[1:])):
        return True
    inside = False
    x, y = point[:2]
    for a, b in zip(polygon, polygon[1:]):
        if (a[1] > y) != (b[1] > y):
            intersection = (b[0]-a[0])*(y-a[1])/(b[1]-a[1]) + a[0]
            if x < intersection:
                inside = not inside
    return inside


def _feature(value):
    if not isinstance(value, dict):
        raise ValueError('Patch feature must be an object')
    kind = value.get('type')
    if kind not in LINE_TYPES | LANE_TYPES:
        raise ValueError('Unsupported map feature type: ' + str(kind))
    fields = {'type', 'polyline'}
    if kind in LANE_TYPES:
        fields |= {'polygon', 'entry_lanes', 'exit_lanes', 'left_neighbor', 'right_neighbor',
                   'speed_limit_kmh', 'speed_limit_mph'}
        missing = {'polygon', 'entry_lanes', 'exit_lanes'} - value.keys()
        if missing:
            raise ValueError('Lane edits require complete centerline, polygon and topology: ' + ', '.join(sorted(missing)))
    if value.keys() - fields:
        raise ValueError('Unsupported feature fields: ' + ', '.join(sorted(value.keys()-fields)))
    result = deepcopy(value)
    result['polyline'] = _points(value.get('polyline'), 'polyline')
    if kind in LANE_TYPES:
        result['polygon'] = _points(value['polygon'], 'polygon', polygon=True)
        ring = result['polygon'].tolist()
        for a, b in zip(result['polyline'], result['polyline'][1:]):
            # Check intermediate samples as well as vertices for concave lanes.
            length = float(_numpy().linalg.norm(b[:2] - a[:2]))
            count = max(1, int(length / .25) + 1)
            if any(not _inside_or_on((a+(b-a)*i/count)[:2], ring) for i in range(count+1)):
                raise ValueError('Lane centerline lies outside its supplied polygon')
        for name in ('entry_lanes', 'exit_lanes', 'left_neighbor', 'right_neighbor'):
            result[name] = _ids(value.get(name, []), name)
        if 'speed_limit_kmh' in value and 'speed_limit_mph' in value:
            raise ValueError('Supply one speed-limit unit')
        for name in ('speed_limit_kmh', 'speed_limit_mph'):
            if name in value and (type(value[name]) not in (float, int) or not isfinite(value[name]) or value[name] <= 0):
                raise ValueError('Speed limit must be positive and finite')
    return result


def _topology_issues(features):
    """Audit all lane edges; preserve but disclose pre-existing cropped-map gaps."""
    lanes = {key: value for key, value in features.items() if value.get('type') in LANE_TYPES}
    issues = set()
    for lane_id, lane in lanes.items():
        for field, inverse in (('entry_lanes', 'exit_lanes'), ('exit_lanes', 'entry_lanes'),
                               ('left_neighbor', None), ('right_neighbor', None)):
            neighbors = lane.get(field, [])
            if neighbors is None:
                neighbors = []
            if not isinstance(neighbors, list):
                issues.add((lane_id, field, '', 'invalid_reference_list'))
                continue
            for raw in neighbors:
                other = raw.get('id', raw.get('feature_id')) if isinstance(raw, dict) else raw
                if not isinstance(other, str) or other not in lanes:
                    issues.add((lane_id, field, str(other), 'missing_lane'))
                elif inverse and lane_id not in lanes[other].get(inverse, []):
                    issues.add((lane_id, field, other, 'nonreciprocal_edge'))
    return issues


def _identity(scenario):
    if not isinstance(scenario, dict) or not isinstance(scenario.get('metadata'), dict):
        raise ValueError('Scenario and metadata must be objects')
    meta = scenario.get('metadata', {})
    identity = {'dataset': meta.get('dataset'), 'scene_id': scenario.get('id'),
                'map': meta.get('map'), 'coordinate': 'nuScenes_global', 'units': 'm'}
    if identity['dataset'] != 'nuscenes' or meta.get('coordinate') != 'right-handed':
        raise ValueError('This bridge requires a right-handed nuScenes city-coordinate scenario')
    for key in ('scene_id', 'map'):
        _nonempty(identity[key], key)
    for key in ('id', 'scenario_id'):
        if key in meta and meta[key] != identity['scene_id']:
            raise ValueError('Scenario metadata identity mismatch')
    if 'old_origin_in_current_coordinate' in meta:
        raise ValueError('Supply original city-coordinate data, not an already centralized scenario')
    if not isinstance(scenario.get('map_features'), dict) or not scenario['map_features']:
        raise ValueError('Scenario has no map features')
    if not isinstance(scenario.get('tracks'), dict) or meta.get('sdc_id') not in scenario['tracks']:
        raise ValueError('Scenario is missing its ego track')
    return identity


def apply_patch(scenario, patch, source_sha256):
    """Validate a complete transaction before returning an independent scenario."""
    identity = _identity(scenario)
    original = scenario['map_features']
    result = deepcopy(scenario)
    if patch is None:
        return result, [], sorted(_topology_issues(original))
    if not isinstance(patch, dict) or patch.get('schema') != PATCH_SCHEMA:
        raise ValueError('Unsupported simulation patch schema')
    for key, expected in identity.items():
        if patch.get(key) != expected:
            raise ValueError('Patch ' + key + ' differs from source scenario')
    if patch.get('base_scenario_sha256') != source_sha256:
        raise ValueError('Patch base scenario SHA256 mismatch')
    provenance = patch.get('provenance')
    if not isinstance(provenance, dict) or type(provenance.get('synthetic')) is not bool:
        raise ValueError('Patch provenance must explicitly identify synthetic data')
    _nonempty(provenance.get('source'), 'Patch provenance source')
    operations = patch.get('operations')
    if not isinstance(operations, list) or not operations:
        raise ValueError('Patch operations must be a nonempty list')
    changes, touched = [], set()
    for op in operations:
        if not isinstance(op, dict):
            raise ValueError('Patch operation must be an object')
        key = _nonempty(op.get('feature_id'), 'feature_id')
        if key in touched:
            raise ValueError('One patch transaction cannot edit one feature twice')
        touched.add(key)
        action = op.get('operation')
        if action not in {'add', 'replace', 'delete'}:
            raise ValueError('Unsupported patch operation: ' + str(action))
        before = original.get(key)
        if action == 'add':
            if before is not None or op.get('before_sha256') is not None:
                raise ValueError('An added feature must have a new exact identity')
        else:
            if before is None or op.get('before_sha256') != feature_sha256(before):
                raise ValueError('Feature before-state SHA256 mismatch: ' + key)
            if before.get('type') not in LINE_TYPES | LANE_TYPES:
                raise ValueError('Cannot edit unsupported feature: ' + key)
        if action == 'delete':
            if 'feature' in op:
                raise ValueError('Delete must not carry replacement geometry')
            result['map_features'].pop(key)
            after = None
        else:
            after = _feature(op.get('feature'))
            if before is not None and ((before['type'] in LANE_TYPES) != (after['type'] in LANE_TYPES)):
                raise ValueError('Cannot replace a marking with a lane or vice versa')
            if before is not None and feature_sha256(before) == feature_sha256(after):
                raise ValueError('Replacement has no change: ' + key)
            result['map_features'][key] = after
        changes.append({'feature_id': key, 'operation': action,
                        'before_sha256': feature_sha256(before), 'after_sha256': feature_sha256(after),
                        'before': json_value(before), 'after': json_value(after),
                        'source_event_id': op.get('source_event_id')})
    previous_issues = _topology_issues(original)
    issues = _topology_issues(result['map_features'])
    introduced = issues - previous_issues
    affected = {issue for issue in issues if issue[0] in touched or issue[2] in touched}
    if introduced or affected:
        raise ValueError('Patch leaves inconsistent lane topology: ' + repr(sorted(introduced | affected)[:8]))
    number_summary = result['metadata'].get('number_summary')
    if isinstance(number_summary, dict):
        number_summary['num_map_features'] = len(result['map_features'])
        number_summary.pop('map_height_diff', None)  # No stale pre-patch geometry statistic.
    return result, changes, sorted(issues)


def _line_type(marking):
    """Only explicitly defined markings have a simulator type; never guess."""
    if marking in LINE_TYPES:
        return marking
    names = {'solid_white': 'ROAD_LINE_SOLID_SINGLE_WHITE',
             'broken_white': 'ROAD_LINE_BROKEN_SINGLE_WHITE',
             'dashed_white': 'ROAD_LINE_BROKEN_SINGLE_WHITE',
             'solid_yellow': 'ROAD_LINE_SOLID_SINGLE_YELLOW',
             'broken_yellow': 'ROAD_LINE_BROKEN_SINGLE_YELLOW',
             'dashed_yellow': 'ROAD_LINE_BROKEN_SINGLE_YELLOW',
             'double_solid_white': 'ROAD_LINE_SOLID_DOUBLE_WHITE',
             'double_solid_yellow': 'ROAD_LINE_SOLID_DOUBLE_YELLOW'}
    if isinstance(marking, str) and marking.lower() in names:
        return names[marking.lower()]
    raise ValueError('A confirmed line edit requires an explicit supported marking type')


def _same_line(geometry, polyline):
    if not isinstance(geometry, dict) or geometry.get('type') != 'LineString':
        return False
    try:
        validate_geometry(geometry, line_only=True)
        a, b = _numpy().asarray(geometry['coordinates']), _numpy().asarray(polyline)[:, :2]
        return a.shape == b.shape and bool(_numpy().allclose(a, b, rtol=0, atol=1e-7))
    except (ValueError, TypeError, IndexError):
        return False


def _event_operation(event, scenario, identity):
    """Adapt a completed verified line decision to the exact simulator feature."""
    from .pipeline_v2 import write_gate
    from .structural_decision import final_structural_decision

    final = event.get('final_structural_decision', {})
    layer = event.get('map_layer', {})
    stored_gate = event.get('decision', {}).get('write_gate', {})
    if final.get('action') != 'update' or layer.get('predicted_layer') != 'base_map_structure' or stored_gate.get('eligible') is not True:
        raise ValueError('Linked event is not a completed eligible structural update')
    ref = {'frame': 'nuScenes_global', 'city_id': identity['map'], 'units': 'm'}
    if event.get('coordinate_reference') != ref:
        raise ValueError('Structural event coordinates differ from source scenario')
    gate = write_gate(event, layer)
    checked = final_structural_decision(event, layer, gate, final.get('policy'))
    if not gate['eligible'] or checked['action'] != 'update':
        raise ValueError('Structural event no longer satisfies its recorded write contract')
    operation = event.get('change_type')
    if operation not in {'geometry_change', 'marking_change', 'add', 'delete'}:
        raise ValueError('Unsupported confirmed structural operation')
    if operation == 'add':
        identifier = event.get('new_element_id')
    else:
        aliases = event.get('map_aliases', [])
        if len(aliases) != 1 or aliases[0].get('boundary_side') is not None:
            raise ValueError('A confirmed line edit requires one exact line object, not a lane-side alias')
        identifier = aliases[0].get('map_element_id')
    identifier = _nonempty(identifier, 'Structural target')
    if ':' in identifier:
        prefix, token = identifier.split(':', 1)
        if prefix not in {'lane_divider', 'road_divider'}:
            raise ValueError('No simulator adapter for structural target layer: ' + prefix)
        identifier = _nonempty(token, 'Structural target token')
    before = scenario['map_features'].get(identifier)
    if operation != 'add' and (before is None or before.get('type') not in LINE_TYPES):
        raise ValueError('Confirmed line target is absent or is not a road marking')
    if operation == 'add' and before is not None:
        raise ValueError('Confirmed new line already exists in simulation map')
    action = {'geometry_change': 'replace', 'marking_change': 'replace',
              'add': 'add', 'delete': 'delete'}[operation]
    adapted = {'operation': action, 'feature_id': identifier, 'source_event_id': event['event_id']}
    if before is not None:
        adapted['before_sha256'] = feature_sha256(before)
    if operation in {'geometry_change', 'marking_change', 'delete'}:
        if not _same_line(event.get('prior_geometry_city'), before['polyline']):
            raise ValueError('Confirmed event prior geometry differs from the simulation target')
    if operation != 'delete':
        feature = json_value(before) if before is not None else {'type': _line_type(event.get('marking_type'))}
        if operation in {'geometry_change', 'add'}:
            geometry = event.get('proposed_geometry_city', event.get('geometry_city'))
            if not isinstance(geometry, dict) or geometry.get('type') != 'LineString':
                raise ValueError('Only complete confirmed line geometry has an automatic simulation adapter')
            validate_geometry(geometry, line_only=True)
            if not _same_line(event.get('geometry_city'), geometry['coordinates']):
                raise ValueError('Write-gated and final proposed geometries disagree')
            feature['polyline'] = deepcopy(geometry['coordinates'])
        if operation == 'marking_change':
            if _line_type(event.get('prior_marking_type')) != before['type']:
                raise ValueError('Confirmed prior marking type differs from simulation target')
            feature['type'] = _line_type(event.get('marking_type'))
        adapted['feature'] = feature
    return adapted


def _resolved_patch(scenario, data, patch, source_sha256, pipeline_sha256):
    """Auto-export confirmed line actions or verify every explicit event link."""
    identity = _identity(scenario)
    events = data.get('events')
    if not isinstance(events, list):
        raise ValueError('Pipeline dataset must contain its event records')
    indexed = {}
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('Pipeline event must be an object')
        key = _nonempty(event.get('event_id'), 'Pipeline event_id')
        if key in indexed:
            raise ValueError('Duplicate pipeline event identity')
        indexed[key] = event
    pending = {key: event for key, event in indexed.items()
               if event.get('final_structural_decision', {}).get('action') == 'update'}
    if patch is None and pending:
        operations = [_event_operation(event, scenario, identity) for event in pending.values()]
        return {'schema': PATCH_SCHEMA, **identity, 'base_scenario_sha256': source_sha256,
                'provenance': {'source': 'verified_pipeline_structural_actions', 'synthetic': False,
                               'pipeline_result_sha256': pipeline_sha256}, 'operations': operations}
    if patch is None:
        return None
    if not isinstance(patch, dict) or not isinstance(patch.get('operations'), list):
        raise ValueError('Patch must contain an operations list')
    patch = deepcopy(patch)
    links = {}
    for operation in patch['operations']:
        if not isinstance(operation, dict):
            raise ValueError('Patch operation must be an object')
        key = operation.get('source_event_id')
        if key is None:
            continue
        _nonempty(key, 'Patch source_event_id')
        if key not in pending or key in links:
            raise ValueError('Patch event link must identify one unique pending confirmed update')
        if patch.get('provenance', {}).get('synthetic') is not False:
            raise ValueError('Synthetic patches cannot claim confirmed pipeline event execution')
        expected = _event_operation(indexed[key], scenario, identity)
        for field in ('operation', 'feature_id', 'before_sha256'):
            if operation.get(field) != expected.get(field):
                raise ValueError('Patch differs from linked event target or operation: ' + key)
        if feature_sha256(operation.get('feature')) != feature_sha256(expected.get('feature')):
            raise ValueError('Patch geometry/type differs from linked event: ' + key)
        links[key] = operation
    if pending.keys() - links.keys():
        raise ValueError('Patch omits confirmed pipeline structural updates: ' + ', '.join(sorted(pending.keys()-links.keys())))
    return patch


def _database_scenario(root, scene_id):
    summary_path = root / 'dataset_summary.pkl'
    mapping_path = root / 'dataset_mapping.pkl'
    summary = load_scenario(summary_path)
    mapping = load_scenario(mapping_path)
    if not isinstance(summary, dict) or not isinstance(mapping, dict):
        raise ValueError('Invalid ScenarioNet database index')
    candidates = []
    for filename, metadata in summary.items():
        if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.pkl'):
            raise ValueError('Invalid scenario filename in database index')
        if filename not in mapping or not isinstance(mapping[filename], str):
            raise ValueError('Missing scenario database mapping')
        path = (root / mapping[filename] / filename).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Scenario database mapping escapes its root')
        if (isinstance(metadata, dict) and scene_id in (metadata.get('id'), metadata.get('scenario_id'))
                or len(summary) == 1):
            candidates.append(path)
    if len(candidates) != 1:
        raise ValueError('Database must contain one unambiguous scenario for ' + scene_id)
    path = candidates[0]
    return path, load_scenario(path)


def _state_join(scenario, data, identity):
    from .state_events import build_state_event_layer

    layer = deepcopy(data.get('state_event_layer'))
    if not isinstance(layer, dict) or layer.get('type') != 'FeatureCollection':
        raise ValueError('Pipeline result is missing its state-event layer')
    ref = layer.get('coordinate_reference')
    expected = {'frame': 'nuScenes_global', 'city_id': identity['map'], 'units': 'm'}
    if layer.get('features') and ref != expected:
        raise ValueError('State-layer coordinates differ from the simulation map')
    source_provenance = data.get('provenance', {})
    if not isinstance(source_provenance, dict):
        raise ValueError('Pipeline dataset provenance must be an object')
    provenance_ref = source_provenance.get('coordinate_reference')
    if provenance_ref != expected:
        raise ValueError('Pipeline coordinate provenance differs from the simulation map')
    if source_provenance.get('dataset_id', identity['dataset']) != identity['dataset'] or source_provenance.get('scene_id', identity['scene_id']) != identity['scene_id']:
        raise ValueError('Pipeline source identity differs from simulation scene')
    # Recompute from the supplied event observations: source IDs, timing and
    # retained geometry cannot be changed only in the display/export layer.
    rebuilt = build_state_event_layer(data.get('events', []), layer.get('as_of_timestamp_ns'))
    if feature_sha256(rebuilt) != feature_sha256(layer):
        raise ValueError('State layer differs from its source event observations')
    joins, seen = [], set()
    for feature in layer.get('features', []):
        prop = feature.get('properties', {})
        event_id = prop.get('event_id', feature.get('id'))
        _nonempty(event_id, 'State event identity')
        if event_id in seen:
            raise ValueError('Duplicate state event identity')
        seen.add(event_id)
        if prop.get('state') not in {'observed_active', 'unknown', 'stale'}:
            raise ValueError('Unsupported or cleared state in active event layer')
        if prop.get('coordinate_reference') != expected:
            raise ValueError('State feature coordinates differ from simulation map')
        sources = prop.get('source_ids')
        if not isinstance(sources, list) or any(not isinstance(s, str) or not s for s in sources) or len(set(sources)) != len(sources) or event_id not in sources:
            raise ValueError('Invalid state observation source identities')
        stamp = prop.get('last_seen_timestamp_ns')
        if not isinstance(stamp, str) or not stamp.isascii() or not stamp.isdecimal():
            raise ValueError('State observation timestamp must be an exact decimal string')
        tokens = sorted({s[9:] for s in sources if s.startswith('instance:')})
        if '' in tokens:
            raise ValueError('Empty state source instance identity')
        for token in tokens:
            if token in scenario['tracks']:
                track_meta = scenario['tracks'][token].get('metadata', {})
                if track_meta.get('original_id', token) != token:
                    raise ValueError('State source instance differs from converted track identity')
        joins.append({'event_id': event_id, 'state': prop['state'],
                      'last_seen_timestamp_ns': prop.get('last_seen_timestamp_ns'),
                      'instance_tokens': tokens,
                      'existing_track_ids': [t for t in tokens if t in scenario['tracks']],
                      'unmatched_instance_tokens': [t for t in tokens if t not in scenario['tracks']],
                      'new_obstacles_spawned': False})
    return layer, joins


def export_simulation_map(database_path, pipeline_result, out, patch=None, obstacle_mode='persistent'):
    """Export one nuScenes scene; all output is written to a new directory."""
    database = Path(database_path).resolve()
    result_path = Path(pipeline_result).resolve()
    target = Path(out).resolve()
    if target.exists():
        raise FileExistsError(target)
    if target.is_relative_to(database):
        raise ValueError('Output cannot be inside the source database')
    pipeline = _read_json(result_path)
    data = pipeline.get('datasets', {}).get('nuscenes')
    if not isinstance(data, dict):
        raise ValueError('Pipeline result must contain a nuScenes dataset')
    scene_id = _nonempty(data.get('scene_id'), 'Pipeline scene_id')
    source_path, scenario = _database_scenario(database, scene_id)
    source_hash = sha256(source_path.read_bytes()).hexdigest()
    identity = _identity(scenario)
    if identity['scene_id'] != scene_id:
        raise ValueError('Pipeline scene differs from simulation scene')
    patch_value = _read_json(patch) if isinstance(patch, (str, Path)) else deepcopy(patch)
    pipeline_hash = sha256(result_path.read_bytes()).hexdigest()
    patch_value = _resolved_patch(scenario, data, patch_value, source_hash, pipeline_hash)
    updated, changes, topology_issues = apply_patch(scenario, patch_value, source_hash)
    states, joins = _state_join(scenario, data, identity)
    from .simulation_obstacles import STATIC_OBSTACLE_TYPES, apply_obstacle_policy
    updated, obstacles = apply_obstacle_policy(updated, obstacle_mode)
    original_dynamic = {k: v for k, v in scenario['tracks'].items() if v.get('type') not in STATIC_OBSTACLE_TYPES}
    exported_dynamic = {k: v for k, v in updated['tracks'].items() if v.get('type') not in STATIC_OBSTACLE_TYPES}
    source_tracks_hash = feature_sha256(scenario['tracks'])
    exported_tracks_hash = feature_sha256(updated['tracks'])
    dynamic_hash = feature_sha256(original_dynamic)
    if dynamic_hash != feature_sha256(exported_dynamic):
        raise ValueError('Obstacle export unexpectedly changed dynamic participants')
    provenance = {'pipeline_result_sha256': pipeline_hash,
                  'source_scenario_sha256': source_hash,
                  'patch_sha256': feature_sha256(patch_value) if patch_value else None,
                  'map_patch_log_sha256': feature_sha256(changes),
                  'patch_provenance': patch_value['provenance'] if patch_value else None,
                  'source_map_features_sha256': feature_sha256(scenario['map_features']),
                  'map_features_sha256': feature_sha256(updated['map_features']),
                  'tracks_sha256': exported_tracks_hash,
                  'source_tracks_sha256': source_tracks_hash,
                  'exported_tracks_sha256': exported_tracks_hash,
                  'tracks_unchanged': source_tracks_hash == exported_tracks_hash,
                  'dynamic_tracks_sha256': dynamic_hash, 'dynamic_tracks_unchanged': True,
                  'obstacle_mode': obstacle_mode,
                  'obstacle_manifest_sha256': feature_sha256(obstacles),
                  'persisted_obstacle_ids': [obj['track_id'] for obj in obstacles['objects'] if obj['persisted']],
                  'state_event_observations_unchanged': True,
                  'obstacle_persistence_is_simulation_policy': obstacle_mode == 'persistent',
                  'observed_permanence_claimed': False,
                  'coordinate_transform': 'none; MetaDrive centralizes once when loading',
                  'workflow': ['map_update', 'simulation_map_export', 'planning', 'vehicle_control'],
                  'synthetic': bool(patch_value and patch_value['provenance']['synthetic'])}
    updated['metadata']['map_update_bridge'] = {**identity, **deepcopy(provenance),
                                                'structural_operations': len(changes)}
    filename = source_path.name
    serialized = pickle.dumps(updated, protocol=4)
    planner_map = {'schema': 'simulation-planning-map-1.0', **identity,
                   'map_version': provenance['map_features_sha256'],
                   'map_features': json_value(updated['map_features']),
                   'state_event_layer': states, 'state_track_links': joins,
                   'obstacle_manifest': obstacles,
                   'provenance': provenance,
                   'planning_contract': {
                       'route_source': 'updated lane centerlines and explicit entry/exit topology',
                       'ego_route_is_not_fixed_by_export': True,
                       'state_time_scope': 'each source event retains its observation timestamp and state; obstacle_manifest is a separate simulation policy',
                       'static_obstacles': 'hold first observed barrier/cone pose from first_seen_step onward' if obstacle_mode == 'persistent' else 'recorded positions and validity',
                       'background_traffic': 'dynamic vehicles, pedestrians and cyclists retain recorded tracks; reactive behavior requires a separate policy',
                       'unknown_is_not_clearance': True,
                       'safety_validation': 'not performed by map export'}}
    manifest = {'schema': 'simulation-map-export-1.0', **identity, 'scenario_file': filename,
                'exported_scenario_sha256': sha256(serialized).hexdigest(), 'provenance': provenance,
                'summary': {'map_features': len(updated['map_features']),
                            'structural_operations': len(changes),
                            'lane_operations': sum((c['after'] or c['before'])['type'] in LANE_TYPES for c in changes),
                            'state_events': len(states.get('features', [])),
                            'matched_state_tracks': len({t for j in joins for t in j['existing_track_ids']}),
                            'unmatched_state_instances': len({t for j in joins for t in j['unmatched_instance_tokens']}),
                            'persisted_obstacles': obstacles['summary']['persisted_instances'],
                            'new_obstacles_spawned': 0},
                'topology': {'introduced_issues': [], 'preserved_source_issues': topology_issues,
                             'modified_lane_issues': []},
                'files': {'planning_map': 'planning_map.json', 'states': 'state_event_layer.json',
                          'patch_log': 'map_patch_log.json', 'obstacles': 'obstacle_manifest.json'},
                'limitations': ['Only explicit road-line and complete lane-feature edits are supported.',
                                'Argoverse maps require their own matching converted scenario and adapter.',
                                'Persistent barriers/cones are a chosen simulation policy, not a finding of observed permanence or road closure.',
                                'A supplied map patch is not independent detection accuracy.']}
    # Stage complete artifacts before exposing the output directory.
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.simulation-export-', dir=target.parent))
    try:
        (temporary / filename).write_bytes(serialized)
        (temporary / 'dataset_summary.pkl').write_bytes(pickle.dumps({filename: updated['metadata']}, protocol=4))
        (temporary / 'dataset_mapping.pkl').write_bytes(pickle.dumps({filename: ''}, protocol=4))
        _write_json(temporary / 'planning_map.json', planner_map)
        _write_json(temporary / 'state_event_layer.json', states)
        _write_json(temporary / 'obstacle_manifest.json', obstacles)
        _write_json(temporary / 'map_patch_log.json', changes)
        _write_json(temporary / 'applied_patch.json', patch_value)
        _write_json(temporary / 'map_before.json', {'map_features': scenario['map_features'], **identity})
        _write_json(temporary / 'map_after.json', {'map_features': updated['map_features'], **identity})
        _write_json(temporary / 'simulation_manifest.json', manifest)
        if target.exists():
            raise FileExistsError(target)
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target
