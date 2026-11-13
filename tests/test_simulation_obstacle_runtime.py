"""Static visibility and dimension contracts without importing the renderer."""
from types import SimpleNamespace
from copy import deepcopy
import unittest

from map_update.simulation_obstacle_runtime import (
    persistent_objects, persistent_spawn_spec, traffic_manager_class, runtime_obstacle_report,
)


def definition():
    return {'track_id': 'barrier', 'type': 'TRAFFIC_BARRIER', 'persisted': True,
            'first_seen_step': 2, 'length_m': 2., 'width_m': .4, 'height_m': 1.}


def track():
    return {'type': 'TRAFFIC_BARRIER', 'state': {'valid': [0, 0, 1],
            'position': [[0., 0.], [0., 0.], [5., 6.]], 'heading': [0., 0., .7],
            'length': [0., 0., 2.], 'width': [0., 0., .4], 'height': [0., 0., 1.]}}


class FakeManager:
    def __init__(self):
        self._data = {'barrier': track(), 'vehicle': {'type': 'VEHICLE'}}
        self.episode_step = 0
        self._scenario_id_to_obj_id, self._obj_id_to_scenario_id = {}, {}
        self.engine = SimpleNamespace(global_config={'force_reuse_object_name': True})
        self.calls = []

    @property
    def current_traffic_data(self):
        return self._data

    def spawn_static_object(self, cls, key, value):
        self.calls.append(('vendor', key))

    def spawn_object(self, cls, **kwargs):
        self.calls.append((cls, kwargs))
        return SimpleNamespace(name=kwargs['name'] or 'new-object')


