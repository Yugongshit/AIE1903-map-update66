"""First-observation obstacle persistence is an explicit simulation policy."""
import unittest

try:
    import numpy as np
except ImportError:
    np = None

from map_update.simulation_bridge import feature_sha256
from map_update.simulation_obstacles import apply_obstacle_policy


def track(kind, validity):
    length = len(validity)
    return {'type': kind, 'state': {
        'position': np.array([[float(i), 2.*i, 0.] for i in range(length)]),
        'valid': np.array(validity, dtype=float), 'heading': np.arange(length, dtype=float)*.1,
        'velocity': np.ones((length, 2)),
        'length': np.arange(length, dtype=float).reshape(-1, 1)+1.,
        'width': np.full((length, 1), .4), 'height': np.full((length, 1), .7),
    }}


def scene():
    return {'length': 5, 'metadata': {'ts': np.arange(5)*.1}, 'tracks': {
        'barrier': track('TRAFFIC_BARRIER', [0, 1, 0, 1, 0]),
        'cone': track('TRAFFIC_CONE', [0, 0, 1, 0, 0]),
        'car': track('VEHICLE', [1, 1, 0, 1, 1]),
        'person': track('PEDESTRIAN', [1, 0, 1, 1, 0]),
        'cyclist': track('CYCLIST', [1, 1, 1, 0, 0]),
    }}


@unittest.skipIf(np is None, 'Simulation obstacle optional NumPy dependency is not installed')
class PersistentObstacleTests(unittest.TestCase):
    def test_first_seen_pose_dimensions_and_visibility_are_held(self):
        source = scene()
        digest = feature_sha256(source)
        result, manifest = apply_obstacle_policy(source)
        self.assertEqual(digest, feature_sha256(source))
        self.assertEqual(set(result['tracks']), set(source['tracks']))
        self.assertEqual(manifest['summary']['persisted_instances'], 2)
        self.assertEqual(len({obj['instance_id'] for obj in manifest['objects']}), 2)
        for key, first in [('barrier', 1), ('cone', 2)]:
            state = result['tracks'][key]['state']
            np.testing.assert_array_equal(state['valid'], [0]*first+[1]*(5-first))
            for name in ('position', 'heading', 'length', 'width', 'height'):
                old = source['tracks'][key]['state'][name]
                np.testing.assert_array_equal(state[name][:first], old[:first])
                np.testing.assert_array_equal(state[name][first:], np.repeat(old[first:first+1], 5-first, axis=0))
            np.testing.assert_array_equal(state['velocity'][first:], np.zeros((5-first, 2)))
            obj = next(o for o in manifest['objects'] if o['track_id'] == key)
            self.assertEqual(obj['first_seen_step'], first)
            self.assertEqual(obj['first_seen_time_s'], first*.1)
            self.assertFalse(obj['observed_permanence'])
        for key in ('car', 'person', 'cyclist'):
            self.assertEqual(feature_sha256(source['tracks'][key]), feature_sha256(result['tracks'][key]))

    def test_recorded_mode_is_exact_track_preservation(self):
        source = scene()
        result, manifest = apply_obstacle_policy(source, 'recorded')
        self.assertEqual(feature_sha256(source['tracks']), feature_sha256(result['tracks']))
        self.assertEqual(manifest['summary']['persisted_instances'], 0)
        self.assertTrue(all(not o['persisted'] for o in manifest['objects']))

    def test_never_observed_object_is_not_invented(self):
        source = scene()
        source['tracks']['never'] = track('TRAFFIC_CONE', [0]*5)
        result, manifest = apply_obstacle_policy(source)
        self.assertEqual(feature_sha256(result['tracks']['never']), feature_sha256(source['tracks']['never']))
        self.assertNotIn('never', [o['track_id'] for o in manifest['objects']])
        self.assertEqual(manifest['skipped_objects'], [{'track_id': 'never', 'instance_id': 'never', 'reason': 'never_observed'}])

    def test_duplicate_source_identity_is_rejected(self):
        source = scene()
        source['tracks']['barrier']['metadata'] = {'original_id': 'same'}
        source['tracks']['cone']['metadata'] = {'original_id': 'same'}
        with self.assertRaisesRegex(ValueError, 'unique source'):
            apply_obstacle_policy(source)

    def test_invalid_first_observation_is_not_replaced_by_later_convenient_pose(self):
        for field, value in [('position', [float('nan'), 0., 0.]), ('length', [0.]), ('height', [-1.])]:
            source = scene()
            source['tracks']['barrier']['state'][field][1] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                apply_obstacle_policy(source)

    def test_summary_is_updated_after_static_jitter_is_removed(self):
        source = scene()
        source['metadata']['object_summary'] = {
            'barrier': {'type': 'TRAFFIC_BARRIER', 'moving_distance': 3.},
            'cone': {'type': 'TRAFFIC_CONE', 'moving_distance': 2.},
            'car': {'type': 'VEHICLE', 'moving_distance': 5.},
        }
        source['metadata']['number_summary'] = {'num_moving_objects': 3}
        result, _ = apply_obstacle_policy(source)
        self.assertEqual(result['metadata']['object_summary']['barrier']['moving_distance'], 0.)
        self.assertEqual(result['metadata']['object_summary']['barrier']['valid_length'], 4)
        self.assertEqual(result['metadata']['number_summary']['num_moving_objects'], 1)
        self.assertEqual(result['metadata']['number_summary']['num_moving_objects_each_type'], {'VEHICLE': 1})

    def test_unknown_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unknown obstacle mode'):
            apply_obstacle_policy(scene(), 'guess')


if __name__ == '__main__':
    unittest.main()
