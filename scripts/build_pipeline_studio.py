"""Build a bilingual before/after map comparison from saved source evidence."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.map_context import build_map_contexts


def build(result_path, output_path, raw_root=None, delivery_root=None):
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
    contexts = build_map_contexts(raw_root or ROOT/'AIE1903 原始资料',
                                  delivery_root or ROOT/'external/perception_delivery')
    guide_path = ROOT/'docs/perception_labels_20261108.json'
    guide = json.loads(guide_path.read_text())
    prediction_sources = {item['path']: item['sha256'] for item in data['sources']}
    for name, details in guide['datasets'].items():
        if details['scene_id'] != data['datasets'][name]['scene_id']:
            raise ValueError('Perception guide and prediction scene differ: ' + name)
        for source in details['source_files']:
            path = (ROOT/source['path']).resolve()
            if not path.is_relative_to(ROOT) or sha256(path.read_bytes()).hexdigest() != source['sha256']:
                raise ValueError('Perception guide source identity differs: ' + source['path'])
            prediction_path = source['path'].removeprefix('AIE1903 原始资料/')
            if prediction_path in prediction_sources and prediction_sources[prediction_path] != source['sha256']:
                raise ValueError('Perception guide differs from prediction inputs: ' + source['path'])
    for name, context in contexts.items():
        if context['scene_id'] != data['datasets'][name]['scene_id']:
            raise ValueError('Map context and prediction scene differ: ' + name)
        if name == 'nuscenes':
            expected_hash = data['datasets'][name]['provenance']['map_sha256']
        else:
            matches = [item['sha256'] for item in data['sources']
                       if item['role'] == 'frozen_prediction_input' and item['path'].endswith('/prior_map.json')]
            if len(matches) != 1:
                raise ValueError('A unique frozen Argoverse prior map is required')
            expected_hash = matches[0]
        if context['provenance']['map_sha256'] != expected_hash:
            raise ValueError('Map context differs from the map used for prediction: ' + name)
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
    view['perception_guide'] = guide
    shutil.copy2(guide_path, output/'perception_guide.json')
    for name, dataset in view['datasets'].items():
        dataset['map_context'] = contexts[name]
        (output/(name+'_map_context.json')).write_text(json.dumps(contexts[name], ensure_ascii=False, indent=2)+'\n')
        dataset.pop('observation_decisions', None)
        for event in dataset['events']:
            for sample in event.get('samples', []):
                sample.pop('geometry_city', None)
    view['presentation'] = {'kind': 'derived_view', 'source_result_sha256': sha256(result_path.read_bytes()).hexdigest(),
        'omitted_fields': ['datasets.*.observation_decisions', 'datasets.*.events.*.samples.*.geometry_city'],
        'added_fields': ['datasets.*.map_context', 'perception_guide'],
        'map_context_source': 'Same supplied reference maps as prediction; current evaluation maps excluded',
        'comparison_contract': {
            'left_view': 'original_reference_map', 'right_view': 'updated_presentation',
            'divider_leftmost': 'full_updated_presentation',
            'divider_rightmost': 'full_original_reference_map',
            'shared_coordinate_projection': True,
            'colors': {'map_state_event': 'yellow', 'confirmed_structure': 'red',
                       'unconfirmed_structure': 'green'},
            'confirmation': 'Executed structural patch, or explicitly eligible verified structural edit. Local conflict support alone is insufficient.',
            'updated_view_includes_unconfirmed_candidates': True,
            'source_base_map_mutated': False},
        'event_geometry_layer_classification_and_lifecycle_preserved': True}
    serialized = json.dumps(view, ensure_ascii=False, allow_nan=False).replace('<', r'\u003c').replace('>', r'\u003e').replace('&', r'\u0026')
    revision = sha256(b''.join((output / 'assets' / ('pipeline_studio.' + suffix)).read_bytes() for suffix in ['css', 'js'])).hexdigest()[:12]
    for language in ['en', 'zh']:
        route = output / language
        route.mkdir()
        (route / 'index.html').write_text(template.replace('__LANG__', language).replace('__RESULT__', serialized).replace('__REVISION__', revision))
    (output / 'index.html').write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=en/index.html"><a href="en/index.html">Map-layer pipeline</a>\n')
    (output / 'version.json').write_text(json.dumps({'schema': 'change-pipeline-studio-3.2', 'result_sha256': sha256(result_path.read_bytes()).hexdigest(), 'asset_revision': revision,
        'map_context_sha256': {name: sha256((output/(name+'_map_context.json')).read_bytes()).hexdigest() for name in contexts},
        'perception_guide_sha256': sha256(guide_path.read_bytes()).hexdigest()}, indent=2) + '\n')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--raw-root', type=Path)
    parser.add_argument('--delivery-root', type=Path)
    args = parser.parse_args()
    print(build(args.result, args.out, args.raw_root, args.delivery_root))
