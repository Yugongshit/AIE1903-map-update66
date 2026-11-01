"""Fuse and rank existing scene-0980 evidence. Python standard library only."""
import argparse
import csv
import hashlib
import json
import math
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / 'Nuscnenes已处理/processed/processed'
RULES = {
    'version': 'stage3-v1',
    'lane_distance_m': 1.5,
    'lane_min_length_m': 3.0,
    'lane_min_points': 5,
    'lane_min_drivable_fraction': 0.5,
    'minimum_support_frames': 3,
    'minimum_duration_s': 1.0,
    'minimum_support_ratio': 0.7,
    'full_persistence_frames': 8,
    'full_persistence_duration_s': 3.0,
    'occupancy_min_area_m2': 0.1,
    'high_score': 0.75,
    'medium_score': 0.5,
    'related_distance_m': 3.0,
}


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    if not rows:
        path.write_text('', encoding='utf-8-sig')
        return
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def median(v):
    return st.median(v) if v else None


def number(v):
    return float(v) if v not in ('', None) else None


def persistence(n, duration):
    return min(n / RULES['full_persistence_frames'], 1) * min(duration / RULES['full_persistence_duration_s'], 1)


def main(source, output):
    output.mkdir(parents=True, exist_ok=True)
    candidates = read_csv(source / 'scene0980_unified_candidates_v1.csv')
    occ = read_csv(source / 'lane_impact_frames_v2.csv')
    regions = {r['event_id']: r for r in json.loads((source / 'occupancy_regions.json').read_text(encoding='utf-8'))['regions']}
    road = source / 'road_perception'
    evidence = {e['event_id']: e for e in json.loads((road / 'scene0980_discrepancy_evidence_v1.json').read_text(encoding='utf-8'))['events']}
    metrics = read_csv(road / 'scene0980_sensor_hdmap_line_metrics.csv')
    all_tracks = read_csv(road / 'lane_track_auto_observations_000_040.csv')
    frame_rows, events, geometries, fingerprints = [], [], {}, {}
    errors = []
    for c in candidates:
        cid, family = c['candidate_id'], c['evidence_family']
        flags, coords, rows = [], [], []
        if family == 'ROAD_OCCUPANCY':
            region = regions[c['source_event_id']]
            rows = [r for r in occ if r['event_id'] == c['source_event_id']]
            for r in rows:
                valid = r['width_geometry_valid'].lower() == 'true'
                supported = valid and float(r['lane_overlap_area_m2']) >= RULES['occupancy_min_area_m2']
                frame_rows.append(dict(candidate_id=cid, frame=int(r['frame_index']), time_s=float(r['time_s']),
                                       observation_usable=valid, supports_hypothesis=supported,
                                       difference_value=r['lane_overlap_area_m2'], difference_unit='m2',
                                       source_file='lane_impact_frames_v2.csv'))
            local = [r for r in frame_rows if r['candidate_id'] == cid]
            n_usable = sum(r['observation_usable'] for r in local)
            n_support = sum(r['supports_hypothesis'] for r in local)
            # No negative observations or visibility denominator in this source.
            n_valid, ratio = None, None
            quality = n_usable / len(rows) if rows else 0
            diff = median([float(r['lane_overlap_area_m2']) for r in rows])
            flags.append('POSITIVE_ONLY_SOURCE;VALID_OBSERVATION_DENOMINATOR_UNAVAILABLE')
            coords = [[float(m['x']), float(m['y'])] for m in region['member_episodes']]
            identity = sorted(region['instance_tokens'])
            eligible = n_support >= RULES['minimum_support_frames']
        else:
            e = evidence[cid]
            track = c['track_id']
            # Include all observed lines of this track within the candidate interval,
            # including those omitted from the positive-only candidate evidence.
            rows = [r for r in metrics if r['track_id'] == track and int(c['first_frame']) <= int(r['frame']) <= int(c['last_frame'])]
            expected = {int(r['frame']) for r in all_tracks if r['track_id'] == track and r['status'] == 'OBSERVED'
                        and int(c['first_frame']) <= int(r['frame']) <= int(c['last_frame'])}
            if expected != {int(r['frame']) for r in rows}:
                errors.append(f'{cid}: observed-track / metric frame mismatch')
            support_by_key = {(int(r['frame']), int(r['line_id'])): r for r in e['frame_evidence']}
            distances = []
            for r in rows:
                key = int(r['frame']), int(r['line_id'])
                curve_path = road / f'frame{key[0]:03d}_multi_lane_{key[1]:02d}.csv'
                curve = read_csv(curve_path)
                xy = [[float(p['global_x_m']), float(p['global_y_m'])] for p in curve]
                coords.extend(xy)
                extra = support_by_key.get(key)
                # Missing context is unknown, not automatically invalid or positive.
                drivable = number(extra['inside_drivable_fraction']) if extra else None
                usable = int(r['sensor_point_count']) >= RULES['lane_min_points'] and float(r['sensor_length_m']) >= RULES['lane_min_length_m']
                distance = number(r['nearest_map_median_m'])
                supported = usable and distance is not None and distance > RULES['lane_distance_m'] and drivable is not None and drivable >= RULES['lane_min_drivable_fraction']
                frame_rows.append(dict(candidate_id=cid, frame=key[0], time_s=extra['time_s'] if extra else '',
                                       observation_usable=usable, supports_hypothesis=supported,
                                       difference_value=distance, difference_unit='m', source_file=curve_path.relative_to(source).as_posix()))
                if distance is not None:
                    distances.append(distance)
            local = [r for r in frame_rows if r['candidate_id'] == cid]
            n_usable = sum(r['observation_usable'] for r in local)
            n_support = sum(r['supports_hypothesis'] for r in local)
            # Denominator refers to usable DETECTED curves only, never visibility.
            n_valid = None
            ratio = n_support / n_usable if n_usable else None
            diff = median(distances)
            quality = n_usable / len(rows) if rows else 0
            identity = track
            flags.extend(['PROVISIONAL_TRACK_ID', 'DETECTED_CURVES_ONLY;VISIBILITY_DENOMINATOR_UNAVAILABLE'])
            if 'CONNECTOR_POLYGON_SUPPORT' in c['context_flags']:
                flags.append('CONNECTOR_REPRESENTATION_AMBIGUITY')
            eligible = (n_support >= RULES['minimum_support_frames'] and ratio is not None and ratio >= RULES['minimum_support_ratio'])
        unique_frames = sorted({r['frame'] for r in local})
        if len(unique_frames) != len(rows):
            errors.append(f'{cid}: duplicate frame evidence')
        first, last = float(c['first_time_s']), float(c['last_time_s'])
        duration = last - first
        span = int(c['last_frame']) - int(c['first_frame']) + 1
        continuity = len(unique_frames) / span
        temporal = persistence(n_support, duration)
        # Ranking heuristic: not a calibrated probability or M4 visibility support score.
        score = 0.6 * temporal + 0.25 * continuity + 0.15 * quality
        eligible = eligible and duration >= RULES['minimum_duration_s']
        if not eligible:
            score = min(score, 0.49)
        status = 'HIGH_EVIDENCE' if score >= RULES['high_score'] and eligible else 'MEDIUM_EVIDENCE' if score >= RULES['medium_score'] and eligible else 'INSUFFICIENT_EVIDENCE'
        if not coords:
            errors.append(f'{cid}: missing geometry')
        bbox = [min(p[0] for p in coords), min(p[1] for p in coords), max(p[0] for p in coords), max(p[1] for p in coords)]
        geometries[cid] = dict(type='MultiPoint', coordinates=coords, meaning='occupancy_member_centers' if family == 'ROAD_OCCUPANCY' else 'observed_curve_points')
        # Merge only identical family, map, identities, frames and geometry.
        fingerprint = json.dumps([family, c['map_token'], identity, unique_frames, coords], sort_keys=True)
        canonical = fingerprints.setdefault(fingerprint, cid)
        events.append(dict(event_id=canonical, candidate_id=cid, evidence_family=family,
                           first_time_s=first, last_time_s=last, observed_frames=len(unique_frames),
                           usable_source_frames=n_usable, support_frames=n_support,
                           valid_visibility_frames=n_valid, detected_curve_support_ratio=ratio,
                           continuity=round(continuity, 4), duration_s=round(duration, 4),
                           median_difference=diff, difference_unit='m2' if family == 'ROAD_OCCUPANCY' else 'm',
                           persistence_component=round(temporal, 4), geometry_quality_component=round(quality, 4),
                           evidence_score=round(score, 4), evidence_level=status,
                           map_change_status='UNVERIFIED', bbox_xy=json.dumps(bbox),
                           flags=';'.join(flags), geometry_id=cid))
    # Spatial links are not deduplication: different objects/families can coexist.
    links = []
    for i, a in enumerate(events):
        for b in events[i + 1:]:
            overlap = min(a['last_time_s'], b['last_time_s']) - max(a['first_time_s'], b['first_time_s'])
            if overlap < 0:
                continue
            d = min(math.dist(p, q) for p in geometries[a['candidate_id']]['coordinates'] for q in geometries[b['candidate_id']]['coordinates'])
            if d <= RULES['related_distance_m']:
                links.append(dict(candidate_a=a['candidate_id'], candidate_b=b['candidate_id'],
                                  relation='DUPLICATE' if a['event_id'] == b['event_id'] else 'SPATIOTEMPORALLY_NEARBY',
                                  min_evidence_point_distance_m=round(d, 4), overlap_s=round(overlap, 4),
                                  cross_family=a['evidence_family'] != b['evidence_family']))
    if len(events) != len(candidates):
        errors.append('candidate coverage mismatch')
    inputs = ['scene0980_unified_candidates_v1.csv', 'lane_impact_frames_v2.csv', 'occupancy_regions.json',
              'road_perception/scene0980_discrepancy_evidence_v1.json', 'road_perception/scene0980_sensor_hdmap_line_metrics.csv',
              'road_perception/lane_track_auto_observations_000_040.csv']
    manifest = {p: hashlib.sha256((source / p).read_bytes()).hexdigest() for p in inputs}
    write_csv(output / 'scored_candidates.csv', events)
    write_csv(output / 'frame_evidence.csv', frame_rows)
    write_csv(output / 'candidate_links.csv', links)
    for name, obj in [('geometry.json', geometries), ('rules.json', RULES), ('input_hashes.json', manifest)]:
        (output / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')
    report = dict(stage=3, candidate_count=len(events), unique_event_count=len({e['event_id'] for e in events}),
                  frame_evidence_rows=len(frame_rows), levels=dict(Counter(e['evidence_level'] for e in events)),
                  nearby_links=len(links), errors=errors, validation='PASS' if not errors else 'FAIL',
                  limitations=['Evidence scores rank persistence and continuity, not probability of real map change.',
                               'No visibility-based N_valid is available; no M4 visibility support ratio is claimed.',
                               'Occupancy evidence is positive-only GT-derived; no false-positive rate can be inferred.',
                               'Lane ratios cover detected usable curves in candidate windows, not all visible frames.',
                               'Spatial links retain separate hypotheses; occupancy centers are not full footprints.',
                               'Thresholds are preset, not independently calibrated.'])
    (output / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/stage3')
    args = parser.parse_args()
    main(args.source, args.output)
