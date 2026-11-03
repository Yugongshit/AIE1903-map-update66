"""Report geometric observation quality for scene-0980.

python M3_scene0980_observation_quality.py scene0980_step1_delivery_v1.zip

Keep the Step 1 script beside this file, or specify --step1-script PATH.
Optional --camera-data PATH accepts one calibrated frame and target in an
NPZ file. Required arrays are described in camera_coverage().
Dependencies: NumPy and Matplotlib.
"""

import argparse
import csv
import runpy
from pathlib import Path
from zipfile import ZipFile

import matplotlib
import numpy as np

matplotlib.use('Agg')
import matplotlib.pyplot as plt


def geometry_rows(samples, metrics):
    by_key = {(int(r['frame']), int(r['line_id']), r['track_id']): r for r in metrics}
    if len(by_key) != len(metrics):
        raise ValueError('Line metrics contain duplicate frame/line/track keys.')
    result = []
    for sample in samples:
        key = (sample['frame'], sample['line_id'], sample['track'])
        metric = by_key[key]
        used, total = sample['used_points'], sample['input_points']
        near = float(metric['fraction_within_075m'])
        if total != int(metric['sensor_point_count']) or not 0 <= used <= total or total == 0 or not 0 <= near <= 1:
            raise ValueError(f'Invalid point counts or distance ratio at {key}')
        result.append(dict(frame=key[0], line=key[1], track=key[2], time_s=sample['time_s'], input_points=total,
                           geometry_points=used, geometry_usable_ratio=used/total, near_map_point_ratio=near,
                           field_of_view_ratio='', occlusion_ratio='', image_quality='', effective_coverage='',
                           camera_coverage_status='UNKNOWN_MISSING_CALIBRATED_DEPTH_AND_QUALITY'))
    return result


def camera_coverage(data, depth_margin_m):
    """Assess one target in one rectified camera frame.

    map_points_world: Nx3 evenly spaced points on a preselected target in metres.
    camera_from_world: 4x4 rigid world-to-camera transform (+z points forward).
    intrinsics: 3x3 pinhole matrix for the same rectified image.
    depth_z_m: HxW registered optical-axis depth, NaN or <=0 for missing data.
    image_quality_mask: HxW values 1=pass, 0=fail, NaN=unknown (target pixel
    quality/resolution checks defined upstream).
    geometry_quality: N values 1=pass, 0=fail, NaN=unknown from independent
    calibration/projection checks, not from a map disagreement threshold.

    The denominator for effective coverage is all N target points. Unknowns
    are excluded from an exact result and retained in coverage bounds.
    Occlusion is measured among in-frame points with valid depth only.
    """
    xyz = np.asarray(data['map_points_world'], dtype=float)
    transform = np.asarray(data['camera_from_world'], dtype=float)
    intrinsics = np.asarray(data['intrinsics'], dtype=float)
    depth = np.asarray(data['depth_z_m'], dtype=float)
    image_quality = np.asarray(data['image_quality_mask'], dtype=float)
    geometry_quality = np.asarray(data['geometry_quality'], dtype=float)
    if (xyz.ndim != 2 or xyz.shape[1] != 3 or len(xyz) == 0 or transform.shape != (4,4)
        or intrinsics.shape != (3,3) or depth.ndim != 2 or depth.size == 0
        or image_quality.shape != depth.shape or geometry_quality.shape != (len(xyz),)):
        raise ValueError('Invalid NPZ array dimensions; see camera_coverage docstring.')
    if (not all(np.isfinite(a).all() for a in (xyz, transform, intrinsics))
        or not np.allclose(transform[3], [0,0,0,1])
        or not np.allclose(transform[:3,:3] @ transform[:3,:3].T, np.eye(3), atol=1e-5)
        or not np.isclose(np.linalg.det(transform[:3,:3]), 1, atol=1e-5)
        or not np.allclose(intrinsics[2], [0,0,1]) or min(intrinsics[0,0],intrinsics[1,1]) <= 0):
        raise ValueError('Require a finite rigid transform and a valid rectified pinhole matrix.')
    for quality in (image_quality, geometry_quality):
        if not np.all(np.isnan(quality) | (quality == 0) | (quality == 1)):
            raise ValueError('Quality values must be 0, 1 or NaN.')
    cam = xyz @ transform[:3,:3].T + transform[:3,3]
    forward = cam[:,2] > 0
    homogeneous = cam[forward] @ intrinsics.T
    pixels = np.full((len(xyz),2), np.nan)
    pixels[forward] = homogeneous[:,:2] / homogeneous[:,2,None]
    height, width = depth.shape
    in_frame = (forward & (pixels[:,0] >= 0) & (pixels[:,0] < width) & (pixels[:,1] >= 0) & (pixels[:,1] < height))
    indices = np.flatnonzero(in_frame)
    pixel_col, pixel_row = np.floor(pixels[indices]).astype(int).T
    measured = depth[pixel_row,pixel_col]
    img = image_quality[pixel_row,pixel_col]
    geom = geometry_quality[indices]
    depth_known = np.isfinite(measured) & (measured > 0)
    blocked = depth_known & (measured + depth_margin_m < cam[indices,2])
    failed = blocked | (img == 0) | (geom == 0)
    passed = ~failed & depth_known & (img == 1) & (geom == 1)
    unknown = ~(failed | passed)
    total, valid, unresolved = len(xyz), int(passed.sum()), int(unknown.sum())
    return dict(target_points=total, in_fov_points=len(indices), field_of_view_ratio=len(indices)/total,
                in_fov_depth_known_points=int(depth_known.sum()), occluded_points=int(blocked.sum()),
                occlusion_ratio=float(blocked.sum()/depth_known.sum()) if depth_known.any() else '',
                effective_points=valid, unknown_points=unresolved, effective_coverage=valid/total if unresolved==0 else '',
                effective_coverage_lower=valid/total, effective_coverage_upper=(valid+unresolved)/total,
                depth_margin_m=depth_margin_m)


