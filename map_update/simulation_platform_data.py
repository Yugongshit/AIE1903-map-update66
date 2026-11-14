"""Scene-bound data and current-state Planning handoff for the local platform.

Full recorded tracks are exposed only to the offline review view. Planning
exports contain one current snapshot and road geometry, never a recorded route
or future participant observations. Both structural and attached-state layers
exist in every response, including when a source has zero changes of a kind.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path

from .simulation_bridge import LANE_TYPES, feature_sha256, json_value, load_scenario
from .simulation_comparison import ATTRIBUTE_FIELDS
from .simulation_states import state_overview, state_snapshot


_SCENES = {
    'nuscenes-scene0980': {
        'title': 'nuScenes · scene-0980', 'dataset': 'nuscenes',
        'updated': 'demo/simulation/nuscenes-states',
        'original': 'demo/simulation/nuscenes-states',
        'kind': 'algorithm_outputs', 'source_label': '真实状态输出 · 道路结构修改为 0',
    },
    'argoverse-winter2021': {
        'title': 'Argoverse · Winter 2021', 'dataset': 'argoverse',
        'updated': 'demo/simulation/argoverse/updated',
        'original': 'demo/simulation/argoverse/original',
        'kind': 'reference_update', 'source_label': '已交付参考道路更新 · 无可用三维状态轨迹',
    },
    'nuscenes-combined-demo': {
        'title': 'nuScenes · 两层联调 DEMO', 'dataset': 'nuscenes',
        'updated': 'demo/simulation/nuscenes-combined',
        'original': 'demo/simulation/nuscenes-states',
        'kind': 'controlled_demo', 'source_label': '受控道路修改 + 同场景真实状态 · 非算法检出',
    },
}


def _json(path):
    def invalid(value):
        raise ValueError('Nonfinite value in simulation artifact: ' + value)
    return json.loads(Path(path).read_text(encoding='utf-8'), parse_constant=invalid)


def _entry(scene_key):
    if not isinstance(scene_key, str) or scene_key not in _SCENES:
        raise ValueError('Unknown platform scene')
    return _SCENES[scene_key]


def _version(version):
    if version not in ('original', 'updated'):
        raise ValueError('Map version must be original or updated')
    return version


def scene_database(repo_root, scene_key, version='updated'):
    """Resolve only a catalogued database, never a client-supplied file path."""
    entry = _entry(scene_key)
    root = Path(repo_root).resolve()
    directory = (root / entry[_version(version)]).resolve()
    if not directory.is_relative_to(root):
        raise ValueError('Scene database escapes repository root')
    if not (directory / 'simulation_manifest.json').is_file():
        raise FileNotFoundError('Scene database is not available: ' + scene_key)
    return directory


def _summary(manifest):
    data = manifest.get('summary', {})
    timeline = data.get('state_timeline', {})
    return {
        'structural_features': data.get('structural_operations', 0),
        'structure_updates': data.get('reference_structural_operations', data.get('structural_operations', 0)),
        'reference_updates': data.get('reference_structural_operations', 0),
        'state_events': timeline.get('events', data.get('state_events', 0)),
        'state_instances': timeline.get('historical_instances', 0),
        'persisted_obstacles': data.get('persisted_obstacles', 0),
        'map_features': data.get('map_features', 0),
    }


def catalog(repo_root):
    """List available repository-packaged scenes with their distinct sources."""
    result = []
    for key, entry in _SCENES.items():
        try:
            directory = scene_database(repo_root, key)
        except FileNotFoundError:
            continue  # The explicitly synthetic combined example is optional.
        manifest = _json(directory / 'simulation_manifest.json')
        if manifest.get('dataset') != entry['dataset']:
            raise ValueError('Catalog dataset differs from export manifest')
        source = manifest.get('provenance', {})
        summary = _summary(manifest)
        result.append({'key': key, 'id': key, 'title': entry['title'], 'dataset': entry['dataset'],
                       'scene_id': manifest['scene_id'], 'source_label': entry['source_label'],
                       'source_kind': entry['kind'], 'synthetic': bool(source.get('synthetic')),
                       **summary, 'summary': summary, 'available': True,
                       'has_original_database': True})
    return result


def _bound_json(directory, filename, expected_hash, label):
    if not expected_hash:
        raise ValueError('Export lacks bound ' + label)
    value = _json(directory / filename)
    if feature_sha256(value) != expected_hash:
        raise ValueError(label + ' differs from its export hash')
    return value


def _verified_database(directory):
    manifest = _json(directory / 'simulation_manifest.json')
    filename = manifest.get('scenario_file')
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith('.pkl'):
        raise ValueError('Invalid exported scenario filename')
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory):
        raise ValueError('Scenario path escapes its database')
    if sha256(path.read_bytes()).hexdigest() != manifest.get('exported_scenario_sha256'):
        raise ValueError('Scenario differs from its export hash')
    scenario = load_scenario(path)
    metadata = scenario.get('metadata', {})
    if (scenario.get('id') != manifest.get('scene_id')
            or metadata.get('dataset') != manifest.get('dataset')
            or metadata.get('map') != manifest.get('map')):
        raise ValueError('Scenario and manifest identities differ')
    provenance = manifest.get('provenance', {})
    if feature_sha256(scenario['map_features']) != provenance.get('map_features_sha256'):
        raise ValueError('Scenario road geometry differs from its bound map version')
    if feature_sha256(scenario['tracks']) != provenance.get('exported_tracks_sha256', provenance.get('tracks_sha256')):
        raise ValueError('Scenario tracks differ from their export hash')
    bridge = metadata.get('map_update_bridge', {})
    for name in ('map_features_sha256', 'map_patch_log_sha256', 'obstacle_manifest_sha256', 'state_source_sha256'):
        if provenance.get(name) != bridge.get(name):
            raise ValueError('Scenario metadata does not bind ' + name)
    return manifest, scenario


def _scalar_series(value, length, name):
    import numpy as np
    result = np.asarray(value, dtype=float)
    if result.size != length or result.ndim == 0 or result.shape[0] != length or not np.isfinite(result).all():
        raise ValueError('Track ' + name + ' must contain one finite value per frame')
    return result.reshape(length).tolist()


def _tracks(scenario, obstacle_manifest):
    length = scenario['length']
    ego_id = str(scenario['metadata']['sdc_id'])
    persistent = {row['track_id'] for row in obstacle_manifest.get('objects', []) if row.get('persisted')}
    result = {}
    for identifier, raw in scenario['tracks'].items():
        state, metadata = raw['state'], raw.get('metadata', {})
        item = {'id': str(identifier), 'type': raw['type'],
                'category': metadata.get('type', raw['type']),
                'original_id': metadata.get('original_id', str(identifier)),
                'is_ego': str(identifier) == ego_id, 'persistent': identifier in persistent}
        for name in ('heading', 'length', 'width', 'height', 'valid'):
            item[name] = _scalar_series(state[name], length, name)
        if any(value not in (0., 1.) for value in item['valid']):
            raise ValueError('Track validity must be binary')
        item['valid'] = [bool(value) for value in item['valid']]
        for name in ('position', 'velocity'):
            vectors = json_value(state[name])
            if (not isinstance(vectors, list) or len(vectors) != length
                    or any(not isinstance(row, list) or len(row) not in (2, 3)
                           or any(type(v) not in (int, float) or not math.isfinite(v) for v in row)
                           for row in vectors)):
                raise ValueError('Track ' + name + ' requires finite XY vectors')
            item[name] = vectors
        for i, valid in enumerate(item['valid']):
            if valid and any(item[name][i] <= 0 for name in ('length', 'width', 'height')):
                raise ValueError('An observed track must have positive dimensions')
        result[str(identifier)] = item
    if ego_id not in result:
        raise ValueError('Scenario ego track is missing')
    return result, ego_id


def _clock(scenario, state_source, trajectory):
    length = scenario['length']
    times = _scalar_series(scenario['metadata']['ts'], length, 'timestamps')
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError('Replay timestamps must increase strictly')
    timestamps = [None] * length
    if state_source is not None:
        frames = state_source['source_frames']
        stamps = [int(frame['timestamp_ns']) for frame in frames]
        if length != (len(frames)-1)*5+1:
            raise ValueError('State source clock differs from scenario length')
        for step in range(length):
            frame, part = divmod(step, 5)
            timestamp = stamps[frame]
            if part:
                timestamp += (stamps[frame+1]-stamps[frame])*part//5
            timestamps[step] = str(timestamp)
        clock_source = 'exact_source_keyframes_with_converter_substeps'
    elif trajectory is not None:
        source = trajectory.get('source_timestamp_ns', [])
        if not source or trajectory.get('simulation_steps') != length:
            raise ValueError('Ego trajectory clock differs from scenario')
        origin = int(source[0])
        timestamps = [str(origin + round((value-times[0])*1_000_000_000)) for value in times]
        if int(timestamps[-1]) > int(source[-1]):
            raise ValueError('Resampled ego clock extrapolates beyond source observations')
        clock_source = 'source_ego_epoch_plus_resampled_10Hz_clock'
    else:
        clock_source = 'source_absolute_time_unavailable'
    return {'steps': length, 'times_s': times, 'source_timestamps_ns': timestamps,
            'duration_s': times[-1]-times[0], 'clock_source': clock_source}


def _check_patch_coverage(before, after, patches):
    changed = {key for key in before.keys() | after.keys()
               if feature_sha256(before.get(key)) != feature_sha256(after.get(key))}
    seen = set()
    for patch in patches:
        key = patch.get('feature_id')
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('Map change IDs must be unique')
        seen.add(key)
        old, new = before.get(key), after.get(key)
        expected = 'add' if old is None else 'delete' if new is None else 'replace'
        if (patch.get('operation') != expected or feature_sha256(patch.get('before')) != feature_sha256(old)
                or feature_sha256(patch.get('after')) != feature_sha256(new)):
            raise ValueError('Map change does not match its before and after maps: ' + key)
    if changed != seen:
        raise ValueError('Map change log does not cover every changed feature')


def _display_polygons(repo_root, manifest, maps, changes):
    """Recover omitted source surfaces for display without changing map hashes."""
    additions, reports = {'original': {}, 'updated': {}}, {}
    if manifest['dataset'] != 'nuscenes':
        return additions, reports
    source = Path(repo_root) / 'AIE1903 原始资料/maps/expansion' / (manifest['map'] + '.json')
    if not source.is_file():
        return additions, {'available': False, 'reason': 'matching_source_map_not_packaged'}
    from .simulation_environment import supplement_source_lane_polygons
    for version, features in maps.items():
        recovered, report = supplement_source_lane_polygons(
            features, raw_map_path=source, map_name=manifest['map'],
            edited_ids=[item['feature_id'] for item in changes] if version == 'updated' else [])
        for identifier, feature in recovered.items():
            if 'polygon' not in features[identifier] and 'polygon' in feature:
                additions[version][identifier] = {'polygon': feature['polygon'],
                                                   'holes': feature.get('_source_polygon_holes', [])}
        reports[version] = report
    return additions, reports


def _empty_overview(identity):
    counts = {'features': 0, 'scene_overview': 0, 'observed_active': 0, 'unknown': 0, 'stale': 0, 'cleared': 0}
    return {'schema': 'simulation-state-overview-1.0', 'scope': 'full_scene_overview', 'frozen': True,
            'scene_id': identity['scene_id'], 'map': identity['map'], 'step': None,
            'source_timestamp_ns': None, 'source_snapshot_age_s': None, 'observation_cutoff_ns': None,
            'source_range_ns': [], 'clock_mode': 'no_state_observations_supplied',
            'sampling_policy': 'first_supported_observation_per_instance',
            'events': [], 'objects': [],
            'layer': {'type': 'FeatureCollection', 'features': [], 'cleared_events': [], 'summary': counts},
            'summary': {'events': deepcopy(counts), 'objects': 0, 'objects_by_status': {},
                        'matched_instances': 0, 'missing_model_instances': 0},
            'planning_eligible': False, 'may_include_later_observations': False,
            'base_map_modified': False, 'new_obstacles_spawned': False}


def _static_overview_actors(tracks, manifest, overview, timeline):
    """Offline static models at their original first observed pose, from frame 0."""
    associations = {identifier: obj for obj in overview['objects'] for identifier in obj['matched_track_ids']}
    actors = []
    for definition in sorted(manifest.get('objects', []), key=lambda item: item['track_id']):
        if not definition.get('persisted'):
            continue
        identifier, first = definition['track_id'], definition['first_seen_step']
        track = tracks.get(identifier)
        if (track is None or track['is_ego'] or track['type'] not in ('TRAFFIC_BARRIER', 'TRAFFIC_CONE')
                or type(first) is not int or not 0 <= first < len(track['valid']) or not track['valid'][first]
                or any(track['valid'][:first]) or track['original_id'] != definition['instance_id']):
            raise ValueError('Static overview differs from its first source observation')
        state = associations.get(identifier)
        if state is not None and state['instance_id'] != track['original_id']:
            raise ValueError('State overview instance differs from its simulation track')
        actor = {key: deepcopy(track[key]) for key in ('id', 'type', 'category', 'original_id', 'is_ego', 'persistent')}
        actor.update({key: deepcopy(track[key][first]) for key in ('position', 'heading', 'length', 'width', 'height')})
        actor.update(velocity=[0.] * len(track['velocity'][first]), valid=True,
                     state_event_ids=deepcopy(state['event_ids']) if state else [],
                     state_status='scene_overview' if state else None, is_map_state_change=state is not None,
                     scope='full_scene_overview', frozen=True, planning_eligible=False,
                     source_step=first, first_seen_step=first,
                     source_timestamp_ns=timeline['source_timestamps_ns'][first],
                     geometry_role='fixed_first_observed_static_pose_for_scene_overview')
        actors.append(actor)
    return actors


def load_scene(repo_root, scene_key):
    """Return a JSON-ready offline-review payload after checking source hashes."""
    entry = _entry(scene_key)
    directory = scene_database(repo_root, scene_key)
    manifest, scenario = _verified_database(directory)
    if manifest['dataset'] != entry['dataset']:
        raise ValueError('Catalog and source datasets differ')
    provenance = manifest['provenance']
    synthetic = bool(provenance.get('synthetic'))
    if synthetic != (entry['kind'] == 'controlled_demo'):
        raise ValueError('Synthetic scene must retain its explicit catalog label')
    patches = _bound_json(directory, 'map_patch_log.json', provenance.get('map_patch_log_sha256'), 'Map change log')
    obstacles = _bound_json(directory, 'obstacle_manifest.json', provenance.get('obstacle_manifest_sha256'), 'Obstacle policy')
    maps = {}
    for version, filename, hash_name in [('original', 'map_before.json', 'source_map_features_sha256'),
                                         ('updated', 'map_after.json', 'map_features_sha256')]:
        artifact = _json(directory / filename)
        if any(artifact.get(name) != manifest.get(name) for name in ('dataset', 'scene_id', 'map')):
            raise ValueError('Map artifact belongs to another scene')
        features = artifact.get('map_features')
        if feature_sha256(features) != provenance.get(hash_name):
            raise ValueError('Map artifact differs from its bound ' + version + ' map hash')
        maps[version] = features
    _check_patch_coverage(maps['original'], maps['updated'], patches)
    version_manifests = {'original': manifest, 'updated': manifest}
    original_dir = scene_database(repo_root, scene_key, 'original')
    if original_dir != directory:
        original_manifest, original = _verified_database(original_dir)
        if (original_manifest['dataset'] != manifest['dataset'] or original['id'] != scenario['id']
                or original_manifest['map'] != manifest['map']
                or feature_sha256(original['map_features']) != provenance['source_map_features_sha256']
                or feature_sha256(original['tracks']) != feature_sha256(scenario['tracks'])
                or original['length'] != scenario['length']
                or feature_sha256(original['metadata']['ts']) != feature_sha256(scenario['metadata']['ts'])):
            raise ValueError('Original and updated versions must use the same scene, clock and participants')
        version_manifests['original'] = original_manifest
    source = None
    if provenance.get('state_source_sha256'):
        source = _bound_json(directory, 'state_source.json', provenance['state_source_sha256'], 'State timeline')
        if (source.get('dataset') != manifest['dataset'] or source.get('scene_id') != scenario['id']
                or source.get('map') != manifest['map'] or source.get('scenario_length') != scenario['length']):
            raise ValueError('State timeline belongs to a different scene or clock')
    trajectory = None
    if provenance.get('ego_trajectory_manifest_sha256'):
        trajectory = _bound_json(directory, 'ego_trajectory_manifest.json', provenance['ego_trajectory_manifest_sha256'], 'Ego clock')
    tracks, ego_id = _tracks(scenario, obstacles)
    timeline = _clock(scenario, source, trajectory)
    points = [point[:2] for features in maps.values() for feature in features.values()
              for name in ('polyline', 'polygon') for point in feature.get(name, [])]
    if not points:
        raise ValueError('Scenario has no road geometry')
    bounds = [min(p[0] for p in points), min(p[1] for p in points),
              max(p[0] for p in points), max(p[1] for p in points)]
    for index, patch in enumerate(patches, 1):
        old, new = patch.get('before') or {}, patch.get('after') or {}
        patch['label'] = ('DEMO ' if synthetic else '') + f'U{index:02d}'
        patch['attribute_changes'] = [{'field': key, 'before': deepcopy(old.get(key)), 'after': deepcopy(new.get(key))}
                                      for key in ATTRIBUTE_FIELDS if old.get(key) != new.get(key)]
        patch['synthetic'] = synthetic
        patch['source_kind'] = entry['kind']
    identity = {key: manifest[key] for key in ('dataset', 'scene_id', 'map', 'coordinate', 'units')}
    display_polygons, supplements = _display_polygons(repo_root, manifest, maps, patches)
    display_walkways=[]
    raw_walkway_map=Path(repo_root)/'AIE1903 原始资料/maps/expansion'/(manifest['map']+'.json')
    if manifest['dataset']=='nuscenes' and raw_walkway_map.is_file():
        from .simulation_walking import walkway_display
        display_walkways=walkway_display(maps['updated'],raw_walkway_map)
    overview = state_overview(source) if source is not None else _empty_overview(identity)
    static_overview = _static_overview_actors(tracks, obstacles, overview, timeline)
    contract = {'coordinates': 'source_city_XY_metres', 'viewer_origin_is_display_only': True,
                'map_layers': ['road_structure', 'attached_states'], 'state_layer_modifies_road_map': False,
                'vehicle_access_and_topology_retained': True,
                'recorded_ego_track_is_not_a_planned_route': True,
                'future_recorded_tracks_excluded_from_planning_export': True,
                'full_scene_preview_excluded_from_planning_export': True,
                'display_scope': 'full_scene_overview_offline_only',
                'unobserved_state_is_not_clearance': True,
                'background_traffic': 'recorded; not reactive',
                'safety_or_collision_avoidance_validation': 'not_claimed'}
    return {'schema': 'simulation-platform-scene-1.0', 'key': scene_key, 'id': scene_key,
            'title': entry['title'], **identity, 'identity': identity,
            'source': {'label': entry['source_label'], 'kind': entry['kind'], 'synthetic': synthetic,
                       'model_prediction': False if entry['kind'] == 'reference_update' else None},
            'summary': {**_summary(manifest), 'tracks': len(tracks), 'other_traffic_tracks': len(tracks)-1},
            'frame_count': scenario['length'], 'timestep_s': timeline['times_s'][1]-timeline['times_s'][0],
            'origin': tracks[ego_id]['position'][0][:2], 'bounds': bounds, 'timeline': timeline,
            'maps': maps, 'versions': {name: {'map_sha256': feature_sha256(features),
                                             'exported_scenario_sha256': version_manifests[name]['exported_scenario_sha256'],
                                             'provenance': deepcopy(version_manifests[name]['provenance'])}
                                       for name, features in maps.items()},
            'display_walkways':display_walkways,'display_polygons': display_polygons, 'source_polygon_supplement': supplements,
            'changes': patches, 'tracks': tracks, 'ego_track_id': ego_id,
            'state_source': source, 'obstacle_manifest': obstacles,
            'state_overview': overview, 'static_overview_actors': static_overview,
            'planning_contract': contract, 'provenance': {**deepcopy(provenance),
                                                         'exported_scenario_sha256': manifest['exported_scenario_sha256']},
            'full_recorded_tracks_scope': 'offline_review_only_not_planning_input',
            'limitations': ['道路高程和建筑几何未提供，显示不重建未知环境。',
                            '参考更新、算法状态和受控演示分别标明来源。']}


def _empty_states(payload, step):
    stamp = payload['timeline']['source_timestamps_ns'][step]
    counts = {'features': 0, 'observed_active': 0, 'unknown': 0, 'stale': 0, 'cleared': 0}
    return {'schema': 'simulation-state-snapshot-1.0', 'scene_id': payload['scene_id'],
            'map': payload['map'], 'step': step, 'source_timestamp_ns': stamp,
            'observation_cutoff_ns': None, 'source_snapshot_age_s': None,
            'clock_mode': 'no_state_observations_supplied', 'events': [], 'objects': [],
            'layer': {'type': 'FeatureCollection', 'features': [], 'cleared_events': [], 'summary': counts},
            'summary': {'events': counts, 'objects': 0, 'objects_by_status': {},
                        'matched_instances': 0, 'missing_model_instances': 0},
            'base_map_modified': False, 'new_obstacles_spawned': False}


def build_snapshot(payload, step, version='updated'):
    """Return both change layers and current actors at one exact replay step."""
    _version(version)
    if type(step) is not int or not 0 <= step < payload['frame_count']:
        raise ValueError('Replay step is outside the recorded scene')
    if payload.get('state_source') is not None:
        states = state_snapshot(payload['state_source'], step)
        states['events'] = states['layer']['features']
    else:
        states = _empty_states(payload, step)
    by_track = {}
    for item in states['objects']:
        for identifier in item['matched_track_ids']:
            if identifier not in payload['tracks'] or payload['tracks'][identifier]['original_id'] != item['instance_id']:
                raise ValueError('State instance identity differs from its simulation track')
            by_track[identifier] = item
    actors = []
    for identifier, track in payload['tracks'].items():
        if not track['valid'][step]:
            continue
        state = by_track.get(identifier)
        actor = {key: deepcopy(track[key]) for key in ('id', 'type', 'category', 'original_id', 'is_ego', 'persistent')}
        actor.update({key: deepcopy(track[key][step]) for key in ('position', 'heading', 'length', 'width', 'height', 'velocity')})
        actor.update(state_event_ids=deepcopy(state['event_ids']) if state else [],
                     state_status=state['status'] if state else None,
                     is_map_state_change=state is not None, valid=True)
        actors.append(actor)
    result = {'schema': 'simulation-platform-snapshot-1.0', 'key': payload['key'],
              'dataset': payload['dataset'], 'scene_id': payload['scene_id'], 'map': payload['map'],
              'step': step, 'time_s': payload['timeline']['times_s'][step],
              'source_timestamp_ns': payload['timeline']['source_timestamps_ns'][step],
              'version': version, 'map_sha256': payload['versions'][version]['map_sha256'],
              'exported_scenario_sha256': payload['versions'][version]['exported_scenario_sha256'],
              'structures': [{key: deepcopy(item.get(key)) for key in
                              ('feature_id', 'operation', 'label', 'source_event_id', 'source_reference_patch_id',
                               'source_element_id', 'source_kind', 'synthetic')} for item in payload['changes']],
              'states': states, 'actors': actors,
              'ego': next((item for item in actors if item['is_ego']), None),
              'summary': {'structural_features': len(payload['changes']),
                          'reference_updates': payload['summary']['reference_updates'],
                          'state_events': len(states['events']), 'state_instances': len(states['objects']),
                          'visible_actors': len(actors)},
              'map_versions_share_attached_states': True,
              'coordinates': 'source_city_XY_metres', 'source': deepcopy(payload['source'])}
    if states['source_timestamp_ns'] != result['source_timestamp_ns']:
        raise ValueError('Road scene and attached states disagree on source time')
    return result


def planning_bundle(repo_root, scene_key, step, version='updated'):
    """Export a current, versioned upstream contract without future tracks."""
    payload = load_scene(repo_root, scene_key)
    snapshot = build_snapshot(payload, step, version)
    features = deepcopy(payload['maps'][version])
    selected = payload['versions'][version]
    selected_provenance = selected['provenance']
    lanes = {key: feature for key, feature in features.items() if feature.get('type') in LANE_TYPES}
    objects = [deepcopy(item) for item in snapshot['actors'] if not item['is_ego']]
    return {'schema': 'simulation-planning-handoff-1.0', 'scene_key': scene_key,
            'identity': deepcopy(payload['identity']), 'map_version': snapshot['map_sha256'],
            'map_selection': version, 'step': step, 'time_s': snapshot['time_s'],
            'source_timestamp_ns': snapshot['source_timestamp_ns'],
            'coordinate_reference': {'frame': payload['coordinate'], 'map': payload['map'], 'units': 'm'},
            'map_features': features,
            'lane_graph': {key: {'entry_lanes': deepcopy(feature.get('entry_lanes', [])),
                                 'exit_lanes': deepcopy(feature.get('exit_lanes', [])),
                                 'source_lane_type': feature.get('source_lane_type'),
                                 'vehicle_access': deepcopy(feature.get('vehicle_access'))}
                           for key, feature in lanes.items()},
            'map_state_snapshot': deepcopy(snapshot['states']),
            'current_objects': objects, 'current_ego': deepcopy(snapshot['ego']),
            'persistent_obstacles': [item for item in objects if item['persistent']],
            'route': None, 'requested_goal': None,
            'planning_contract': deepcopy(payload['planning_contract']),
            'provenance': {'scene_export_sha256': selected['exported_scenario_sha256'],
                           'source': deepcopy(payload['source']),
                           'pipeline_result_sha256': selected_provenance.get('pipeline_result_sha256'),
                           'state_source_sha256': selected_provenance.get('state_source_sha256'),
                           'source_scenario_sha256': selected_provenance.get('source_scenario_sha256'),
                           'data_scope': 'current_snapshot_no_future_tracks_or_recorded_ego_route'}}
