"""Convert a scene's delivered reference map update and measured ego poses.

The reference replay is an explicitly supplied update source, never a model
prediction. Both databases retain the same Argoverse city XY axes. No nuScenes
geometry, invented surrounding scenery or untracked 2D traffic is imported.
NumPy and Shapely are required; MetaDrive is needed only to run the output.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import pickle
import shutil
import tempfile

from .simulation_bridge import feature_sha256, json_value


COORDINATE = 'Argoverse_city_XY'
MAP_COORDINATES = 'City XY; no heights in ArgoTweak input'
LAYERS = {'laneSegments': 'lane_segments', 'pedCrossings': 'pedestrian_crossings'}
ACCESS = {'VEHICLE': ['vehicle', 'bus'], 'BUS': ['bus'], 'BIKE': ['bicycle']}
MARKINGS = {
    'DOUBLE_SOLID_YELLOW': 'ROAD_LINE_SOLID_DOUBLE_YELLOW',
    'DOUBLE_SOLID_WHITE': 'ROAD_LINE_SOLID_DOUBLE_WHITE',
    'SOLID_YELLOW': 'ROAD_LINE_SOLID_SINGLE_YELLOW',
    'SOLID_WHITE': 'ROAD_LINE_SOLID_SINGLE_WHITE',
    'DASHED_WHITE': 'ROAD_LINE_BROKEN_SINGLE_WHITE',
    'DASHED_YELLOW': 'ROAD_LINE_BROKEN_SINGLE_YELLOW',
    'DOUBLE_DASH_YELLOW': 'ROAD_LINE_BROKEN_SINGLE_YELLOW',
    'DOUBLE_DASH_WHITE': 'ROAD_LINE_BROKEN_SINGLE_WHITE',
    'NONE': 'UNKNOWN_LINE',
}


def _read(path):
    def invalid(value):
        raise ValueError('Non-finite JSON constant: ' + value)
    return json.loads(Path(path).read_text(encoding='utf-8'), parse_constant=invalid)


def _write(path, value):
    Path(path).write_text(json.dumps(json_value(value), ensure_ascii=False, indent=2,
                                     allow_nan=False) + '\n', encoding='utf-8')


def _file_hash(path):
    return sha256(Path(path).read_bytes()).hexdigest()


def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + ' must be a nonempty string')
    return value


def _objects(map_data):
    if not isinstance(map_data, dict) or map_data.get('coordinates') != MAP_COORDINATES:
        raise ValueError('Expected the supplied ArgoTweak City XY map, with no heights')
    _name(map_data.get('scene_id'), 'Map scene_id')
    result = {}
    for layer, key in LAYERS.items():
        rows = map_data.get(key)
        if not isinstance(rows, list):
            raise ValueError('Map requires ' + key)
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('Map objects must be dictionaries')
            identifier = _name(row.get('id'), 'Map object id')
            prefix = 'ls-' if layer == 'laneSegments' else 'pc-'
            if not identifier.startswith(prefix) or not identifier[len(prefix):].isdigit():
                raise ValueError('Unexpected ArgoTweak object ID: ' + identifier)
            if identifier in result:
                raise ValueError('Duplicate map object ID: ' + identifier)
            result[identifier] = (layer, row)
    return result


def validate_reference_replay(prior, updated, patches, replay):
    """Re-execute the complete reference transaction and verify every hash."""
    original, final = _objects(prior), _objects(updated)
    scene = prior['scene_id']
    if updated['scene_id'] != scene or replay.get('scene_id') != scene:
        raise ValueError('Reference maps and replay must identify the same scene')
    if (replay.get('source') != 'delivered_ground_truth'
            or replay.get('confirmation_source') != 'reference'
            or replay.get('prediction_input') is not False):
        raise ValueError('Only explicitly identified delivered reference updates are supported')
    version = replay.get('version', {})
    for name, value in [('prior_map_sha256', prior), ('rollback_map_sha256', prior),
                        ('updated_map_sha256', updated)]:
        if feature_sha256(value) != version.get(name):
            raise ValueError('Reference replay hash mismatch: ' + name)
    if not isinstance(patches, list) or len(patches) != replay.get('summary', {}).get('executed_patches'):
        raise ValueError('Reference patch count differs from the completed replay')
    state = deepcopy(prior)
    seen, patch_ids = set(), set()
    for patch in patches:
        if not isinstance(patch, dict):
            raise ValueError('Reference patches must be objects')
        identifier, layer = patch.get('element_id'), patch.get('layer')
        patch_id = _name(patch.get('patch_id'), 'Reference patch id')
        if identifier in seen or patch_id in patch_ids or layer not in LAYERS:
            raise ValueError('Duplicate or unsupported reference patch')
        seen.add(identifier)
        patch_ids.add(patch_id)
        if (patch.get('source') != 'delivered_ground_truth'
                or patch.get('confirmation_source') != 'reference'
                or patch.get('executed') is not True or patch.get('synthetic', False)):
            raise ValueError('A reference patch must be executed, non-synthetic and source identified')
        before, after = patch.get('before_state'), patch.get('after_state')
        operation = patch.get('operation')
        presence = {'add': (False, True), 'replace': (True, True), 'delete': (True, False)}
        if operation not in presence or (before is not None, after is not None) != presence[operation]:
            raise ValueError('Reference operation and before/after presence disagree')
        for field, value in [('before', before), ('after', after)]:
            if feature_sha256(value) != patch.get(field + '_sha256'):
                raise ValueError('Reference patch ' + field + ' hash mismatch')
            if value is not None and value.get('id') != identifier:
                raise ValueError('Reference patch targets a different object')
        if before == after:
            raise ValueError('Reference replacement contains no change')
        rows = state[LAYERS[layer]]
        index = next((i for i, row in enumerate(rows) if row['id'] == identifier), None)
        stored = rows[index] if index is not None else None
        if stored != before or patch.get('before_index') != index:
            raise ValueError('Reference patch before-state does not match the supplied prior')
        if after is None:
            rows.pop(index)
        elif index is None:
            rows.append(deepcopy(after))
        else:
            rows[index] = deepcopy(after)
    state['role'] = 'trusted_reference_replay_copy_from_delivered_ground_truth'
    if state != updated:
        raise ValueError('Reference patches do not reconstruct the complete updated map')
    actual = {key for key in original.keys() | final.keys() if original.get(key) != final.get(key)}
    if actual != seen:
        raise ValueError('Reference patches do not cover every changed object')


def _polyline(points, label):
    import numpy as np
    try:
        result = np.asarray(points, dtype=float)
    except (TypeError, ValueError) as error:
        raise ValueError(label + ' requires finite XY points') from error
    if result.ndim != 2 or result.shape[1] != 2 or len(result) < 2 or not np.isfinite(result).all():
        raise ValueError(label + ' requires at least two finite XY points')
    # Consecutive duplicates have no arc length and must not enter interpolation.
    result = result[np.r_[True, np.linalg.norm(np.diff(result, axis=0), axis=1) > 1e-9]]
    if len(result) < 2:
        raise ValueError(label + ' has zero length')
    return result


def _lane_references(value, label):
    if not isinstance(value, list):
        raise ValueError(label + ' must be an explicit list')
    result = []
    for identifier in value:
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
            raise ValueError('Lane references must be numeric IDs')
        value = str(identifier)
        value = value[3:] if value.startswith('ls-') else value
        if not value.isdigit():
            raise ValueError('Invalid lane reference')
        result.append('ls-' + value)
    if len(set(result)) != len(result):
        raise ValueError('Repeated lane reference')
    return result


def convert_map_features(map_data, *, sample_spacing_m=0.5):
    """Derive centerlines within supplied footprints; preserve access/topology.