def write(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def plot(rows, output):
    fig, ax = plt.subplots(figsize=(11.5,5.9))
    fig.subplots_adjust(left=.10, right=.96, top=.75, bottom=.22)
    ink, muted, blue, amber = '#19324C', '#53677B', '#176B9C', '#B55419'
    fig.text(.10,.93,'Geometric quality of matched lane observations',size=21,weight='bold',color=ink)
    fig.text(.10,.873,f"nuScenes scene-0980  /  {len(rows)} line observations  /  {len({r['track'] for r in rows})} tracks",size=11.5,color=muted)
    series = [('geometry_usable_ratio','Passed projection checks',blue,'o'),
              ('near_map_point_ratio','Within 0.75 m of a map divider',amber,'s')]
    for j, track in enumerate(sorted({r['track'] for r in rows})):
        subset = [r for r in rows if r['track']==track]
        for field, label, color, mark in series:
            ax.plot([r['frame'] for r in subset],[100*r[field] for r in subset],
                    label=label if j==0 else None, color=color, marker=mark, ms=4, lw=1.7)
    worst = min(rows,key=lambda r:r['geometry_usable_ratio'])
    ax.annotate(f"Frame {worst['frame']}: {worst['geometry_points']}/{worst['input_points']} pass",
                (worst['frame'],100*worst['geometry_usable_ratio']), xytext=(12,-3),textcoords='offset points',color=blue,fontsize=9.5)
    ax.set(xlabel='Frame', ylabel='Share of detected line points (%)',ylim=(0,110))
    ax.legend(loc='lower left',fontsize=9.5,frameon=False)
    ax.set_axisbelow(True)
    ax.grid(axis='y',color='#D9E5EF',lw=.8)
    ax.spines[['top','right']].set_visible(False)
    ax.spines[['bottom','left']].set_color('#ABBACA')
    ax.tick_params(colors=muted)
    fig.text(.10,.095,'Both ratios describe already-detected points. Camera field of view, occlusion, and effective coverage remain unknown.',fontsize=9.2,color=ink)
    fig.savefig(output,dpi=180,facecolor='white')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zip_file', type=Path)
    parser.add_argument('--step1-script', type=Path, default=Path(__file__).with_name('M3_scene0980_error_analysis.py'))
    parser.add_argument('--camera-data', type=Path)
    parser.add_argument('--depth-margin-m', type=float, default=.3, help='Example depth tolerance, set using sensor uncertainty')
    parser.add_argument('--output-dir', type=Path, default=Path('m3_step3_results'))
    args = parser.parse_args()
    if not np.isfinite(args.depth_margin_m) or args.depth_margin_m < 0:
        parser.error('Depth margin must be finite and nonnegative.')
    step1 = runpy.run_path(str(args.step1_script))
    with ZipFile(args.zip_file) as archive:
        def load(name):
            return step1['read_table'](archive,name)
        metrics = load('scene0980_sensor_hdmap_line_metrics.csv')
        samples, _, _ = step1['build_samples'](metrics,load('scene0980_sensor_hdmap_point_distances.csv'),
            load('scene0980_hdmap_divider_vertices.csv'),load('frames.csv'))
    rows = geometry_rows(samples, metrics)
    camera_result = None
    if args.camera_data:
        with np.load(args.camera_data,allow_pickle=False) as data:
            camera_result = camera_coverage(data,args.depth_margin_m)
    args.output_dir.mkdir(parents=True,exist_ok=True)
    write(args.output_dir/'observation_quality.csv', rows)
    plot(rows,args.output_dir/'M3_scene0980_observation_quality.png')
    if camera_result is not None:
        write(args.output_dir/'calibrated_camera_coverage.csv',[camera_result])
    print(f'{len(rows)} matched observations. Geometric checks retained {sum(r["geometry_points"] for r in rows)}/{sum(r["input_points"] for r in rows)} detected points.')


if __name__ == '__main__':
    main()
