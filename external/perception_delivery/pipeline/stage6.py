"""Associate behaviors and discrepancy evidence in the same observed frames."""
import json
import math
from collections import Counter, defaultdict
from stage3 import ROOT, DEFAULT_SOURCE, read_csv, write_csv

RULES = dict(near_distance_m=3.0, far_distance_m=5.0, min_samples=3, min_near_duration_s=1.0)


def segment_distance(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    norm = dx * dx + dy * dy
    u = max(0, min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / norm)) if norm else 0
    return math.dist(p, (a[0] + u * dx, a[1] + u * dy))


def polygon_distance(p, poly):
    inside = False
    for a, b in zip(poly, poly[1:] + poly[:1]):
        if (a[1] > p[1]) != (b[1] > p[1]) and p[0] < (b[0] - a[0]) * (p[1] - a[1]) / (b[1] - a[1]) + a[0]:
            inside = not inside
    return 0.0 if inside else min(segment_distance(p, a, b) for a, b in zip(poly, poly[1:] + poly[:1]))


def rectangle(r):
    x, y = float(r['x_global_m']), float(r['y_global_m'])
    angle = math.radians(float(r['yaw_global_deg']))
    length, width = float(r['length_m']) / 2, float(r['width_m']) / 2
    return [(x + u * math.cos(angle) - v * math.sin(angle), y + u * math.sin(angle) + v * math.cos(angle))
            for u, v in [(-length, -width), (length, -width), (length, width), (-length, width)]]


def main():
    output = ROOT / 'outputs/stage6'
    output.mkdir(parents=True, exist_ok=True)
    source = DEFAULT_SOURCE
    scored = read_csv(ROOT / 'outputs/stage3/scored_candidates.csv')
    events = read_csv(ROOT / 'outputs/stage5/behavior_events.csv')
    behavior_geometry = json.loads((ROOT / 'outputs/stage5/behavior_geometry.json').read_text())
    source_candidates = {r['candidate_id']: r for r in read_csv(source / 'scene0980_unified_candidates_v1.csv')}
    regions = {r['event_id']: r for r in json.loads((source / 'occupancy_regions.json').read_text(encoding='utf-8'))['regions']}
    objects = {(r['instance_token'], int(r['frame_index'])): r for r in read_csv(source / 'objects.csv')}
    evidence = {e['event_id']: e for e in json.loads((source / 'road_perception/scene0980_discrepancy_evidence_v1.json').read_text(encoding='utf-8'))['events']}
    result, per_frame, coverage, errors = [], [], [], []
    for c in scored:
        cid = c['candidate_id']
        src = source_candidates[cid]
        geometry = defaultdict(list)
        occupancy = c['evidence_family'] == 'ROAD_OCCUPANCY'
        if occupancy:
            region = regions[src['source_event_id']]
            allowed = {int(r['frame_index']) for r in read_csv(source / 'lane_impact_frames_v2.csv') if r['event_id'] == src['source_event_id']}
            for member in region['member_episodes']:
                for frame in member['frame_ids']:
                    if int(frame) not in allowed:
                        continue
                    r = objects.get((member['instance'], int(frame)))
                    if r:
                        geometry[int(frame)].append(rectangle(r))
        else:
            for r in evidence[cid]['frame_evidence']:
                path = source / 'road_perception' / PathName(r['source_csv'])
                curve = [(float(p['global_x_m']), float(p['global_y_m'])) for p in read_csv(path)]
                geometry[int(r['frame'])].append(curve)
        count = 0
        for e in events:
            if e['behavior'] == 'stationary_presence':
                continue
            distances = []
            for p in behavior_geometry[e['behavior_id']]:
                shapes = geometry.get(p['frame'], [])
                if not shapes:
                    continue
                point = (p['x_m'], p['y_m'])
                d = min(polygon_distance(point, s) if occupancy else min(segment_distance(point, a, b) for a, b in zip(s, s[1:])) for s in shapes)
                distances.append((p['time_s'], d))
                per_frame.append(dict(candidate_id=cid, behavior_id=e['behavior_id'], track_id=e['track_id'],
                                      frame=p['frame'], time_s=p['time_s'], distance_m=round(d, 4)))
            if not distances:
                continue
            count += 1
            # Require a consecutive near run rather than two separated near hits.
            runs = [[]]
            for t, d in distances:
                if d > RULES['near_distance_m']:
                    if runs[-1]:
                        runs.append([])
                    continue
                if runs[-1] and t - runs[-1][-1] > 1.1:
                    runs.append([])
                runs[-1].append(t)
            near_run = max(runs, key=lambda r: (r[-1] - r[0] if r else 0, len(r)))
            near_duration = near_run[-1] - near_run[0] if near_run else 0
            minimum = min(d for _, d in distances)
            status, reason = 'uncertain', 'LIMITED_OR_BOUNDARY_EVIDENCE'
            if e['behavior'] == 'uncertain':
                reason = 'BEHAVIOR_UNCERTAIN'
            elif c['evidence_level'] == 'INSUFFICIENT_EVIDENCE':
                reason = 'DISCREPANCY_EVIDENCE_INSUFFICIENT'
            elif len(near_run) >= RULES['min_samples'] and near_duration >= RULES['min_near_duration_s']:
                status, reason = 'associated', 'OBSERVED_BEHAVIOR_NEAR_CANDIDATE'
            elif len(distances) >= RULES['min_samples'] and minimum > RULES['far_distance_m']:
                status, reason = 'not_associated', 'OBSERVED_BEHAVIOR_FAR_FROM_CANDIDATE'
            result.append(dict(candidate_id=cid, behavior_id=e['behavior_id'], track_id=e['track_id'],
                               behavior=e['behavior'], association=status, reason=reason,
                               minimum_distance_m=round(minimum, 4), simultaneous_samples=len(distances),
                               consecutive_near_samples=len(near_run), consecutive_near_duration_s=round(near_duration, 4),
                               overlap_start_s=distances[0][0], overlap_end_s=distances[-1][0],
                               distance_definition='vehicle_center_to_observed_object_footprint' if occupancy else 'vehicle_center_to_observed_marking_polyline',
                               scope='OBSERVED_WINDOW_ONLY;NO_CAUSAL_CLAIM'))
        coverage.append(dict(candidate_id=cid, evaluable_behavior_pairs=count,
                             status='HAS_OBSERVED_BEHAVIOR' if count else 'NO_EVALUABLE_VEHICLE_BEHAVIOR'))
    write_csv(output / 'associations.csv', result)
    write_csv(output / 'association_frame_evidence.csv', per_frame)
    write_csv(output / 'candidate_coverage.csv', coverage)
    (output / 'rules.json').write_text(json.dumps(RULES, indent=2))
    report = dict(stage=6, behavior_candidate_pairs=len(result), associations=dict(Counter(r['association'] for r in result)),
                  candidates_without_evaluable_behavior=sum(r['evaluable_behavior_pairs'] == 0 for r in coverage),
                  frame_evidence_rows=len(per_frame), validation='PASS' if not errors else 'FAIL', errors=errors,
                  limitations=['Associations describe observed space/time proximity only, not causation or verified map changes.',
                               'Distances use observed geometry in the exact same keyframe; missing frames are not interpolated.',
                               'Vehicle center distances are not clearance or collision distances.',
                               'Not-associated applies only to the observed simultaneous window.',
                               'Incomplete maneuver episodes and provisional marking identities remain upstream limitations.'])
    (output / 'summary.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def PathName(path):
    return path.replace('\\', '/').split('/')[-1]


if __name__ == '__main__':
    main()
