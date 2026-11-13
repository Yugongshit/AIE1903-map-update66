"""Build a self-contained GitHub Pages scene-review site from verified exports.

Native MetaDrive and multiplayer control remain server APIs. This build exposes
the existing offline review, source-bound frame snapshots and Planning exports
without requiring a Python process on the visitor's computer.
"""
from __future__ import annotations

import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_data(path, value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    path.parent.mkdir(parents=True, exist_ok=True)
    compressed = gzip.compress(raw, compresslevel=6, mtime=0)
    path.write_bytes(compressed)
    return {'file': str(path.name), 'sha256': sha256(raw).hexdigest(),
            'uncompressed_bytes': len(raw), 'compressed_bytes': len(compressed)}


def refresh_assets(output, manifest=None):
    """Refresh UI after browser fixes without recomputing verified frame data."""
    output = Path(output).resolve()
    manifest = manifest or json.loads((output/'build_manifest.json').read_text())
    for name in ('simulation_platform.css', 'simulation_platform.js', 'simulation_platform_transport.js'):
        shutil.copyfile(ROOT/'map_update'/name, output/name)
    vendor = output/'vendor'
    vendor.mkdir(exist_ok=True)
    for name in ('three.min.js', 'three.LICENSE'):
        shutil.copyfile(ROOT/'map_update/vendor'/name, vendor/name)
    html = (ROOT/'map_update/simulation_platform.html').read_text()
    html = html.replace('href="/simulation_platform.css"', 'href="./simulation_platform.css"')
    html = html.replace('src="/vendor/three.min.js"', 'src="./vendor/three.min.js"')
    html = html.replace('src="/simulation_platform.js"', 'src="./simulation_platform.js"')
    html = html.replace('src="/simulation_platform_transport.js"', 'src="./simulation_platform_transport.js"')
    html = html.replace('<head>', '<head>\n  <script src="./simulation_public_config.js"></script>', 1)
    if 'simulation_platform_transport.js' not in html:
        html = html.replace('<script src="./simulation_platform.js"',
            '<script src="./simulation_platform_transport.js" defer></script>\n  <script src="./simulation_platform.js"')
    html = html.replace('href="/" class="brand"', 'href="./" class="brand"')
    (output/'index.html').write_text(html)
    (output/'simulation_platform.html').write_text(html)
    (output/'simulation_public_config.js').write_text('window.SIMULATION_PUBLIC_BUILD = '+
        json.dumps({'schema': manifest['schema'], 'chunk_size': manifest['chunk_size'],
                    'compression': 'gzip', 'scene_keys': [row['key'] for row in manifest['scenes']]}, separators=(',', ':'))+';\n')
    (output/'.nojekyll').write_text('')


def build(output, *, chunk_size=20, scene_keys=None):
    from map_update.simulation_platform_data import catalog, load_scene, build_snapshot, scene_database
    from map_update.simulation_validation import validate_database

    output = Path(output).resolve()
    if not 1 <= chunk_size <= 100:
        raise ValueError('chunk_size must be between 1 and 100')
    if output == ROOT or output in ROOT.parents:
        raise ValueError('Choose a dedicated output directory, not the repository root')
    output.mkdir(parents=True, exist_ok=True)
    # Only generated files in this dedicated subtree are replaced. Existing
    # Pages directories such as algorithms/ and review/ are never touched.
    data = output / 'data'
    if data.exists():
        shutil.rmtree(data)
    data.mkdir()
    entries = catalog(ROOT)
    if scene_keys is not None:
        wanted = set(scene_keys)
        entries = [row for row in entries if row['key'] in wanted]
        if {row['key'] for row in entries} != wanted:
            raise ValueError('Unknown public scene selection')
    manifest = {'schema': 'simulation-public-build-1.0', 'mode': 'offline_review',
                'native_engine_included': False, 'compression': 'gzip', 'chunk_size': chunk_size,
                'scenes': [], 'files': {}}
    manifest['files']['catalog'] = write_data(data/'catalog.json.gz', {'scenes': entries})
    metrics = ROOT/'demo/simulation/validation/detection_metrics.json'
    for entry in entries:
        key = entry['key']
        payload = load_scene(ROOT, key)
        directory = data/key
        scene_meta = {'key': key, 'frame_count': payload['frame_count'],
                      'maps': {name: row['map_sha256'] for name, row in payload['versions'].items()},
                      'files': {'scene': write_data(directory/'scene.json.gz', payload)}, 'chunks': []}
        for start in range(0, payload['frame_count'], chunk_size):
            snapshots = [build_snapshot(payload, step, 'updated') for step in
                         range(start, min(payload['frame_count'], start+chunk_size))]
            row = write_data(directory/f'frames-{start//chunk_size:04d}.json.gz', snapshots)
            scene_meta['chunks'].append({'start': start, 'end': start+len(snapshots)-1, **row})
        for version in ('original', 'updated'):
            validation = validate_database(scene_database(ROOT, key, version),
                detection_metrics=metrics if metrics.exists() else None)
            if not validation['integrity']['passed']:
                failures = [row['name'] for row in validation['integrity']['checks'] if row['status'] == 'fail']
                raise ValueError(f'Refusing to publish invalid {key}/{version}: {", ".join(failures)}')
            scene_meta['files']['validation_'+version] = write_data(
                directory/f'validation-{version}.json.gz', validation)
        manifest['scenes'].append(scene_meta)
        print(f'Built {key}: {payload["frame_count"]} source frames', flush=True)
    (output/'build_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    refresh_assets(output, manifest)
    total = sum(path.stat().st_size for path in output.rglob('*') if path.is_file())
    print(f'Public review: {output} ({total/1024/1024:.1f} MiB)', flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--chunk-size', type=int, default=20)
    parser.add_argument('--scene', action='append', help='Build selected catalog keys; default all three')
    parser.add_argument('--refresh-assets', action='store_true', help='Refresh UI files in an existing verified build')
    args = parser.parse_args()
    if args.refresh_assets:
        refresh_assets(args.output)
    else:
        build(args.output, chunk_size=args.chunk_size, scene_keys=args.scene)


if __name__ == '__main__':
    main()
