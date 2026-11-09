# Member 4 Planning handoff: scene-0980

## What this is

Reproducible offline static planning baseline over the 41 recorded snapshots in scene-0980 (19.85016 s). It compares three policies: `map_only`, `fixed_candidate_penalty`, and `reliability_aware`. The package includes the runnable script, frozen input snapshot, per-frame results, map-space GeoJSON, figures, parameters, provenance, and a Member 5 handoff.

## Results

Each policy has 41 records (123 total). For all three policies, static route geometry passed in 7/41 snapshots; stopping geometry passed in 36/41, failed in 4/41, and was not evaluable in 1/41. Behaviors: 5 `NO_VERIFIED_PLAN`, 29 `STOP_CANDIDATE_UNVERIFIED`, and 7 `VERIFY_REQUIRED`. Mean runtime was about 57-58 ms per snapshot. The policies produced the same aggregate counts in this scene; do not claim a planning improvement from these results.

`static_route_geometry_feasible` means only that the frozen candidate geometry passed the implemented static checks against the prior map and supplied obstacle boxes. It is not a drivable route certified by observed free space. The package has 0 verified-observed-space records and 0 execution-authorized records. Treat routes as proposals for analysis, not executed trajectories or safety claims.

## Run

Python 3.12 recommended. Install dependencies:

```powershell
python -m pip install -r requirements.txt
```

Reproduce using only bundled inputs:

```powershell
python run_planning.py --inputs inputs --output output_reproduced
```

From the repository, `planning_m4/run_planning.py` can also be run against this package's `inputs` directory. The local temporary dependency bundle used by the original workspace is not included.

## Files to start with

- `planning_summary.csv`: one row per frame and policy; main handoff table.
- `planning_results.jsonl`: full decisions, route candidates, reasons, and measurements.
- `summary.json`: aggregate counts and runtime.
- `routes_city.geojson`, `obstacles_city.geojson`: metric City XY geometries (not WGS84).
- `figures/`: selected snapshots for visual inspection.
- `map_graph_audit.json`: graph construction, repairs, and dangling successor references.
- `candidate_evidence_used.jsonl`: upstream candidate evidence and provenance used by planning.
- `m5_handoff.json`: schema and join keys for the final map-update/evaluation module.
- `run_manifest.json`: parameters, source hashes, and provenance.
- `inputs/`: frozen inputs needed to reproduce the run.

## Limitations and next integration steps

1. This is one short nuScenes scene and one prior map. The destination region is fixed for a controlled policy comparison.
2. Obstacle boxes come from annotations and candidate regions. The source evidence says occupancy geometry alone is not confirmed road closure or HD-map error.
3. Member 3's final reliability review is available only at scene end in this offline replay. Earlier snapshots use prefix-only candidate evidence; do not leak future information into online decisions.
4. Unknown visibility stays unknown. Do not interpret it as clear space or zero risk.
5. Dynamic prediction, lane-change edges, traffic signals, and observed free-space validation are unavailable. The result is a static MVP.
6. Member 2's Argoverse archive was not integrated: it could not be opened as a valid ZIP, and it represents a different scene. This run uses scene-0980 inputs from the sensor/map/processed data and Member 3's reliability handoff.
7. Before calling a route executable, integrate valid free-space/visibility evidence, obstacle dimensions and timing, and a common interface from Members 1-3. Preserve `scene_id`, `map_sha256`, frame/time, and candidate IDs in all joins.
