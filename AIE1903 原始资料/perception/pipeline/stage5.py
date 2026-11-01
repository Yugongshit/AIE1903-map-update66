"""Rule-based behavior episodes from existing annotated vehicle trajectories."""
import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from stage3 import ROOT, DEFAULT_SOURCE, read_csv, write_csv

RULES = dict(max_gap_s=1.1, stop_speed_mps=0.5, moving_speed_mps=1.0,
             min_stop_duration_s=2.0, min_moving_duration_s=1.0,
             heading_min_displacement_m=0.75, straight_max_net_deg=15,
             straight_max_accumulated_deg=35, turn_min_net_deg=30,
             uturn_min_net_deg=150, uturn_max_net_deg=210,
             uturn_max_displacement_path_ratio=0.65)


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def main(source, output):
    output.mkdir(parents=True, exist_ok=True)
    tracks = defaultdict(list)
    for r in read_csv(source / 'track_points.csv'):
        if r['category'] in ('vehicle.car', 'vehicle.truck', 'vehicle.bus.bendy', 'vehicle.bus.rigid'):
            tracks[r['instance_token']].append(r)
    features, events, geometries, errors = [], [], {}, []
    for tid, rows in sorted(tracks.items()):
        rows.sort(key=lambda r: float(r['time_s']))
        chunks = [[]]
        for r in rows:
            if chunks[-1] and float(r['time_s']) - float(chunks[-1][-1]['time_s']) > RULES['max_gap_s']:
                chunks.append([])
            chunks[-1].append(r)
        for chunk_idx, chunk in enumerate(chunks):
            ts = [float(r['time_s']) for r in chunk]
            xy = [(float(r['x_global_m']), float(r['y_global_m'])) for r in chunk]
            speeds, states = [], []
            for i, r in enumerate(chunk):
                # Centered, up to ~2 second baseline reduces annotation jitter.
                lo, hi = max(0, i - 2), min(len(chunk) - 1, i + 2)
                dt = ts[hi] - ts[lo]
                speed = math.dist(xy[hi], xy[lo]) / dt if dt else None
                speeds.append(speed)
                state = 'unknown' if speed is None else 'stationary' if speed < RULES['stop_speed_mps'] else 'moving' if speed >= RULES['moving_speed_mps'] else 'transition'
                states.append(state)
                features.append(dict(track_id=tid, category=r['category'], frame=int(r['frame_index']),
                                     time_s=ts[i], x_m=xy[i][0], y_m=xy[i][1],
                                     speed_mps=speed, motion_state=state, observed=True, source='NUSCENES_GT_DERIVED'))
            # Preserve transition samples as uncertain rather than filling them.
            runs = []
            for i, state in enumerate(states):
                if not runs or runs[-1][0] != state:
                    runs.append((state, [i]))
                else:
                    runs[-1][1].append(i)
            for run_index, (state, ids) in enumerate(runs):
                start, end = ids[0], ids[-1]
                duration = ts[end] - ts[start]
                points = [xy[i] for i in ids]
                path = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
                displacement = math.dist(points[0], points[-1])
                # Headings over ~1-second baselines, excluding low displacement.
                headings = []
                for i in range(start, end - 1):
                    j = min(i + 2, end)
                    if math.dist(xy[i], xy[j]) >= RULES['heading_min_displacement_m']:
                        headings.append(math.atan2(xy[j][1] - xy[i][1], xy[j][0] - xy[i][0]))
                angles = [wrap(b - a) for a, b in zip(headings, headings[1:])]
                net = abs(math.degrees(sum(angles))) if len(headings) >= 2 else None
                cumulative = math.degrees(sum(abs(a) for a in angles)) if len(headings) >= 2 else None
                behavior, reason = 'uncertain', 'SHORT_OR_TRANSITION_EPISODE'
                if state == 'stationary' and duration >= RULES['min_stop_duration_s']:
                    # Require moving context in this same continuous track chunk.
                    before = any(s == 'moving' for s in states[:start])
                    after = any(s == 'moving' for s in states[end + 1:])
                    behavior = 'stop' if before or after else 'stationary_presence'
                    reason = 'LOW_SPEED_WITH_MOVING_CONTEXT' if before or after else 'NO_OBSERVED_STOP_TRANSITION'
                elif state == 'moving' and duration >= RULES['min_moving_duration_s'] and net is not None:
                    if RULES['uturn_min_net_deg'] <= net <= RULES['uturn_max_net_deg'] and path and displacement / path <= RULES['uturn_max_displacement_path_ratio']:
                        behavior, reason = 'u_turn_candidate', 'HEADING_REVERSAL_AND_CURVED_PATH'
                    elif net >= RULES['turn_min_net_deg']:
                        behavior, reason = 'turn', 'NET_HEADING_CHANGE'
                    elif net <= RULES['straight_max_net_deg'] and cumulative <= RULES['straight_max_accumulated_deg']:
                        behavior, reason = 'straight', 'STABLE_HEADING'
                    else:
                        reason = 'AMBIGUOUS_HEADING_PATTERN'
                eid = f'B-{tid}-{chunk_idx:02d}-{run_index:02d}'
                geometries[eid] = [dict(frame=int(chunk[i]['frame_index']), time_s=ts[i], x_m=xy[i][0], y_m=xy[i][1]) for i in ids]
                events.append(dict(behavior_id=eid, track_id=tid, category=chunk[0]['category'],
                                   behavior=behavior, start_s=ts[start], end_s=ts[end],
                                   duration_s=round(duration, 4), samples=len(ids),
                                   path_length_m=round(path, 4), net_displacement_m=round(displacement, 4),
                                   net_heading_change_deg=net, accumulated_heading_change_deg=cumulative,
                                   left_censored=start == 0, right_censored=end == len(chunk) - 1,
                                   reason=reason, source='NUSCENES_GT_DERIVED'))
    keys = [(r['track_id'], r['frame']) for r in features]
    if len(keys) != len(set(keys)):
        errors.append('duplicate track/frame')
    if sum(e['samples'] for e in events) != len(features):
        errors.append('episode coverage mismatch')
    write_csv(output / 'vehicle_features.csv', features)
    write_csv(output / 'behavior_events.csv', events)
    (output / 'behavior_geometry.json').write_text(json.dumps(geometries, indent=2), encoding='utf-8')
    (output / 'rules.json').write_text(json.dumps(RULES, indent=2), encoding='utf-8')
    report = dict(stage=5, vehicle_tracks=len(tracks), vehicle_samples=len(features), episodes=len(events),
                  behaviors=dict(Counter(e['behavior'] for e in events)), errors=errors,
                  validation='PASS' if not errors else 'FAIL',
                  limitations=['Uses existing nuScenes annotated positions and identities.',
                               'Episodes summarize consecutive motion-state runs; complex multi-maneuver runs may be uncertain.',
                               'No reference route or complete detour annotation; detour is not inferred.',
                               'Stationary presence is not a stop behavior event.',
                               'Censored episodes do not establish maneuver completeness.',
                               'Rules are preset and require independent behavior labels for accuracy evaluation.'])
    (output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    p.add_argument('--output', type=Path, default=ROOT / 'outputs/stage5')
    a = p.parse_args()
    main(a.source, a.output)
