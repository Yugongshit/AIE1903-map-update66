# AIE1903 scene and map-maintenance studio

Live site: https://yugongshit.github.io/AIE1903-map-update66/

Current dataset: nuScenes / scene-0980. Argoverse remains pending review.

The scene view uses actual map geometry, camera-exposure poses, annotations and recorded ego positions. It is explicitly a geometric reconstruction, not camera photography or a planning recommendation. The original supplied perception screen recording is included separately. Camera photos can be selected locally in the browser.

The write experiment runs the existing maintenance engine on a copy of one source map line, with explicitly invented offsets, coverage and revisit evidence. Before/after geometries, patches, version history and rollback are inspectable. No real base-map updates are claimed.

The third page has eleven concrete data / implementation / evaluation issues and a one-click Word report. Existing evidence review and local draft storage remain available in `review/index.html`.

Source branch: `feature/evidence-workbench-20261104` at `88c185b`.

Validation: 80 unit tests; 40 studio interaction checks; 36 evidence-workbench checks. 17 upstream structured outputs and 13 M3 files reproduce. See `validation.json`.
