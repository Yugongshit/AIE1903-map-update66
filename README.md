# AIE1903 interactive 3D map comparison

[中文三维地图](algorithms/zh/index.html?view=3d) · [English 3D map](algorithms/en/index.html?view=3d) · [2D map](algorithms/en/index.html?view=2d)

Current source: [`main` / `1d4a0b9`](https://github.com/Yugongshit/AIE1903-map-update66/tree/1d4a0b96f5c5fcd6e44b8d9977ca45d226a3c917). [3D implementation and controls](https://github.com/Yugongshit/AIE1903-map-update66/blob/main/docs/3d_map_view_20261109.md) · [Release validation](algorithms/three_dimensional_release_validation_20261109.json)

The default map now supports 3D orbit, pan, zoom, overhead view and mobile two-finger gestures. Before and after retain a single shared camera; drag the divider right for the original and left for the updated map. Click yellow state events or red accepted structures for the saved evidence. Use the 2D button to return to the original flat renderer. WebGL failures automatically retain the working 2D comparison.

The map positions and boundaries are supplied XY geometry. Road display offsets and 1 m state extrusions are schematic, not measured elevation or building reconstruction. All inference results and exported source geometry are unchanged. NuScenes retains 23 yellow states and 6 closed no-update candidates; Argoverse reference replay retains 93 material changes and all 27 additions. The complete 98-change reference audit remains separate and available.

Three.js 0.180.0 is bundled locally with its MIT license and integrity manifest; no runtime CDN is needed. Validated with 14 presentation identity tests, 44 real WebGL browser checks and 174 retained 2D browser checks. Actual WebGL context loss, missing dependencies, mobile gestures, clipping andsource immutability are covered. Result SHA256: `d9e494b00aa1ade1ee472c3123df44745bbfe9d0a35e044589303ff7b31d8b40`.

[Material-change method](https://github.com/Yugongshit/AIE1903-map-update66/blob/main/docs/material_structural_decisions_20261109.md). Historical recordings remain accessible with `?legacy=1`; older releases and downloads are retained. GitHub Pages delivery is checked separately from a successful branch push.
