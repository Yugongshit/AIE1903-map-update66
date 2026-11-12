"""Source-state annotations bind identities without changing replay objects."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from map_update.simulation_state_visuals import attach_state_overlay, prepare_state_annotations, state_label_groups


def event(identifier="event-1", state="observed_active"):
    return {"id": identifier, "properties": {"event_id": identifier, "state": state,
            "change_type": "occupancy", "last_seen_timestamp_ns": "100"}}


def source_object(identifier="instance-1", events=("event-1",), **kwargs):
    return {"instance_id": identifier, "event_ids": list(events), "status": "observed_active",
            "category": "movable_object.barrier", "last_observed_timestamp_ns": "100",
            "runtime_paths": [[[1, 2], [3, 2], [3, 3], [1, 3], [1, 2]]],
            "matched_track_ids": ["track-1"], "missing_model": False, **kwargs}


def snapshot(objects=None, events=None):
    return {"source_timestamp_ns": "120", "observation_cutoff_ns": "100",
            "source_snapshot_age_s": .000000020,
            "layer": {"features": [event()] if events is None else events, "cleared_events": []},
            "objects": [source_object()] if objects is None else objects}


def environment():
    objects = {"live-1": SimpleNamespace(position=[2, 2.5]),
               "unrelated-pedestrian": SimpleNamespace(position=[4, 8])}
    mapping = {"track-1": "live-1", "unrelated-track": "unrelated-pedestrian"}
    tracks = {"track-1": {"metadata": {"original_id": "instance-1"}},
              "unrelated-track": {"metadata": {"original_id": "unrelated-instance"}}}
    env = SimpleNamespace(engine=SimpleNamespace(mode="none", get_objects=lambda: objects,
            data_manager=SimpleNamespace(current_scenario={"tracks": tracks}),
            traffic_manager=SimpleNamespace(scenario_id_to_obj_id=mapping)),
            agent=SimpleNamespace(position=[0, 0]), episode_step=5,
            config={"agent_policy": "ReplayEgoCarPolicy", "manual_control": False})
    return env, objects


class SimulationStateVisualsTests(unittest.TestCase):
    def test_vertical_annotations_use_only_observed_dimensions(self):
        model = prepare_state_annotations(snapshot([source_object(height_m=1.35)]))
        self.assertEqual(model['objects'][0]['source_height_m'], 1.35)
        self.assertIsNone(prepare_state_annotations(snapshot())['objects'][0]['source_height_m'])
        with self.assertRaisesRegex(ValueError, 'source dimension'):
            prepare_state_annotations(snapshot([source_object(height_m=float('nan'))]))

    def test_one_instance_in_multiple_events_has_one_footprint(self):
        data = snapshot([source_object(), source_object(events=("event-2",))],
                        [event(), event("event-2")])
        saved = deepcopy(data)
        result = prepare_state_annotations(data)
        self.assertEqual(len(result["objects"]), 1)
        self.assertEqual(result["objects"][0]["event_ids"], ["event-1", "event-2"])
        self.assertEqual(result["summary"]["events_seen"], 2)
        self.assertEqual(data, saved)

    def test_existing_instance_is_bound_without_marking_unrelated_traffic(self):
        env, objects = environment()
        original_ids = set(objects)
        data = snapshot()
        saved = deepcopy(data)
        handle, report = attach_state_overlay(env, data)
        self.assertEqual(report["summary"]["bound_instances"], 1)
        self.assertEqual(report["objects"][0]["bound_object_ids"], ["live-1"])
        self.assertEqual(report["objects_spawned"], 0)
        self.assertFalse(report["affects_physics_or_planning"])
        self.assertEqual(set(objects), original_ids)
        self.assertEqual(data, saved)
        handle.close()

    def test_live_anchor_moves_but_observed_footprint_does_not(self):
        env, objects = environment()
        handle, report = attach_state_overlay(env, snapshot())
        paths = deepcopy(report["objects"][0]["runtime_paths"])
        objects["live-1"].position = [12, 20]
        handle.refresh(snapshot())
        self.assertEqual(report["objects"][0]["runtime_paths"], paths)
        self.assertEqual(report["objects"][0]["anchor_runtime_m"], [12, 20])
        self.assertEqual(paths[0][0], [1, 2])
        handle.close()

    def test_missing_debris_gets_only_source_footprint_not_a_model(self):
        env, objects = environment()
        missing = source_object("debris-source", category="movable_object.debris",
                                matched_track_ids=[], missing_model=True)
        handle, report = attach_state_overlay(env, snapshot([missing]))
        self.assertEqual(report["summary"]["missing_model_instances"], 1)
        self.assertEqual(report["summary"]["footprint_only_instances"], 1)
        self.assertEqual(report["objects"][0]["anchor_runtime_m"], [2, 2.5])
        self.assertEqual(set(objects), {"live-1", "unrelated-pedestrian"})
        handle.close()

    def test_future_observation_or_conflicting_instance_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Future instance"):
            prepare_state_annotations(snapshot([source_object(last_observed_timestamp_ns="130")]))
        future_event = event()
        future_event["properties"]["last_seen_timestamp_ns"] = "130"
        with self.assertRaisesRegex(ValueError, "Future event"):
            prepare_state_annotations(snapshot(events=[future_event]))
        with self.assertRaisesRegex(ValueError, "conflicting"):
            prepare_state_annotations(snapshot([source_object(), source_object(status="unknown")]))

    def test_track_identity_must_match_source_instance(self):
        env, _ = environment()
        with self.assertRaisesRegex(ValueError, "instance differs"):
            attach_state_overlay(env, snapshot([source_object("different-instance")]))

    def test_clearance_removes_annotations_but_keeps_replayed_object(self):
        env, objects = environment()
        handle, report = attach_state_overlay(env, snapshot())
        cleared = snapshot(events=[])
        cleared["layer"]["cleared_events"] = [{"event_id": "event-1"}]
        handle.refresh(cleared)
        self.assertEqual(report["objects"], [])
        self.assertEqual(report["summary"]["cleared"], 1)
        self.assertIsNone(handle.selected_event_id)
        self.assertIn("live-1", objects)
        handle.close()

    def test_state_selection_and_visibility_leave_replay_unchanged(self):
        env, objects = environment()
        data = snapshot([source_object(events=("event-1", "event-2"))],
                        [event(), event("event-2", "unknown")])
        handle, report = attach_state_overlay(env, data)
        config, pose = deepcopy(env.config), list(objects["live-1"].position)
        handle.select_state(-1)
        self.assertEqual(handle.selected_event_id, "event-2")
        handle.toggle_visible()
        self.assertFalse(report["annotations_visible"])
        self.assertEqual(env.config, config)
        self.assertEqual(env.episode_step, 5)
        self.assertEqual(objects["live-1"].position, pose)
        handle.refresh(snapshot([source_object(events=("event-2",))], [event("event-2", "unknown")]))
        self.assertEqual(handle.selected()["label"], "S02")
        handle.close()

    def test_focus_uses_source_footprints_without_moving_live_instance(self):
        env, objects = environment()
        calls = []
        env.main_camera = SimpleNamespace(
            stop_track=lambda **kw: calls.append(("stop", kw)),
            set_bird_view_pos=lambda p: calls.append(("bird", p)),
            camera=SimpleNamespace(setPos=lambda *p: None, lookAt=lambda *p: None))
        lens = SimpleNamespace(getFov=lambda: (60, 45))
        env.engine.cam = SimpleNamespace(node=lambda: SimpleNamespace(getLens=lambda: lens))
        objects["live-1"].position = [60, 70]
        handle, _ = attach_state_overlay(env, snapshot())
        self.assertTrue(handle.focus_selected())
        self.assertEqual(calls[1][1][:2], [2, 2.5])
        self.assertEqual(objects["live-1"].position, [60, 70])
        self.assertEqual(env.episode_step, 5)
        self.assertFalse(env.config["manual_control"])
        handle.close()

    def test_after_recording_panel_does_not_claim_a_new_source_observation(self):
        env, _ = environment()
        data = snapshot([source_object(status="unknown", historical=True)], [event(state="unknown")])
        data.update(source_snapshot_age_s=None, source_timestamp_ns=None, clock_mode="outside_recorded_window")
        handle, report = attach_state_overlay(env, data)
        self.assertIn("Past recording; no new observations", [line for line, _ in handle._panel_lines()])
        self.assertIsNone(report["selected_event_last_seen_age_s"])
        handle.close()

    def test_event_last_seen_age_is_distinct_from_current_frame_age(self):
        env, _ = environment()
        selected = event(state="unknown")
        selected["properties"]["last_seen_timestamp_ns"] = "6000000000"
        data = snapshot([source_object(status="unknown", last_observed_timestamp_ns="6000000000")], [selected])
        data.update(source_timestamp_ns="12000000000", observation_cutoff_ns="11800000000", source_snapshot_age_s=.2)
        handle, report = attach_state_overlay(env, data)
        lines = [line for line, _ in handle._panel_lines()]
        self.assertIn("Frame snapshot age: 0.200 s", lines)
        self.assertTrue(any(line.startswith("Event last seen: 6.0 s ago") for line in lines))
        self.assertEqual(report["selected_event_last_seen_age_s"], 6.)
        self.assertEqual(report["selected_event_last_seen_timestamp_ns"], "6000000000")
        handle.close()

    def test_adjacent_debris_and_selected_objects_get_one_label_per_event(self):
        debris = [source_object("debris-"+str(i), category="movable_object.debris",
                                matched_track_ids=[], missing_model=True) for i in range(3)]
        prepared = prepare_state_annotations(snapshot(debris), labels={"event-1": "S01"})
        groups = state_label_groups(prepared)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["label"], "S01: 3 debris footprints")
        self.assertEqual(len(groups[0]["instance_ids"]), 3)
        self.assertEqual(len(prepared["objects"]), 3)
        self.assertEqual(state_label_groups(prepared, "event-1"), groups)
        regular = prepare_state_annotations(snapshot([source_object("id-"+str(i)) for i in range(6)]),
                                            labels={"event-1": "S01"})
        groups = state_label_groups(regular, "event-1")
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["instance_ids"]), 6)

    def test_explicit_overview_keeps_annotations_and_labels_fixed_when_live_objects_move(self):
        env, objects = environment()
        data = snapshot([source_object(status='scene_overview')], [event(state='scene_overview')])
        data.update(scope='full_scene_overview', frozen=True, source_timestamp_ns=None,
                    clock_mode='full_scene_fixed_overview', source_snapshot_age_s=None)
        handle, report = attach_state_overlay(env, data)
        initial = deepcopy(report['objects'])
        labels = deepcopy(report['label_groups'])
        objects['live-1'].position = [500., 600.]
        env.episode_step = 100
        handle.refresh(data)
        self.assertEqual(report['objects'], initial)
        self.assertEqual(report['label_groups'], labels)
        self.assertEqual(initial[0]['anchor_runtime_m'], [2., 2.5])
        self.assertFalse(initial[0]['historical'])
        self.assertIn('SCENE OVERVIEW', labels[0]['label'])
        self.assertNotIn('OBSERVED_ACTIVE', labels[0]['label'])
        self.assertIsNone(report['selected_event_last_seen_age_s'])
        self.assertIn('FULL SCENE OVERVIEW - FIXED', [line for line, _ in handle._panel_lines()])
        handle.close()

    def test_overview_status_cannot_masquerade_as_a_current_observation(self):
        data = snapshot([source_object(status='scene_overview')], [event(state='scene_overview')])
        with self.assertRaisesRegex(ValueError, 'explicit frozen'):
            prepare_state_annotations(data)
        data.update(scope='full_scene_overview', frozen=True)
        data['objects'][0]['last_observed_timestamp_ns'] = '130'
        with self.assertRaisesRegex(ValueError, 'Future instance'):
            prepare_state_annotations(data)


if __name__ == "__main__":
    unittest.main()
