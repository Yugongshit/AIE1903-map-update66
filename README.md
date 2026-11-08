# AIE1903 map comparison and structural evidence

Latest update: nuScenes local detection and status audit. [Chinese nuScenes result](algorithms/zh/index.html?dataset=nuscenes&event=S0980-LD-004) · [English](algorithms/en/index.html?dataset=nuscenes&event=S0980-LD-004). The page now shows 23 annotation-backed states, 1 computed local structural detection, and 5 insufficient structural candidates. Final structural uncertainty is 5/6 (83.3%); the requested ≤5% target is explicitly not achieved. Object-motion uncertainty is a separate source statistic. Argoverse predictions, all four reference map exports, and both datasets' state-layer exports remain unchanged. Validated with 255 core tests and 166 browser checks. [Validation](algorithms/nuscenes_confirmation_release_validation.json).


Latest update: Argoverse confirmation repair. [Chinese known-change replay](algorithms/zh/index.html?dataset=argoverse) · [English](algorithms/en/index.html?dataset=argoverse). The reference view replays 98 delivered ground-truth changes on an isolated map copy. The separate model view has 14 local detections: 9 changed-boundary identity hits, 5 false positives, and only 3 detections within the 0.5 m same-anchor geometry tolerance. Model map patches remain 0; reference-copy patches are 98. Validated with 245 core tests and 153 browser checks. [Validation](algorithms/confirmation_release_validation.json). Earlier entries below describe prior releases.


Source: `f41880667d7cf026f9fb1eee4aa7599f56bb4985` on `feat/structure-state-routing`.

[English map](algorithms/en/index.html) · [中文地图](algorithms/zh/index.html) · [Method and threshold audit](https://github.com/Yugongshit/AIE1903-map-update66/blob/feat/structure-state-routing/docs/structural_evidence_revision_20261108.md)

Drag the divider right for the original map and left for the updated presentation. Yellow identifies state events, red confirmed structural changes, and green unconfirmed structural candidates. All real structural candidates remain unconfirmed; no base-map patch was executed.

The revised nuScenes adapter computes exact-window correspondence instead of assigning every marking unknown. LD004 has six fixed-anchor frames supporting a local discrepancy; the reference divider is also observed in the same window, so an old-line displacement or replacement is not confirmed. The other five marking candidates retain explicit reasons. All 23 state records are unchanged. Argoverse NONE references produce explicit paint-presence hypotheses; its deployed geometric thresholds and 1/70/5 reliability counts remain unchanged. The threshold audit records that raising MAD tolerance adds a false positive, not a verified improvement.

Structural eligibility now depends on the edit operation: geometry requires complete geometry, attributes require verified properties, and deletion requires fully observed same-target support. Shared physical references remain blocked until synchronized edits are verified.

Validation: 232 core tests and 142 local Chromium checks (63 pipeline, 79 map), in `algorithms/structural_evidence_release_validation.json`. Sensor neural-network inference was not rerun; existing observations were reprocessed. Prior validation records, baseline views and historical downloads are retained. Live GitHub Pages delivery was not verified from this environment.