class PersistentObstacleRuntimeTests(unittest.TestCase):
    def test_fixed_source_box_stops_real_bullet_body_without_nonzero_inverse_mass(self):
        # Renderer-free native regression. setStatic(True) alone preserves the
        # vendor inverse mass and produces contacts while the car passes through.
        try:
            from panda3d.bullet import BulletBoxShape, BulletRigidBodyNode, BulletWorld
            from panda3d.core import NodePath
            from map_update.simulation_obstacle_runtime import _measured_classes
            classes = _measured_classes()
        except ImportError:
            self.skipTest('Requires the simulation interpreter with Panda3D/MetaDrive')
        for name, cls in classes.items():
            with self.subTest(source_type=name):
                world = BulletWorld()
                wall = BulletRigidBodyNode('source-fixed-box')
                wall.addShape(BulletBoxShape((.15, 2., 1.)))
                wall.setMass(cls.MASS)
                wall.setStatic(True)
                world.attachRigidBody(wall)
                car = BulletRigidBodyNode('physical-car')
                car.addShape(BulletBoxShape((2.25, .9, .6)))
                car.setMass(1100.)
                node = NodePath(car)
                node.setPos(-5., 0., 0.)
                car.setLinearVelocity((3., 0., 0.))
                world.attachRigidBody(car)
                for _ in range(150):
                    world.doPhysics(.02, 1, .02)
                self.assertEqual(wall.getInvMass(), 0.)
                self.assertLess(node.getX(), -2.3)
                self.assertLess(abs(car.getLinearVelocity().x), .05)
                world.removeRigidBody(car)
                world.removeRigidBody(wall)

    def test_first_observation_not_revealed_early_and_remains_beyond_recording(self):
        self.assertIsNone(persistent_spawn_spec('barrier', track(), definition(), 1))
        at_first = persistent_spawn_spec('barrier', track(), definition(), 2)
        after_record = persistent_spawn_spec('barrier', track(), definition(), 900)
        self.assertEqual(at_first, after_record)
        self.assertEqual(at_first['position'], [5., 6.])
        self.assertEqual(at_first['length'], 2.)
        self.assertTrue(at_first['static'])

    def test_moving_objects_cannot_be_declared_persistent(self):
        manifest = {'schema': 'simulation-obstacle-policy-1.0', 'mode': 'persistent',
                    'objects': [{**definition(), 'type': 'VEHICLE'}]}
        with self.assertRaisesRegex(ValueError, 'barriers and cones'):
            persistent_objects(manifest)

    def test_source_dimension_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'dimensions differ'):
            persistent_spawn_spec('barrier', track(), {**definition(), 'width_m': 5.}, 2)

    def test_single_last_frame_object_not_filtered_and_not_duplicated(self):
        manager = traffic_manager_class(FakeManager, {'barrier': definition()},
                    object_classes={'TRAFFIC_BARRIER': 'MeasuredBarrier'})()
        manager.spawn_static_object('VendorBarrier', 'barrier', track())
        self.assertFalse(manager.calls)
        manager.episode_step = 2
        manager.spawn_static_object('VendorBarrier', 'barrier', track())
        manager.episode_step = 900
        manager.spawn_static_object('VendorBarrier', 'barrier', track())
        self.assertEqual(len(manager.calls), 1)
        self.assertEqual(manager.calls[0][0], 'MeasuredBarrier')
        self.assertTrue(manager.calls[0][1]['static'])
        self.assertEqual(manager._scenario_id_to_obj_id, {'barrier': 'barrier'})

    def test_no_moving_traffic_keeps_persisted_static_objects(self):
        manager = traffic_manager_class(FakeManager, {'barrier': definition()},
                    no_moving_traffic=True, object_classes={})()
        self.assertEqual(set(manager.current_traffic_data), {'barrier'})

    def test_unlisted_static_object_uses_vendor_behavior(self):
        manager = traffic_manager_class(FakeManager, {'barrier': definition()}, object_classes={})()
        manager.spawn_static_object('Cone', 'other', {'type': 'TRAFFIC_CONE'})
        self.assertEqual(manager.calls, [('vendor', 'other')])

    def test_duplicate_identity_is_rejected(self):
        manifest = {'schema': 'simulation-obstacle-policy-1.0', 'mode': 'persistent',
                    'objects': [definition(), definition()]}
        with self.assertRaisesRegex(ValueError, 'unique'):
            persistent_objects(manifest)

    def test_full_scene_preview_preloads_first_pose_without_rewriting_recorded_validity(self):
        source = track()
        saved = deepcopy(source)
        start = persistent_spawn_spec('barrier', source, definition(), 0, preload_all=True)
        later = persistent_spawn_spec('barrier', source, definition(), 900, preload_all=True)
        self.assertEqual(start, later)
        self.assertEqual(start['position'], [5., 6.])
        self.assertEqual(start['heading_theta'], .7)
        self.assertEqual(source, saved)
        self.assertIsNone(persistent_spawn_spec('barrier', source, definition(), 0))

    def test_preloaded_static_is_not_duplicated_and_background_tracks_remain_available(self):
        manager = traffic_manager_class(FakeManager, {'barrier': definition()},
                    object_classes={'TRAFFIC_BARRIER': 'MeasuredBarrier'}, preload_all=True)()
        original = deepcopy(manager.current_traffic_data)
        manager.spawn_static_object('VendorBarrier', 'barrier', track())
        manager.episode_step = 2
        manager.spawn_static_object('VendorBarrier', 'barrier', track())
        self.assertEqual(len(manager.calls), 1)
        self.assertEqual(manager.current_traffic_data, original)
        self.assertIn('vehicle', manager.current_traffic_data)
        self.assertTrue(manager.full_scene_preview)

    def test_preloaded_runtime_report_explicitly_identifies_early_preview_objects(self):
        obstacle = SimpleNamespace(position=[5., 6.], heading_theta=.7, LENGTH=2., WIDTH=.4,
                                   HEIGHT=1., body=SimpleNamespace(isStatic=lambda: True))
        manager = SimpleNamespace(persistence_definitions={'barrier': definition()},
                                  full_scene_preview=True, _scenario_id_to_obj_id={'barrier': 'runtime-1'},
                                  spawned_objects={'runtime-1': obstacle})
        env = SimpleNamespace(episode_step=0, engine=SimpleNamespace(traffic_manager=manager))
        report = runtime_obstacle_report(env)
        self.assertEqual(report['expected_ids'], ['barrier'])
        self.assertEqual(report['missing_expected_ids'], [])
        self.assertTrue(report['future_objects_visible_early'])
        self.assertEqual(report['display_scope'], 'full_scene_overview')
        self.assertEqual(report['objects']['barrier']['position_runtime_m'], [5., 6.])
        self.assertTrue(report['objects']['barrier']['static'])


if __name__ == '__main__':
    unittest.main()
