"""Build a separate bilingual comparison of the executed structure/state pipeline."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def build(result_path, output_path):
    result_path, output = Path(result_path), Path(output_path)
    data = json.loads(result_path.read_text())
    if data.get('schema') != 'change-pipeline-v3':
        raise ValueError('A change-pipeline-v3 structure/state result is required')
    for dataset in ['nuscenes', 'argoverse']:
        if dataset not in data.get('datasets', {}):
            raise ValueError('Missing dataset result: ' + dataset)
        for field in ['summary', 'events']:
            if field not in data['datasets'][dataset]:
                raise ValueError('Missing dataset field: ' + field)
    if any('map_layer' not in event for dataset in data['datasets'].values() for event in dataset['events']):
        raise ValueError('Every displayed event requires a map-layer classification')
    if output.exists():
        raise ValueError('Choose a new output directory')
    (output / 'assets').mkdir(parents=True)
    shutil.copy2(result_path, output / 'result.json')
    for name, dataset in data['datasets'].items():
        filename = name + '_state_layer.geojson'
        original = result_path.parent / filename
        if original.exists() and json.loads(original.read_text()) == dataset['state_event_layer']:
            shutil.copy2(original, output / filename)
        else:
            (output / filename).write_text(json.dumps(dataset['state_event_layer'], ensure_ascii=False, indent=2) + '\n')
    for suffix in ['css', 'js']:
        shutil.copy2(ROOT / 'map_update' / ('pipeline_studio.' + suffix), output / 'assets')
    template = (ROOT / 'map_update/pipeline_studio.html').read_text()
    view = deepcopy(data)
    for dataset in view['datasets'].values():
        dataset.pop('observation_decisions', None)
        for event in dataset['events']:
            for sample in event.get('samples', []):
                sample.pop('geometry_city', None)
    view['presentation'] = {'kind': 'derived_view', 'source_result_sha256': sha256(result_path.read_bytes()).hexdigest(),
        'omitted_fields': ['datasets.*.observation_decisions', 'datasets.*.events.*.samples.*.geometry_city'],
        'event_geometry_layer_classification_and_lifecycle_preserved': True}
    serialized = json.dumps(view, ensure_ascii=False, allow_nan=False).replace('<', r'\u003c').replace('>', r'\u003e').replace('&', r'\u0026')
    revision = sha256(b''.join((output / 'assets' / ('pipeline_studio.' + suffix)).read_bytes() for suffix in ['css', 'js'])).hexdigest()[:12]
    for language in ['en', 'zh']:
        route = output / language
        route.mkdir()
        (route / 'index.html').write_text(template.replace('__LANG__', language).replace('__RESULT__', serialized).replace('__REVISION__', revision))
    (output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=en/index.html"><a href="en/index.html">Map-layer pipeline</a>\n')
    (output / 'version.json').write_text(json.dumps({'schema': 'change-pipeline-studio-3.0', 'result_sha256': sha256(result_path.read_bytes()).hexdigest(), 'asset_revision': revision}, indent=2) + '\n')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    print(build(args.result, args.out))
