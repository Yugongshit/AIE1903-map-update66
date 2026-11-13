"""Live controls, actual actor observations and native stepping boundaries."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from map_update.simulation_bridge import feature_sha256
from map_update.simulation_interactive import (InteractiveRuntime, IDS, actor_state,
    braking_actor, checked_action, validated_controls, validated_missions)
from map_update.simulation_session import SimulationSession, validated_camera


class Vehicle:
    LENGTH, WIDTH, HEIGHT = 4.515, 1.852, 1.2
    FRONT_WHEELBASE, REAR_WHEELBASE, max_steering = 1.1, 1.4, 60.

    def __init__(self, *, name, position, heading, **kwargs):
        self.id, self.position, self.heading_theta = name, list(position), heading
        self.velocity, self.speed, self.action = [0., 0.], 0., [0., 0.]
        self.after_calls = 0

    def set_velocity(self, value):
        self.velocity = list(value)
        self.speed = sum(v*v for v in value)**.5

    def before_step(self, action):
        self.action = list(action)

    def after_step(self):
        self.after_calls += 1


class Engine:
    def __init__(self, source):
        self.objects = {source.id: source}
        self.data_manager = SimpleNamespace(current_scenario={'id': 'lanes',
            'metadata': {'old_origin_in_current_coordinate': [-100., -200.]}})

    def spawn_object(self, cls, **kwargs):
        obj = cls(**kwargs)
        self.objects[obj.id] = obj
        return obj

    def clear_objects(self, identifiers, force_destroy=False):
        for identifier in identifiers:
            del self.objects[identifier]

    def get_objects(self):
        return self.objects


class Env:
    def __init__(self):
        self.agent = Vehicle(name='native-ego', position=[0., -15.], heading=0.)
        self.agent.set_velocity([4., 0.])
        self.engine, self.episode_step = Engine(self.agent), 0
        self.config = {'vehicle_config': {}, 'physics_world_step_size': .02, 'decision_repeat': 5}

    def step(self, action):
        self.agent.before_step(action)
        self.episode_step += 1
        for vehicle in self.engine.objects.values():
            vehicle.position[0] += max(0., vehicle.action[1])*.1

    def reset(self, seed):
        assert len(self.engine.objects) == 1
        self.episode_step = 0
        self.agent.position = [0., -15.]


def scene():
    features, missions = {}, []
    for i, identifier in enumerate(IDS[1:]):
        y = 200.+10*i
        features[identifier] = {'type': 'LANE_SURFACE_STREET',
            'polyline': [[100., y], [180., y]], 'exit_lanes': []}
        missions.append({'vehicle_id': identifier, 'position_scenario_m': [100., y],
            'goal_scenario_m': [160., y], 'heading_rad': 0., 'cruise_speed_m_s': 2.5})
    return features, {'missions': missions}


class InteractiveTests(unittest.TestCase):
    def setUp(self):
        self.native = patch('map_update.simulation_interactive._vehicle_class', return_value=Vehicle)
        self.native.start()
        self.addCleanup(self.native.stop)

    def runtime(self):
        features, config = scene()
        env = Env()
        runtime = InteractiveRuntime(env, config, features, map_sha256=feature_sha256(features))
        self.addCleanup(runtime.close)
        return env, runtime

    def test_six_actual_cars_ego_waits_and_five_independent_ai_controllers(self):
        env, runtime = self.runtime()
        self.assertEqual(set(runtime.vehicles), set(IDS))
        self.assertEqual(len({id(c) for c in runtime.controllers.values()}), 5)
        self.assertEqual(env.agent.speed, 0.)
        runtime.controls({})
        action = runtime.before_step()
        self.assertEqual(action, [0., -1.])
        self.assertTrue(all(runtime.actions[key][1] > 0 for key in IDS[1:]))
        # Commands choose wheel actions; they do not assign a new pose.
        self.assertEqual(runtime.vehicles['P1'].position, [0., 0.])
        env.step(action)
        runtime.after_step()
        self.assertGreater(runtime.vehicles['P1'].position[0], 0.)
        self.assertEqual(env.agent.position, [0., -15.])
        self.assertEqual(runtime.vehicles['P1'].after_calls, 1)

    def test_manual_and_external_controls_move_distinct_physical_cars_and_expire(self):
        env, runtime = self.runtime()
        runtime.controls({'ego': {'mode': 'external', 'action': [.1, .4]},
                          'P2': {'mode': 'manual', 'action': [-.2, .3]},
                          'P3': {'mode': 'external', 'action': [.3, .2]}})
        env.step(runtime.before_step())
        runtime.after_step()
        self.assertGreater(env.agent.position[0], 0)
        self.assertEqual(runtime.vehicles['P2'].action, [-.2, .3])
        self.assertEqual(runtime.vehicles['P3'].action, [.3, .2])
        runtime.controls({})
        runtime.before_step()
        self.assertEqual(runtime.actions['ego'], [0., -1.])
        self.assertEqual(runtime.actions['P2'], [0., -1.])
        self.assertEqual(runtime.actions['P3'], [0., -1.])
        self.assertEqual(runtime.reasons['P2'], 'waiting_for_action')

    def test_invalid_batch_is_atomic_and_main_ego_never_runs_builtin_ai(self):
        _, runtime = self.runtime()
        before = deepcopy(runtime.modes)
        with self.assertRaises(ValueError):
            runtime.controls({'P1': {'mode': 'manual'}, 'P2': {'action': [0, float('nan')]}})
        self.assertEqual(runtime.modes, before)
        with self.assertRaisesRegex(ValueError, 'P0 requires'):
            runtime.controls({'ego': {'mode': 'ai'}})

    def test_action_deadline_is_checked_at_execution_and_again_between_substeps(self):
        _, runtime = self.runtime()
        runtime.controls({'ego': {'action': [0., .5], 'expires_at_monotonic': 10.},
                          'P1': {'mode': 'manual', 'action': [.2, .4], 'expires_at_monotonic': 30.}})
        with patch('map_update.simulation_interactive.time.monotonic', return_value=20.):
            runtime.before_step()
        self.assertEqual(runtime.actions['ego'], [0., -1.])
        self.assertEqual(runtime.reasons['ego'], 'action_expired')
        self.assertEqual(runtime.actions['P1'], [.2, .4])
        with patch('map_update.simulation_interactive.time.monotonic', return_value=31.):
            runtime.before_step()
        self.assertEqual(runtime.actions['P1'], [0., -1.])
        self.assertEqual(runtime.reasons['P1'], 'action_expired')
        self.assertGreater(runtime.actions['P2'][1], 0.)

    def test_actual_six_car_observation_does_not_expose_recorded_future_tracks(self):
        env, runtime = self.runtime()
        env.engine.data_manager.current_scenario['tracks'] = {'secret-future-track': 'must never appear'}
        env.episode_step = 6000
        report = runtime.report()
        self.assertEqual(report['step'], 6000)
        self.assertEqual(report['sim_time_s'], 600.)
        self.assertFalse(report['recorded_moving_traffic'])
        self.assertFalse(report['source_recording_is_planning_input'])
        self.assertNotIn('tracks', report)
        self.assertEqual(report['vehicles']['P1']['position_city_m'], [100., 200.])
        self.assertEqual(report['vehicles']['ego']['label'], 'P0')

    def test_ai_brakes_for_live_car_ahead_without_freezing_other_lane(self):
        env, runtime = self.runtime()
        # The real ego is moved by the test environment into P1's corridor.
        env.agent.position = [3., 0.]
        runtime.controls({})
        runtime.before_step()
        self.assertEqual(runtime.actions['P1'][1], -1.)
        self.assertEqual(runtime.braking['P1']['vehicle_id'], 'ego')
        self.assertGreater(runtime.actions['P2'][1], 0.)
        env.agent.position = [30., 0.]
        runtime.before_step()
        self.assertGreater(runtime.actions['P1'][1], 0.)

    def test_tick_renders_once_per_batch_and_seek_only_permits_explicit_reset(self):
        env, runtime = self.runtime()
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env, session.interactive, session.length = env, runtime, 2
            renders = []
            session.frame = lambda: renders.append(env.episode_step) or runtime.report()
            session._stop_native_camera = lambda: None
            session._attach_annotations = lambda: None
            result = session.interactive_tick({'ego': {'action': [0., .2]}}, 3)
            self.assertEqual(result['step'], 3)  # beyond the source recording
            self.assertEqual(renders, [3])
            with self.assertRaisesRegex(ValueError, 'reset to zero'):
                session.seek(1)
            with self.assertRaisesRegex(ValueError, 'interactive_tick'):
                session.advance()
            session.seek(0)
            self.assertEqual(env.episode_step, 0)
            self.assertEqual(len(runtime.vehicles), 6)
            self.assertEqual(runtime.modes['ego'], 'external')

    def test_headless_tick_reports_new_physics_without_reusing_old_png_then_captures(self):
        env, runtime = self.runtime()
        scenario = env.engine.data_manager.current_scenario
        scenario['metadata'].update(ts=[0., .1], dataset='test')
        scenario['map_features'] = runtime.source_features
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env, session.interactive, session.length = env, runtime, 2
            session.source_features = runtime.source_features
            session.export = {'states': None, 'patches': [], 'manifest': {'provenance': {}}}
            session.persistent, session.map_hash = False, 'map-hash'
            session.update_handle = SimpleNamespace(comparison={}, refresh=lambda: None,
                visible=True, legend_node=None)
            session.sequence, session.frame_file, session._headless = 5, 'previous-image.png', False
            session._rendered_step = 0
            initial_position = list(env.agent.position)
            result = session.interactive_tick({'ego': {'action': [0., .3]}}, 3, capture=False)
            self.assertNotEqual(env.agent.position, initial_position)
            self.assertEqual(result['step'], 3)
            self.assertEqual(result['interactive']['vehicles']['ego']['position_city_m'],
                             actor_state(env.agent, runtime.metadata)['position_city_m'])
            self.assertIsNone(result['frame_file'])
            self.assertIsNone(result['rendered_step'])
            self.assertFalse(result['image_available'])
            self.assertEqual(result['sequence'], 5)
            self.assertIsNone(session.command({'command': 'status'})['frame_file'])
            renders = []
            runtime.refresh_visuals = lambda: None
            env.main_camera = SimpleNamespace(camera=SimpleNamespace(setPos=lambda *v: None,
                                                                     lookAt=lambda *v: None))
            env.engine.graphicsEngine = SimpleNamespace(renderFrame=lambda: renders.append(env.episode_step))
            env.capture = lambda filename: Path(filename).write_bytes(b'test-image')
            # Default capture=True remains compatible and produces a distinct
            # image for the current physical step after the headless updates.
            captured = session.interactive_tick({}, 1)
            self.assertTrue(captured['image_available'])
            self.assertEqual(captured['rendered_step'], 4)
            self.assertEqual(captured['sequence'], 6)
            self.assertEqual(captured['frame_file'], 'frame-00000006.png')
            self.assertTrue((Path(directory)/captured['frame_file']).is_file())
            self.assertTrue(renders)

    def test_capture_flag_is_validated_before_advancing_physics(self):
        env, runtime = self.runtime()
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env, session.interactive = env, runtime
            with self.assertRaisesRegex(ValueError, 'capture must be boolean'):
                session.interactive_tick({}, 1, capture='false')
            self.assertEqual(env.episode_step, 0)

    def test_camera_follows_live_p_car_in_rear_and_top_views(self):
        env, runtime = self.runtime()
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env, session.interactive = env, runtime
            session.frame = lambda: session.camera
            camera = session.set_camera({'preset': 'follow', 'target': 'P3'})
            self.assertEqual(session._camera_center(), ([0., 20.], 0.))
            self.assertEqual(validated_camera(camera, {'preset': 'rear'})['target'], 'P3')
            self.assertEqual(validated_camera(camera, {'preset': 'top'})['target'], 'P3')
            session.interactive = None
            with self.assertRaisesRegex(ValueError, 'Launch interactive'):
                session.set_camera({'target': 'P3'})

    def test_source_map_is_not_mutated_by_modes_or_live_controls(self):
        env, runtime = self.runtime()
        before = feature_sha256(runtime.source_features)
        runtime.controls({'P1': {'mode': 'manual', 'action': [.5, .3]}})
        env.step(runtime.before_step())
        runtime.after_step()
        runtime.controls({'P1': {'mode': 'ai'}})
        runtime.before_step()
        self.assertEqual(feature_sha256(runtime.source_features), before)

    def test_ai_advances_only_through_supplied_patrol_goals_then_stops(self):
        env, runtime = self.runtime()
        mission = runtime.missions['P1']
        mission['patrol_goals_scenario_m'] = [[120., 200.], [140., 200.]]
        mission['patrol_loop'] = False
        runtime.vehicles['P1'].position = [19., 0.]
        runtime.before_step()
        self.assertEqual(runtime.goal('P1'), [140., 200.])
        self.assertEqual(runtime.completed_goals['P1'], 1)
        runtime.vehicles['P1'].position = [39., 0.]
        runtime.before_step()
        self.assertEqual(runtime.actions['P1'][1], -1.)
        self.assertEqual(runtime.reasons['P1'], 'goal_reached')
        self.assertEqual(runtime.patrol_indices['P1'], 1)
        self.assertEqual(runtime.completed_goals['P1'], 2)
        runtime.before_step()
        self.assertEqual(runtime.completed_goals['P1'], 2)

    def test_live_clock_does_not_advance_frozen_source_observations(self):
        env, runtime = self.runtime()
        env.episode_step = 10000
        env.engine.data_manager.current_scenario['metadata']['ts'] = [0., .1]
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env, session.interactive = env, runtime
            session.source_features, session.export = runtime.source_features, {'states': {'future': 'unused'}}
            session._interactive_source_snapshot = {'step': 0, 'source_timestamp_ns': '100'}
            with patch('map_update.simulation_session.controller_state', return_value={
                    'recorded_time_s': None, 'simulation_time_s': 1000., 'map_state_snapshot': None}) as read:
                current = session._controller_state()
            self.assertIsNone(read.call_args.kwargs['state_source'])
            self.assertEqual(current['simulation_time_s'], 1000.)
            self.assertEqual(current['map_state_snapshot']['step'], 0)
            self.assertEqual(current['recorded_time_s'], 0.)

    def test_validation_rejects_nonfinite_controls_wrong_ids_and_bad_missions(self):
        for controls in ({'P6': {}}, {'P1': {'mode': 'replay'}}, {'P2': {'action': [0, True]}},
                         {'ego': {'action': [0, 2]}}, {'P1': {'action': [float('inf'), 0]}},
                         {'P1': {'expires_at_monotonic': float('nan')}}):
            with self.subTest(controls=controls), self.assertRaises(ValueError):
                validated_controls(controls)
        _, config = scene()
        config['missions'][0]['heading_rad'] = float('nan')
        with self.assertRaises(ValueError):
            validated_missions(config)


if __name__ == '__main__':
    unittest.main()