External references are retained as source attributes and reported, but are
omitted from the navigable graph because their geometry is outside this map.
"""
    import numpy as np
    from shapely.geometry import LineString, Polygon

    if not math.isfinite(sample_spacing_m) or sample_spacing_m <= 0:
        raise ValueError('Centerline sampling spacing must be positive')
    objects = _objects(map_data)
    lane_ids = {key for key, (layer, _) in objects.items() if layer == 'laneSegments'}
    features, external, graph = {}, [], {}
    for identifier, (layer, row) in objects.items():
        if layer == 'pedCrossings':
            ring = _polyline(row.get('points'), identifier)
            polygon = Polygon(ring)
            if not polygon.is_valid or polygon.area <= 1e-8:
                raise ValueError('Invalid supplied crossing polygon: ' + identifier)
            features[identifier] = {'type': 'CROSSWALK', 'polygon': np.asarray(polygon.exterior.coords),
                                    'source_element_id': identifier, 'source_layer': layer,
                                    'geometry_origin': 'supplied_crossing_polygon_xy'}
            continue
        lane_type = row.get('lane_type')
        if lane_type not in ACCESS or type(row.get('is_intersection')) is not bool:
            raise ValueError('Unknown lane access or intersection semantics: ' + identifier)
        sides = {}
        for side in ('left', 'right'):
            try:
                points = [[point['x'], point['y']] for point in row[side + '_lane_boundary']]
            except (KeyError, TypeError) as error:
                raise ValueError('Lane requires both supplied boundaries') from error
            sides[side] = _polyline(points, identifier + ' ' + side)
            marking = row.get(side + '_lane_mark_type')
            if marking not in MARKINGS:
                raise ValueError('Unsupported supplied marking type: ' + str(marking))
            features[identifier + ':' + side] = {
                'type': MARKINGS[marking], 'polyline': sides[side],
                'source_element_id': identifier, 'source_layer': layer, 'source_boundary_side': side,
                'source_marking_type': marking,
                'rendering_approximation': 'double dashed rendered as single dashed' if marking.startswith('DOUBLE_DASH') else None,
            }
        left, right = sides['left'], sides['right']
        paired = np.linalg.norm(left[0] - right[0]) + np.linalg.norm(left[-1] - right[-1])
        reversed_pair = np.linalg.norm(left[0] - right[-1]) + np.linalg.norm(left[-1] - right[0])
        if reversed_pair + 1e-6 < paired:
            raise ValueError('Lane boundaries disagree on direction: ' + identifier)
        polygon = Polygon(np.concatenate((left, right[::-1])))
        if not polygon.is_valid or polygon.area <= 1e-8:
            raise ValueError('Invalid supplied lane footprint: ' + identifier)
        arcs = {side: np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
                for side, points in sides.items()}
        count = max(2, int(math.ceil(max(arc[-1] for arc in arcs.values()) / sample_spacing_m)) + 1)
        samples = {side: np.column_stack([np.interp(np.linspace(0., arc[-1], count), arc, sides[side][:, axis])
                                          for axis in (0, 1)]) for side, arc in arcs.items()}
        center = (samples['left'] + samples['right']) / 2
        if LineString(center).length <= 1e-8 or not polygon.buffer(1e-7).covers(LineString(center)):
            raise ValueError('Derived centerline leaves supplied footprint: ' + identifier)
        refs = {}
        for source, destination in [('predecessors', 'entry_lanes'), ('successors', 'exit_lanes')]:
            values = _lane_references(row.get(source), identifier + ' ' + source)
            refs['source_' + source] = values
            refs[destination] = [key for key in values if key in lane_ids]
            external.extend({'feature_id': identifier, 'field': destination, 'target_id': key,
                             'reason': 'outside_supplied_map', 'navigation_action': 'excluded_no_geometry'}
                            for key in values if key not in lane_ids)
        graph[identifier] = refs
        features[identifier] = {
            'type': 'LANE_BIKE_LANE' if lane_type == 'BIKE' else 'LANE_SURFACE_STREET',
            'polyline': center, 'polygon': np.asarray(polygon.exterior.coords),
            'width': np.column_stack((np.linalg.norm(center - samples['left'], axis=1),
                                      np.linalg.norm(center - samples['right'], axis=1))),
            'interpolating': True, **refs, 'left_neighbor': [], 'right_neighbor': [],
            'source_element_id': identifier, 'source_layer': layer,
            'source_lane_type': lane_type, 'vehicle_access': list(ACCESS[lane_type]),
            'is_intersection': row['is_intersection'],
            'source_left_lane_mark_type': row['left_lane_mark_type'],
            'source_right_lane_mark_type': row['right_lane_mark_type'],
            'geometry_origin': 'footprint_from_supplied_boundaries_centerline_from_equal_fraction_arclength_midpoints',
            'centerline_measured': False, 'centerline_sample_spacing_bound_m': sample_spacing_m,
        }
    for identifier, refs in graph.items():
        for field, opposite in [('exit_lanes', 'entry_lanes'), ('entry_lanes', 'exit_lanes')]:
            for target in refs[field]:
                if identifier not in graph[target][opposite]:
                    raise ValueError('Asymmetric supplied lane connection: ' + identifier + ' -> ' + target)
    return features, external


def convert_ego_track(frames, scene_id, *, ego_dimensions=(4.5, 1.9, 1.5)):
    """Resample measured city poses to the engine's 10 Hz clock without extrapolation."""
    import numpy as np
    if len(frames) < 2:
        raise ValueError('At least two measured ego poses are required')
    dimensions = np.asarray(ego_dimensions, dtype=float)
    if dimensions.shape != (3,) or not np.isfinite(dimensions).all() or np.any(dimensions <= 0):
        raise ValueError('Ego dimensions must be positive length, width and height')
    timestamps, transforms, seen = [], [], set()
    for frame in frames:
        if frame.get('scene_id') != scene_id or not str(frame.get('clip_id', '')).startswith(scene_id + '__'):
            raise ValueError('Ego poses belong to a different scene or clip')
        frame_id = _name(frame.get('frame_id'), 'Ego frame_id')
        timestamp = frame.get('timestamp_ns')
        if frame_id in seen or not isinstance(timestamp, str) or not timestamp.isdigit():
            raise ValueError('Ego frame IDs and integer nanosecond timestamps must be unique')
        seen.add(frame_id)
        transform = np.asarray(frame.get('city_from_ego_at_lidar'), dtype=float)
        if transform.shape != (4, 4) or not np.isfinite(transform).all():
            raise ValueError('Ego pose requires a finite 4x4 city-from-ego transform')
        rotation = transform[:3, :3]
        if (not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-8)
                or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5)
                or not np.isclose(np.linalg.det(rotation), 1., atol=1e-5)):
            raise ValueError('Ego pose is not a right-handed rigid transform')
        timestamps.append(int(timestamp))
        transforms.append(transform)
    if any(b <= a for a, b in zip(timestamps, timestamps[1:])):
        raise ValueError('Ego timestamps must be strictly increasing')
    if len({frame['clip_id'] for frame in frames}) != 1:
        raise ValueError('Do not concatenate separate ego clips')
    source_times = np.asarray([(stamp - timestamps[0]) / 1e9 for stamp in timestamps])
    if np.max(np.diff(source_times)) > .2:
        raise ValueError('Ego poses have an unsupported observation gap above 0.2 seconds')
    times = np.arange(int(math.floor(source_times[-1] * 10 + 1e-9)) + 1, dtype=float) / 10
    if len(times) < 2:
        raise ValueError('Ego recording is shorter than one simulation step')
    transforms = np.asarray(transforms)
    position = np.zeros((len(times), 3))
    for axis in (0, 1):
        position[:, axis] = np.interp(times, source_times, transforms[:, axis, 3])
    headings = np.unwrap(np.arctan2(transforms[:, 1, 0], transforms[:, 0, 0]))
    heading = np.interp(times, source_times, headings)
    heading = (heading + np.pi) % (2 * np.pi) - np.pi
    velocity = np.gradient(position[:, :2], .1, axis=0)
    track = {'type': 'VEHICLE', 'state': {
        'position': position, 'heading': heading, 'velocity': velocity,
        'valid': np.ones(len(times), dtype=bool),
        **{key: np.full(len(times), value) for key, value in zip(('length', 'width', 'height'), dimensions)},
    }, 'metadata': {'object_id': 'ego', 'type': 'VEHICLE', 'dataset': 'argoverse',
                   'track_length': len(times), 'pose_source': 'M2.city_from_ego_at_lidar',
                   'dimensions_source': 'explicit_simulation_vehicle_configuration',
                   'dimensions_observed': False}}
    left = np.searchsorted(source_times, times, side='right') - 1
    left = np.clip(left, 0, len(frames) - 2)
    fractions = (times - source_times[left]) / (source_times[left + 1] - source_times[left])
    report = {
        'source': 'measured_city_from_ego_at_lidar', 'scene_id': scene_id,
        'clip_id': frames[0]['clip_id'], 'source_frame_count': len(frames),
        'source_timestamp_ns': [str(stamp) for stamp in timestamps],
        'source_frame_ids': [frame['frame_id'] for frame in frames],
        'source_transforms_sha256': feature_sha256(transforms),
        'source_duration_s': float(source_times[-1]), 'simulation_steps': len(times),
        'simulation_timestep_s': .1, 'simulation_duration_s': float(times[-1]),
        'resampling': 'linear_xy_unwrapped_yaw_at_10Hz_no_extrapolation',
        'source_left_index': left.tolist(), 'interpolation_fraction': fractions.tolist(),
        'velocity_source': 'finite_difference_of_resampled_measured_xy',
        'map_elevation_available': False, 'simulation_z_m': 0.,
        'discarded_source_ego_z_range_m': [float(transforms[:, 2, 3].min()), float(transforms[:, 2, 3].max())],
        'roll_and_pitch_used': False, 'coordinate_axes_changed': False,
        'ego_dimensions_m': dict(zip(('length', 'width', 'height'), dimensions.tolist())),
        'ego_dimensions_source': 'explicit_simulation_vehicle_configuration',
        'other_traffic_source': 'not_available_as_3d_tracks', 'invented_traffic': False,
    }
    return track, times, report


