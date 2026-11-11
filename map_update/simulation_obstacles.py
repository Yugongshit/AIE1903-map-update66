"""Choose how annotated barriers and cones populate a simulation environment.

The persistent policy is an explicit simulation choice: retain each instance at
its first observed pose from its first valid sample onward. It does not assert
that the source observations prove permanence or that a road is closed.
"""

from collections import Counter
from copy import deepcopy


STATIC_OBSTACLE_TYPES = {'TRAFFIC_BARRIER', 'TRAFFIC_CONE'}
OBSTACLE_MODES = {'persistent', 'recorded'}


def _array(value, label, length):
    import numpy as np

    array = np.asarray(value)
    if array.ndim == 0 or array.shape[0] != length or array.dtype.kind not in 'biuf':
        raise ValueError(label + ' requires one numeric value per scenario step')
    if not np.isfinite(array).all():
        raise ValueError(label + ' must contain finite values')
    return array


def apply_obstacle_policy(scenario, mode='persistent'):
    """Return a copied scenario and explicit first-seen obstacle manifest.

Dynamic vehicle, pedestrian and cyclist tracks are retained exactly. Existing
instance IDs are reused, so one obstacle cannot be duplicated by several state
events. Samples before first observation remain invalid. A simulator continuing
beyond the recording must keep already spawned persistent obstacles alive.
"""
    import numpy as np

    if mode not in OBSTACLE_MODES:
        raise ValueError('Unknown obstacle mode: ' + str(mode))
    length = scenario.get('length')
    if type(length) is not int or length <= 0:
        raise ValueError('Scenario length must be a positive integer')
    result = deepcopy(scenario)
    objects, skipped, seen = [], [], set()
    timestamps = scenario.get('metadata', {}).get('ts')
    if timestamps is not None:
        timestamps = _array(timestamps, 'Scenario timestamps', length).reshape(length, -1)
        if timestamps.shape[1] != 1:
            raise ValueError('Scenario timestamps require one value per step')
    for track_id, original in scenario['tracks'].items():
        if original.get('type') not in STATIC_OBSTACLE_TYPES:
            continue
        identity = original.get('metadata', {}).get('original_id', track_id)
        if not isinstance(identity, str) or not identity or identity in seen:
            raise ValueError('Static obstacles require unique source instance identities')
        seen.add(identity)
        state = original.get('state', {})
        valid = _array(state.get('valid'), track_id + ' validity', length)
        if valid.size != length or not np.isin(valid, [0, 1]).all():
            raise ValueError('Static obstacle validity must contain one binary flag per step')
        valid_steps = np.flatnonzero(valid.reshape(-1))
        if not len(valid_steps):
            skipped.append({'track_id': track_id, 'instance_id': identity, 'reason': 'never_observed'})
            continue
        first, last = int(valid_steps[0]), int(valid_steps[-1])
        position = _array(state.get('position'), track_id + ' position', length)
        if position.ndim != 2 or position.shape[1] != 3:
            raise ValueError('Static obstacle position requires XYZ for every step')
        held = {'position': position}
        for name in ('heading', 'length', 'width', 'height'):
            array = _array(state.get(name), track_id + ' ' + name, length)
            if array.size != length:
                raise ValueError('Static obstacle ' + name + ' requires one scalar per step')
            if name != 'heading' and float(array[first].reshape(-1)[0]) <= 0:
                raise ValueError('First observed obstacle dimensions must be positive')
            held[name] = array
        velocity = None
        if 'velocity' in state:
            velocity = _array(state['velocity'], track_id + ' velocity', length)
            if velocity.ndim != 2 or velocity.shape[1] not in (2, 3):
                raise ValueError('Static obstacle velocity requires XY or XYZ per step')
        for name in ('rotation',):
            if name in state:
                held[name] = _array(state[name], track_id + ' ' + name, length)
        if mode == 'persistent':
            destination = result['tracks'][track_id]['state']
            for name, source in held.items():
                transformed = np.array(source, copy=True)
                transformed[first:] = source[first]
                destination[name] = transformed
            transformed = np.array(valid, copy=True)
            transformed[first:] = 1
            destination['valid'] = transformed
            if velocity is not None:
                transformed = np.array(velocity, copy=True)
                transformed[first:] = 0
                destination['velocity'] = transformed
        objects.append({
            'track_id': track_id, 'instance_id': identity, 'type': original['type'],
            'first_seen_step': first,
            'first_seen_time_s': float(timestamps[first, 0]) if timestamps is not None else None,
            'source_last_valid_step': last, 'source_valid_steps': int(len(valid_steps)),
            'position_xyz': position[first].tolist(),
            'heading_rad': float(held['heading'][first].reshape(-1)[0]),
            'length_m': float(held['length'][first].reshape(-1)[0]),
            'width_m': float(held['width'][first].reshape(-1)[0]),
            'height_m': float(held['height'][first].reshape(-1)[0]),
            'persisted': mode == 'persistent',
            'valid_until_step': length-1 if mode == 'persistent' else last,
            'pose_source': 'first_valid_source_track_sample',
            'persistence_source': 'user_selected_simulation_policy' if mode == 'persistent' else 'source_recorded_validity',
            'observed_permanence': False,
        })
    manifest = {
        'schema': 'simulation-obstacle-policy-1.0', 'mode': mode,
        'coordinate': 'nuScenes_global', 'units': 'm', 'objects': objects,
        'skipped_objects': skipped,
        'policy': {
            'types': sorted(STATIC_OBSTACLE_TYPES),
            'appearance': 'first_valid_source_step',
            'pose': 'hold_first_observed_pose_and_dimensions' if mode == 'persistent' else 'recorded_track',
            'validity': 'first_seen_through_recording_end' if mode == 'persistent' else 'recorded_valid_mask',
            'after_recording': 'keep_previously_spawned_objects' if mode == 'persistent' else 'no_persistence_extension',
            'future_objects_visible_early': False, 'dynamic_participants': 'unchanged_recorded_tracks',
            'source': 'user_selected_simulation_policy', 'observed_permanence_claimed': False,
            'road_closure_inferred': False, 'source_annotations_modified': False,
        },
        'summary': {
            'observed_static_instances': len(objects), 'persisted_instances': len(objects) if mode == 'persistent' else 0,
            'types': dict(sorted(Counter(o['type'] for o in objects).items())),
            'never_observed_instances': len(skipped), 'new_instance_ids': 0,
        },
    }
    # Avoid stale trajectory summaries after replacing validity and pose jitter.
    if mode == 'persistent':
        summaries = result['metadata'].get('object_summary')
        if isinstance(summaries, dict):
            for obj in objects:
                summaries[obj['track_id']] = {
                    'type': obj['type'], 'object_id': obj['track_id'], 'track_length': length,
                    'moving_distance': 0., 'valid_length': length-obj['first_seen_step'],
                    'continuous_valid_length': length-obj['first_seen_step'],
                }
            number = result['metadata'].get('number_summary')
            if isinstance(number, dict):
                moving = Counter(row['type'] for row in summaries.values() if row.get('moving_distance', row.get('distance', 0.)) > 1)
                number['num_moving_objects'] = sum(moving.values())
                number['num_moving_objects_each_type'] = dict(moving)
    result['metadata']['simulation_obstacles'] = deepcopy(manifest)
    return result, manifest
