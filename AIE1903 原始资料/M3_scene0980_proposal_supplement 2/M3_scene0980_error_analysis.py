"""Describe lane-to-map projection residuals in scene-0980.

Example:
  python M3_scene0980_error_analysis.py scene0980_step1_delivery_v1.zip --output-dir m3_step1_results

Requires NumPy and Matplotlib. Reads the ZIP directly, without extracting or
changing inputs. Writes four CSV tables and one PNG to the output directory.
The samples are upstream local matches from one drive and are not an
independent unchanged-road calibration set.
"""

import argparse
import csv
import io
from collections import Counter, defaultdict
from pathlib import Path
from zipfile import ZipFile

import matplotlib
import numpy as np

matplotlib.use('Agg')
import matplotlib.pyplot as plt


FIELDS = ('lateral_signed_m', 'lateral_absolute_m', 'dx_m', 'dy_m')
FIGURE_NAME = 'M3_scene0980_error_distribution.png'
MAX_YAW_ANGLE_DEG = 45.0
MIN_INTERIOR_POINTS = 5


def read_table(archive, name):
    matches = [key for key in archive.namelist() if key.rsplit('/', 1)[-1] == name]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one {name}; found {len(matches)}')
    return list(csv.DictReader(io.StringIO(archive.read(matches[0]).decode('utf-8-sig'))))