def _converted_patch_log(before, after, reference_patches, changes):
    lookup = {patch['element_id']: patch for patch in reference_patches}
    descriptions = {change['element_id']: change for change in changes}
    result, represented = [], set()
    for identifier in sorted(before.keys() | after.keys()):
        old, new = before.get(identifier), after.get(identifier)
        if feature_sha256(old) == feature_sha256(new):
            continue
        owner = (new or old)['source_element_id']
        if owner not in lookup:
            raise ValueError('Converted feature changed without a reference patch: ' + identifier)
        patch = lookup[owner]
        represented.add(owner)
        result.append({'feature_id': identifier, 'operation': 'add' if old is None else 'delete' if new is None else 'replace',
                       'before': json_value(old), 'after': json_value(new),
                       'before_sha256': feature_sha256(old), 'after_sha256': feature_sha256(new),
                       'source_reference_patch_id': patch['patch_id'], 'source_element_id': owner,
                       'source_event_id': descriptions.get(owner, {}).get('change_id'),
                       'source': 'delivered_ground_truth', 'confirmation_source': 'reference',
                       'change_types': descriptions.get(owner, {}).get('change_types', []),
                       'model_prediction': False, 'synthetic': False})
    if represented != set(lookup):
        raise ValueError('Conversion omitted reference modifications: ' + repr(sorted(set(lookup) - represented)))
    return result


