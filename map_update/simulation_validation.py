"""Audit simulation-map application separately from frozen detector accuracy.

Passing these checks means that an export reconstructs its declared map edits
and state observations. It never establishes that source labels are perfect,
that a reference update was detected by the model, or that driving is safe.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import pickle

from .simulation_bridge import LANE_TYPES, _topology_issues, feature_sha256, json_value, load_scenario


SCHEMA = 'simulation-validation-1.0'
METRICS_SCHEMA = 'simulation-frozen-detection-metrics-1.0'


def _read(path):
    def invalid(value):
        raise ValueError('Nonfinite JSON value: ' + value)
    return json.loads(Path(path).read_text(encoding='utf-8'), parse_constant=invalid)


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _metrics(samples):
    """Recompute every score from per-unit frozen predictions and truth."""
    identifiers = [row['id'] for row in samples]
    _require(len(identifiers) == len(set(identifiers)), 'Duplicate evaluation unit')
    _require(all(type(row.get('predicted')) is bool and (row.get('truth') is None or type(row['truth']) is bool)
                 for row in samples), 'Evaluation labels must be booleans or explicitly unavailable')
    rows = [row for row in samples if row['truth'] is not None]
    tp = sum(row['predicted'] and row['truth'] for row in rows)
    fp = sum(row['predicted'] and not row['truth'] for row in rows)
    fn = sum(not row['predicted'] and row['truth'] for row in rows)
    return {'true_positives': tp, 'false_positives': fp, 'false_negatives': fn,
            'predicted_positives': tp + fp, 'truth_positives': tp + fn,
            'precision': tp / (tp + fp) if tp + fp else None,
            'recall': tp / (tp + fn) if tp + fn else None,
            'f1': 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None}


def build_detection_metrics(pipeline_result, raw_root):
    """Freeze a small portable evaluation from verified model and truth files."""
    result_path, raw = Path(pipeline_result).resolve(), Path(raw_root).resolve()
    pipeline = _read(result_path)
    freeze = pipeline['prediction_freeze']
    _require(freeze.get('gt_read') is False, 'Detector predictions were not frozen before truth was read')
    for filename, digest in freeze['files_sha256'].items():
        path = result_path.parent / filename
        _require(path.is_file() and sha256(path.read_bytes()).hexdigest() == digest,
                 'Frozen prediction file changed: ' + filename)
    truth_sources = [row for row in pipeline['sources'] if '_M8_v1/' in row['path']]
    for source in truth_sources:
        path = (raw / source['path']).resolve()
        _require(path.is_relative_to(raw) and path.is_file() and sha256(path.read_bytes()).hexdigest() == source['sha256'],
                 'Independent truth file changed: ' + source['path'])
    by_name = {Path(row['path']).name: raw / row['path'] for row in truth_sources}
    prior = _read(by_name['prior_map_reference.json'])
    inventory = [json.loads(line) for line in by_name['gt_object_inventory.jsonl'].read_text().splitlines() if line]
    lookup = {(row['layer'], row['element_id']): row for row in inventory}
    from .reliability_v2 import canonical_boundaries
    argo = pipeline['datasets']['argoverse']
    detected = {event['physical_boundary_id'] for event in argo['events']
                if event.get('model_change_decision', {}).get('detected', event['reliability']['status'] == 'supported_conflict')}
    samples = []
    for boundary in canonical_boundaries(prior):
        labels = []
        for alias in boundary['aliases']:
            row = lookup.get(('laneSegments', alias['map_element_id']))
            distance = row.get('differences', {}).get(alias['boundary_side'] + '_geometry_difference_m') if row else None
            labels.append(distance > .01 if row and row['state_comparison_supported'] and distance is not None else None)
        truth = labels[0] if labels and None not in labels and len(set(labels)) == 1 else None
        samples.append({'id': boundary['physical_boundary_id'], 'truth': truth,
                        'predicted': boundary['physical_boundary_id'] in detected})
    full = pipeline['evaluation']['argoverse']['full_boundary_geometry']
    measured = _metrics(samples)
    _require(measured == full['metrics'], 'Recomputed independent-boundary scores differ from the pipeline')
    final_count = argo['summary']['final_structural_changes']
    _require(final_count == 0, 'A nonempty final patch set needs an explicit independent final-map matching evaluator')
    reference_count = len(argo['material_reference_replay']['patch_log'])
    final_metrics = _metrics([{'id': row['element_id'], 'truth': True, 'predicted': False}
                              for row in argo['material_reference_replay']['patch_log']])
    geometry = full['local_geometry_summary']
    summary = {
        'schema': METRICS_SCHEMA,
        'datasets': {
            'argoverse': {
                'dataset': 'argoverse', 'scene_id': argo['scene_id'], 'available': True,
                'label': '冻结模型的局部边界变化检出', 'scope': full['scope'],
                'scope_description': '同一交付场景内340条可评估物理边界；包括未涂漆边界。衡量找到变更对象，不证明整条道路修改正确。',
                'prediction_source': 'frozen_model_local_boundary_decisions', 'metrics': measured,
                'samples': samples, 'eligible_units': full['eligible_boundaries'],
                'excluded_units': full['excluded_boundaries'], 'total_units': full['physical_boundaries'],
                'final_model_structural_updates': final_count, 'reference_updates_available': reference_count,
                'final_structure_metrics': final_metrics,
                'final_structure_scope': '93项material参考对象变化的最终写入覆盖：模型实际写入0项；参考重放不计模型预测。',
                'geometry_accuracy': {'passed': geometry['within_tolerance_detections'],
                                      'total': geometry['evaluable_detections'], 'tolerance_m': geometry['tolerance_m'],
                                      'scope': '同一锚点的局部偏移误差；所有边界别名均须满足阈值，非完整补丁精度。'},
                'independent_test_accuracy': None,
                'limitations': ['仅一个已交付场景，未提供独立留出测试场景。',
                                '93项参考更新是对照输入，不作为模型检出。',
                                '局部边界命中率与最终地图写入质量必须分开解释。'],
            },
            'nuscenes': {
                'dataset': 'nuscenes', 'scene_id': pipeline['datasets']['nuscenes']['scene_id'],
                'available': False, 'label': '尚无独立变化真值', 'scope': 'supplied_occupancy_and_marking_candidates',
                'metrics': None, 'independent_test_accuracy': None,
                'final_model_structural_updates': pipeline['datasets']['nuscenes']['summary']['final_structural_changes'],
                'reason': 'nuScenes没有独立核验的原图/新图变化标签；对象标注及实例对齐不能替代变化正确率。',
                'limitations': ['23个状态事件来自已有对象标注足迹，未提供独立状态变化真值。',
                                '71个实例绑定成功只证明身份接入，不等于检测精度。'],
            },
        },
        'provenance': {
            'pipeline_result_sha256': sha256(result_path.read_bytes()).hexdigest(),
            'pipeline_run_id': pipeline.get('run_id'), 'prediction_freeze': freeze,
            'source_files': [{**row, 'path': str(Path(raw.name) / row['path'])} for row in truth_sources],
            'method': 'recompute_all_prior_boundary_labels_from_independent_M8_inventory_after_prediction_freeze',
            'reference_replay_counted_as_model_prediction': False,
        },
    }
    return summary


def load_detection_metrics(repo_root, dataset, scene_id, *, pipeline_sha256=None):
    """Read the portable frozen evaluation, checking its scope and source files.

