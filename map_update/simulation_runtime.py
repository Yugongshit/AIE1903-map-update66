"""Run a ScenarioNet database with independent ego control on the loaded map.

MetaDrive is optional and imported only by ``run``. A controller is a callable
``controller(state) -> [steering, throttle_or_brake]``. Both actions must lie in
[-1, 1]. Map geometry in state is the *loaded*, centered simulation geometry.
"""
from __future__ import annotations

import importlib
from contextlib import ExitStack, contextmanager
from hashlib import sha256
import json
import math
from pathlib import Path
from typing import Callable


def validated_action(value) -> list[float]:
    try:
        if len(value) != 2:
            raise ValueError
        action = [float(x) for x in value]
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Controller action must be [steering, throttle/brake]") from error
    if any(not math.isfinite(x) or not -1 <= x <= 1 for x in action):
        raise ValueError("Controller actions must be finite and within [-1, 1]")
    return action


def load_controller(spec: str) -> Callable:
    module, separator, name = spec.partition(":")
    if not separator or not module or not name or ":" in name:
        raise ValueError("Use --controller package.module:function")
    callback = getattr(importlib.import_module(module), name)
    if not callable(callback):
        raise ValueError(f"Controller {spec} is not callable")
    return callback


def scenario_position(position, metadata) -> list[float]:
    """Undo MetaDrive's scene centralization; retain the source scenario's axes."""
    offset = metadata.get("old_origin_in_current_coordinate", [0, 0])
    return [float(position[i]) - float(offset[i]) for i in range(2)]


def episode_step_limit(steps, scene_length, *, mode, no_traffic):
    """Only a fully controlled, traffic-free scene can outlast observations."""
    return steps if no_traffic and mode != "replay" else min(steps, max(0, scene_length - 1))


def environment_config(database_path, *, mode, render, steps, num_scenarios,
                       input_policy, replay_policy, no_traffic=False) -> dict:
    if mode not in {"manual", "replay", "controller"}:
        raise ValueError("Unknown control mode")
    if render not in {"none", "2D", "3D"}:
        raise ValueError("Unknown render mode")
    if mode == "manual" and render == "none":
        raise ValueError("Manual control requires --render 2D or 3D")
    if steps < 1 or num_scenarios < 1:
        raise ValueError("steps and num_scenarios must be positive")
    return {
        "data_directory": str(Path(database_path).resolve()),
        "num_scenarios": num_scenarios,
        "agent_policy": replay_policy if mode == "replay" else input_policy,
        "manual_control": mode == "manual",
        "use_render": render == "3D",
        "no_traffic": no_traffic,
        # Without recorded traffic, signal observations must not silently freeze
        # at their last recorded state when a new route outlasts the recording.
        "no_light": no_traffic,
        "horizon": steps,
        # The source map renderer supplies only measured polygon geometry.
        # Do not invent surrounding mountains, grassland or a flat heightfield.
        "height_scale": 1,
        "use_mesh_terrain": False,
        "show_terrain": False,
        "show_skybox": False,
        "show_logo": False,
        "show_fps": False,
        "show_interface": render == "3D",
        "show_interface_navi_mark": False,
        "interface_panel": [],
        "vehicle_config": {
            "show_navi_mark": False,
            "show_line_to_dest": False,
            "show_dest_mark": False,
        },
    }


@contextmanager
def rendering_adapters(engine_core, numpy, *, offscreen=False):
    """Adapt this vendored engine without changing its source or asset files.

The renderer uses source geometry rather than replacing terrain data. Keep the
temporary software-rendering binding scoped to one engine, then restore it.
"""
    original_pbr_init = engine_core.init

    def software_pbr_init(*args, **kwargs):
        # The vendored engine hard-codes 16x MSAA, which cannot allocate a
        # framebuffer on the cloud software EGL renderer.
        kwargs["msaa_samples"] = 0
        return original_pbr_init(*args, **kwargs)

    if offscreen:
        engine_core.init = software_pbr_init
    try:
        yield {"source_geometry_only": True, "flat_heightfield": False,
               "offscreen_msaa_override": 0 if offscreen else None}
    finally:
        engine_core.init = original_pbr_init


