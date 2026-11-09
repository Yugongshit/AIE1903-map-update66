# AIE1903 map structure and state events

Current source: [`main` / `94638e4`](https://github.com/Yugongshit/AIE1903-map-update66/tree/94638e4d2a5d538c17babdd9ea4d635621bfb823). Application code and the original Planning delivery are now together in main.

[中文地图](algorithms/zh/index.html) · [English map](algorithms/en/index.html) · [Method and results](https://github.com/Yugongshit/AIE1903-map-update66/blob/main/docs/material_structural_decisions_20261109.md) · [Validation](algorithms/material_structural_release_validation_20261109.json)

The map uses completed actions from the supplied recordings. NuScenes retains 23 yellow state events; its six marking candidates close without structural updates, with no pending or revisit queue. Local residual diagnostics remain inspectable but do not directly become red structural changes.

Argoverse's separate reference replay applies 93 material changes, including all 27 additions, and skips five minor geometry differences. The complete 98-change reference audit remains available. Reference replay is not model detection accuracy, and a completed no-update action is not a ground-truth negative.

For same-target geometry, the shared engineering policy requires a displacement above 0.5 m over at least 3 m of continuous affected length. Adaptive interval checks prevent small oscillations from joining into a false long change. Verified additions, deletions and property changes have their own operation checks.

Drag the divider right for the original map and left for the updated map. Yellow denotes state events; red denotes accepted material structure. Closed no-update candidates have no change overlay. All source inputs and the full reference exports are preserved; updated map copies have exact rollback artifacts.

Validated with 290 core tests, 174 browser checks and 111 entrypoint routing checks. Default and old `#decisions` links open the current view; `?legacy=1` and other historical routes retain the older evidence studio. Sensor inference was not retrained. Live GitHub Pages delivery is checked separately from a successful branch push.
