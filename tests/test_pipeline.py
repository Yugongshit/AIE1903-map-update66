from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

from map_update.adapters import adapt_proposal
from map_update.demo import T0, base_map, demo_records, line, record
from map_update.engine import Maintainer, Policy, rollback
from map_update.geometry import line_distance
from map_update.output import write_bundle


def visits(kind="geometry_change", target="line-1", geometry=None):
    return [record(f"r{i}", f"v{i}", hour, kind, target, geometry) for i, hour in enumerate([0, 12, 26])]


def run(rows, **kwargs):
    return Maintainer(base_map()).run(rows, **kwargs)


class PipelineTests(unittest.TestCase):
    def test_one_visit_many_high_score_frames_never_promotes(self):
        rows = [record(f"r{i}", "one-drive", i/3600) for i in range(50)]
        bundle = run(rows)
        self.assertEqual(len(bundle["versions"]), 1)
        self.assertEqual(bundle["events"][0]["independent_visits"], 1)

    def test_distinct_ids_in_same_clip_do_not_make_revisits(self):
        rows = [record(f"r{i}", f"v{i}", i/3600) for i in range(10)]
        self.assertEqual(run(rows)["events"][0]["independent_visits"], 1)

    def test_three_consistent_visits_update_only_target(self):
        base = base_map()
        bundle = run(visits())
        self.assertEqual(len(bundle["versions"]), 2)
        self.assertEqual(bundle["events"][0]["status"], "applied")
        self.assertEqual(bundle["current_map"]["elements"][1]["geometry_city"], line(4.3))
        for index in (0, 2, 3):
            self.assertEqual(bundle["current_map"]["elements"][index], base["elements"][index])
        self.assertEqual(bundle["versions"][1]["patch"]["evidence_record_ids"], ["r0", "r1", "r2"])
        self.assertEqual(bundle["baseline"], base)

    def test_future_evidence_not_used(self):
        bundle = run(visits(), as_of_ns=str(T0+13*3600*10**9))
        self.assertEqual(len(bundle["evidence"]), 2)
        self.assertEqual(len(bundle["versions"]), 1)

    def test_exact_duplicate_and_reordering_are_idempotent(self):
        rows = visits()
        self.assertEqual(run(rows), run(list(reversed(rows))+[rows[0]]))

    def test_conflicting_duplicate_rejected(self):
        rows = visits()
        other = deepcopy(rows[0]); other["evidence_score"] = .5
        with self.assertRaisesRegex(ValueError, "record_id reused"):
            run(rows+[other])

    def test_bad_reference_or_map_version_rejected(self):
        for key, value in [("map_version", "wrong"), ("map_id", "another-map"), ("coordinate_reference", {"frame": "Ego", "units": "m", "city_id": "other"})]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                rows = visits(); rows[0][key] = value; run(rows)

    def test_current_map_not_silently_used_as_prior(self):
        base = base_map(); base["map_role"] = "current_base_map"
        with self.assertRaisesRegex(ValueError, "prior-map"):
            Maintainer(base)

    def test_missing_m3_cannot_be_replaced_by_score(self):
        rows = visits()
        for r in rows:
            r["reliability"] = None
        bundle = run(rows)
        self.assertEqual(len(bundle["versions"]), 1)
        self.assertTrue(all(a["reason"] == "missing_reliability" for a in bundle["audit"]))

    def test_quality_gates_block_bad_evidence(self):
        for field, value in [("alignment_valid", False), ("effective_coverage", .3), ("decision", "Needs Further Verification")]:
            rows = visits()
            for r in rows:
                r["reliability"][field] = value
            with self.subTest(field=field):
                self.assertEqual(len(run(rows)["versions"]), 1)

    def test_occupancy_never_edits_base_even_after_revisits(self):
        bundle = run(visits("occupancy", None))
        self.assertEqual(bundle["current_map"], base_map())
        self.assertEqual(bundle["events"][0]["status"], "temporary")
        self.assertEqual(len(bundle["planning_view"]["temporary_events"]), 1)

    def test_absence_expires_but_does_not_clear(self):
        bundle = run([record(kind="occupancy", target=None)], as_of_ns=str(T0+48*3600*10**9))
        event = bundle["events"][0]
        self.assertTrue(event["stale"])
        self.assertFalse(event["clearance_confirmed"])
        self.assertEqual(event["status"], "temporary")
        self.assertTrue(bundle["timeline"][-1]["events"][0]["stale"])

    def test_recovery_needs_explicit_good_observation(self):
        positive = record(kind="occupancy", target=None)
        recovery = record("recovery", "v2", 12, "occupancy", None)
        recovery["observation"] = "supports_prior"
        recovery["support_frame_count"] = 0
        recovery["reliability"]["decision"] = "No Significant Mismatch"
        recovery["fully_observed"] = False
        self.assertEqual(run([positive, recovery])["events"][0]["status"], "temporary")
        recovery["fully_observed"] = True
        self.assertEqual(run([positive, recovery])["events"][0]["status"], "resolved")

    def test_distinct_obstacles_on_one_element_keep_geometry_and_clear_separately(self):
        def footprint(y):
            return {"type": "Polygon", "coordinates": [
                [[3., y], [4., y], [4., y+1.], [3., y+1.], [3., y]]
            ]}

        a = record("obstacle-A", "drive", 0, "occupancy", "line-1", footprint(5.))
        b = record("obstacle-B", "drive", .01, "occupancy", "line-1", footprint(20.))
        a["change_id"], b["change_id"] = "A", "B"
        observed = run([a, b, deepcopy(a)])
        self.assertEqual(len(observed["events"]), 2)
        self.assertEqual([e["geometry_city"] for e in observed["events"]],
                         [a["geometry_city"], b["geometry_city"]])
        self.assertEqual([e["record_ids"] for e in observed["events"]],
                         [["obstacle-A"], ["obstacle-B"]])

        # This observation covers A's own occupied region, not the whole lane
        # or B. fully_observed is scoped to the identified occupancy event.
        recovery = record("clear-A", "visit2", 2, "occupancy", "line-1", footprint(5.))
        recovery.update(change_id="A", observation="supports_prior", support_frame_count=0)
        recovery["reliability"]["decision"] = "No Significant Mismatch"
        bundle = run([a, b, recovery])
        events = {e["change_ids"][0]: e for e in bundle["events"]}
        self.assertEqual(set(events), {"A", "B"})
        self.assertEqual(events["A"]["status"], "resolved")
        self.assertTrue(events["A"]["clearance_confirmed"])
        self.assertEqual(events["B"]["status"], "temporary")
        self.assertFalse(events["B"]["clearance_confirmed"])
        self.assertEqual(events["B"]["geometry_city"], b["geometry_city"])
        self.assertEqual(events["B"]["record_ids"], ["obstacle-B"])
        self.assertEqual(bundle["planning_view"]["temporary_events"], [events["B"]])
        self.assertEqual(bundle["current_map"], base_map())

    def test_contradiction_resets_accumulation(self):
        rows = visits()
        normal = record("normal", "v-normal", 13)
        normal["observation"] = "supports_prior"
        normal["reliability"]["decision"] = "No Significant Mismatch"
        bundle = run(rows+[normal])
        self.assertEqual(len(bundle["versions"]), 1)
        self.assertEqual(bundle["events"][0]["independent_visits"], 1)

    def test_unstable_or_missing_or_partial_geometry_blocks_write(self):
        for geometry, reason in [(line(5.5), "unstable_geometry_across_visits"), (None, "missing_observed_line_geometry")]:
            rows = visits(); rows[-1]["geometry_city"] = geometry
            with self.subTest(reason=reason):
                bundle = run(rows)
                self.assertEqual(len(bundle["versions"]), 1)
                self.assertEqual(bundle["events"][0]["reason"], reason)
        bundle = run(visits(geometry=line(4.3, 10, 20)))
        self.assertEqual(bundle["events"][0]["reason"], "partial_or_incompatible_geometry_do_not_replace_whole_feature")

    def test_ld_is_not_geometry_change(self):
        bundle = run(visits("line_discrepancy"))
        self.assertEqual(len(bundle["versions"]), 1)
        self.assertEqual(bundle["events"][0]["status"], "long_term_candidate")

    def test_delete_is_deactivation_only_with_full_visibility(self):
        rows = visits("delete")
        for r in rows:
            r["geometry_city"] = None
            r["fully_observed"] = False
        self.assertEqual(len(run(rows)["versions"]), 1)
        for r in rows:
            r["fully_observed"] = True
        bundle = run(rows)
        self.assertFalse(bundle["current_map"]["elements"][1]["active"])
        self.assertEqual(len(bundle["current_map"]["elements"]), 4)

    def test_add_associates_spatially_across_different_ids(self):
        rows = visits("add", None, line(15))
        for i, r in enumerate(rows):
            r["change_id"] = f"clip-local-id-{i}"
        bundle = run(rows)
        self.assertEqual(len(bundle["events"]), 1)
        self.assertEqual(len(bundle["current_map"]["elements"]), 5)
        self.assertEqual(len(bundle["versions"]), 2)

    def test_duplicate_add_and_noop_geometry_blocked(self):
        self.assertEqual(len(run(visits("add", None, line(3.5)))["versions"]), 1)
        self.assertEqual(len(run(visits(geometry=line(3.5)))["versions"]), 1)

    def test_marking_change_preserves_geometry(self):
        rows = visits("marking_change")
        for r in rows:
            r["marking_type"] = "dashed_white"
        bundle = run(rows)
        after = bundle["current_map"]["elements"][1]
        self.assertEqual(after["properties"]["marking_type"], "dashed_white")
        self.assertEqual(after["geometry_city"], base_map()["elements"][1]["geometry_city"])

    def test_competing_changes_do_not_overwrite_changed_target(self):
        rows = visits()
        for i, r in enumerate(visits("marking_change")):
            r["record_id"] = f"marking-{i}"
            r["change_id"] = "marking-candidate"
            r["marking_type"] = "dashed_white"
            r["timestamp_ns"] = str(int(r["timestamp_ns"])+1000)
            rows.append(r)
        bundle = run(rows)
        self.assertEqual(len(bundle["versions"]), 2)
        self.assertEqual(bundle["events"][1]["reason"], "target_changed_since_baseline")

    def test_reverse_orientation_and_sampling_density(self):
        self.assertLess(line_distance([[0, 0], [0, 10]], [[0, 10], [0, 5], [0, 0]]), 1e-8)

    def test_invalid_numbers_and_timestamp_precision_rejected(self):
        for value in (float('nan'), float('inf'), True):
            rows = visits(); rows[0]["geometry_city"]["coordinates"][0][0] = value
            with self.assertRaises(ValueError):
                run(rows)
        rows = visits(); rows[0]["timestamp_ns"] = float(T0)
        with self.assertRaises(ValueError):
            run(rows)

    def test_rollback_creates_new_history_and_restores_snapshot(self):
        original = run(visits())
        result = rollback(original, "v0")
        self.assertEqual(len(result["versions"]), 3)
        self.assertEqual(result["current_map"]["elements"], base_map()["elements"])
        self.assertEqual(result["versions"][-1]["parent"], original["current_map"]["version"])
        self.assertEqual(len(original["versions"]), 2)
        self.assertEqual(result["events"][0]["status"], "review_required")

    def test_output_refuses_overwrite_and_escapes_embedded_html(self):
        bundle = run(demo_records())
        bundle["baseline"]["provenance"] = '</script><script>alert("x")</script>'
        with tempfile.TemporaryDirectory() as temp:
            path = write_bundle(bundle, Path(temp)/"out")
            self.assertEqual(json.loads((path/"map.json").read_text())["version"], bundle["current_map"]["version"])
            self.assertNotIn('</script><script>alert', (path/"index.html").read_text())
            with self.assertRaises(ValueError):
                write_bundle(bundle, path)

    def test_policy_validation(self):
        for kw in ({"min_visits": 1}, {"min_coverage": 2}, {"min_visit_gap_s": -1}):
            with self.assertRaises(ValueError):
                Policy(**kw)


