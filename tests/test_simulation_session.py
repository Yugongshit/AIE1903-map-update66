"""Replay command, camera, and export binding contracts without a GPU."""
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import pickle
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from map_update.simulation_bridge import feature_sha256
from map_update.simulation_session import (SimulationSession, load_bound_export,
                                            orbit_pose, validated_camera, _suspended_rendering)


class SimulationSessionTests(unittest.TestCase):
    def test_rear_view_reverses_camera_without_changing_target(self):
        front = validated_camera({}, {'preset': 'follow', 'pitch': 25})
        rear = validated_camera(front, {'preset': 'rear'})
        a, target_a = orbit_pose(front, [10., 20.], math.pi/3)
        b, target_b = orbit_pose(rear, [10., 20.], math.pi/3)
        self.assertEqual(target_a, target_b)
        self.assertAlmostEqual(a[0]+b[0], 20.)
        self.assertAlmostEqual(a[1]+b[1], 40.)
        self.assertAlmostEqual(a[2], b[2])
        self.assertEqual(front['yaw'], 0.)

    def test_camera_rejects_invalid_bounds_without_mutating_configuration(self):
        current = validated_camera({}, {'preset': 'follow'})
        saved = deepcopy(current)
        for command in [{'distance': 0}, {'distance': float('inf')}, {'pitch': 90},
                        {'yaw': True}, {'pan': [0]}, {'pan': [float('nan'), 0]},
                        {'target': 'arbitrary_path'}, {'preset': 'unknown'}]:
            with self.subTest(command=command), self.assertRaises(ValueError):
                validated_camera(current, command)
        self.assertEqual(current, saved)
        self.assertEqual(validated_camera(current, {'yaw': -90})['yaw'], 270)

    def test_export_requires_bound_state_timeline_and_scenario(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = {'metadata': {'id': 'source-scene'}, 'map_features': {}}
            payload = pickle.dumps(source, protocol=4)
            (root/'scene.pkl').write_bytes(payload)
            (root/'dataset_summary.pkl').write_bytes(pickle.dumps({'scene.pkl': source['metadata']}))
            (root/'dataset_mapping.pkl').write_bytes(pickle.dumps({'scene.pkl': ''}))
            fixtures = {'map_patch_log.json': [], 'obstacle_manifest.json': {'mode': 'recorded'},
                        'state_source.json': {'scene_id': 'source-scene'}}
            for filename, value in fixtures.items():
                (root/filename).write_text(json.dumps(value))
            manifest = {'scenario_file': 'scene.pkl', 'exported_scenario_sha256': sha256(payload).hexdigest(),
                        'provenance': {'map_patch_log_sha256': feature_sha256(fixtures['map_patch_log.json']),
                                       'map_features_sha256': feature_sha256(source['map_features']),
                                       'obstacle_manifest_sha256': feature_sha256(fixtures['obstacle_manifest.json']),
                                       'state_source_sha256': feature_sha256(fixtures['state_source.json'])}}
            (root/'simulation_manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(load_bound_export(root)['states'], fixtures['state_source.json'])
            (root/'dataset_mapping.pkl').write_bytes(pickle.dumps({'scene.pkl': '../other-version'}))
            with self.assertRaisesRegex(ValueError, 'Database index'):
                load_bound_export(root)
            (root/'dataset_mapping.pkl').write_bytes(pickle.dumps({'scene.pkl': ''}))
            (root/'state_source.json').write_text(json.dumps({'scene_id': 'other-scene'}))
            with self.assertRaisesRegex(ValueError, 'states differs'):
                load_bound_export(root)
            (root/'scene.pkl').write_bytes(b'changed source')
            with self.assertRaisesRegex(ValueError, 'scenario differs'):
                load_bound_export(root)

    def test_fast_forward_restores_render_buffer_after_step_failure(self):
        class Window:
            active = True
            def isActive(self): return self.active
            def setActive(self, value): self.active = value
        window = Window()
        env = SimpleNamespace(engine=SimpleNamespace(win=window))
        with self.assertRaisesRegex(RuntimeError, 'step failed'):
            with _suspended_rendering(env):
                self.assertFalse(window.active)
                raise RuntimeError('step failed')
        self.assertTrue(window.active)

    def test_seek_backward_resets_then_replays_only_up_to_target(self):
        class Env:
            def __init__(self):
                self.episode_step = 30
                self.engine = SimpleNamespace(win=None)
                self.resets = 0
                self.actions = []
            def reset(self, seed):
                self.resets += 1
                self.episode_step = 0
            def step(self, action):
                self.actions.append(action)
                self.episode_step += 1
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            env = session.env = Env()
            session.length = 201
            calls = []
            session._stop_native_camera = lambda: calls.append('stop_camera')
            session._attach_annotations = lambda: calls.append('attach_at_'+str(env.episode_step))
            session._refresh_state = lambda: calls.append('refresh_at_'+str(env.episode_step))
            def frame():
                session._refresh_state()
                return {'step': env.episode_step}
            session.frame = frame
            self.assertEqual(session.seek(5), {'step': 5})
            self.assertEqual(env.resets, 1)
            self.assertEqual(env.actions, [[0., 0.]]*5)
            self.assertIn('attach_at_5', calls)
            self.assertEqual(session.seek(30), {'step': 30})
            self.assertEqual(env.resets, 1)
            self.assertEqual(len(env.actions), 30)
            self.assertIn('refresh_at_30', calls)

    def test_seek_rejects_future_and_fractional_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env = SimpleNamespace(episode_step=0)
            session.length = 201
            for step in [-1, 201, 3.5, True, None]:
                with self.subTest(step=step), self.assertRaises(ValueError):
                    session.seek(step)
            for count in [0, 21, 1.5, True]:
                with self.subTest(count=count), self.assertRaises(ValueError):
                    session.advance(count)

    def test_backward_seek_selection_reports_only_current_observed_event(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env = SimpleNamespace(episode_step=5)
            session.export = {'states': {}}
            session.source_features = {}
            session.selected_event_id = 'later-event'
            handle = SimpleNamespace(selected_event_id='early-event', panel_node=None, visible=True,
                                     refresh=lambda snapshot: None, set_visible=lambda visible: None)
            session.state_handle = handle
            with patch('map_update.simulation_session.controller_state',
                       return_value={'map_state_snapshot': {'step': 5}}):
                session._refresh_state()
            self.assertEqual(session.selected_event_id, 'early-event')

    def test_status_restores_map_center_to_source_coordinates_once(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            metadata = {'ts': [0., .5], 'old_origin_in_current_coordinate': [-100., -200.]}
            session.env = SimpleNamespace(episode_step=1, engine=SimpleNamespace(
                data_manager=SimpleNamespace(current_scenario={'metadata': metadata})))
            session.map_center = [3., 4.]
            session.length, session.persistent, session.map_hash = 2, False, 'example-map'
            session.export = {'manifest': {'provenance': {}}, 'patches': []}
            session.update_handle = SimpleNamespace(comparison={})
            session.current = {'map_state_snapshot': None, 'recorded_time_s': .5,
                               'scenario_id': 'example', 'dataset': 'nuscenes',
                               'position_scenario_m': [100., 200.], 'map_features_runtime': {}}
            self.assertEqual(session.status()['map_center_scenario_m'], [103., 204.])
            self.assertEqual(session.map_center, [3., 4.])

    def test_fast_forward_suspends_auxiliary_buffers_and_restores_each_state(self):
        class Window:
            def __init__(self, active): self.active = active
            def isActive(self): return self.active
            def setActive(self, value): self.active = value
        windows = [Window(True), Window(True), Window(False)]
        env = SimpleNamespace(engine=SimpleNamespace(win=windows[0],
              graphicsEngine=SimpleNamespace(getWindows=lambda: windows)))
        with self.assertRaises(RuntimeError):
            with _suspended_rendering(env):
                self.assertEqual([window.active for window in windows], [False, False, False])
                raise RuntimeError('physics step failed')
        self.assertEqual([window.active for window in windows], [True, True, False])

    def test_camera_only_refresh_reuses_state_but_new_step_and_selection_update(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env = SimpleNamespace(episode_step=10)
            session.export, session.source_features = {'states': {}}, {}
            redraws = []
            handle = SimpleNamespace(selected_event_id='event-a', panel_node=None, visible=True,
                                     refresh=lambda snapshot: redraws.append(snapshot),
                                     set_visible=lambda visible: None)
            session.state_handle = handle
            with patch('map_update.simulation_session.controller_state',
                       return_value={'map_state_snapshot': {'step': 10}}) as build:
                session._refresh_state()
                session.camera['yaw'] = 180
                session._refresh_state()
                self.assertEqual(build.call_count, 1)
                self.assertEqual(len(redraws), 1)
                handle.selected_event_id = 'event-b'
                session._refresh_state()
                self.assertEqual(build.call_count, 1)
                self.assertEqual(len(redraws), 2)
                session.env.episode_step = 11
                session._refresh_state()
                self.assertEqual(build.call_count, 2)
                self.assertEqual(len(redraws), 3)

    def test_full_scene_annotations_stay_cached_while_current_observation_clock_advances(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env = SimpleNamespace(episode_step=0)
            session.export, session.source_features = {'states': {}}, {}
            session.overview = {'scope': 'full_scene_overview', 'frozen': True, 'objects': ['late-object']}
            redraws = []
            handle = SimpleNamespace(selected_event_id='late-event', panel_node=None, visible=True,
                                     refresh=lambda snapshot: redraws.append(snapshot),
                                     set_visible=lambda visible: None)
            session.state_handle = handle
            with patch('map_update.simulation_session.controller_state', side_effect=lambda *a, **k:
                       {'map_state_snapshot': {'step': session.env.episode_step}}) as build:
                session._refresh_state()
                session.env.episode_step = 137
                session._refresh_state()
                self.assertEqual(build.call_count, 2)
                self.assertEqual(session.current['map_state_snapshot']['step'], 137)
                self.assertEqual(redraws, [session.overview])

    def test_scene_overview_event_can_be_focused_before_its_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SimulationSession(directory)
            session.env = SimpleNamespace(episode_step=0)
            session.state_handle = SimpleNamespace(prepared={'events': [{'event_id': 'late-event'}]},
                selected_event_id=None, selected_objects=lambda: [{'runtime_paths': [[[5., 6.], [7., 8.]]]}])
            session.frame = lambda: {'step': 0}
            self.assertEqual(session.select(event_id='late-event'), {'step': 0})
            self.assertEqual(session.selected_event_id, 'late-event')
            self.assertEqual(session.selection_center, [6., 7.])
            self.assertEqual(session.camera['target'], 'selection')


if __name__ == '__main__':
    unittest.main()
