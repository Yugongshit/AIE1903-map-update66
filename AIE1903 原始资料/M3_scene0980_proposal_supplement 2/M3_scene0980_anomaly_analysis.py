"""Screen matched line residuals in one drive, using T001 as the reference.

Run with the source ZIP and the Step 1 script in the same directory:
  python M3_scene0980_anomaly_analysis.py scene0980_step1_delivery_v1.zip

Or pass --residuals line_frame_residuals.csv to reuse Step 1 output.
Requires NumPy and Matplotlib. Writes three CSVs and a PNG.
The empirical cutoff is descriptive. Both tracks are from scene-0980.
"""

import argparse
import csv
import io
import runpy
from pathlib import Path
from zipfile import ZipFile

import matplotlib
import numpy as np

matplotlib.use('Agg')
import matplotlib.pyplot as plt


REFERENCE = 'T001'
TARGET = 'T004'
MAD_FACTOR = 1.4826
QUANTILE = .95
FIGURE = 'M3_scene0980_anomaly_chart.png'


def zip_rows(archive, name):
    matches = [path for path in archive.namelist() if path.rsplit('/', 1)[-1] == name]
    if len(matches) != 1:
        raise ValueError(f'Expected one {name}, found {len(matches)}')
    return list(csv.DictReader(io.StringIO(archive.read(matches[0]).decode('utf-8-sig'))))


def residual_rows(archive, csv_path, step1_path):
    if csv_path is not None:
        with csv_path.open(encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))
    if not step1_path.is_file():
        raise FileNotFoundError('Provide the Step 1 script beside this file, --step1-script PATH, or --residuals PATH.')
    build_samples = runpy.run_path(str(step1_path))['build_samples']
    samples, _, _ = build_samples(
        zip_rows(archive, 'scene0980_sensor_hdmap_line_metrics.csv'),
        zip_rows(archive, 'scene0980_sensor_hdmap_point_distances.csv'),
        zip_rows(archive, 'scene0980_hdmap_divider_vertices.csv'),
        zip_rows(archive, 'frames.csv'),
    )
    return samples


def analyze(source):
    rows = []
    ids = set()
    for sample in source:
        if sample['track'] not in (REFERENCE, TARGET):
            continue
        if sample['scene'] != 'scene-0980':
            raise ValueError('This comparison requires residuals from scene-0980.')
        row = dict(frame=int(sample['frame']), time_s=float(sample['time_s']), track=sample['track'],
                   line_id=int(sample['line_id']), used_points=int(sample['used_points']),
                   input_points=int(sample['input_points']), lateral_signed_m=float(sample['lateral_signed_m']))
        key = (row['track'], row['frame'], row['line_id'])
        if key in ids or not np.isfinite([row['time_s'], row['lateral_signed_m']]).all():
            raise ValueError(f'Duplicate or nonfinite residual at {key}')
        ids.add(key)
        rows.append(row)
    rows.sort(key=lambda row: (row['track'], row['frame'], row['line_id']))
    reference = np.array([row['lateral_signed_m'] for row in rows if row['track'] == REFERENCE])
    count = sum(row['track'] == TARGET for row in rows)
    if len(reference) < 2 or not count:
        raise ValueError('Need at least two T001 observations and at least one T004 observation.')
    center = float(np.median(reference))
    mad = float(np.median(np.abs(reference - center)))
    scale = MAD_FACTOR * mad
    if scale <= 0:
        raise ValueError('Reference MAD is zero; a standardized score cannot be computed.')
    cutoff = float(np.quantile(np.abs((reference - center) / scale), QUANTILE))
    for row in rows:
        z = (row['lateral_signed_m'] - center) / scale
        score = abs(z)
        row.update(z=z, anomaly_score=score, threshold=cutoff,
                   status='REFERENCE' if row['track'] == REFERENCE else 'ABOVE_REFERENCE' if score > cutoff else 'WITHIN_REFERENCE')
    summary = dict(reference_track=REFERENCE, target_track=TARGET, reference_n=len(reference), target_n=count,
                   reference_median_m=center, reference_mad_m=mad, robust_scale_m=scale, empirical_quantile=QUANTILE,
                   threshold=cutoff, lower_reference_m=center-cutoff*scale, upper_reference_m=center+cutoff*scale,
                   flagged_n=sum(row['status'] == 'ABOVE_REFERENCE' for row in rows))
    return rows, summary


