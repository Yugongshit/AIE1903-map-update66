"""Contracts for independent ego control without importing the 3D engine."""
import math
import json
from hashlib import sha256
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from map_update.simulation_runtime import (
    controlled_environment_class, controller_state, environment_config, episode_step_limit, scenario_map_features,
    rendering_adapters, run, scenario_position, validated_action,
)


class RuntimeContractTests(unittest.TestCase):
    def test_controller_rejects_state_observations_from_another_scene(self):
        scenario = {'id': 'other-scene', 'length': 201,
                    'metadata': {'dataset': 'argoverse', 'map': 'other-city'}, 'map_features': {}}
        env = SimpleNamespace(engine=SimpleNamespace(data_manager=SimpleNamespace(current_scenario=scenario)),
                              agent=SimpleNamespace(), episode_step=1)
        bundle = {'dataset': 'nuscenes', 'scene_id': 'scene-0980',
                  'map': 'singapore-onenorth', 'scenario_length': 201}
        with self.assertRaisesRegex(ValueError, 'State source identity differs'):
            controller_state(env, state_source=bundle)

    def test_state_timeline_hash_is_checked_before_engine_load(self):
        from map_update.simulation_bridge import feature_sha256
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'dataset_summary.pkl').touch()
            (root / 'scene.pkl').write_bytes(b'example scenario')
            (root / 'map_patch_log.json').write_text('[]')
            manifest = {'scenario_file': 'scene.pkl', 'exported_scenario_sha256':
                        sha256(b'example scenario').hexdigest(), 'provenance': {
                            'map_patch_log_sha256': feature_sha256([]),
                            'state_source_sha256': feature_sha256({'events': ['source']})}}
            (root / 'simulation_manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'missing its bound state'):
                run(root, render='none', steps=1)
            (root / 'state_source.json').write_text(json.dumps({'events': ['stale']}))
            with self.assertRaisesRegex(ValueError, 'State observation timeline no longer matches'):
                run(root, render='none', steps=1)

    def test_stale_update_annotation_is_rejected_before_engine_load(self):
        from map_update.simulation_bridge import feature_sha256
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dataset_summary.pkl").touch()
            (root / "scene.pkl").write_bytes(b"example scenario")
            manifest = {"scenario_file": "scene.pkl", "exported_scenario_sha256":
                        sha256(b"example scenario").hexdigest(),
                        "provenance": {"map_patch_log_sha256": feature_sha256([])}}
            (root / "simulation_manifest.json").write_text(json.dumps(manifest))
            (root / "map_patch_log.json").write_text(json.dumps([{"feature_id": "stale-edit"}]))
            with self.assertRaisesRegex(ValueError, "Map update log no longer matches"):
                run(root, mode="controller", render="none", action=[0, 0], steps=1)
            manifest["provenance"] = {}
            (root / "simulation_manifest.json").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "re-export"):
                run(root, mode="controller", render="none", action=[0, 0], steps=1)

    def test_control_without_recorded_traffic_outlasts_recording(self):
        self.assertEqual(episode_step_limit(500, 201, mode="controller", no_traffic=True), 500)
        self.assertEqual(episode_step_limit(500, 201, mode="manual", no_traffic=True), 500)
        self.assertEqual(episode_step_limit(500, 201, mode="controller", no_traffic=False), 200)
        self.assertEqual(episode_step_limit(500, 201, mode="replay", no_traffic=True), 200)

    def test_controller_receives_actual_goal_radius_after_recorded_window(self):
        scenario = {"id": "example", "metadata": {"ts": [0, .1]}, "map_features": {}}
        env = SimpleNamespace(
            engine=SimpleNamespace(data_manager=SimpleNamespace(current_scenario=scenario)),
            agent=SimpleNamespace(position=[0, 0], velocity=[0, 0], heading_theta=0, speed=0,
                                  FRONT_WHEELBASE=1, REAR_WHEELBASE=1, max_steering=60),
            episode_step=5, config={"physics_world_step_size": .02, "decision_repeat": 5})
        state = controller_state(env, goal=[3, 4], goal_radius=.35)
        self.assertEqual(state["goal_radius_m"], .35)
        self.assertIsNone(state["recorded_time_s"])
        self.assertEqual(state["goal_scenario_m"], [3, 4])

    def test_controller_action_rejects_invalid_values(self):
        for action in ([0], [0, 0, 0], [math.nan, 0], [0, math.inf], [1.01, 0], "ab", None):
            with self.subTest(action=action), self.assertRaises(ValueError):
                validated_action(action)
        self.assertEqual(validated_action([-1, 1]), [-1.0, 1.0])

    def test_source_coordinates_restore_translation_without_changing_z(self):
        metadata = {"old_origin_in_current_coordinate": [-400, -1200]}
        feature = {"lane": {"type": "LANE_SURFACE_STREET", "polyline": [[1, 2, .5], [4, 5, .7]]}}
        restored = scenario_map_features(feature, metadata)
        self.assertEqual(scenario_position([1, 2], metadata), [401, 1202])
        self.assertEqual(restored["lane"]["polyline"], [[401, 1202, .5], [404, 1205, .7]])
        self.assertEqual(feature["lane"]["polyline"][0], [1, 2, .5])

    def test_control_mode_uses_external_actions_and_source_geometry(self):
        external, replay = object(), object()
        config = environment_config(".", mode="controller", render="none", steps=5,
                                    num_scenarios=1, input_policy=external, replay_policy=replay)
        self.assertIs(config["agent_policy"], external)
        self.assertFalse(config["manual_control"])
        self.assertGreater(config["height_scale"], 0)
        self.assertFalse(config["show_terrain"])
        self.assertFalse(config["show_skybox"])
        with self.assertRaisesRegex(ValueError, "Manual control requires"):
            environment_config(".", mode="manual", render="none", steps=5,
                               num_scenarios=1, input_policy=external, replay_policy=replay)

    def test_replay_uses_recorded_ego_and_retains_traffic_without_manual_input(self):
        external, replay = object(), object()
        config = environment_config(".", mode="replay", render="3D", steps=600,
                                    num_scenarios=1, input_policy=external, replay_policy=replay)
        self.assertIs(config["agent_policy"], replay)
        self.assertFalse(config["manual_control"])
        self.assertFalse(config["no_traffic"])
        self.assertFalse(config["no_light"])
        self.assertEqual(episode_step_limit(600, 201, mode="replay", no_traffic=False), 200)

    def test_rendering_adapter_does_not_replace_source_geometry_and_restores_on_error(self):
        class OriginalTerrain:
            pass
        calls = []
        def pbr_init(**kwargs):
            calls.append(kwargs)
        def full(shape, value, dtype):
            return [[[value] for _ in range(shape[1])] for _ in range(shape[0])]
        core = SimpleNamespace(Terrain=OriginalTerrain, init=pbr_init)
        numpy = SimpleNamespace(full=full, uint16="uint16")
        with self.assertRaisesRegex(RuntimeError, "example"):
            with rendering_adapters(core, numpy, offscreen=True) as compatibility:
                self.assertIs(core.Terrain, OriginalTerrain)
                core.init(msaa_samples=16)
                self.assertEqual(calls[0]["msaa_samples"], 0)
                self.assertFalse(compatibility["flat_heightfield"])
                self.assertTrue(compatibility["source_geometry_only"])
                raise RuntimeError("example")
        self.assertIs(core.Terrain, OriginalTerrain)
        self.assertIs(core.init, pbr_init)

    def make_env(self, goal=None):
        env = controlled_environment_class(object, goal=goal, goal_radius=1.0)()
        # There is intentionally no navigation/recorded route to consult.
        env.agents = {"ego": SimpleNamespace(position=[10, 20], crash_vehicle=False)}
        env.engine = SimpleNamespace(data_manager=SimpleNamespace(current_scenario={
            "metadata": {"old_origin_in_current_coordinate": [-400, -1200]}
        }))
        env.episode_lengths = {"ego": 1}
        env.config = {"horizon": 10}
        return env

    def test_new_goal_uses_world_coordinates_not_logged_route(self):
        env = self.make_env(goal=[410, 1220])
        done, info = env.done_function("ego")
        self.assertTrue(done)
        self.assertTrue(info["arrive_dest"])
        self.assertFalse(info["recorded_route_evaluated"])
        self.assertFalse(info["out_of_road_evaluated"])
        self.assertEqual(env.reward_function("ego")[0], 0)

    def test_no_goal_has_no_implicit_recorded_destination(self):
        env = self.make_env()
        done, info = env.done_function("ego")
        self.assertFalse(done)
        self.assertFalse(info["goal_defined"])
        env.episode_lengths["ego"] = 10
        done, info = env.done_function("ego")
        self.assertFalse(done)
        self.assertTrue(info["max_step"])

    def test_physics_collisions_terminate_independent_control(self):
        env = self.make_env()
        env.agents["ego"].crash_vehicle = True
        done, info = env.done_function("ego")
        self.assertTrue(done)
        self.assertTrue(info["crash_vehicle"])
        self.assertEqual(env.cost_function("ego")[0], 1)


if __name__ == "__main__":
    unittest.main()
