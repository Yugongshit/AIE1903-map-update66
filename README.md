# AIE1903 interactive 3D map comparison

[Download the updated portable share package · ZIP, 10.2 MB](downloads/AIE1903_3D_Map_Share_20261109_v2.zip) — includes source-sized barrier/cone models, both datasets and local launchers.

[中文三维地图](algorithms/zh/index.html?view=3d) · [English 3D map](algorithms/en/index.html?view=3d) · [2D map](algorithms/en/index.html?view=2d)

Current source: [`main` / `a973f1c`](https://github.com/Yugongshit/AIE1903-map-update66/tree/a973f1cce0974d58ef04b6e069f77531e40d02c0). [Implementation and controls](https://github.com/Yugongshit/AIE1903-map-update66/blob/main/docs/3d_map_view_20261109.md) · [Release validation](algorithms/semantic_object_release_validation_20261109.json)

The 23 saved nuScenes state events now show 35 tapered barriers and 11 traffic cones from exact instance/frame matches, using the supplied dimensions and heading. Three debris instances retain their original footprints. Click Inspect barriers / Inspect cones to zoom in, or switch Object shapes off to inspect the source footprints. Yellow still indicates state events; red indicates accepted structure changes. Models are generic category illustrations, not recovered meshes or textures.

Objects retain each event's last observation, with translucent appearance for observations not confirmed at clip end. Their altitude is relative to the display ground; source global Z is not treated as measured road elevation. Source XY, frozen prediction results, state lifecycle, exports and map decisions remain unchanged. Argoverse retains 93 material reference changes and all 27 additions, with the complete 98-change audit separate.

The map supports orbit, pan, zoom, overhead view and mobile gestures. Before and after use one camera, and WebGL failure retains the working 2D comparison. Three.js r180 is bundled locally; no runtime CDN is required.

Validated with 25 source/presentation unit tests, 62 real WebGL checks and 174 retained 2D checks. Result SHA256: `d9e494b00aa1ade1ee472c3123df44745bbfe9d0a35e044589303ff7b31d8b40`.

[Portable share packages and older releases](downloads/index.html). Historical recording workspaces remain available with `?legacy=1`; model predictions and reference replay remain separate. GitHub Pages deployment is checked separately from a successful branch push.
