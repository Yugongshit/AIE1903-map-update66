"""State attachment uses exact clocks, complete history and original identities."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest

from map_update.scene import footprint
from map_update.simulation_states import build_state_source, state_overview, state_snapshot
from map_update.state_events import build_state_event_layer


T0 = 1537845352297764000
TIMES = [T0, T0+499880000, T0+999207000]
REFERENCE = {"frame": "nuScenes_global", "city_id": "singapore-onenorth", "units": "m"}


def object_row(token, frame, category="movable_object.barrier"):
    return {"instance_token": token, "frame_index": str(frame), "sample_token": "sample"+str(frame),
            "annotation_token": token+"-"+str(frame), "category": category,
            "x_global_m": str(100+frame), "y_global_m": "200", "length_m": "2", "width_m": "1",
            "height_m": "1.2", "yaw_global_deg": "0"}


def sample(frame, tokens, eid="A"):
    return {"timestamp_ns": str(TIMES[frame]), "frame_id": str(frame), "visit_id": "visit",
            "observation": "supports_change", "quality": 1, "fully_observed": False,
            "source_ids": [eid, "frame:"+str(frame)]+["instance:"+token for token in tokens],
            "instance_tokens": tokens, "geometry_city": {"type": "GeometryCollection",
            "geometries": [footprint(object_row(token, frame)) for token in tokens]}}


def event(eid, samples):
    return {"event_id": eid, "change_type": "occupancy", "samples": samples,
            "map_layer": {"predicted_layer": "map_state_event"}, "reliability": {"status": "supported_conflict"},
            "coordinate_reference": REFERENCE, "map_aliases": [{"map_element_id": "lane:a"}]}


class SimulationStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root/"processed").mkdir()
        self.write("frames.csv", [{"frame_index": str(i), "sample_token": "sample"+str(i),
                                   "timestamp_us": str(t//1000)} for i, t in enumerate(TIMES)])
        self.write("objects.csv", [object_row(token, frame, "movable_object.debris" if token == "debris" else "movable_object.barrier")
                                   for token in ("old", "new", "debris") for frame in range(3)])
        self.scenario = {"id": "scene-0980", "length": 11,
                         "metadata": {"dataset": "nuscenes", "map": "singapore-onenorth", "coordinate": "right-handed",
                                      "ts": [i/10 for i in range(11)]},
                         "tracks": {token: {"type": "TRAFFIC_BARRIER", "metadata": {"original_id": token}}
                                    for token in ("old", "new")}}

    def write(self, filename, rows):
        with (self.root/"processed"/filename).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)

    def build(self, events=None):
        events = events or [event("A", [sample(0, ["old"]), sample(1, ["new", "debris"])])]
        self.dataset = {"scene_id": "scene-0980", "events": events,
                        "provenance": {"coordinate_reference": REFERENCE},
                        "state_event_layer": build_state_event_layer(events)}
        return build_state_source(self.scenario, self.dataset, self.root)

    def test_complete_history_retained_not_only_last_snapshot(self):
        bundle = self.build()
        self.assertEqual(bundle["summary"]["historical_instances"], 3)
        snap = state_snapshot(bundle, 5)
        objects = {obj["instance_id"]: obj for obj in snap["objects"]}
        self.assertEqual(set(objects), {"old", "new", "debris"})
        self.assertEqual(objects["old"]["status"], "unknown")
        self.assertEqual(objects["new"]["status"], "observed_active")
        self.assertTrue(objects["debris"]["missing_model"])
        self.assertEqual(objects["debris"]["matched_track_ids"], [])

    def test_overview_has_every_instance_fixed_at_its_first_supported_observation(self):
        bundle = self.build([event("A", [sample(0, ["old"]), sample(1, ["new", "debris"]),
                                         sample(2, ["old"])]),
                             event("B", [sample(1, ["old"], "B")])])
        before = deepcopy(bundle)
        overview = state_overview(bundle)
        objects = {obj["instance_id"]: obj for obj in overview["objects"]}
        self.assertEqual(set(objects), {"old", "new", "debris"})
        self.assertEqual(objects["old"]["center_xy"], [100., 200.])
        self.assertEqual(objects["old"]["source_frame_id"], "0")
        self.assertEqual(objects["old"]["event_ids"], ["A", "B"])
        self.assertEqual(objects["new"]["center_xy"], [101., 200.])
        self.assertEqual(len(overview["events"]), 2)
        self.assertTrue(all(obj["status"] == "scene_overview" and obj["frozen"] for obj in objects.values()))
        self.assertEqual(overview["scope"], "full_scene_overview")
        self.assertFalse(overview["planning_eligible"])
        self.assertIsNone(overview["source_timestamp_ns"])
        self.assertEqual(bundle, before)
        self.assertEqual([obj["instance_id"] for obj in state_snapshot(bundle, 0)["objects"]], ["old"])

    def test_overview_preserves_a_past_change_even_when_source_observations_clear_it(self):
        clear = {**sample(1, []), "observation": "supports_prior", "fully_observed": True}
        source_event = event("A", [sample(0, ["old"]), clear])
        source_event["ttl_s"] = 0
        bundle = self.build([source_event])
        overview = state_overview(bundle)
        self.assertEqual(overview["objects"][0]["instance_id"], "old")
        self.assertEqual(overview["events"][0]["properties"]["state"], "scene_overview")
        self.assertEqual(state_snapshot(bundle, 10)["objects"], [])
        self.assertEqual(state_snapshot(bundle, 10)["layer"]["cleared_events"][0]["event_id"], "A")

    def test_overview_is_deterministic_across_input_order_and_translates_geometry_once(self):
        bundle = self.build([event("B", [sample(2, ["old"], "B"), sample(0, ["old"], "B")]),
                             event("A", [sample(1, ["new", "debris"])])])
        reordered = deepcopy(bundle)
        reordered["events"].reverse()
        for source_event in reordered["events"]:
            source_event["samples"].reverse()
        self.assertEqual(state_overview(bundle), state_overview(reordered))
        overview = state_overview(bundle, (-100, -200))
        old = next(obj for obj in overview["objects"] if obj["instance_id"] == "old")
        self.assertEqual(old["center_runtime_xy"], [0., 0.])
        original = old["geometry_city"]["coordinates"][0][0]
        self.assertEqual(old["runtime_paths"][0][0], [original[0]-100, original[1]-200])
        self.assertEqual(overview["events"], overview["layer"]["features"])

    def test_overview_rejects_invalid_bundle_and_nonfinite_offsets(self):
        with self.assertRaises(ValueError):
            state_overview({"schema": "unknown"})
        for offset in ([0], [float('inf'), 0], [0, float('nan')]):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                state_overview(self.build(), offset)

    def test_overview_event_provenance_covers_the_full_union_beyond_its_first_sample(self):
        source = Path(__file__).resolve().parents[1] / 'demo/simulation/nuscenes-states/state_source.json'
        overview = state_overview(json.loads(source.read_text()))
        feature = next(event for event in overview['events'] if event['id'] == 'S0980-OCC-scene-0980-O008')
        prop = feature['properties']
        first_instances = [source for source in prop['first_support_source_ids'] if source.startswith('instance:')]
        self.assertEqual(len(first_instances), 1)
        self.assertEqual(prop['overview_instance_count'], 9)
        self.assertEqual(len(feature['geometry']['geometries']), 9)
        members = {obj['instance_id']: obj for obj in overview['objects'] if prop['event_id'] in obj['event_ids']}
        self.assertEqual(prop['overview_instance_ids'], sorted(members))
        self.assertEqual(prop['history'][0]['source_ids'], sorted(prop['first_support_source_ids']))
        self.assertEqual(len(prop['history']), 1)
        self.assertEqual(len(prop['overview_source_observations']), 9)
        for representative in prop['overview_source_observations']:
            obj = members[representative['instance_id']]
            self.assertEqual(representative['source_timestamp_ns'], obj['first_supported_timestamp_ns'])
            self.assertEqual(representative['source_frame_id'], obj['source_frame_id'])
            for prefix, field in [('instance:', 'instance_id'), ('frame:', 'source_frame_id'),
                                  ('sample:', 'sample_token'), ('annotation:', 'annotation_token')]:
                self.assertIn(prefix+representative[field], prop['source_ids'])

    def test_future_objects_not_shown_and_sample_hold_does_not_flicker(self):
        bundle = self.build()
        for step in range(5):
            snap = state_snapshot(bundle, step)
            self.assertEqual([obj["instance_id"] for obj in snap["objects"]], ["old"])
            self.assertEqual(snap["objects"][0]["status"], "observed_active")
            self.assertEqual(snap["observation_cutoff_ns"], str(T0))
        self.assertAlmostEqual(state_snapshot(bundle, 4)["source_snapshot_age_s"], .399904)

    def test_exact_ns_interpolation_does_not_use_nominal_clock(self):
        bundle = self.build()
        snap = state_snapshot(bundle, 7)
        expected = TIMES[1]+(TIMES[2]-TIMES[1])*2//5
        self.assertEqual(snap["source_timestamp_ns"], str(expected))
        self.assertNotEqual(expected, T0+700000000)
        self.assertEqual(snap["observation_cutoff_ns"], str(TIMES[1]))

    def test_portable_bundle_and_one_runtime_translation(self):
        bundle = json.loads(json.dumps(self.build()))
        before = deepcopy(bundle)
        (self.root/"processed/objects.csv").unlink()
        (self.root/"processed/frames.csv").unlink()
        obj = state_snapshot(bundle, 0, (-100, -200))["objects"][0]
        source_point = obj["geometry_city"]["coordinates"][0][0]
        self.assertEqual(obj["runtime_paths"][0][0], [source_point[0]-100, source_point[1]-200])
        self.assertEqual(bundle, before)

    def test_clearance_revokes_only_that_event_then_new_observation_reopens(self):
        clear = {**sample(1, []), "observation": "supports_prior", "fully_observed": True}
        a = event("A", [sample(0, ["old"]), clear, sample(2, ["new"])])
        b = event("B", [sample(0, ["old"], "B")])
        bundle = self.build([a, b])
        snap = state_snapshot(bundle, 5)
        self.assertEqual(snap["objects"][0]["event_ids"], ["B"])
        self.assertEqual(snap["layer"]["cleared_events"][0]["event_id"], "A")
        reopened = {obj["instance_id"]: obj for obj in state_snapshot(bundle, 10)["objects"]}
        self.assertEqual(reopened["old"]["event_ids"], ["B"])
        self.assertEqual(reopened["new"]["event_ids"], ["A"])

    def test_same_instance_across_events_is_not_duplicated(self):
        bundle = self.build([event("A", [sample(0, ["old"])]), event("B", [sample(0, ["old"], "B")])])
        objects = state_snapshot(bundle, 0)["objects"]
        self.assertEqual(len(objects), 1)
        self.assertEqual(objects[0]["event_ids"], ["A", "B"])

    def test_after_recording_does_not_claim_fresh_observation(self):
        bundle = self.build([event("A", [sample(2, ["new"])])])
        snap = state_snapshot(bundle, 11)
        self.assertEqual(snap["objects"][0]["status"], "unknown")
        self.assertEqual(snap["observation_cutoff_ns"], str(TIMES[-1]))
        self.assertIsNone(snap["source_timestamp_ns"])
        self.assertIsNone(snap["source_snapshot_age_s"])
        self.assertEqual(snap["clock_mode"], "outside_recorded_window")

    def test_unknown_and_explicit_ttl_keep_historical_geometry(self):
        a = event("A", [sample(0, ["old"])])
        a["ttl_s"] = .3
        snap = state_snapshot(self.build([a]), 5)
        self.assertEqual(snap["objects"][0]["status"], "stale")
        self.assertTrue(snap["objects"][0]["historical"])
        self.assertEqual(snap["objects"][0]["last_observed_timestamp_ns"], str(T0))

    def test_sample_hold_advances_explicit_ttl_without_fabricating_observations(self):
        a = event("A", [sample(0, ["old"]), sample(1, ["old"])])
        a["ttl_s"] = .3
        bundle = self.build([a])
        before = state_snapshot(bundle, 3)
        expired = state_snapshot(bundle, 4)
        self.assertEqual(before["objects"][0]["status"], "observed_active")
        self.assertEqual(expired["objects"][0]["status"], "stale")
        self.assertEqual(expired["layer"]["summary"]["stale"], 1)
        self.assertEqual(expired["observation_cutoff_ns"], str(T0))
        self.assertEqual(expired["expiry_evaluation_timestamp_ns"], str(T0+399904000))
        self.assertEqual(expired["layer"]["features"][0]["properties"]["history"],
                         before["layer"]["features"][0]["properties"]["history"])
        self.assertEqual(expired["objects"][0]["geometry_city"], before["objects"][0]["geometry_city"])
        self.assertEqual(state_snapshot(bundle, 5)["objects"][0]["status"], "observed_active")

    def test_absolute_expiry_at_interpolated_step_is_inclusive(self):
        a = event("A", [sample(0, ["old"])])
        a["expires_at_timestamp_ns"] = str(T0+299928000)
        bundle = self.build([a])
        self.assertEqual(state_snapshot(bundle, 2)["objects"][0]["status"], "observed_active")
        expired = state_snapshot(bundle, 3)
        self.assertEqual(expired["source_timestamp_ns"], a["expires_at_timestamp_ns"])
        self.assertEqual(expired["objects"][0]["status"], "stale")
        self.assertEqual(expired["layer"]["features"][0]["properties"]["validity"], "expired")
        self.assertIsNone(expired["layer"]["features"][0]["properties"]["current_quality"])

    def test_source_timestamp_geometry_and_scene_mismatches_rejected(self):
        self.build()
        data = deepcopy(self.dataset)
        data["scene_id"] = "foreign"
        with self.assertRaises(ValueError):
            build_state_source(self.scenario, data, self.root)
        for mutation in ("time", "geometry", "order"):
            data = deepcopy(self.dataset)
            sample_row = data["events"][0]["samples"][1]
            if mutation == "time": sample_row["timestamp_ns"] = str(TIMES[1]+1)
            elif mutation == "geometry": sample_row["geometry_city"]["geometries"][0]["coordinates"][0][0][0] += .1
            else: sample_row["instance_tokens"] = list(reversed(sample_row["instance_tokens"]))
            data["state_event_layer"] = build_state_event_layer(data["events"])
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                build_state_source(self.scenario, data, self.root)


if __name__ == "__main__":
    unittest.main()