def controlled_environment_class(base, goal=None, goal_radius=2.0):
    """Do not terminate or reward a new controller against the recorded route.

TrajectoryNavigation remains an internal ScenarioEnv compatibility component.
Its old route is not exposed through the controller API and is not an evaluation
target. Road departure is not evaluated here; a planner must check loaded map
geometry. Collision flags still come from the running physics simulation.
"""
    if goal is not None and (len(goal) != 2 or not all(math.isfinite(float(x)) for x in goal)):
        raise ValueError("Goal must contain two finite scenario-frame coordinates")
    if not math.isfinite(goal_radius) or goal_radius <= 0:
        raise ValueError("Goal radius must be finite and positive")

    class ControlledScenarioEnv(base):
        def done_function(self, vehicle_id):
            vehicle = self.agents[vehicle_id]
            metadata = self.engine.data_manager.current_scenario["metadata"]
            position = scenario_position(vehicle.position, metadata)
            arrived = goal is not None and math.dist(position, goal) <= goal_radius
            collisions = {
                name: bool(getattr(vehicle, name, False)) for name in (
                    "crash_vehicle", "crash_object", "crash_human",
                    "crash_building", "crash_sidewalk",
                )
            }
            crashed = any(collisions.values())
            max_step = self.episode_lengths[vehicle_id] >= self.config["horizon"]
            info = dict(collisions, crash=crashed, arrive_dest=arrived,
                        max_step=max_step, out_of_road=False,
                        out_of_road_evaluated=False, goal_defined=goal is not None,
                        recorded_route_evaluated=False)
            # BaseEnv returns max_step as truncation as well.
            return arrived or crashed, info

        def reward_function(self, vehicle_id):
            return 0.0, {"step_reward": 0.0, "reward_contract": "none"}

        def cost_function(self, vehicle_id):
            vehicle = self.agents[vehicle_id]
            collided = any(bool(getattr(vehicle, key, False)) for key in (
                "crash_vehicle", "crash_object", "crash_human", "crash_building", "crash_sidewalk"))
            return float(collided), {"cost": float(collided)}

    return ControlledScenarioEnv


def scenario_map_features(features, metadata) -> dict:
    """Return source-scenario XY geometry, undoing only runtime centralization."""
    result = {}
    for feature_id, feature in features.items():
        item = dict(feature)
        for key in ("polyline", "polygon"):
            if key in feature:
                item[key] = [scenario_position(point, metadata) + [float(v) for v in point[2:]]
                             for point in feature[key]]
        result[feature_id] = item
    return result


