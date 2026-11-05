# AIE1903 map decisions, evidence and evaluation

English: https://yugongshit.github.io/AIE1903-map-update66/en/index.html#decisions

Chinese: https://yugongshit.github.io/AIE1903-map-update66/zh/index.html#decisions

Release: 2026-11-06. Current dataset: nuScenes / scene-0980. Argoverse remains pending review.

The default workspace lets you select all 29 source candidates, drag a map comparison divider, inspect exact-frame evidence and open the source artifacts behind verification, reliability and map decisions. English retains the original object categories; Chinese translates their display names. The evidence workspace synchronizes known candidates and frames with the existing source scene and embedded official Rerun viewer.

The evaluation workspace separates unknown real accuracy from 126 controlled perturbation cases. Resolved F1, decision coverage and recall over all positive cases appear together. Errors and abstentions can be inspected case by case and source CSV files can be downloaded.

Real base-map writes remain zero. The separate write lab displays saved maintenance-engine outputs on an isolated copy with explicitly invented offsets, coverage and revisits. Actual copy edits, versions and exact geometry rollback are inspectable. The source scene includes the original perception video; geometric projections are not camera photographs or planning recommendations. Planning and cross-vehicle sharing are not integrated.

Source branch: `feature/evidence-workbench-20261104` at `0d90d1fe429b2e36ec4ef2b901aba45a1fc1b626`.

Current validation: 99 core tests; 56 decision-workspace checks; 40 bilingual regression checks; 35 real Rerun/WebGL checks. See `assets/runtime_validation.json` and `version.json`. The older root `validation.json` describes an earlier release. Browser validation covers cloud Chromium and SwiftShader, not physical Mac/Safari or vehicle operation.

Architecture, walkthrough, limits and rebuild commands: [decision-studio.md](https://github.com/Yugongshit/AIE1903-map-update66/blob/feature/evidence-workbench-20261104/docs/decision-studio.md).
