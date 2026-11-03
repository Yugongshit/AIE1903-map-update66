"""Enhance existing Rerun recording, preserving source geometry and colors."""
import rerun_env
import argparse
import csv
import hashlib
import json
import math
import re
import subprocess
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import rerun as rr
import rerun.blueprint as bp
import rerun.dataframe as rdf
from stage3 import DEFAULT_SOURCE, ROOT, read_csv
from stage6 import rectangle

OUT = ROOT / 'outputs/visualization'
BASE = DEFAULT_SOURCE / 'scene-0980_discrepancy_full_v2.rrd'
EXE = rerun_env.RUNTIME / 'rerun_sdk/rerun_cli/rerun.exe'
NEUTRAL = [205, 205, 205]
LABEL_PATHS = ['/bev/discrepancy/road_occupancy/event_labels',
               '/bev/discrepancy/lane_event_labels', '/bev/tracking/track_id_labels']


def closest_on_segment(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    norm = dx * dx + dy * dy
    u = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / norm)) if norm else 0
    return [a[0] + u * dx, a[1] + u * dy]


def layout(pair_names):
    def map_view():
        return bp.Spatial2DView(origin='/bev', name='HD Map + scores + behaviors')
    cameras = ['CAM_FRONT', 'CAM_FRONT_LEFT', 'CAM_FRONT_RIGHT', 'CAM_BACK', 'CAM_BACK_LEFT', 'CAM_BACK_RIGHT']
    grid = bp.Grid(*(bp.Spatial2DView(origin=f'/cameras/{name}', name=name) for name in cameras), grid_columns=2)
    plots = [bp.TimeSeriesView(origin=f'/analysis/distances/{name}', name=name) for name in pair_names]
    return bp.Blueprint(
        bp.Tabs(
            bp.Horizontal(map_view(), grid, column_shares=[0.46, 0.54], name='Scene + six cameras'),
            bp.Horizontal(map_view(), bp.Vertical(
                bp.TextDocumentView(origin='/analysis/frame_summary', name='Scores and association evidence'),
                bp.Tabs(*plots, name='Distances (m)'), row_shares=[0.58, 0.42]),
                column_shares=[0.48, 0.52], name='Results + distance curves'),
        ),
        bp.TimePanel(expanded=False), bp.SelectionPanel(expanded=False), bp.BlueprintPanel(expanded=False),
        auto_layout=False, auto_views=False,
    )


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    original_hash = hashlib.sha256(BASE.read_bytes()).hexdigest()
    recording = rdf.load_recording(str(BASE))
    scores = {r['candidate_id']: r for r in read_csv(ROOT / 'outputs/stage3/scored_candidates.csv')}
    regions = {r['event_id']: r for r in json.loads((DEFAULT_SOURCE / 'occupancy_regions.json').read_text(encoding='utf-8'))['regions']}
    objects = {(r['instance_token'], int(r['frame_index'])): r for r in read_csv(DEFAULT_SOURCE / 'objects.csv')}
    candidates = {r['candidate_id']: r for r in read_csv(DEFAULT_SOURCE / 'scene0980_unified_candidates_v1.csv')}
    behaviors = read_csv(ROOT / 'outputs/stage5/behavior_events.csv')
    bgeom = json.loads((ROOT / 'outputs/stage5/behavior_geometry.json').read_text())
    frame_evidence = read_csv(ROOT / 'outputs/stage3/frame_evidence.csv')
    active_candidates = defaultdict(set)
    for row in frame_evidence:
        active_candidates[int(row['frame'])].add(row['candidate_id'])
    all_associations = read_csv(ROOT / 'outputs/stage6/associations.csv')
    linked = {(r['candidate_id'], r['behavior_id']): r for r in all_associations if r['association'] == 'associated'}
    associated_frames = defaultdict(list)
    for r in read_csv(ROOT / 'outputs/stage6/association_frame_evidence.csv'):
        key = r['candidate_id'], r['behavior_id']
        if key in linked:
            associated_frames[int(r['frame'])].append((r, linked[key]))
    pairs = {key: key[0].split('-')[-1] + '_' + linked[key]['track_id'][:8] for key in linked}
    frame_features = defaultdict(list)
    for r in read_csv(ROOT / 'outputs/stage5/vehicle_features.csv'):
        frame_features[int(r['frame'])].append(r)
    behavior_by_frame = {}
    for b in behaviors:
        for p in bgeom[b['behavior_id']]:
            behavior_by_frame[b['track_id'][:8], p['frame']] = b['behavior']
    frames = read_csv(DEFAULT_SOURCE / 'frames.csv')
    table = recording.view(index='elapsed_s', contents={p: ['Text'] for p in LABEL_PATHS}).select().read_all()
    original_times = table['elapsed_s'].cast(pa.int64()).to_pylist()
    times = [int(round(float(r['time_s']) * 1e9)) for r in frames]
    if original_times != times:
        raise ValueError('Original timeline does not exactly match frames.csv')
    table_rows = table.to_pylist()
    overlay = OUT / 'scene-0980_results_overlay.rrd'
    merged = OUT / 'scene-0980_results_enhanced.rrd'
    rr.init(recording.application_id(), recording_id=recording.recording_id(), spawn=False)
    rr.save(overlay)
    rr.send_blueprint(layout(list(pairs.values())))
    for name in pairs.values():
        for series, color in [('distance', NEUTRAL), ('near_3m', [150, 150, 150]), ('far_5m', [105, 105, 105])]:
            rr.log(f'/analysis/distances/{name}/{series}', rr.SeriesLine(color=color, name=series, width=2 if series == 'distance' else 1), static=True)
    updates, link_count = 0, 0
    for i, (frame, row) in enumerate(zip(frames, table_rows)):
        fi, time = int(frame['frame_index']), float(frame['time_s'])
        rr.set_time_nanos('elapsed_s', times[i])
        for path in LABEL_PATHS:
            labels = row.get(path + ':Text')
            if labels is None:
                continue
            enriched = []
            for label in labels:
                updated = label
                if path == LABEL_PATHS[0]:
                    match = re.search(r'OCC (O\d+)', label)
                    cid = 'S0980-OCC-scene-0980-' + match.group(1) if match else None
                    if cid in scores:
                        updated += '\nscore=' + format(float(scores[cid]['evidence_score']), '.2f')
                elif path == LABEL_PATHS[1]:
                    match = re.search(r'LD-\d+', label)
                    cid = 'S0980-' + match.group(0) if match else None
                    if cid in scores:
                        updated += '\nscore=' + format(float(scores[cid]['evidence_score']), '.2f')
                else:
                    match = re.search(r'#([a-f0-9]{8})', label)
                    if match and (match.group(1), fi) in behavior_by_frame:
                        updated += '\n' + behavior_by_frame[match.group(1), fi]
                enriched.append(updated)
                updates += updated != label
            # Update Text only: existing point positions, colors and radii stay intact.
            rr.log(path, [rr.components.TextBatch(enriched)])
        segments, link_labels = [], []
        for distance_row, pair in associated_frames[fi]:
            cid, bid = distance_row['candidate_id'], distance_row['behavior_id']
            d = float(distance_row['distance_m'])
            name = pairs[cid, bid]
            for series, value in [('distance', d), ('near_3m', 3), ('far_5m', 5)]:
                rr.log(f'/analysis/distances/{name}/{series}', rr.Scalar(value))
            if d > 3:
                continue
            p = next(p for p in bgeom[bid] if p['frame'] == fi)
            start = [p['x_m'], p['y_m']]
            # Currently associated pairs are occupancy; preserve general lane support.
            if candidates[cid]['evidence_family'] == 'ROAD_OCCUPANCY':
                region = regions[candidates[cid]['source_event_id']]
                shapes = [rectangle(objects[m['instance'], fi]) for m in region['member_episodes']
                          if fi in m['frame_ids'] and (m['instance'], fi) in objects]
            else:
                point_rows = [r for r in frame_evidence if r['candidate_id'] == cid and int(r['frame']) == fi]
                shapes = [[(float(q['global_x_m']), float(q['global_y_m'])) for q in read_csv(DEFAULT_SOURCE / r['source_file'])] for r in point_rows]
            endpoints = [closest_on_segment(start, a, b) for shape in shapes
                         for a, b in zip(shape, shape[1:] + shape[:1] if candidates[cid]['evidence_family'] == 'ROAD_OCCUPANCY' else shape[1:])]
            if endpoints:
                end = min(endpoints, key=lambda q: math.dist(q, start))
                segments.append([start, end])
                link_labels.append(f'{cid.split("-")[-1]} / {pair["track_id"][:8]}\nassociated | d={d:.2f}m')
                link_count += 1
        # Explicit clear at every frame prevents stale association lines.
        rr.log('/bev/results/association_links', rr.Clear(recursive=True))
        if segments:
            rr.log('/bev/results/association_links', rr.LineStrips2D(segments, colors=NEUTRAL,
                   radii=rr.Radius.ui_points(0.8), labels=link_labels, show_labels=True))
        text = [f'# scene-0980 | {time:.2f}s',
                'Scores are evidence rankings, not map-change probabilities. Original colors are preserved.',
                '## Candidate scores', '| ID | score | support frames |', '| --- | ---: | ---: |']
        for cid in sorted(active_candidates[fi]):
            r = scores[cid]
            text.append(f'| {cid} | {float(r["evidence_score"]):.2f} | {r["support_frames"]} |')
        text += ['## Vehicle behaviors', '| vehicle | behavior | speed (m/s) |', '| --- | --- | ---: |']
        for r in frame_features[fi]:
            behavior = behavior_by_frame.get((r['track_id'][:8], fi), 'uncertain')
            speed = f'{float(r["speed_mps"]):.2f}' if r['speed_mps'] else 'unknown'
            text.append(f'| {r["track_id"][:8]} | {behavior} | {speed} |')
        text += ['## Association evidence', 'Gray map links show associated pairs only when currently within 3m. Association is proximity, not causation.',
                 '| candidate | vehicle | distance (m) |', '| --- | --- | ---: |']
        for d, pair in associated_frames[fi]:
            text.append(f'| {d["candidate_id"].split("-")[-1]} | {pair["track_id"][:8]} | {float(d["distance_m"]):.2f} |')
        text += ['', 'Global pair results: 5 associated / 81 not_associated / 89 uncertain.',
                 'Independent accuracy evaluation is pending labels. Vehicle tracks are nuScenes GT-derived.']
        rr.log('/analysis/frame_summary', rr.TextDocument('\n\n'.join(text[:3]) + '\n' + '\n'.join(text[3:]), media_type='text/markdown'))
    rr.disconnect()
    subprocess.run([str(EXE), 'rrd', 'merge', '-o', str(merged), str(BASE), str(overlay)], check=True)
    # Verify all original geometry, styles and image columns survived unchanged.
    enhanced = rdf.load_recording(str(merged))
    checks, errors = [], []
    for col in recording.schema().component_columns():
        if col.component_name.endswith('Indicator') or col.component_name.endswith('ClearIsRecursive'):
            continue
        if col.entity_path in LABEL_PATHS and col.component_name.endswith('.Text'):
            continue
        selector = rdf.ComponentColumnSelector(col.entity_path, col.component_name)
        va = recording.view(index='elapsed_s', contents={col.entity_path: [col.component_name]})
        vb = enhanced.view(index='elapsed_s', contents={col.entity_path: [col.component_name]})
        if col.is_static:
            a, b = va.select_static(selector).read_all(), vb.select_static(selector).read_all()
        else:
            a, b = va.select('elapsed_s', selector).read_all(), vb.select('elapsed_s', selector).read_all()
        equal = a.equals(b, check_metadata=False)
        checks.append(dict(entity=col.entity_path, component=col.component_name, preserved=equal))
        if not equal:
            errors.append(f'{col.entity_path}:{col.component_name}')
    if hashlib.sha256(BASE.read_bytes()).hexdigest() != original_hash:
        errors.append('original file hash changed')
    enhanced_labels = enhanced.view(index='elapsed_s', contents={p: ['Text'] for p in LABEL_PATHS}).select().read_all().to_pylist()
    for i, label_row in enumerate(enhanced_labels):
        for path in LABEL_PATHS:
            before = table_rows[i].get(path + ':Text') or []
            after = label_row.get(path + ':Text') or []
            if len(before) != len(after):
                errors.append(f'label cardinality mismatch at frame {i}: {path}')
            elif not all(new.startswith(old) for old, new in zip(before, after)):
                errors.append(f'original label prefix not preserved at frame {i}: {path}')
    report = dict(source=str(BASE.relative_to(ROOT)), output=str(merged.relative_to(ROOT)),
                  sdk_version=rr.__version__, application_id=recording.application_id(), recording_id=recording.recording_id(),
                  source_sha256=original_hash, frames=len(frames), enriched_label_instances=updates,
                  association_link_instances=link_count, distance_plot_pairs=len(pairs),
                  score_color_encoding=False, preservation_checks=checks, errors=errors,
                  validation='PASS' if not errors else 'FAIL')
    (OUT / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'preservation_checks'}, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
