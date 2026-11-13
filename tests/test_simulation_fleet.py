"""Recipient lifecycle, causal delivery, and session/physics boundary tests."""
from copy import deepcopy
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from map_update.simulation_fleet import FleetRuntime, actor_state, checked_action, validated_fleet
from map_update.simulation_session import SimulationSession, validated_camera


class Vehicle:
    LENGTH, WIDTH, FRONT_WHEELBASE, REAR_WHEELBASE = 4.5, 1.8, 1.1, 1.4
    max_steering, speed = 60., 0.

    def __init__(self, *, name, position, heading, **kwargs):
        self.id = name
        self.position, self.heading_theta = list(position), heading
        self.action = [0., 0.]
        self.after_steps = 0

    def before_step(self, action):
        self.action = list(action)

    def after_step(self):
        self.after_steps += 1


class Engine:
    def __init__(self, source):
        self.objects = {source.id: source}
        self.data_manager = SimpleNamespace(current_scenario={'id': 'two-lane-source',
            'metadata': {'old_origin_in_current_coordinate': [-100., -200.]}})

    def spawn_object(self, cls, **kwargs):
        vehicle = cls(**kwargs)
        self.objects[vehicle.id] = vehicle
        return vehicle

    def clear_objects(self, identifiers, force_destroy=False):
        for identifier in identifiers:
            del self.objects[identifier]

    def get_objects(self):
        return self.objects


class Env:
    def __init__(self):
        self.agent = Vehicle(name='source-A', position=[0., -15.], heading=0.)
        self.engine = Engine(self.agent)
        self.episode_step = 0
        self.config = {'vehicle_config': {}}
        self.last_source_action = None

    def step(self, action):
        self.last_source_action = action
        self.episode_step += 1
        for vehicle in self.engine.objects.values():
            # Test-only physics boundary: a controller/command cannot cause
            # motion unless the owning environment explicitly advances.
            vehicle.position[0] += max(0., vehicle.action[1]) * .1

    def reset(self, seed):
        assert len(self.engine.objects) == 1, 'Recipients must be cleared before native reset'
        self.episode_step = 0


def config(mode='online', variant='full'):
    original = {
        'b': {'type': 'LANE_SURFACE_STREET', 'polyline': [[100., 200.], [170., 200.]], 'exit_lanes': []},
        'c': {'type': 'LANE_SURFACE_STREET', 'polyline': [[100., 210.], [170., 210.]], 'exit_lanes': []},
    }
    updated = deepcopy(original)
    updated['b']['vehicle_access'] = ['bus']
    missions = [{'vehicle_id': key, 'position_scenario_m': [100., y],
                 'goal_scenario_m': [150., y], 'heading_rad': 0.}
                for key, y in [('B', 200.), ('C', 210.)]]
    return {'mode': mode, 'variant': variant,
            'maps': {'original': original, 'updated': updated}, 'missions': missions}


class FleetRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.native_patch = patch('map_update.simulation_fleet._vehicle_class', return_value=Vehicle)
        self.native_patch.start()
        self.addCleanup(self.native_patch.stop)

    def fleet(self, mode='online', variant='full', *, states=None):
        settings = config(mode, variant)
        env = Env()
        fleet = FleetRuntime(env, {'states': states}, settings, settings['maps']['updated'])
        self.addCleanup(fleet.close)
        return env, fleet

    def test_native_recipient_moves_only_when_environment_steps_and_a_is_untouched(self):
        env, fleet = self.fleet()
        self.assertEqual(fleet.vehicles['B'].position, [0., 0.])
        self.assertEqual(fleet.report()['vehicles']['B']['position_city_m'], [100., 200.])
        self.assertGreater(fleet.actions['B'][1], 0.)
        fleet.before_step()
        self.assertEqual(fleet.vehicles['B'].position, [0., 0.])
        self.assertEqual(env.agent.action, [0., 0.])
        env.step([0., 0.])
        fleet.after_step()
        self.assertGreater(fleet.vehicles['B'].position[0], 0.)
        report = fleet.report()
        self.assertEqual(report['vehicles']['B']['actual_trace'][-1]['step'], 1)
        self.assertEqual(report['vehicles']['B']['motion_source'], 'metadrive_bullet_physics')
        self.assertFalse(report['vehicles']['B']['uses_recorded_trajectory'])
        self.assertEqual(env.agent.position, [0., -15.])

    def test_subscription_and_manual_publish_replan_without_teleporting(self):
        env, fleet = self.fleet()
        fleet.command({'action': 'subscription', 'vehicle_id': 'B', 'enabled': False})
        fleet.command({'action': 'publish'})
        report = fleet.report()
        self.assertNotEqual(report['vehicles']['B']['received_version'], report['vehicles']['C']['received_version'])
        self.assertEqual(fleet.vehicles['B'].position, [0., 0.])
        self.assertGreater(fleet.actions['B'][1], 0.)
        fleet.command({'action': 'subscription', 'vehicle_id': 'B', 'enabled': True})
        self.assertEqual(fleet.actions['B'], [0., -1.])
        self.assertEqual(fleet.report()['vehicles']['B']['current_route']['status'], 'no_route')
        self.assertEqual(fleet.vehicles['B'].position, [0., 0.])
        self.assertEqual(env.episode_step, 0)

    def test_receiver_export_contains_its_received_map_not_publisher_or_future_map(self):
        _, fleet = self.fleet()
        fleet.command({'action': 'subscription', 'vehicle_id': 'B', 'enabled': False})
        fleet.command({'action': 'publish'})
        inputs = fleet.report()['planning_inputs']
        self.assertEqual(inputs['B']['map_key'], 'original')
        self.assertEqual(inputs['C']['map_key'], 'updated')
        self.assertNotIn('map_features', inputs['B'])
        self.assertEqual(inputs['B']['vehicle_state']['position'], [100., 200.])
        self.assertEqual(inputs['B']['goal_scenario_m'], [150., 200.])

    def test_seek_rebuilds_physics_and_replays_delivery_at_recorded_step(self):
        env, fleet = self.fleet()
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env, session.fleet, session.length = env, fleet, 20
            session.frame = fleet.report
            session._stop_native_camera = lambda: None
            session._attach_annotations = lambda: None
            session.seek(3)
            fleet.command({'action': 'publish'})
            first = session.seek(6)
            pose = first['vehicles']['B']['position_city_m']
            version = first['vehicles']['B']['received_version']
            before = session.seek(2)
            self.assertGreater(before['vehicles']['B']['action'][1], 0.)
            replayed = session.seek(6)
            self.assertEqual(replayed['vehicles']['B']['position_city_m'], pose)
            self.assertEqual(replayed['vehicles']['B']['received_version'], version)
            self.assertEqual(replayed['vehicles']['B']['received_step'], 3)
            self.assertEqual(env.last_source_action, [0., 0.])

    def test_online_uses_causal_snapshot_and_never_overview(self):
        bundle = {'source': 'test-bound-source'}
        causal = {'objects': [{'id': 'observed', 'last_observed_timestamp_ns': 100,
                              'geometry_city': {'type': 'Point', 'coordinates': [130, 210]}}],
                  'source_timestamp_ns': 100}
        with patch('map_update.simulation_fleet.state_snapshot', return_value=causal) as snapshot, \
             patch('map_update.simulation_fleet.state_overview') as overview:
            _, fleet = self.fleet(states=bundle)
            overview.assert_not_called()
            snapshot.assert_called_once_with(bundle, 0)
            self.assertEqual(fleet.report()['planning_inputs']['B']['state_objects'][0]['id'], 'observed')
            self.assertFalse(fleet.report()['static_preview_is_planner_knowledge'])

    def test_offline_full_declares_prior_and_other_variants_do_not_load_state_overview(self):
        with patch('map_update.simulation_fleet.state_overview', return_value={'objects': []}) as overview:
            _, full = self.fleet('offline', 'full', states={'bound': True})
            overview.assert_called_once()
            self.assertTrue(full.report()['static_preview_is_planner_knowledge'])
            overview.reset_mock()
            _, old = self.fleet('offline', 'old', states={'bound': True})
            overview.assert_not_called()
            self.assertGreater(old.actions['B'][1], 0.)
            with self.assertRaisesRegex(ValueError, 'Offline prior'):
                old.command({'action': 'publish'})

    def test_nearby_guard_does_not_export_global_actor_positions(self):
        env, fleet = self.fleet()
        far = Vehicle(name='far-future-preview-obstacle', position=[1000., 0.], heading=0.)
        near = Vehicle(name='near-road-user', position=[15., 0.], heading=0.)
        env.engine.objects.update({far.id: far, near.id: near})
        fleet._update_actions()
        nearby = fleet.report()['planning_inputs']['B']['vehicle_state']['nearby_actors']
        self.assertIn('near-road-user', [a['id'] for a in nearby])
        self.assertNotIn('far-future-preview-obstacle', [a['id'] for a in nearby])

    def test_native_collision_latches_brake_and_remains_reported_after_contact(self):
        env, fleet = self.fleet()
        fleet.before_step()
        env.step([0., 0.])
        fleet.vehicles['C'].crash_human = True
        fleet.after_step()
        self.assertEqual(fleet.actions['C'], [0., -1.])
        fleet.vehicles['C'].crash_human = False
        env.step([0., 0.])
        fleet.after_step()
        row = fleet.report()['vehicles']['C']
        self.assertTrue(row['collision_stop'])
        self.assertEqual(row['collision_contact_steps'], [1])
        self.assertEqual(row['stopped_reason'], 'native_collision_stop')
        self.assertEqual(fleet.actions['C'], [0., -1.])

    def test_recipient_camera_tracks_actual_car_and_keeps_target_in_rear_or_top_view(self):
        env, fleet = self.fleet()
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env, session.fleet = env, fleet
            session.frame = lambda: session.camera
            camera = session.set_camera({'target': 'recipient-C', 'preset': 'follow'})
            self.assertEqual(camera['target'], 'recipient-C')
            self.assertEqual(session._camera_center(), ([0., 10.], 0.))
            fleet.vehicles['C'].position = [5., 10.]
            self.assertEqual(session._camera_center(), ([5., 10.], 0.))
            for preset in ('rear', 'top'):
                self.assertEqual(validated_camera(camera, {'preset': preset})['target'], 'recipient-C')
            session.fleet = None
            with self.assertRaisesRegex(ValueError, 'Launch a fleet'):
                session.set_camera({'target': 'recipient-B'})

    def test_rejects_mismatched_world_nonfinite_goals_and_unsafe_control(self):
        settings = config()
        wrong_world = {'other': {'type': 'LANE_SURFACE_STREET', 'polyline': [[0, 0], [1, 0]]}}
        with self.assertRaisesRegex(ValueError, 'updated source map'):
            validated_fleet(settings, wrong_world)
        settings['missions'][0]['goal_scenario_m'] = [float('nan'), 200.]
        with self.assertRaisesRegex(ValueError, 'finite'):
            validated_fleet(settings, settings['maps']['updated'])
        for action in ([0, float('nan')], [0, 2], [0], None):
            with self.subTest(action=action), self.assertRaises(ValueError):
                checked_action(action)


if __name__ == '__main__':
    unittest.main()
