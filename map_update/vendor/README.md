# Local Three.js distribution

`three.min.js` is Three.js **0.180.0 / r180**, bundled as a browser IIFE exposing `THREE`. It is served locally and requires no CDN or runtime package download. The upstream MIT license is included in `three.LICENSE`; exact upstream package integrity and local file SHA256 values are in `three.manifest.json`.

To reproduce, install `three@0.180.0` and `esbuild@0.25.11` into a temporary directory, then run its esbuild executable against `node_modules/three/build/three.module.js` with the flags recorded in the manifest and `--outfile=map_update/vendor/three.min.js`. Copy the upstream `three/LICENSE` unchanged. Package versions are pinned; no runtime npm installation is needed.

The map renderer uses recorded XY geometry on a flat plane. Extrusion and lift are display aids, not measured object heights or reconstructed terrain.