def map_xy_digest(features) -> str:
    """Fingerprint loaded XY/type data at 1e-8 m precision after recentering."""
    geometry = {}
    for feature_id, feature in features.items():
        item = {"type": feature.get("type")}
        for key in ("polyline", "polygon"):
            if key in feature:
                item[key] = [[round(float(v), 8) for v in point[:2]] for point in feature[key]]
        geometry[str(feature_id)] = item
    return sha256(json.dumps(geometry, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def controller_state(env, *, goal=None, goal_radius=2.0, source_map_features=None) -> dict:
    scenario = env.engine.data_manager.current_scenario
    metadata = scenario["metadata"]
    vehicle = env.agent
    offset = metadata.get("old_origin_in_current_coordinate", [0, 0])
    step = env.episode_step
    timestamps = metadata.get("ts", [])
    obstacles = {}
    if getattr(getattr(env.engine, "traffic_manager", None), "persistence_definitions", None):
        from .simulation_obstacle_runtime import runtime_obstacle_report
        obstacles = runtime_obstacle_report(env)["objects"]
    return {
        "step": step,
        "simulation_time_s": float(step * env.config["physics_world_step_size"] * env.config["decision_repeat"]),
        "recorded_time_s": float(timestamps[step]) if step < len(timestamps) else None,
        "scenario_id": scenario["id"],
        "dataset": metadata.get("dataset"),
        "coordinate_frame": "metadrive_centered_m",
        "scenario_coordinate_frame": metadata.get("coordinate", "metadrive"),
        "runtime_origin_in_scenario_m": [-float(offset[i]) for i in range(2)],
        "position_runtime_m": [float(v) for v in vehicle.position[:2]],
        "position_scenario_m": scenario_position(vehicle.position, metadata),
        # The supplied nuScenes exporter retains city XY metres. These aliases
        # share the scenario frame, not latitude/longitude or ego-local axes.
        "position_city_xy": scenario_position(vehicle.position, metadata),
        "velocity_runtime_m_s": [float(v) for v in vehicle.velocity[:2]],
        "heading_rad": float(vehicle.heading_theta),
        "speed_m_s": float(vehicle.speed),
        "wheelbase_m": float(vehicle.FRONT_WHEELBASE + vehicle.REAR_WHEELBASE),
        "max_steering_rad": math.radians(float(vehicle.max_steering)),
        "goal_scenario_m": list(goal) if goal is not None else None,
        "goal_radius_m": float(goal_radius),
        "goal_city_xy": list(goal) if goal is not None else None,
        "map_features_runtime": scenario["map_features"],
        "map_features": source_map_features,
        "persistent_obstacles_runtime": obstacles,
    }


def run(database_path, *, mode="manual", render="3D", steps=600,
        scenario_index=0, controller=None, action=None, goal=None,
        goal_radius=2.0, no_traffic=False, snapshot=None, offscreen=False,
        environment_geometry=None, show_updates=True) -> dict:
    """Load a trusted local ScenarioNet dataset and run one finite episode.

Database files use pickle, as required by MetaDrive. Only pass trusted data.
Callbacks read ``state['map_features_runtime']``; do not mutate this shared map.
The original database files are never written by this runner.
"""
    database_path = Path(database_path).resolve()
    if not database_path.is_dir():
        raise ValueError(f"Scenario database does not exist: {database_path}")
    if not (database_path / "dataset_summary.pkl").is_file():
        raise ValueError("Database must contain dataset_summary.pkl")
    if mode == "controller" and controller is None and action is None:
        raise ValueError("Controller mode requires --controller or --action")
    if mode != "controller" and (controller is not None or action is not None):
        raise ValueError("--controller and --action require --mode controller")
    if controller is not None and action is not None:
        raise ValueError("Choose either a controller or a fixed smoke-test action")
    if mode == "replay" and goal is not None:
        raise ValueError("A new goal requires manual or controller mode")
    if offscreen and (render != "3D" or mode == "manual"):
        raise ValueError("Offscreen rendering requires --render 3D and replay/controller mode")
    snapshot_path = Path(snapshot).resolve() if snapshot is not None else None
    if snapshot_path is not None:
        if render != "3D" or snapshot_path.suffix.lower() != ".png":
            raise ValueError("--snapshot requires --render 3D and a .png filename")
        if snapshot_path.exists():
            raise FileExistsError(snapshot_path)
    fixed_action = validated_action(action) if action is not None else [0.0, 0.0]
    callback = load_controller(controller) if isinstance(controller, str) else controller
    manifest_path = database_path / "simulation_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else None
    if manifest is not None:
        filename = manifest.get("scenario_file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError("Export manifest has an invalid scenario filename")
        if sha256((database_path / filename).read_bytes()).hexdigest() != manifest.get("exported_scenario_sha256"):
            raise ValueError("Exported scenario no longer matches its manifest hash")
    patch_log = json.loads((database_path / "map_patch_log.json").read_text(encoding="utf-8")) if manifest else []
    if manifest is not None:
        from .simulation_bridge import feature_sha256
        patch_digest = manifest.get("provenance", {}).get("map_patch_log_sha256")
        if patch_digest is None:
            raise ValueError("Export lacks a bound map update log; re-export this database")
        if feature_sha256(patch_log) != patch_digest:
            raise ValueError("Map update log no longer matches its export manifest hash")
    obstacle_path = database_path / "obstacle_manifest.json"
    obstacle_manifest = json.loads(obstacle_path.read_text(encoding="utf-8")) if obstacle_path.is_file() else None
    if manifest and manifest.get("provenance", {}).get("obstacle_manifest_sha256"):
        from .simulation_bridge import feature_sha256
        if obstacle_manifest is None or feature_sha256(obstacle_manifest) != manifest["provenance"]["obstacle_manifest_sha256"]:
            raise ValueError("Obstacle policy no longer matches its export manifest hash")

    try:
        from metadrive.envs.scenario_env import ScenarioEnv
        from metadrive.policy.env_input_policy import EnvInputPolicy
        from metadrive.policy.replay_policy import ReplayEgoCarPolicy
        from metadrive.scenario.utils import get_number_of_scenarios
        import metadrive.envs.scenario_env as scenario_env_module
    except ImportError as error:
        raise RuntimeError("Install the vendored MetaDrive runtime in Python 3.10/3.11; see docs/simulation_bridge.md") from error

    count = get_number_of_scenarios(str(database_path))
    if scenario_index < 0 or scenario_index >= count:
        raise ValueError(f"scenario_index must be in [0, {count})")
    config = environment_config(database_path, mode=mode, render=render, steps=steps,
                                num_scenarios=count, input_policy=EnvInputPolicy,
                                replay_policy=ReplayEgoCarPolicy, no_traffic=no_traffic)
    persistent_obstacles = bool(obstacle_manifest and obstacle_manifest.get("mode") == "persistent")
    if persistent_obstacles:
        # no-traffic means no moving replay agents. Mapped stationary obstacles
        # remain part of the environment presented to the planner and physics.
        config["no_traffic"] = False
        config["static_traffic_object"] = True
    if offscreen:
        from panda3d.core import loadPrcFileData
        from metadrive.obs.state_obs import LidarStateObservation
        loadPrcFileData("", "load-display p3headlessgl")
        config.update(use_render=False, image_observation=True, show_interface=False,
                      multi_thread_render=False, agent_observation=LidarStateObservation,
                      sensors={"main_camera": ("MainCamera", 1200, 900)})
        config["vehicle_config"]["image_source"] = "main_camera"
    env_class = ScenarioEnv if mode == "replay" else controlled_environment_class(ScenarioEnv, goal, goal_radius)
    env = None
    resources = ExitStack()
    trace = []
    info = {}
    ended = "step_limit"
    obstacle_trace = []
    scene_report = None
    update_report = None
    overlay_handle = None
    try:
        import numpy
        from metadrive.engine.core import engine_core
        compatibility = resources.enter_context(rendering_adapters(engine_core, numpy, offscreen=offscreen))
        if persistent_obstacles:
            from .simulation_obstacle_runtime import persistent_obstacle_adapter
            resources.enter_context(persistent_obstacle_adapter(
                scenario_env_module, obstacle_manifest, no_moving_traffic=no_traffic))
        env = env_class(config)
        env.reset(seed=scenario_index)
        scenario = env.engine.data_manager.current_scenario
        if render == "3D":
            from .simulation_environment import attach_source_environment
            surface_handle, scene_report = attach_source_environment(env, extra=environment_geometry)
            resources.callback(surface_handle.close)
        if show_updates:
            from .simulation_visuals import attach_update_overlay
            overlay_handle, update_report = attach_update_overlay(
                env, patch_log, synthetic=bool(manifest and manifest.get("provenance", {}).get("synthetic")))
            resources.callback(overlay_handle.close)
        source_features = scenario_map_features(scenario["map_features"], scenario["metadata"])
        initial = controller_state(env, goal=goal, goal_radius=goal_radius, source_map_features=source_features)
        scene_length = int(env.engine.data_manager.current_scenario_length)
        # Traffic replay has no observations beyond this recorded time window.
        limit = episode_step_limit(steps, scene_length, mode=mode, no_traffic=no_traffic)
        for _ in range(limit):
            state = controller_state(env, goal=goal, goal_radius=goal_radius, source_map_features=source_features)
            command = validated_action(callback(state)) if callback is not None else fixed_action
            _, _, terminated, truncated, info = env.step(command)
            current = controller_state(env, goal=goal, goal_radius=goal_radius, source_map_features=source_features)
            trace.append({"step": current["step"],
                          "position_scenario_m": current["position_scenario_m"],
                          "speed_m_s": current["speed_m_s"], "action": command})
            if persistent_obstacles:
                from .simulation_obstacle_runtime import runtime_obstacle_report
                obstacle_trace.append(runtime_obstacle_report(env))
            if render == "2D":
                env.render(mode="top_down", film_size=(1200, 1200),
                           target_vehicle_heading_up=False)
                if overlay_handle is not None:
                    overlay_handle.draw_2d(env.top_down_renderer)
            elif render == "3D":
                env.render(text={"mode": mode, "step": env.episode_step,
                                 "environment": "source geometry; missing surroundings left unspecified"})
            if terminated or truncated:
                ended = "terminated" if terminated else "truncated"
                break
        else:
            if limit < steps:
                ended = "recorded_window_end"
        final = controller_state(env, goal=goal, goal_radius=goal_radius, source_map_features=source_features)
        if snapshot_path is not None:
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            env.engine.graphicsEngine.renderFrame()
            env.engine.graphicsEngine.renderFrame()
            env.capture(str(snapshot_path))
            if not snapshot_path.is_file() or snapshot_path.stat().st_size == 0:
                raise RuntimeError("The renderer did not save the requested snapshot")
        report = {
            "database_path": str(database_path), "scenario_id": final["scenario_id"],
            "mode": mode, "render": render, "steps_executed": len(trace),
            "termination": ended, "ego_replay": mode == "replay",
            "map_feature_count": len(final["map_features_runtime"]),
            "loaded_map_xy_sha256": map_xy_digest(source_features),
            "map_update_bridge": scenario["metadata"].get("map_update_bridge"),
            "terrain": "source geometry only; no invented surrounding terrain",
            "render_compatibility": compatibility,
            "source_environment": scene_report, "update_highlights": update_report,
            "obstacle_mode": obstacle_manifest.get("mode") if obstacle_manifest else "recorded",
            "obstacle_trace": obstacle_trace,
            "persistent_obstacles": ({"active_count": obstacle_trace[-1]["active_count"],
                                      "missing_expected_ids": obstacle_trace[-1]["missing_expected_ids"],
                                      "first_seen_retention": True}
                                     if obstacle_trace else None),
            "runtime_origin_in_scenario_m": final["runtime_origin_in_scenario_m"],
            "initial_position_scenario_m": initial["position_scenario_m"],
            "final_position_scenario_m": final["position_scenario_m"],
            "goal_scenario_m": final["goal_scenario_m"],
            "goal_radius_m": goal_radius,
            "snapshot": str(snapshot_path) if snapshot_path is not None else None,
            "offscreen": offscreen,
            "arrive_dest": bool(info.get("arrive_dest", False)),
            "crash": bool(info.get("crash", False)),
            "recorded_route_evaluated": mode == "replay",
            "traffic_behavior": ("moving traffic/signals disabled; persistent mapped obstacles retained"
                                 if no_traffic and persistent_obstacles else
                                 "traffic and signals disabled" if no_traffic else "recorded replay (not reactive)"),
            "trace": trace,
        }
        if callback is not None and callable(getattr(callback, "diagnostics", None)):
            report["controller_diagnostics"] = callback.diagnostics()
        if manifest is not None:
            from .simulation_bridge import json_value
            report["export_manifest"] = manifest
            report["export_manifest_path"] = str(manifest_path)
            report["export_manifest_verified"] = True
            engine_map = scenario_map_features(env.engine.current_map.map_data, scenario["metadata"])
            report["changed_features_loaded_from_engine"] = {
                item["feature_id"]: json_value(engine_map.get(item["feature_id"])) for item in patch_log
            }
        return report
    finally:
        try:
            resources.close()
        finally:
            if env is not None:
                env.close()
