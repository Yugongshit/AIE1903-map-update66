"""Build a standalone bilingual view of an actual Argoverse pipeline rerun."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def build(result_path, output_path):
    result_path, output = Path(result_path), Path(output_path)
    data = json.loads(result_path.read_text())
    if data.get('schema') != 'argoverse-rerun-1.0' or data.get('dataset_id') != 'argoverse':
        raise ValueError('An Argoverse rerun result is required')
    for key in ['summary', 'stages', 'candidates', 'evaluation', 'map', 'sources']:
        if key not in data:
            raise ValueError('Missing rerun result field: ' + key)
    if output.exists():
        raise ValueError('Choose a new output directory')
    (output / 'assets').mkdir(parents=True)
    shutil.copy2(result_path, output / 'result.json')
    for suffix in ['css', 'js']:
        shutil.copy2(ROOT / 'map_update' / ('argoverse_studio.' + suffix), output / 'assets')
    template = (ROOT / 'map_update/argoverse_studio.html').read_text()
    serialized = json.dumps(data, ensure_ascii=False, allow_nan=False).replace('<', r'\u003c').replace('>', r'\u003e').replace('&', r'\u0026')
    asset_hash = sha256(b''.join((output / 'assets' / ('argoverse_studio.' + suffix)).read_bytes() for suffix in ['css', 'js'])).hexdigest()[:12]
    for language in ['en', 'zh']:
        route = output / language
        route.mkdir()
        page = template.replace('__LANG__', language).replace('__RESULT__', serialized).replace('__REVISION__', asset_hash)
        (route / 'index.html').write_text(page)
    (output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=en/index.html"><a href="en/index.html">Argoverse rerun</a>\n')
    (output / 'version.json').write_text(json.dumps({'schema': 'argoverse-studio-1.0', 'result_sha256': sha256(result_path.read_bytes()).hexdigest(), 'run_id': data['run_id'], 'asset_revision': asset_hash}, indent=2) + '\n')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.result, args.out))
