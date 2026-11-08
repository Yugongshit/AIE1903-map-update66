# AIE1903 composite map and perception labels

Source: `6881840a75df3d30086288a4c397191f3d3739bc` on `feat/structure-state-routing`.

[English map](algorithms/en/index.html) · [中文大地图](algorithms/zh/index.html) · [Perception labels and map guide](https://github.com/Yugongshit/AIE1903-map-update66/blob/feat/structure-state-routing/docs/perception_and_composite_map.md)

The composite view overlays supplied gray base geometry, red stored state events and yellow dashed structural hypotheses in the same source coordinate frame. Click a map marker to inspect the corresponding evidence. Source perception categories and their provenance are available in the expandable guide. Candidate display does not imply a base-map edit.

This presentation update does not rerun inference or alter the saved predictions. Current validation: 208 core tests and 90 local Chromium checks, in `algorithms/composite_release_validation.json`. The previous full-site validation remains in `release_validation.json`; original baseline views and historical downloads are preserved. Live Pages delivery was not verified from this environment.
