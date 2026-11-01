scene-0980 | HD Map Discrepancy Candidates | Step 1 V1

PRIMARY OUTPUT
processed/scene0980_unified_candidates_v1.csv

SOURCE INDEX
processed/scene0980_unified_manifest_v1.json

VALIDATION
processed/scene0980_delivery_validation_v1.json

CONTENTS
- 23 road occupancy candidate records.
- 6 lane-marking discrepancy candidate records.
- 29 candidate records in total.
- 57 camera-derived lane-curve observations.
- Per-frame geometry and map-context evidence.
- Map divider geometry and occupancy measurements.
- Selected visual review images.

INTERPRETATION
All events are unverified candidates.
29 records do not imply 29 independent road changes.

Road occupancy evidence uses nuScenes 3D ground-truth
annotations and prior LiDAR checks.
Lane marking evidence uses camera perception, BEV
projection, cross-frame geometry and HD Map comparison.

Four lane-marking events have not been visually reviewed.
Track IDs are provisional.
Confidence scores and cross-family deduplication
are not included.

The extracted divider lines and context tables are
included, but the full original nuScenes dataset,
camera/LiDAR streams and map database are not.
This package supports downstream evidence review and
candidate scoring; it cannot independently reproduce
the complete perception pipeline.

All source paths are relative to this package root.
