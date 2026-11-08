# AIE1903 map comparison and structural evidence

Source: `f41880667d7cf026f9fb1eee4aa7599f56bb4985` on `feat/structure-state-routing`.

[English map](algorithms/en/index.html) · [中文地图](algorithms/zh/index.html) · [Method and threshold audit](https://github.com/Yugongshit/AIE1903-map-update66/blob/feat/structure-state-routing/docs/structural_evidence_revision_20261108.md)

Drag the divider right for the original map and left for the updated presentation. Yellow identifies state events, red confirmed structural changes, and green unconfirmed structural candidates. All real structural candidates remain unconfirmed; no base-map patch was executed.

The revised nuScenes adapter computes exact-window correspondence instead of assigning every marking unknown. LD004 has six fixed-anchor frames supporting a local discrepancy; the reference divider is also observed in the same window, so an old-line displacement or replacement is not confirmed. The other five marking candidates retain explicit reasons. All 23 state records are unchanged. Argoverse NONE references produce explicit paint-presence hypotheses; its deployed geometric thresholds and 1/70/5 reliability counts remain unchanged. The threshold audit records that raising MAD tolerance adds a false positive, not a verified improvement.

Structural eligibility now depends on the edit operation: geometry requires complete geometry, attributes require verified properties, and deletion requires fully observed same-target support. Shared physical references remain blocked until synchronized edits are verified.

Validation: 232 core tests and 142 local Chromium checks (63 pipeline, 79 map), in `algorithms/structural_evidence_release_validation.json`. Sensor neural-network inference was not rerun; existing observations were reprocessed. Prior validation records, baseline views and historical downloads are retained. Live GitHub Pages delivery was not verified from this environment.
