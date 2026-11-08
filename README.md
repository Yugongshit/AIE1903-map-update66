# AIE1903 before/after map comparison

Source: `14a75beec49ef84184ff7d39df450de8a7efb15e` on `feat/structure-state-routing`.

[English map](algorithms/en/index.html) · [中文地图对照](algorithms/zh/index.html) · [Perception labels and map guide](https://github.com/Yugongshit/AIE1903-map-update66/blob/feat/structure-state-routing/docs/perception_and_composite_map.md)

Drag the vertical divider to the far right for the original map, or far left for the updated presentation. Both sides share the same projection, pan and zoom. The legend is above the map: yellow state events, red confirmed structural changes, and green unconfirmed structural candidates. Current data contain no confirmed structural edits. Click a labeled marker to inspect its details; technical analysis starts collapsed.

This presentation update does not rerun inference or alter saved predictions. Current validation: 3 builder identity tests and 123 local Chromium checks (49 pipeline, 74 map interaction/source checks), in `algorithms/swipe_release_validation.json`. The previous presentation validation remains in `algorithms/composite_release_validation.json`; its 208 core tests were not rerun for this display-only update. The previous full-site validation remains in `release_validation.json`. Original baseline views and historical downloads are preserved. Live Pages delivery was not verified from this environment.