The complete pipeline result is not required on another computer. Its hash and
prediction-freeze manifest remain provenance, while the shipped per-boundary
labels/predictions allow the displayed metrics to be recomputed.
"""
    root = Path(repo_root).resolve()
    path = root / 'demo/simulation/validation/detection_metrics.json'
    if not path.is_file():
        return {'available': False, 'label': '未提供独立检测评估', 'metrics': None,
                'reason': 'Portable frozen evaluation is missing'}
    payload = _read(path)
    _require(payload.get('schema') == METRICS_SCHEMA, 'Unknown frozen evaluation schema')
    data = deepcopy(payload.get('datasets', {}).get(dataset))
    _require(isinstance(data, dict) and data.get('scene_id') == scene_id, 'Evaluation belongs to a different scene')
    if pipeline_sha256 is not None:
        _require(payload['provenance']['pipeline_result_sha256'] == pipeline_sha256,
                 'Evaluation does not match this export pipeline run')
    for source in payload['provenance']['source_files']:
        source_path = (root / source['path']).resolve()
        _require(source_path.is_relative_to(root), 'Evaluation source escapes repository')
        _require(source_path.is_file() and sha256(source_path.read_bytes()).hexdigest() == source['sha256'],
                 'Evaluation truth source changed: ' + source['path'])
    if data.get('available'):
        calculated = _metrics(data['samples'])
        _require(calculated == data['metrics'], 'Evaluation metrics differ from frozen unit predictions')
        _require(data.get('prediction_source') == 'frozen_model_local_boundary_decisions',
                 'Reference replay cannot be counted as model predictions')
        final = data['final_structure_metrics']
        _require(final['predicted_positives'] == data['final_model_structural_updates'],
                 'Final map metrics include unexecuted or reference changes')
        data['sample_count'] = len(data.pop('samples'))
    data['provenance'] = deepcopy(payload['provenance'])
    data['evaluation_artifact_sha256'] = sha256(path.read_bytes()).hexdigest()
    data['source_hashes_verified'] = True
    return data


def validate_database(database_path, *, detection_metrics=None):
    """Return checks and known source limitations for one trusted local export.