def unmatched_rows(archive):
    result = []
    for row in zip_rows(archive, 'scene0980_discrepancy_candidates_v1.csv'):
        result.append(dict(event_id=row['event_id'], track=row['track_id'], first_frame=int(row['first_frame']),
                           last_frame=int(row['last_frame']), observed_frames=int(row['observed_frames']),
                           upstream_unsigned_distance_m=float(row['map_distance_median_m']),
                           status='NO_RELIABLE_DIVIDER_MATCH', anomaly_score='', context=row['context_flags']))
    return result


def write_rows(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot(rows, summary, path):
    target = [r for r in rows if r['track'] == TARGET]
    frames = [r['frame'] for r in target]
    scores = [r['anomaly_score'] for r in target]
    cutoff = summary['threshold']
    top = max(target, key=lambda r: r['anomaly_score'])
    ink, muted, blue, amber = '#19324C', '#53677B', '#176B9C', '#B55419'
    fig, ax = plt.subplots(figsize=(11.5, 5.8))
    fig.subplots_adjust(left=.095, right=.956, top=.73, bottom=.22)
    fig.text(.095, .925, 'How unusual are the T004 lane offsets?', size=23, weight='bold', color=ink)
    fig.text(.095, .865, f"nuScenes scene-0980  /  {summary['reference_n']} T001 reference observations  /  {len(target)} T004 observations", size=11.5, color=muted)
    fig.text(.095, .792, f"{summary['flagged_n']} of {len(target)} above the T001 empirical cutoff", size=12, weight='bold', color=ink)
    ax.axhspan(0, cutoff, color='#EAF4FA', zorder=0)
    ax.axhline(cutoff, ls=(0, (5, 4)), lw=1.65, color=amber, label=f'T001 empirical P95 = {cutoff:.2f}')
    ax.plot(frames, scores, color=blue, lw=1.9, marker='o', ms=5, label='T004 local residual score', zorder=3)
    hits = [r for r in target if r['status'] == 'ABOVE_REFERENCE']
    if hits:
        ax.scatter([r['frame'] for r in hits], [r['anomaly_score'] for r in hits], c=amber, s=55, zorder=4)
    ax.annotate(f"Frame {top['frame']}  ·  {top['anomaly_score']:.2f}", (top['frame'], top['anomaly_score']),
                xytext=(11,-15), textcoords='offset points', ha='left', color=blue, fontsize=9.5)
    ax.set(xlabel='Frame', ylabel='Absolute standardized residual |z|', xticks=sorted(set(frames)),
           ylim=(0, max(cutoff, *scores)*1.25), xlim=(min(frames)-.4, max(frames)+.4))
    ax.set_axisbelow(True)
    ax.grid(axis='y', color='#D9E5EF', lw=.8)
    ax.spines[['top','right']].set_visible(False)
    ax.spines[['bottom','left']].set_color('#ABBACA')
    ax.tick_params(colors=muted)
    ax.legend(loc='upper right', fontsize=9.5, frameon=False)
    fig.text(.095, .095, 'Scores use only the locally matched portion of each line. Unmatched lane-marking candidates are listed separately.', fontsize=9.2, color=ink)
    fig.text(.095, .052, 'T001 and T004 come from the same drive. The cutoff is a within-scene reference for review.', fontsize=9.2, color=muted)
    fig.savefig(path, dpi=180, facecolor='white')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zip_file', type=Path)
    parser.add_argument('--residuals', type=Path)
    parser.add_argument('--step1-script', type=Path, default=Path(__file__).with_name('M3_scene0980_error_analysis.py'))
    parser.add_argument('--output-dir', type=Path, default=Path('m3_step2_results'))
    args = parser.parse_args()
    with ZipFile(args.zip_file) as archive:
        rows, summary = analyze(residual_rows(archive, args.residuals, args.step1_script))
        unmatched = unmatched_rows(archive)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_rows(args.output_dir/'anomaly_statistics.csv', rows)
    write_rows(args.output_dir/'reference_parameters.csv', [summary])
    write_rows(args.output_dir/'unmatched_lane_candidates.csv', unmatched)
    plot(rows, summary, args.output_dir/FIGURE)
    print(f"T001 cutoff {summary['threshold']:.4f}; {summary['flagged_n']}/{summary['target_n']} T004 observations above cutoff; {len(unmatched)} separate candidates.")


if __name__ == '__main__':
    main()