class AdapterTests(unittest.TestCase):
    def payload(self):
        r = record()
        context = {k: r[k] for k in ("map_id", "map_version", "visit_id", "source", "coordinate_reference")}
        rel = {k: r[k] for k in ("map_id", "map_version", "visit_id", "change_id", "map_element_id", "coordinate_reference")}
        rel.update(r["reliability"])
        rel.update(window_start_ns=str(T0), window_end_ns=str(T0+10**9))
        return {"context": context, "records": [r], "reliability_records": [rel]}

    def test_exact_context_and_window_join(self):
        payload = self.payload()
        self.assertEqual(adapt_proposal(payload)[0]["reliability"]["decision"], "Suspected Map Change")
        payload["reliability_records"][0]["visit_id"] = "wrong-visit"
        self.assertIsNone(adapt_proposal(payload)[0]["reliability"])

    def test_no_match_and_overlapping_window(self):
        payload = self.payload()
        payload["reliability_records"] *= 2
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            adapt_proposal(payload)
        payload["reliability_records"] = []
        self.assertIsNone(adapt_proposal(payload)[0]["reliability"])

    def test_timestamp_not_fabricated(self):
        payload = self.payload(); del payload["records"][0]["timestamp_ns"]
        with self.assertRaises(ValueError):
            adapt_proposal(payload)


if __name__ == "__main__":
    unittest.main()