Known source topology/coverage problems are disclosed as warnings. New problems
or inconsistent artifacts fail validation. No source data is repaired or edited.
"""
    root = Path(database_path).resolve()
    checks = []
    report = {'schema': SCHEMA, 'identity': {}, 'integrity': {'passed': False, 'checks': checks, 'summary': {}},
              'detection': deepcopy(detection_metrics) if isinstance(detection_metrics, dict) else {
                  'available': False, 'label': '未提供独立检测评估', 'metrics': None,
                  'reason': 'Validation checks exported data, not detector truth'},
              'limitations': ['写入一致性校验不等于算法检测正确率，也不等于驾驶安全验证。',
                              '参考更新与受控演示不计为模型检出；背景参与者不自动计为地图状态变化。']}

    def check(name, callback):
        try:
            result = callback() or {}
            status = result.pop('_status', 'pass')
            checks.append({'name': name, 'status': status, 'details': result})
            return result
        except (ValueError, KeyError, TypeError, OSError, ImportError, IndexError, EOFError, pickle.UnpicklingError) as error:
            checks.append({'name': name, 'status': 'fail', 'details': {'error': str(error)}})
            return None

    def finish():
        summary = report['integrity']['summary']
        summary.update(checks=len(checks), failures=sum(c['status'] == 'fail' for c in checks),
                       warnings=sum(c['status'] == 'warning' for c in checks))
        report['integrity']['passed'] = not summary['failures']
        return report

    loaded = {}

    def read_artifacts():
        manifest = _read(root / 'simulation_manifest.json')
        filename = manifest.get('scenario_file')
        _require(isinstance(filename, str) and Path(filename).name == filename, 'Invalid scenario filename')
        serialized = (root / filename).read_bytes()
        _require(sha256(serialized).hexdigest() == manifest.get('exported_scenario_sha256'), 'Scenario file hash mismatch')
        scenario = load_scenario(root / filename)
        loaded.update(manifest=manifest, scenario=scenario,
                      before=_read(root / 'map_before.json')['map_features'],
                      after=_read(root / 'map_after.json')['map_features'],
                      patches=_read(root / 'map_patch_log.json'),
                      planning=_read(root / 'planning_map.json'),
                      obstacles=_read(root / 'obstacle_manifest.json'))
        summary, mapping = load_scenario(root / 'dataset_summary.pkl'), load_scenario(root / 'dataset_mapping.pkl')
        _require(list(summary) == [filename] and mapping == {filename: ''}, 'Database index does not select the bound scenario')
        # Upstream nuScenes metadata uses -inf for unavailable map-height
        # statistics. Compare it as data; do not interpret it as geometry.
        _require(json_value(summary[filename]) == json_value(scenario['metadata']), 'Database summary metadata differs')
        return {'scenario_file_sha256': manifest['exported_scenario_sha256'], 'single_bound_scenario': True}

    if check('场景文件与索引', read_artifacts) is None:
        return finish()
    m, scenario = loaded['manifest'], loaded['scenario']
    provenance, metadata = m['provenance'], scenario['metadata']
    before, after, patches = loaded['before'], loaded['after'], loaded['patches']
    report['identity'] = {key: m.get(key) for key in ('dataset', 'scene_id', 'map', 'coordinate', 'units')}
    if isinstance(detection_metrics, (str, Path)):
        def evaluation_source():
            path = Path(detection_metrics).resolve()
            _require(path.name == 'detection_metrics.json' and path.parent.name == 'validation'
                     and path.parent.parent.name == 'simulation' and path.parent.parent.parent.name == 'demo',
                     'Portable evaluation must live in demo/simulation/validation/detection_metrics.json')
            report['detection'] = load_detection_metrics(path.parents[3], m['dataset'], m['scene_id'],
                                                       pipeline_sha256=provenance.get('pipeline_result_sha256'))
            return {'source_hashes_verified': report['detection'].get('source_hashes_verified', False)}
        check('独立评估来源', evaluation_source)
    if provenance.get('synthetic'):
        report['detection']['displayed_map_contains_controlled_edits'] = True
        report['detection']['controlled_edits_counted_as_model_prediction'] = False
        report['limitations'].append('当前场景含受控结构修改，用于两层联合接入测试，不计入真实检测指标。')
    report['integrity']['summary'].update(map_features=len(after), applied_feature_operations=len(patches),
                                          reference_operations=m.get('summary', {}).get('reference_structural_operations', 0),
                                          source_is_synthetic=bool(provenance.get('synthetic')))

    def identity():
        _require(metadata.get('dataset') == m['dataset'] and scenario['id'] == m['scene_id']
                 and metadata.get('map') == m['map'] and metadata.get('coordinate') == 'right-handed',
                 'Scenario/export coordinate identities differ')
        for key in ('dataset', 'scene_id', 'map', 'coordinate', 'units'):
            _require(loaded['planning'].get(key) == m.get(key), 'Planning identity differs: ' + key)
        if report['detection'].get('scene_id') is not None:
            _require(report['detection']['scene_id'] == m['scene_id'], 'Detector evaluation scene mismatch')
        return {**report['identity'], 'city_coordinates_retained': 'old_origin_in_current_coordinate' not in metadata}
    check('场景与坐标身份', identity)

    def bindings():
        for value, field in [(before, 'source_map_features_sha256'), (after, 'map_features_sha256'),
                             (patches, 'map_patch_log_sha256'), (loaded['obstacles'], 'obstacle_manifest_sha256')]:
            digest = feature_sha256(value)
            _require(digest == provenance.get(field), 'Export hash mismatch: ' + field)
            _require(digest == metadata['map_update_bridge'].get(field), 'Scenario metadata hash mismatch: ' + field)
        _require(feature_sha256(after) == feature_sha256(scenario['map_features']), 'Export map differs from simulation map')
        _require(feature_sha256(after) == feature_sha256(loaded['planning']['map_features']), 'Planning map differs from simulation map')
        track_digest = provenance.get('exported_tracks_sha256', provenance.get('tracks_sha256'))
        _require(feature_sha256(scenario['tracks']) == track_digest, 'Exported object tracks changed')
        return {'map_sha256': feature_sha256(after), 'road_map_and_planning_identical': True}
    check('地图、规划与日志绑定', bindings)

    def reconstruct():
        current = deepcopy(before)
        seen = set()
        for item in patches:
            fid, operation = item['feature_id'], item['operation']
            _require(fid not in seen, 'Repeated map edit identity: ' + fid)
            seen.add(fid)
            old, new = item.get('before'), item.get('after')
            _require(operation in {'add', 'replace', 'delete'}, 'Unknown map operation')
            expected = {'add': (False, True), 'replace': (True, True), 'delete': (True, False)}[operation]
            _require((old is not None, new is not None) == expected, 'Invalid before/after operation')
            _require(feature_sha256(current.get(fid)) == feature_sha256(old), 'Patch does not match original feature: ' + fid)
            if 'before_sha256' in item:
                _require(item['before_sha256'] == feature_sha256(old), 'Patch before hash mismatch')
            if new is None:
                del current[fid]
            else:
                current[fid] = deepcopy(new)
            if 'after_sha256' in item:
                _require(item['after_sha256'] == feature_sha256(new), 'Patch after hash mismatch')
        _require(feature_sha256(current) == feature_sha256(after), 'Patch replay does not reconstruct updated map')
        if provenance.get('update_source') == 'reference_update':
            reference = _read(root / 'reference_patch_log.json')
            _require(feature_sha256(reference) == provenance.get('reference_patch_log_sha256'), 'Reference log hash mismatch')
            _require(provenance.get('model_prediction') is False, 'Reference updates are incorrectly claimed as model predictions')
            if provenance.get('variant') == 'updated':
                expected = {item['element_id'] for item in reference}
                _require({item.get('source_element_id') for item in patches} == expected, 'Reference updates missing from simulation edits')
                _require(len(expected) == m['summary']['reference_structural_operations'], 'Reference update count mismatch')
        return {'operations_replayed': len(patches), 'all_changes_reconstructed': True,
                'reference_is_model_prediction': False}
    check('逐项重放修改前后地图', reconstruct)

    def geometry():
        from shapely.geometry import LineString, Polygon
        def inspect(features):
            invalid, outside, missing = [], [], []
            polygons = 0
            for fid, feature in features.items():
                for field in ('polyline', 'polygon'):
                    if field in feature:
                        points = feature[field]
                        _require(len(points) >= (3 if field == 'polygon' else 2), 'Insufficient points: ' + fid)
                        _require(all(len(p) >= 2 and all(type(x) in (int, float) and math.isfinite(x) for x in p[:2]) for p in points),
                                 'Invalid metric coordinates: ' + fid)
                if 'polygon' in feature:
                    polygons += 1
                    polygon = Polygon([p[:2] for p in feature['polygon']])
                    if not polygon.is_valid or polygon.area <= 1e-8:
                        invalid.append(fid)
                    elif feature['type'] in LANE_TYPES and 'polyline' in feature:
                        if not polygon.buffer(.01).covers(LineString([p[:2] for p in feature['polyline']])):
                            outside.append(fid)
                elif feature['type'] in LANE_TYPES:
                    missing.append(fid)
            return invalid, outside, missing, polygons
        old_invalid, old_outside, old_missing, _ = inspect(before)
        invalid, outside, missing, polygons = inspect(after)
        changed = {item['feature_id'] for item in patches}
        introduced = ((set(invalid)-set(old_invalid)) | (set(outside)-set(old_outside)) | (set(missing)-set(old_missing))
                      | (changed & (set(invalid) | set(outside) | set(missing))))
        _require(not introduced, 'Edited map introduces invalid/missing lane geometry: ' + ', '.join(sorted(introduced)))
        return {'_status': 'warning' if invalid or outside or missing else 'pass',
                'valid_polygons': polygons-len(invalid), 'invalid_source_polygons': invalid,
                'preserved_source_centerlines_outside_polygon': outside,
                'source_lanes_without_polygon': missing, 'introduced_geometry_issues': [],
                'coverage_tolerance_m': .01, 'note': '原输入几何问题仍保留并列出；未用修补或重采样掩盖。'}
    check('几何有效性与道路覆盖', geometry)

    def topology():
        old, new = _topology_issues(before), _topology_issues(after)
        introduced = new-old
        _require(not introduced, 'New topology defects: ' + repr(sorted(introduced)))
        documented = m.get('topology', {}).get('preserved_source_issues', [])
        legacy = {tuple(row) for row in documented if isinstance(row, list)}
        _require(new <= legacy, 'Existing topology gaps are not disclosed in manifest')
        external = m.get('topology', {}).get('external_references', [])
        for row in external:
            feature = after[row['feature_id']]
            _require(row['target_id'] not in feature.get(row['field'], []), 'External source lane is navigable without geometry')
        return {'_status': 'warning' if new or external else 'pass', 'introduced_issues': [],
                'preserved_source_issues': sorted(new), 'excluded_external_references': len(external),
                'note': '源裁剪边界的缺失连接不等于完整道路可通行。'}
    check('道路连接关系', topology)

    def access():
        expected = {'VEHICLE': ['vehicle', 'bus'], 'BUS': ['bus'], 'BIKE': ['bicycle']}
        count = 0
        for fid, feature in after.items():
            if 'source_lane_type' not in feature and 'vehicle_access' not in feature:
                continue
            lane_type = feature.get('source_lane_type')
            _require(lane_type in expected and feature.get('vehicle_access') == expected[lane_type], 'Lane access differs from source type: ' + fid)
            count += 1
        return {'lanes_with_explicit_access': count, 'legacy_unspecified_access_inferred': False}
    check('公交与自行车道通行属性', access)

    def state_timeline():
        digest = provenance.get('state_source_sha256')
        if not digest:
            return {'state_source_available': False, 'events': m.get('summary', {}).get('state_events', 0),
                    'note': '此数据库没有已绑定的状态时间线；零事件不代表现实中没有障碍。'}
        from .simulation_states import _objects_in_sample, _same, state_snapshot
        from .scene import footprint
        bundle = _read(root / 'state_source.json')
        _require(feature_sha256(bundle) == digest == metadata['map_update_bridge'].get('state_source_sha256'), 'State source hash mismatch')
        _require(bundle['dataset'] == m['dataset'] and bundle['scene_id'] == m['scene_id'] and bundle['map'] == m['map']
                 and bundle['scenario_length'] == scenario['length'], 'State source belongs to a different scene/clock')
        frames = bundle['source_frames']
        _require((len(frames)-1)*5+1 == scenario['length'], 'State source keyframes do not match the replay clock')
        times = [int(frame['timestamp_ns']) for frame in frames]
        _require(all(b > a for a, b in zip(times, times[1:])), 'State source clock is not strictly increasing')
        expected_coordinates = {'frame': 'nuScenes_global', 'city_id': m['map'], 'units': 'm'}
        _require(bundle['coordinate_reference'] == expected_coordinates, 'State coordinates differ from the road map')
        for event in bundle['events']:
            _require(event['coordinate_reference'] == expected_coordinates, 'State event coordinates differ')
            for sample in event['samples']:
                frame = int(sample['frame_id'])
                _require(0 <= frame < len(frames) and sample['timestamp_ns'] == frames[frame]['timestamp_ns'],
                         'State sample uses a different source keyframe time')
                if sample.get('observation') != 'supports_change' or sample.get('quality', 0) <= 0:
                    continue
                for token, shape in _objects_in_sample(sample):
                    source = bundle['object_sources'][token]['observations'][sample['frame_id']]
                    _require(source['timestamp_ns'] == sample['timestamp_ns']
                             and source['sample_token'] == frames[frame]['sample_token'], 'Object state source time/identity differs')
                    expected = footprint({'x_global_m': source['center_xy'][0], 'y_global_m': source['center_xy'][1],
                                          'length_m': source['length_m'], 'width_m': source['width_m'],
                                          'yaw_global_deg': math.degrees(source['yaw_rad'])})
                    _require(_same(shape, expected), 'State footprint differs from its supplied source dimensions and pose')
        for token, obj in bundle['object_sources'].items():
            for track_id in obj['matched_track_ids']:
                _require(scenario['tracks'][track_id].get('metadata', {}).get('original_id', track_id) == token,
                         'State instance does not match existing simulation track')
        observed = set()
        samples = sorted(set([frame['replay_step'] for frame in frames] + [min(4, scenario['length']-1)]))
        for step in samples:
            snapshot = state_snapshot(bundle, step)
            cutoff = int(snapshot['observation_cutoff_ns'])
            _require(cutoff <= int(snapshot['source_timestamp_ns']), 'State observation is in the future')
            for obj in snapshot['objects']:
                _require(int(obj['last_observed_timestamp_ns']) <= cutoff, 'Future object observation appears in replay')
                observed.add(obj['instance_id'])
        _require(observed == set(bundle['object_sources']), 'Some observed state instances never appear in replay')
        _require(feature_sha256(after) == feature_sha256(scenario['map_features']), 'State processing changed road geometry')
        return {'state_source_available': True, 'events': len(bundle['events']), 'instances': len(observed),
                'checked_timeline_steps': len(samples), 'future_observations_shown': False,
                'state_objects_written_into_road_map': False,
                'missing_models': bundle['summary']['missing_model_instances']}
    check('状态实例身份与时间对齐', state_timeline)
    return finish()