def _bound_source(pipeline, package, relative):
    path = package / relative
    digest = _file_hash(path)
    suffix = '/' + package.name + '/' + relative
    matches = [row for row in pipeline.get('sources', []) if row.get('path', '').endswith(suffix)]
    if len(matches) != 1 or matches[0].get('sha256') != digest:
        raise ValueError('M2 source is not bound to this pipeline: ' + relative)
    return {'path': str(path), 'sha256': digest, 'role': 'source_scene_' + relative.replace('/', '_')}


def export_argoverse_simulation(pipeline_result, m2_package, out, *, ego_dimensions=(4.5, 1.9, 1.5)):
    """Write ``out/original`` and ``out/updated`` as independent ScenarioNet DBs.

Inputs are local JSON; every material reference patch is verified and converted.
No arbitrary pickle input is needed. Existing directories are never overwritten.
"""
    result_path, package, target = (Path(value).resolve() for value in (pipeline_result, m2_package, out))
    if target.exists():
        raise FileExistsError(target)
    if target.is_relative_to(package) or target == result_path.parent:
        raise ValueError('Output must be separate from source inputs')
    pipeline = _read(result_path)
    data = pipeline.get('datasets', {}).get('argoverse', {})
    replay = data.get('material_reference_replay')
    if not isinstance(replay, dict):
        raise ValueError('Pipeline lacks a completed Argoverse material reference replay')
    scene_id = _name(data.get('scene_id'), 'Pipeline scene_id')
    sources = [{'path': str(result_path), 'sha256': _file_hash(result_path), 'role': 'completed_pipeline_result'}]
    artifacts = {}
    for field, filename in [('updated_map', 'argoverse_material_reference_updated_map.json'),
                            ('rollback_map', 'argoverse_material_reference_rollback_map.json'),
                            ('patch_log', 'argoverse_material_reference_patch_log.json')]:
        path = result_path.parent / filename
        artifacts[field] = _read(path)
        if artifacts[field] != replay.get(field):
            raise ValueError('Reference file differs from the completed pipeline: ' + filename)
        sources.append({'path': str(path), 'sha256': _file_hash(path), 'role': field})
    prior, updated, reference_patches = (artifacts[key] for key in ('rollback_map', 'updated_map', 'patch_log'))
    validate_reference_replay(prior, updated, reference_patches, replay)
    if prior['scene_id'] != scene_id:
        raise ValueError('Pipeline and reference map scenes differ')
    for relative in ('prior_map.json', 'tables/frames.jsonl', 'run.json'):
        sources.append(_bound_source(pipeline, package, relative))
    run = _read(package / 'run.json')
    if run.get('scene_id') != scene_id or run.get('inference_gt_read') is not False:
        raise ValueError('M2 run scene/provenance differs from the reference scene')
    if _read(package / 'prior_map.json') != prior:
        raise ValueError('Ego source package uses a different prior map')
    frames = [json.loads(line) for line in (package / 'tables/frames.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    track, times, trajectory = convert_ego_track(frames, scene_id, ego_dimensions=ego_dimensions)
    before, prior_external = convert_map_features(prior)
    after, updated_external = convert_map_features(updated)
    converted_patches = _converted_patch_log(before, after, reference_patches, replay.get('changes', []))
    # Opening the viewer should show a real change near the measured start.
    # Reordering display records never changes their source patch identities.
    from shapely.geometry import LineString, Point, Polygon
    start = Point(track['state']['position'][0, :2])
    for patch in converted_patches:
        shapes = []
        for feature in (patch['before'], patch['after']):
            if feature is not None:
                shapes.append(Polygon(feature['polygon']) if 'polygon' in feature else LineString(feature['polyline']))
        patch['distance_to_initial_ego_m'] = min(shape.distance(start) for shape in shapes)
    converted_patches.sort(key=lambda patch: (patch['distance_to_initial_ego_m'], patch['feature_id']))
    states = deepcopy(data.get('state_event_layer', {'type': 'FeatureCollection', 'features': []}))
    if states.get('features'):
        raise ValueError('Argoverse reference export does not yet support unjoined 3D state events')
    states['coordinate_reference'] = {'frame': COORDINATE, 'scene_id': scene_id, 'units': 'm'}
    obstacles = {'schema': 'simulation-obstacle-policy-1.0', 'mode': 'recorded',
                 'coordinate': COORDINATE, 'units': 'm', 'objects': [], 'skipped_objects': [],
                 'policy': {'source': 'no_3d_obstacle_tracks_supplied', 'observed_permanence_claimed': False,
                            'source_annotations_modified': False, 'invented_traffic': False},
                 'summary': {'observed_static_instances': 0, 'persisted_instances': 0,
                             'types': {}, 'never_observed_instances': 0, 'new_instance_ids': 0}}
    identity = {'dataset': 'argoverse', 'scene_id': scene_id, 'map': scene_id,
                'coordinate': COORDINATE, 'units': 'm'}
    filename = 'sd_argoverse_reference_' + sha256(scene_id.encode()).hexdigest()[:16] + '.pkl'
    common = {
        'pipeline_result_sha256': sources[0]['sha256'], 'source_scenario_sha256': None,
        'source_map_features_sha256': feature_sha256(before),
        'source_reference_prior_map_sha256': feature_sha256(prior),
        'source_reference_updated_map_sha256': feature_sha256(updated),
        'reference_patch_log_sha256': feature_sha256(reference_patches),
        'source_ego_frames_sha256': _file_hash(package / 'tables/frames.jsonl'),
        'ego_trajectory_manifest_sha256': feature_sha256(trajectory),
        'tracks_sha256': feature_sha256({'ego': track}),
        'source_tracks_sha256': None, 'exported_tracks_sha256': feature_sha256({'ego': track}),
        'tracks_unchanged_between_map_versions': True, 'dynamic_tracks_unchanged': True,
        'obstacle_mode': 'recorded', 'obstacle_manifest_sha256': feature_sha256(obstacles),
        'persisted_obstacle_ids': [], 'state_event_observations_unchanged': True,
        'obstacle_persistence_is_simulation_policy': False, 'observed_permanence_claimed': False,
        'coordinate_transform': 'city XY retained; map/ego Z flattened to zero because source map has no heights; MetaDrive centralizes once',
        'workflow': ['reference_map_update', 'simulation_map_export', 'planning', 'vehicle_control'],
        'source': 'delivered_ground_truth', 'update_source': 'reference_update',
        'confirmation_source': 'reference', 'model_prediction': False, 'synthetic': False,
        'source_files': sources, 'reference_delivery_provenance': replay.get('provenance'),
        'source_environment': 'source_geometry_only',
        'converted_patch_display_order': 'distance_to_initial_measured_ego_then_feature_id',
        'unavailable_geometry': ['map_elevation', 'buildings', 'terrain', 'tracked_other_traffic'],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.argoverse-simulation-', dir=target.parent))
    try:
        for variant, features, patches, external in [('original', before, [], prior_external),
                                                     ('updated', after, converted_patches, updated_external)]:
            destination = temporary / variant
            destination.mkdir()
            provenance = {**deepcopy(common), 'variant': variant,
                          'map_features_sha256': feature_sha256(features),
                          'map_patch_log_sha256': feature_sha256(patches),
                          'applied_reference_operations': len(reference_patches) if variant == 'updated' else 0}
            metadata = {'id': scene_id, 'scenario_id': scene_id, 'dataset': 'argoverse', 'map': scene_id,
                        'coordinate': 'right-handed', 'metadrive_processed': False, 'sdc_id': 'ego',
                        'ts': times, 'track_length': len(times), 'sdc_track_index': 0,
                        'source_file': str(package / 'tables/frames.jsonl'),
                        'map_update_bridge': {**identity, **deepcopy(provenance), 'structural_operations': len(patches)},
                        'simulation_obstacles': obstacles, 'ego_pose_conversion': trajectory,
                        'source_map_coordinates': MAP_COORDINATES}
            scenario = {'id': scene_id, 'version': 'argoverse-reference-simulation-1.0', 'length': len(times),
                        'metadata': metadata, 'tracks': {'ego': track}, 'map_features': features,
                        'dynamic_map_states': {}}
            serialized = pickle.dumps(scenario, protocol=4)
            planning = {'schema': 'simulation-planning-map-1.0', **identity,
                        'map_version': provenance['map_features_sha256'], 'map_features': features,
                        'state_event_layer': states, 'state_track_links': [], 'obstacle_manifest': obstacles,
                        'provenance': provenance, 'planning_contract': {
                            'route_source': 'current lane centerlines, explicit topology and vehicle_access',
                            'ego_route_is_not_fixed_by_export': True,
                            'centerline_source': 'derived_within_supplied_boundary_footprints',
                            'default_vehicle_class': 'vehicle', 'bus_and_bicycle_lanes_are_restricted': True,
                            'state_layer_changes_road_geometry': False,
                            'background_traffic': 'none; source provides only untracked 2D detections',
                            'unknown_is_not_clearance': True}}
            manifest = {'schema': 'simulation-map-export-1.0', **identity, 'scenario_file': filename,
                        'exported_scenario_sha256': sha256(serialized).hexdigest(), 'provenance': provenance,
                        'summary': {'map_features': len(features), 'structural_operations': len(patches),
                                    'reference_structural_operations': len(reference_patches) if variant == 'updated' else 0,
                                    'available_reference_operations': len(reference_patches),
                                    'lane_operations': sum((p['after'] or p['before'])['type'].startswith('LANE_') for p in patches),
                                    'lanes': len((updated if variant == 'updated' else prior)['lane_segments']),
                                    'crossings': len((updated if variant == 'updated' else prior)['pedestrian_crossings']),
                                    'state_events': 0, 'matched_state_tracks': 0, 'unmatched_state_instances': 0,
                                    'persisted_obstacles': 0, 'new_obstacles_spawned': 0,
                                    'ego_tracks': 1, 'other_traffic_tracks': 0, 'ego_source_frames': len(frames),
                                    'simulation_steps': len(times)},
                        'topology': {'introduced_issues': [], 'preserved_source_issues': external,
                                     'external_references': external, 'modified_lane_issues': [],
                                     'external_references_are_navigable': False},
                        'files': {'planning_map': 'planning_map.json', 'states': 'state_event_layer.json',
                                  'patch_log': 'map_patch_log.json', 'obstacles': 'obstacle_manifest.json',
                                  'reference_patch_log': 'reference_patch_log.json', 'ego_trajectory': 'ego_trajectory_manifest.json'},
                        'limitations': ['Reference changes are supplied ground truth, not model detections.',
                                        'Source map has XY only; all road/ego heights are flattened for simulation.',
                                        'Centerlines are derived from supplied boundaries; ego dimensions are simulation configuration.',
                                        'No 3D traffic tracks, buildings, terrain or signals are supplied.',
                                        'Double-dashed marking patterns use the engine single-dashed rendering type; original semantics retained.',
                                        'Source references outside the supplied map are reported and excluded from navigation.']}
            (destination / filename).write_bytes(serialized)
            (destination / 'dataset_summary.pkl').write_bytes(pickle.dumps({filename: metadata}, protocol=4))
            (destination / 'dataset_mapping.pkl').write_bytes(pickle.dumps({filename: ''}, protocol=4))
            for name, value in [('planning_map.json', planning), ('state_event_layer.json', states),
                                ('obstacle_manifest.json', obstacles), ('map_patch_log.json', patches),
                                ('reference_patch_log.json', reference_patches), ('ego_trajectory_manifest.json', trajectory),
                                ('map_before.json', {'map_features': before, **identity}),
                                ('map_after.json', {'map_features': features, **identity}),
                                ('simulation_manifest.json', manifest)]:
                _write(destination / name, value)
        _write(temporary / 'comparison_manifest.json', {
            'schema': 'argoverse-simulation-comparison-1.0', **identity,
            'original_database': 'original', 'updated_database': 'updated',
            'reference_operations': len(reference_patches), 'converted_operations': len(converted_patches),
            'source': 'delivered_ground_truth', 'update_source': 'reference_update',
            'model_prediction': False, 'synthetic': False, 'provenance': common,
            'databases': {variant: {'manifest_sha256': _file_hash(temporary / variant / 'simulation_manifest.json')}
                          for variant in ('original', 'updated')}})
        if target.exists():
            raise FileExistsError(target)
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target
