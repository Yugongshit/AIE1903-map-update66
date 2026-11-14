"""Interactive, serial MetaDrive replay sessions for the local review platform.

Every frame is rendered by Panda3D/MetaDrive. Static scene changes are presented
as a fixed full-scene preview; current observation snapshots remain time-bound.
Seeking backwards reconstructs the episode and restores the same preview.
The platform owns play/pause scheduling; this module advances only on commands.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

from .simulation_bridge import feature_sha256, json_value, load_scenario
from .simulation_runtime import (controller_state, environment_config, map_xy_digest,
                                 rendering_adapters, scenario_map_features, scenario_position)


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(name + ' must be a finite number')
    return float(value)


TRAFFIC_CAMERA_TARGETS = {f'P{i}' for i in range(1,13)}


def validated_camera(current, command):
    """Validate a complete orbit camera without touching simulation state."""
    result = deepcopy(current)
    preset = command.get('preset')
    presets = {
        'follow': {'target': 'ego', 'yaw': 0., 'pitch': 30., 'distance': 24., 'pan': [0., 0.]},
        'rear': {'target': 'ego', 'yaw': 180., 'pitch': 25., 'distance': 24., 'pan': [0., 0.]},
        'top': {'target': 'ego', 'yaw': 0., 'pitch': 88., 'distance': 65., 'pan': [0., 0.]},
        'overview': {'target': 'map', 'yaw': 0., 'pitch': 75., 'distance': 180., 'pan': [0., 0.]},
    }
    if preset == 'map':
        preset = 'overview'
    if preset is not None:
        if preset not in presets:
            raise ValueError('Unknown camera preset')
        result.update(presets[preset])
        if preset in {'follow', 'rear', 'top'} and current.get('target') in TRAFFIC_CAMERA_TARGETS:
            result['target'] = current['target']
    for name in ('yaw', 'pitch', 'distance'):
        if name in command:
            result[name] = _number(command[name], name)
    if 'target' in command:
        if command['target'] not in ({'ego', 'map', 'selection'} | TRAFFIC_CAMERA_TARGETS):
            raise ValueError('Camera target must be ego, map, selection or P1..P12')
        result['target'] = command['target']
    if 'pan' in command:
        value = command['pan']
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError('Camera pan requires two metric coordinates')
        result['pan'] = [_number(item, 'pan') for item in value]
    if not 3 <= result['distance'] <= 2000 or not 3 <= result['pitch'] <= 89:
        raise ValueError('Camera distance must be 3..2000 m and pitch 3..89 degrees')
    if any(abs(item) > 5000 for item in result['pan']):
        raise ValueError('Camera pan exceeds scene review bounds')
    result['yaw'] %= 360.
    return result


def orbit_pose(camera, center, ego_heading_rad=0.):
    """Camera yaw zero is behind ego; positive yaw turns freely around it."""
    x, y = [float(center[i]) + camera['pan'][i] for i in (0, 1)]
    theta = ego_heading_rad + math.pi + math.radians(camera['yaw'])
    pitch = math.radians(camera['pitch'])
    horizontal = camera['distance'] * math.cos(pitch)
    position = [x + horizontal * math.cos(theta), y + horizontal * math.sin(theta),
                1.0 + camera['distance'] * math.sin(pitch)]
    return position, [x, y, 1.0]


def load_bound_export(database_path):
    """Read and bind the trusted export before importing the simulator."""
    root = Path(database_path).resolve()
    manifest = json.loads((root / 'simulation_manifest.json').read_text(encoding='utf-8'))
    filename = manifest.get('scenario_file')
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError('Invalid exported scenario filename')
    if sha256((root / filename).read_bytes()).hexdigest() != manifest.get('exported_scenario_sha256'):
        raise ValueError('Exported scenario differs from its bound manifest')
    summary = load_scenario(root / 'dataset_summary.pkl')
    mapping = load_scenario(root / 'dataset_mapping.pkl')
    if not isinstance(summary, dict) or set(summary) != {filename} or mapping != {filename: ''}:
        raise ValueError('Database index must select the single bound scenario in this export directory')
    source = load_scenario(root / filename)
    # Legacy converter statistics contain NaN for undefined measurements. These
    # must agree between the index and source, but are not map geometry values.
    metadata_text = lambda value: json.dumps(json_value(value), sort_keys=True, allow_nan=True)
    if metadata_text(summary[filename]) != metadata_text(source.get('metadata')):
        raise ValueError('Database summary differs from the bound scenario metadata')
    provenance = manifest.get('provenance', {})
    if feature_sha256(source.get('map_features')) != provenance.get('map_features_sha256'):
        raise ValueError('Source map differs from the export map binding')
    result = {'root': root, 'manifest': manifest,
              'expected_map_xy_sha256': map_xy_digest(source['map_features'])}
    for name, filename, digest, required in [
        ('patches', 'map_patch_log.json', 'map_patch_log_sha256', True),
        ('obstacles', 'obstacle_manifest.json', 'obstacle_manifest_sha256', True),
        ('states', 'state_source.json', 'state_source_sha256', False),
    ]:
        expected = provenance.get(digest)
        if not expected:
            if required:
                raise ValueError('Export is missing bound ' + name)
            result[name] = None
            continue
        value = json.loads((root / filename).read_text(encoding='utf-8'))
        if feature_sha256(value) != expected:
            raise ValueError('Export ' + name + ' differs from its bound manifest')
        result[name] = value
    return result


@contextmanager
def _suspended_rendering(env):
    """Fast-forward physics and recorded policies without rendering each step."""
    graphics = getattr(env.engine, 'graphicsEngine', None)
    if graphics is not None and hasattr(graphics, 'getWindows'):
        windows = list(graphics.getWindows())
    else:
        window = getattr(env.engine, 'win', None)
        windows = [window] if window is not None else []
    # PBR creates intermediate/shadow buffers separate from the main output.
    # Disabling only engine.win still renders those buffers every physics step.
    previous = [(window, window.isActive()) for window in windows]
    for window, _ in previous:
        window.setActive(False)
    try:
        yield
    finally:
        for window, active in previous:
            window.setActive(active)


class SimulationSession:
    """One source scenario, one real engine, and serialized preview commands."""
    def __init__(self, output_dir, *, width=1000, height=650):
        if any(type(value) is not int or not 320 <= value <= 1920 for value in (width, height)):
            raise ValueError('Frame dimensions must be integers between 320 and 1920')
        self.output_dir = Path(output_dir).resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.width, self.height = width, height
        self.env = None
        self.interactive = None
        self._interactive_source_snapshot = None
        self.resources = ExitStack()
        self.annotations = ExitStack()
        self.export = None
        self.state_handle = self.update_handle = None
        self.current = None
        self.overview = None
        self.overview_hash = None
        self.sequence = 0
        self.frame_file = None
        self._headless = True
        self._rendered_step = None
        self.camera = validated_camera({}, {'preset': 'follow'})
        self.selection_center = None
        self.show_structure = self.show_states = True
        self.selected_feature_id = self.selected_event_id = None
        self.map_center = [0., 0.]
        self._state_step = None
        self._annotation_selection = None

    def open(self, database_path, *, step=0, interactive=None):
        bound = load_bound_export(database_path)
        if type(step) is not int or step < 0:
            raise ValueError('Replay step must be a nonnegative integer')
        if interactive is not None:
            from .simulation_interactive import validated_missions
            validated_missions(interactive)
            if step != 0:
                raise ValueError('Live interaction starts at step zero; recorded seeking is not available')
        same_scene = self.export is not None and all(
            self.export['manifest'].get(key) == bound['manifest'].get(key)
            for key in ('dataset', 'scene_id', 'map'))
        saved_camera = deepcopy(self.camera) if same_scene else None
        saved_selection = None
        if same_scene and self.selection_center is not None and self.current is not None:
            origin = self.current['runtime_origin_in_scenario_m']
            saved_selection = [self.selection_center[i] + origin[i] for i in (0, 1)]
        self.close()
        self.resources, self.annotations = ExitStack(), ExitStack()
        self.export = bound
        try:
            import numpy
            from metadrive.engine.core import engine_core
            from metadrive.envs.scenario_env import ScenarioEnv
            import metadrive.envs.scenario_env as scenario_env_module
            from metadrive.policy.env_input_policy import EnvInputPolicy
            from metadrive.policy.replay_policy import ReplayEgoCarPolicy
            from metadrive.obs.state_obs import LidarStateObservation
            from metadrive.scenario.utils import get_number_of_scenarios
            from panda3d.core import ConfigVariableInt, loadPrcFileData
            count = get_number_of_scenarios(str(bound['root']))
            if count != 1:
                raise ValueError('Interactive review requires one scene per exported database')
            from .simulation_bridge import _database_scenario
            indexed_path, _ = _database_scenario(bound['root'], bound['manifest']['scene_id'])
            if indexed_path.resolve() != (bound['root'] / bound['manifest']['scenario_file']).resolve():
                raise ValueError('Database index selects a different scenario from the verified export')
            self.resources.enter_context(rendering_adapters(engine_core, numpy, offscreen=True))
            compression = ConfigVariableInt('png-compression-level')
            self.resources.callback(compression.setValue, compression.getValue())
            compression.setValue(1)  # Same lossless pixels; less CPU on localhost.
            if sys.platform.startswith('linux'):
                loadPrcFileData('', 'load-display p3headlessgl')
            config = environment_config(bound['root'], mode='replay', render='3D', steps=100000,
                                        num_scenarios=count, input_policy=EnvInputPolicy,
                                        replay_policy=ReplayEgoCarPolicy)
            config.update(use_render=False, image_observation=True, show_interface=False,
                          multi_thread_render=False, agent_observation=LidarStateObservation,
                          window_size=(self.width, self.height),
                          sensors={'main_camera': ('MainCamera', self.width, self.height)})
            config['vehicle_config']['image_source'] = 'main_camera'
            env_class = ScenarioEnv
            if interactive is not None:
                from .simulation_interactive import native_environment
                env_class, actual_observation = native_environment(ScenarioEnv)
                config.update(agent_policy=EnvInputPolicy, manual_control=False, no_traffic=True,
                              no_light=True, horizon=None, agent_observation=actual_observation)
                config['vehicle_config'].update(navigation_module=None, overtake_stat=False)
            self.persistent = bound['obstacles'].get('mode') == 'persistent'
            if self.persistent:
                from .simulation_obstacle_runtime import persistent_obstacle_adapter
                config.update(no_traffic=False, static_traffic_object=True)
                self.resources.enter_context(persistent_obstacle_adapter(
                    scenario_env_module, bound['obstacles'], preload_all=True,
                    no_moving_traffic=interactive is not None))
            self.env = env_class(config)
            self.env.reset(seed=0)
            self.length = int(self.env.engine.data_manager.current_scenario_length)
            if step >= self.length:
                raise ValueError('Replay step exceeds the recorded scene')
            scenario = self.env.engine.data_manager.current_scenario
            metadata = scenario['metadata']
            manifest = bound['manifest']
            if (scenario['id'] != manifest.get('scene_id') or metadata.get('dataset') != manifest.get('dataset')
                    or metadata.get('map') != manifest.get('map')):
                raise ValueError('Loaded scene identity differs from the selected export')
            if bound['states'] is not None and metadata.get('map_update_bridge', {}).get('state_source_sha256') != manifest['provenance']['state_source_sha256']:
                raise ValueError('Loaded scenario is not bound to its state observations')
            self.source_features = scenario_map_features(scenario['map_features'], metadata)
            self.map_hash = map_xy_digest(self.source_features)
            if self.map_hash != bound['expected_map_xy_sha256']:
                raise ValueError('Actual engine map differs from the verified exported map')
            if metadata.get('map_update_bridge', {}).get('map_features_sha256') != manifest['provenance']['map_features_sha256']:
                raise ValueError('Loaded scenario is not bound to this map version')
            points = [point for feature in scenario['map_features'].values()
                      for point in feature.get('polygon', feature.get('polyline', []))]
            if points:
                self.map_center = [(min(float(p[i]) for p in points)+max(float(p[i]) for p in points))/2 for i in (0, 1)]
            self.camera = saved_camera or validated_camera({}, {'preset': 'follow'})
            if interactive is None and self.camera['target'] in TRAFFIC_CAMERA_TARGETS:
                self.camera['target'] = 'ego'
            offset = metadata.get('old_origin_in_current_coordinate', [0, 0])
            self.selection_center = [saved_selection[i] + float(offset[i]) for i in (0, 1)] if saved_selection else None
            if self.camera['target'] == 'selection' and self.selection_center is None:
                self.camera['target'] = 'ego'
            self.selected_feature_id = self.selected_event_id = None
            self._stop_native_camera()
            if interactive is not None:
                from .simulation_interactive import InteractiveRuntime
                self.interactive = InteractiveRuntime(self.env, interactive, self.source_features,
                    map_sha256=manifest['provenance']['map_features_sha256'])
                if self.camera['target'] in TRAFFIC_CAMERA_TARGETS and self.camera['target'] not in self.interactive.vehicles:
                    self.camera['target'] = 'ego'
                if bound['states'] is not None:
                    from .simulation_states import state_snapshot
                    self._interactive_source_snapshot = state_snapshot(bound['states'], 0, source_to_runtime_offset=offset)
            with _suspended_rendering(self.env):
                for _ in range(step):
                    self.env.step([0., 0.])
            self._attach_annotations()
            return self.frame()
        except BaseException:
            self.close()
            raise

    def _require(self):
        if self.env is None:
            raise ValueError('Choose a scene before controlling playback')

    def _stop_native_camera(self):
        camera = self.env.main_camera
        camera.stop_track(bird_view_on_current_position=False)
        for name in (camera.CHASE_TASK_NAME, camera.TOP_DOWN_TASK_NAME):
            self.env.engine.task_manager.remove(name)
        camera.run_task = False

    def _refresh_state(self):
        changed = self.current is None or self._state_step != self.env.episode_step
        if changed:
            self.current = self._controller_state()
            self._state_step = self.env.episode_step
        if self.state_handle is not None:
            selection = self.state_handle.selected_event_id
            if (changed and self.overview is None) or self._annotation_selection != selection:
                self.state_handle.refresh(self.overview if self.overview is not None else self.current['map_state_snapshot'])
                self._annotation_selection = self.state_handle.selected_event_id
            self.selected_event_id = self.state_handle.selected_event_id
            if self.state_handle.visible != self.show_states:
                self.state_handle.set_visible(self.show_states)
            if self.state_handle.panel_node is not None:
                self.state_handle.panel_node.hide()
        if self.update_handle is not None:
            if changed:
                self.update_handle.refresh()
            if self.update_handle.visible != self.show_structure:
                self.update_handle.set_visible(self.show_structure)
            if self.update_handle.legend_node is not None:
                self.update_handle.legend_node.hide()

    def _controller_state(self):
        current = controller_state(self.env, source_map_features=self.source_features,
                                   state_source=None if self.interactive is not None else self.export['states'])
        if self.interactive is not None:
            current['map_state_snapshot'] = self._interactive_source_snapshot
            current['recorded_time_s'] = float(self.env.engine.data_manager.current_scenario['metadata']['ts'][0])
        return current

    def _attach_annotations(self):
        from .simulation_environment import attach_source_environment
        from .simulation_visuals import attach_update_overlay
        self.annotations.close()
        self._needs_render_warmup = True
        self.annotations = ExitStack()
        self.update_handle = self.state_handle = None
        surface, self.environment_report = attach_source_environment(self.env)
        self.annotations.callback(surface.close)
        manifest = self.export['manifest']
        provenance = manifest['provenance']
        self.update_handle, _ = attach_update_overlay(
            self.env, self.export['patches'], synthetic=bool(provenance.get('synthetic')),
            update_source=provenance.get('update_source'),
            reference_operations=manifest.get('summary', {}).get('reference_structural_operations'))
        self.annotations.callback(self.update_handle.close)
        self.current = self._controller_state()
        self._state_step = self.env.episode_step
        self.overview = None
        if self.export['states'] is not None:
            from .simulation_states import state_overview
            metadata = self.env.engine.data_manager.current_scenario['metadata']
            self.overview = state_overview(self.export['states'],
                source_to_runtime_offset=metadata.get('old_origin_in_current_coordinate', [0, 0]))
        self.overview_hash = feature_sha256(self.overview) if self.overview is not None else None
        if self.overview is not None:
            from .simulation_state_visuals import attach_state_overlay
            self.state_handle, _ = attach_state_overlay(self.env, self.overview)
            self.annotations.callback(self.state_handle.close)
            self._annotation_selection = self.state_handle.selected_event_id
        if self.selected_feature_id is not None:
            self._select_feature(self.selected_feature_id)
        if self.selected_event_id is not None and self.state_handle is not None:
            ids = [row['event_id'] for row in self.state_handle.prepared['events']]
            if self.selected_event_id in ids:
                self.state_handle.selected_event_id = self.selected_event_id
        self._refresh_state()

    def seek(self, step):
        self._require()
        if self.interactive is not None:
            if type(step) is not int or step != 0:
                raise ValueError('Live interaction supports reset to zero only; player input cannot be replay-seeked')
            self.annotations.close()
            self.update_handle = self.state_handle = None
            self._state_step = self._annotation_selection = None
            self.interactive.close()
            self.env.reset(seed=0)
            self._stop_native_camera()
            self.interactive.reset()
            self._attach_annotations()
            return self.frame()
        if type(step) is not int or not 0 <= step < self.length:
            raise ValueError('Replay step must lie within the recorded scene')
        if step < self.env.episode_step:
            # Replay actors rewind; full-scene static preview is restored at reset.
            self.annotations.close()
            self.update_handle = self.state_handle = None
            self._state_step = self._annotation_selection = None
            self.env.reset(seed=0)
            self._stop_native_camera()
            with _suspended_rendering(self.env):
                for _ in range(step):
                    self.env.step([0., 0.])
            self._attach_annotations()
        else:
            with _suspended_rendering(self.env):
                while self.env.episode_step < step:
                    self.env.step([0., 0.])
        return self.frame()

    def advance(self, steps=1):
        self._require()
        if self.interactive is not None:
            raise ValueError('Live interaction advances through interactive_tick with renewed controls')
        if type(steps) is not int or not 1 <= steps <= 20:
            raise ValueError('Advance requires 1..20 recorded steps')
        return self.seek(min(self.length-1, self.env.episode_step+steps))

    def set_camera(self, command):
        self._require()
        camera = validated_camera(self.camera, command)
        if camera['target'] == 'selection' and self.selection_center is None:
            raise ValueError('Select a visible change before focusing it')
        if camera['target'] in TRAFFIC_CAMERA_TARGETS and self.interactive is None:
            raise ValueError('Launch interactive mode before following P1..P12')
        if camera['target'] in TRAFFIC_CAMERA_TARGETS and camera['target'] not in self.interactive.vehicles:
            raise ValueError('Camera vehicle is not present in this experiment')
        self.camera = camera
        return self.frame()

    def interactive_tick(self, controls, steps=1, *, capture=True):
        self._require()
        if self.interactive is None:
            raise ValueError('Launch interactive mode before sending controls')
        if type(steps) is not int or not 1 <= steps <= 3:
            raise ValueError('Interactive ticks advance 1..3 physics decisions')
        if type(capture) is not bool:
            raise ValueError('Interactive capture must be boolean')
        self.interactive.controls(controls)
        # The previous PNG remains immutable in the broker cache, but must not
        # be advertised as an image of these newly advanced physical poses.
        self._headless = True
        with _suspended_rendering(self.env):
            for _ in range(steps):
                action = self.interactive.before_step()
                self.env.step(action)
                self.interactive.after_step()
        if capture:
            return self.frame()
        self._refresh_state()
        return self.status()

    def _camera_center(self):
        target = self.camera['target']
        if target in TRAFFIC_CAMERA_TARGETS:
            if self.interactive is None:
                raise ValueError('Interactive car camera requires live interaction')
            vehicle = self.interactive.vehicles[target]
            return list(vehicle.position[:2]), float(vehicle.heading_theta)
        if target == 'ego':
            return list(self.env.agent.position[:2]), float(self.env.agent.heading_theta)
        return (self.map_center if target == 'map' else self.selection_center), 0.

    def _select_feature(self, feature_id):
        items = self.update_handle.prepared['items']
        indices = [i for i, row in enumerate(items) if row['feature_id'] == feature_id]
        if not indices:
            raise ValueError('Road change is not present in this loaded map version')
        self.update_handle.select_change(indices[0] - self.update_handle.selected_index)
        self.selected_feature_id = feature_id
        self.selection_center = list(self.update_handle.comparison['viewport']['center_xy'])

    def select(self, *, feature_id=None, event_id=None, focus=True):
        self._require()
        if (feature_id is None) == (event_id is None):
            raise ValueError('Select one road change or one state event')
        if feature_id is not None:
            self._select_feature(feature_id)
        else:
            if self.state_handle is None:
                raise ValueError('This scene has no attached-state timeline')
            ids = [row['event_id'] for row in self.state_handle.prepared['events']]
            if event_id not in ids:
                raise ValueError('State event is not part of this scene overview')
            self.state_handle.selected_event_id = event_id
            self.selected_event_id = event_id
            points = [p for row in self.state_handle.selected_objects() for path in row['runtime_paths'] for p in path]
            if not points:
                raise ValueError('Selected event has no source geometry at this frame')
            self.selection_center = [(min(p[i] for p in points)+max(p[i] for p in points))/2 for i in (0, 1)]
        if focus:
            self.camera.update(target='selection', pan=[0., 0.], pitch=65., distance=35.)
        return self.frame()

    def overlays(self, *, structure=None, state=None):
        self._require()
        for name, value in [('structure', structure), ('state', state)]:
            if value is not None and type(value) is not bool:
                raise ValueError(name + ' visibility must be boolean')
        if structure is not None:
            self.show_structure = structure
        if state is not None:
            self.show_states = state
        return self.frame()

    def frame(self):
        self._require()
        self._headless = True
        self._refresh_state()
        if self.interactive is not None:
            self.interactive.refresh_visuals()
        center, heading = self._camera_center()
        position, look_at = orbit_pose(self.camera, center, heading)
        self.env.main_camera.camera.setPos(*position)
        self.env.main_camera.camera.lookAt(*look_at)
        self.env.engine.graphicsEngine.renderFrame()
        # Prime a newly loaded/reset scene once. Steady playback and camera
        # movement need one complete render, not two copies of the same step.
        if getattr(self, '_needs_render_warmup', True):
            self.env.engine.graphicsEngine.renderFrame()
            self._needs_render_warmup = False
        self.sequence += 1
        self.frame_file = 'frame-{:08d}.png'.format(self.sequence)
        destination = self.output_dir / self.frame_file
        self.env.capture(str(destination))
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError('MetaDrive did not produce a rendered frame')
        self._headless = False
        self._rendered_step = int(self.env.episode_step)
        # Keep a small immutable window for in-flight browser frame requests.
        for path in sorted(self.output_dir.glob('frame-*.png'))[:-8]:
            path.unlink()
        return self.status()

    def status(self):
        self._require()
        current = self.current
        snapshot = current['map_state_snapshot']
        obstacle = None
        if self.persistent:
            from .simulation_obstacle_runtime import runtime_obstacle_report
            obstacle = runtime_obstacle_report(self.env)
        return {
            'renderer': 'MetaDrive/Panda3D', 'live_engine': True,
            'frame_file': None if self._headless else self.frame_file,
            'image_available': not self._headless and self.frame_file is not None,
            'rendered_step': None if self._headless else self._rendered_step,
            'sequence': self.sequence, 'step': self.env.episode_step, 'length': self.length,
            'last_step': self.length-1,
            'time_s': current['simulation_time_s'] if self.interactive is not None else current['recorded_time_s'],
            'recorded_step': 0 if self.interactive is not None else self.env.episode_step,
            'duration_s': float(self.env.engine.data_manager.current_scenario['metadata']['ts'][-1]),
            'at_end': False if self.interactive is not None else self.env.episode_step == self.length-1,
            'ego_replay': self.interactive is None, 'manual_control': False,
            'scene_id': current['scenario_id'], 'dataset': current['dataset'],
            'position_scenario_m': current['position_scenario_m'], 'camera': deepcopy(self.camera),
            'map_center_scenario_m': scenario_position(
                self.map_center, self.env.engine.data_manager.current_scenario['metadata']),
            'loaded_map_xy_sha256': self.map_hash, 'map_feature_count': len(current['map_features_runtime']),
            'map_features_sha256': self.export['manifest']['provenance'].get('map_features_sha256'),
            'state_source_sha256': self.export['manifest']['provenance'].get('state_source_sha256'),
            'structural_changes': len(self.export['patches']),
            'structure_visible': self.show_structure, 'states_visible': self.show_states,
            'selected_feature_id': self.selected_feature_id, 'selected_event_id': self.selected_event_id,
            'state_summary': snapshot['summary'] if snapshot else None,
            'display_scope': 'full_scene_overview',
            'display_state_summary': self.overview['summary'] if self.overview is not None else None,
            'display_state_sha256': self.overview_hash,
            'state_events': self.state_handle.prepared['events'] if self.state_handle is not None else [],
            'state_objects': self.state_handle.prepared['objects'] if self.state_handle is not None else [],
            'source_timestamp_ns': snapshot['source_timestamp_ns'] if snapshot else None,
            'observation_cutoff_ns': snapshot['observation_cutoff_ns'] if snapshot else None,
            'persistent_obstacle_count': obstacle['active_count'] if obstacle else 0,
            'persistent_obstacle_ids': sorted(obstacle['objects']) if obstacle else [],
            'persistent_obstacles_runtime': obstacle['objects'] if obstacle else {},
            'obstacle_display_scope': obstacle['display_scope'] if obstacle else 'no_static_objects',
            'preview_future_objects_visible': obstacle['future_objects_visible_early'] if obstacle else False,
            'missing_expected_obstacles': obstacle['missing_expected_ids'] if obstacle else [],
            'comparison': self.update_handle.comparison,
            'interactive': self.interactive.report() if self.interactive is not None else None,
            'export_verified': True,
        }

    def close(self):
        try:
            self.annotations.close()
        finally:
            try:
                try:
                    if self.interactive is not None:
                        self.interactive.close()
                finally:
                    self.interactive = None
                    self._interactive_source_snapshot = None
                    if self.env is not None:
                        self.env.close()
            finally:
                self.env = None
                self.frame_file = None
                self._headless = True
                self._rendered_step = None
                self.current = None
                self.overview = None
                self.overview_hash = None
                self._state_step = self._annotation_selection = None
                self.state_handle = self.update_handle = None
                self.resources.close()

    def command(self, message):
        if not isinstance(message, dict):
            raise ValueError('Session command must be an object')
        name = message.get('command')
        if name == 'open':
            return self.open(message['database_path'], step=message.get('step', 0), interactive=message.get('interactive'))
        if name == 'interactive_tick':
            return self.interactive_tick(message.get('controls', {}), message.get('steps', 1),
                                         capture=message.get('capture', True))
        if name == 'interactive_mode':
            self._require()
            if self.interactive is None:
                raise ValueError('Launch interactive mode before changing controls')
            self.interactive.controls({message.get('vehicle_id'): {'mode': message.get('mode')}})
            return self.frame()
        if name == 'seek':
            return self.seek(message.get('step'))
        if name == 'advance':
            return self.advance(message.get('steps', 1))
        if name == 'camera':
            return self.set_camera(message)
        if name in {'select', 'selection'}:
            return self.select(feature_id=message.get('feature_id'), event_id=message.get('event_id'),
                               focus=message.get('focus', True))
        if name == 'overlays':
            return self.overlays(structure=message.get('structure'), state=message.get('state'))
        if name == 'frame':
            return self.frame()
        if name == 'status':
            return self.status()
        if name == 'close':
            self.close()
            return {'closed': True}
        raise ValueError('Unknown replay command')