def write_table(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def project(points, vertices, yaw_degrees):
    """Find closest points on finite map segments and orient left with ego heading."""
    vectors = np.diff(vertices, axis=0)
    lengths = np.linalg.norm(vectors, axis=1)
    nonzero = lengths > 1e-9
    if not np.any(nonzero):
        return None
    starts = vertices[:-1][nonzero]
    vectors = vectors[nonzero]
    lengths = lengths[nonzero]
    relative = points[:, None, :] - starts[None, :, :]
    raw_t = np.sum(relative * vectors, axis=2) / lengths**2
    projected = starts + np.clip(raw_t, 0, 1)[:, :, None] * vectors
    delta = points[:, None, :] - projected
    squared = np.sum(delta**2, axis=2)
    idx = np.arange(len(points))
    segment = squared.argmin(axis=1)
    t = raw_t[idx, segment]
    interior = (t > 1e-8) & (t < 1 - 1e-8)
    unique = np.sum(np.isclose(squared, squared[idx, segment, None], rtol=0, atol=1e-12), axis=1) == 1
    direction = vectors[segment] / lengths[segment, None]
    yaw = np.deg2rad(yaw_degrees)
    heading = np.array([np.cos(yaw), np.sin(yaw)])
    alignment = direction @ heading
    aligned = np.abs(alignment) >= np.cos(np.deg2rad(MAX_YAW_ANGLE_DEG))
    forward = direction * np.where(alignment >= 0, 1, -1)[:, None]
    normal = np.column_stack((-forward[:, 1], forward[:, 0]))
    offset = delta[idx, segment]
    signed = np.sum(offset * normal, axis=1)
    return offset, signed, interior & unique, aligned


def build_samples(metrics, point_rows, vertex_rows, ego_rows):
    by_line, by_part = defaultdict(list), defaultdict(list)
    for row in point_rows:
        by_line[(row['frame'], row['line_id'], row['track_id'])].append(row)
    for row in vertex_rows:
        by_part[(row['map_token'], row['part_id'])].append(row)
    pose = {row['frame_index']: row for row in ego_rows}
    samples = []
    counts = Counter({'source_observations': len(metrics), 'upstream_local_matches': 0,
                      'invalid_points': 0, 'endpoint_or_ambiguous_points': 0,
                      'orientation_rejected_points': 0, 'other_divider_points': 0,
                      'points_in_accepted_observations': 0, 'accepted_observations': 0})
    rejected = []
    for line in metrics:
        if line['status'] != 'LOCAL_MAP_ALIGNMENT_CANDIDATE':
            continue
        counts['upstream_local_matches'] += 1
        frame, line_id, track = (line[k] for k in ('frame', 'line_id', 'track_id'))
        token, part = line['best_map_token'], line['best_map_part_id']
        records = by_line.get((frame, line_id, track), [])
        vertices = sorted(by_part.get((token, part), []), key=lambda r: int(r['vertex_id']))
        if not records or len(vertices) < 2 or frame not in pose:
            rejected.append(f'{frame}/{line_id}/{track}: missing points, map vertices or pose')
            continue
        if len(records) != int(line['sensor_point_count']) or len({r['point_id'] for r in records}) != len(records):
            raise ValueError(f'Point count or identity mismatch for frame {frame}, line {line_id}')
        try:
            xy = np.array([[float(r[k]) if r[k] else np.nan for k in ('global_x_m', 'global_y_m')] for r in records])
            map_xy = np.array([[float(r[k]) for k in ('global_x_m', 'global_y_m')] for r in vertices])
            yaw = float(pose[frame]['ego_yaw_deg'])
            time_s = float(pose[frame]['time_s'])
        except ValueError:
            rejected.append(f'{frame}/{line_id}/{track}: malformed coordinates or pose')
            continue
        finite = np.isfinite(xy).all(axis=1)
        counts['invalid_points'] += int(np.sum(~finite))
        if not np.isfinite(map_xy).all() or not np.isfinite([yaw, time_s]).all() or not finite.any():
            rejected.append(f'{frame}/{line_id}/{track}: invalid geometry or pose')
            continue
        result = project(xy[finite], map_xy, yaw)
        if result is None:
            rejected.append(f'{frame}/{line_id}/{track}: zero-length map part')
            continue
        delta, signed, interior, aligned = result
        same_divider = np.array([r['nearest_map_token'] == token for r, valid in zip(records, finite) if valid])
        counts['endpoint_or_ambiguous_points'] += int(np.sum(~interior))
        counts['orientation_rejected_points'] += int(np.sum(interior & ~aligned))
        counts['other_divider_points'] += int(np.sum(interior & aligned & ~same_divider))
        keep = interior & aligned & same_divider
        if keep.sum() < MIN_INTERIOR_POINTS:
            rejected.append(f'{frame}/{line_id}/{track}: fewer than {MIN_INTERIOR_POINTS} usable points')
            continue
        counts['points_in_accepted_observations'] += int(keep.sum())
        counts['accepted_observations'] += 1
        samples.append(dict(scene='scene-0980', frame=int(frame), time_s=time_s, line_id=int(line_id), track=track,
                            map_token=token, map_part=int(part), input_points=len(records), used_points=int(keep.sum()),
                            lateral_signed_m=float(np.median(signed[keep])),
                            lateral_absolute_m=float(np.median(np.abs(signed[keep]))),
                            dx_m=float(np.median(delta[keep, 0])), dy_m=float(np.median(delta[keep, 1]))))
    if len(samples) < 2:
        raise ValueError('Need at least two valid line observations to report sample variation.')
    return sorted(samples, key=lambda r: (r['frame'], r['line_id'], r['track'])), counts, rejected


def summarize(samples):
    groups = {'pooled': samples}
    for track in sorted({r['track'] for r in samples}):
        groups[track] = [r for r in samples if r['track'] == track]
    parameters, covariances = [], []
    for group, rows in groups.items():
        for field in FIELDS:
            x = np.array([r[field] for r in rows])
            center = np.median(x)
            parameters.append(dict(group=group, metric=field, n=len(x), mean_m=float(x.mean()), median_m=float(center),
                                   sample_sd_m=float(x.std(ddof=1)) if len(x) > 1 else '',
                                   mad_m=float(np.median(np.abs(x - center))),
                                   q25_m=float(np.quantile(x, .25)), q75_m=float(np.quantile(x, .75)),
                                   min_m=float(x.min()), max_m=float(x.max())))
        if len(rows) >= 2:
            matrix = np.cov([[r['dx_m'] for r in rows], [r['dy_m'] for r in rows]], ddof=1)
            covariances.append(dict(group=group, n=len(rows), xx_m2=float(matrix[0, 0]), xy_m2=float(matrix[0, 1]),
                                    yx_m2=float(matrix[1, 0]), yy_m2=float(matrix[1, 1])))
    return groups, parameters, covariances


def plot(groups, destination):
    ink, muted = '#19324C', '#526579'
    colors = ['#176A99', '#BA561E', '#47775C', '#6B568E']
    pooled = groups['pooled']
    track_groups = [(track, rows) for track, rows in groups.items() if track != 'pooled']
    median_cm = 100 * np.median([r['lateral_signed_m'] for r in pooled])
    fig, (hist, scatter) = plt.subplots(1, 2, figsize=(12, 6.0))
    fig.subplots_adjust(left=.095, right=.97, top=.75, bottom=.195, wspace=.27)
    fig.text(.094, .93, 'Lane marking residuals in scene-0980', color=ink, size=23, weight='bold')
    fig.text(.095, .875, f'One drive  /  {len(pooled)} line observations  /  {len(track_groups)} tracks', color=muted, size=12)
    signed_arrays = [[100 * r['lateral_signed_m'] for r in rows] for _, rows in track_groups]
    all_signed = np.concatenate(signed_arrays)
    edges = np.linspace(np.floor(all_signed.min() - .5), np.ceil(all_signed.max() + .5), 9)
    hist.hist(signed_arrays, bins=edges, stacked=True, color=colors[:len(track_groups)],
              label=[f'{name}  (n={len(rows)})' for name, rows in track_groups], edgecolor='white', linewidth=.85)
    hist.axvline(median_cm, color=ink, linestyle='--', linewidth=1.5, label=f'Pooled median {median_cm:.1f} cm')
    hist.set(xlabel='Signed lateral residual (cm)', ylabel='Line observations')
    hist.set_ylim(top=hist.get_ylim()[1] * 1.16)
    hist.legend(loc='upper right', fontsize=8.3, framealpha=.94, edgecolor='none')
    for (track, rows), color in zip(track_groups, colors):
        scatter.scatter([100 * r['dx_m'] for r in rows], [100 * r['dy_m'] for r in rows],
                        s=36, c=color, alpha=.82, edgecolors='white', linewidth=.4, label=track)
    scatter.set(xlabel='Map X residual (cm)', ylabel='Map Y residual (cm)')
    scatter.legend(loc='upper right', fontsize=9, frameon=False)
    for axis, title in [(hist, 'A   Lateral bias and spread'), (scatter, 'B   Residuals in map coordinates')]:
        axis.set_title(title, loc='left', pad=12, weight='bold', size=13, color=ink)
        axis.set_axisbelow(True)
        axis.grid(color='#E0E8EF', alpha=.8)
        axis.spines[['top', 'right']].set_visible(False)
        axis.spines[['bottom', 'left']].set_color('#A7B5C4')
        axis.tick_params(colors=muted)
    fig.text(.095, .085, 'Each sample is the median for one observed line in one frame. Negative lateral values lie right of the oriented divider.', size=9.6, color=ink)
    fig.text(.095, .048, 'Selected local matches from one drive. The distribution describes this sample and is not an independent normal-error baseline.', size=9.6, color=muted)
    fig.savefig(destination, dpi=180, facecolor='white')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zip_file', type=Path, help='scene0980_step1_delivery_v1.zip')
    parser.add_argument('--output-dir', type=Path, default=Path('m3_step1_results'))
    args = parser.parse_args()
    with ZipFile(args.zip_file) as archive:
        samples, counts, rejected = build_samples(
            read_table(archive, 'scene0980_sensor_hdmap_line_metrics.csv'),
            read_table(archive, 'scene0980_sensor_hdmap_point_distances.csv'),
            read_table(archive, 'scene0980_hdmap_divider_vertices.csv'),
            read_table(archive, 'frames.csv'),
        )
    groups, parameters, covariances = summarize(samples)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_table(args.output_dir / 'error_parameters.csv', parameters)
    write_table(args.output_dir / 'error_covariance.csv', covariances)
    write_table(args.output_dir / 'line_frame_residuals.csv', samples)
    audit = [dict(item=k, value=v) for k, v in counts.items()]
    audit.extend(dict(item='rejected_observation', value=reason) for reason in rejected)
    write_table(args.output_dir / 'sample_audit.csv', audit)
    plot(groups, args.output_dir / FIGURE_NAME)
    print(f'{len(samples)} accepted line observations from {len(groups)-1} tracks; {len(rejected)} local matches rejected.')
    print(f'Outputs written to {args.output_dir}')


if __name__ == '__main__':
    main()
