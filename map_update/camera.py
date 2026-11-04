"""Project supplied nuScenes geometry using each CAM_FRONT exposure's pose.

Map lines use a z=0 ground-plane approximation. These projections are diagnostic
overlays, not pixel-validated lane predictions or collision-free driving advice.
"""
from hashlib import sha256
from math import isfinite, sqrt
from pathlib import Path

from .delivery import read_csv, read_json


def rotation(quaternion):
    if len(quaternion) != 4 or not all(isfinite(x) for x in quaternion):
        raise ValueError('Invalid quaternion')
    norm = sqrt(sum(x*x for x in quaternion))
    if abs(norm-1) > .001:
        raise ValueError('Quaternion must be normalized')
    w, x, y, z = [v/norm for v in quaternion]
    return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]


def matvec(matrix, vector):
    return [sum(a*b for a, b in zip(row, vector)) for row in matrix]


def inverse_pose(point, pose):
    r = rotation(pose['rotation'])
    return matvec(list(zip(*r)), [v-t for v, t in zip(point, pose['translation'])])


def camera_point(point, ego, calibration):
    return inverse_pose(inverse_pose(point, ego), calibration)


def pixel(point, intrinsic):
    if point[2] <= 0:
        raise ValueError('Point is behind camera')
    q = matvec(intrinsic, point)
    return [q[0]/q[2], q[1]/q[2]]


def clip_polygon(points, inside, intersection):
    result = []
    for a, b in zip(points[-1:]+points[:-1], points):
        ia, ib = inside(a), inside(b)
        if ia != ib:
            result.append(intersection(a, b))
        if ib:
            result.append(b)
    return result


def cut_axis(a, b, axis, value):
    t = (value-a[axis])/(b[axis]-a[axis])
    return [x+t*(y-x) for x, y in zip(a, b)]


def screen_segment(a, b, width, height):
    # Clip to the image, including segments with both endpoints outside it.
    for axis, bound, minimum in [(0, 0, True), (0, width, False), (1, 0, True), (1, height, False)]:
        ia = a[axis] >= bound if minimum else a[axis] <= bound
        ib = b[axis] >= bound if minimum else b[axis] <= bound
        if not ia and not ib:
            return None
        if ia != ib:
            cut = cut_axis(a, b, axis, bound)
            if not ia:
                a = cut
            else:
                b = cut
    return [[round(v, 3) for v in p] for p in (a, b)]


def project_segment(a, b, ego, calibration, width=1600, height=900):
    a, b = (camera_point(p, ego, calibration) for p in (a, b))
    for depth, minimum in [(.5, True), (100., False)]:
        ia = a[2] >= depth if minimum else a[2] <= depth
        ib = b[2] >= depth if minimum else b[2] <= depth
        if not ia and not ib:
            return None
        if ia != ib:
            cut = cut_axis(a, b, 2, depth)
            if not ia:
                a = cut
            else:
                b = cut
    return screen_segment(pixel(a, calibration['camera_intrinsic']), pixel(b, calibration['camera_intrinsic']), width, height)


def project_ground(points, ego, calibration, closed=False, width=1600, height=900):
    if not closed:
        return [segment for a, b in zip(points, points[1:])
                if (segment := project_segment([*a, 0.], [*b, 0.], ego, calibration, width, height))]
    camera = [camera_point([*p, 0.], ego, calibration) for p in points]
    for depth, minimum in [(.5, True), (100., False)]:
        camera = clip_polygon(camera, lambda p: p[2] >= depth if minimum else p[2] <= depth,
                              lambda a, b: cut_axis(a, b, 2, depth))
    projected = [pixel(p, calibration['camera_intrinsic']) for p in camera]
    for axis, value, minimum in [(0, 0, True), (0, width, False), (1, 0, True), (1, height, False)]:
        projected = clip_polygon(projected, lambda p: p[axis] >= value if minimum else p[axis] <= value,
                                 lambda a, b: cut_axis(a, b, axis, value))
    return [[round(v, 3) for v in p] for p in projected] if len(projected) >= 3 else []


def box_corners(row):
    import json
    r = rotation(json.loads(row['rotation_wxyz']))
    lengths = [float(row[name]) for name in ('length_m', 'width_m', 'height_m')]
    center = [float(row[name]) for name in ('x_global_m', 'y_global_m', 'z_global_m')]
    return [[a+b for a, b in zip(matvec(r, [sx*lengths[0]/2, sy*lengths[1]/2, sz*lengths[2]/2]), center)]
            for sz in (-1, 1) for sx, sy in [(-1, -1), (1, -1), (1, 1), (-1, 1)]]


def build_camera_views(raw_root, scene):
    root = Path(raw_root)
    if scene.get('dataset_id') != 'nuscenes':
        raise ValueError('nuScenes camera adapter only')
    files = ['v1.0-trainval/sample_data.json', 'v1.0-trainval/calibrated_sensor.json',
             'v1.0-trainval/ego_pose.json', 'v1.0-trainval/sensor.json', 'processed/frames.csv', 'processed/objects.csv']
    data, calibrations, poses, sensors = [read_json(root/p) for p in files[:4]]
    calibrations = {c['token']: c for c in calibrations}
    poses = {p['token']: p for p in poses}
    sensors = {s['token']: s for s in sensors}
    samples = [s for s in data if s.get('channel') == 'CAM_FRONT' and s['is_key_frame']]
    by_sample = {s['sample_token']: s for s in samples}
    if len(by_sample) != len(samples):
        raise ValueError('Duplicate CAM_FRONT sample')
    frame_rows, objects = read_csv(root/files[-2]), read_csv(root/files[-1])
    result, checks = [], []
    for row, frame in zip(frame_rows, scene['replay']):
        sample = by_sample[row['sample_token']]
        calibration = calibrations[sample['calibrated_sensor_token']]
        ego = poses[sample['ego_pose_token']]
        if sensors[calibration['sensor_token']]['channel'] != 'CAM_FRONT' or ego['timestamp'] != sample['timestamp']:
            raise ValueError('Camera pose / sensor identity mismatch')
        if int(row['frame_index']) != frame['frame_index'] or sample['width'] != 1600 or sample['height'] != 900:
            raise ValueError('Camera frame / dimensions mismatch')
        delta = (sample['timestamp']-int(row['timestamp_us']))/1000
        if abs(delta) > 50:
            raise ValueError('Unexpected camera / frame timestamp difference')
        surfaces, lines, observations, boxes = [], [], [], []
        for element in scene['reference_map']['elements']:
            geometry = element['geometry_city']
            if geometry['type'] == 'LineString':
                segments = project_ground(geometry['coordinates'], ego, calibration)
                if segments:
                    lines.append({'id': element['element_id'], 'layer': element['layer'], 'segments': segments})
            else:
                # Preserve polygon holes for canvas even-odd filling.
                rings = [project_ground(ring, ego, calibration, closed=True) for ring in geometry['coordinates']]
                if rings[0]:
                    surfaces.append({'id': element['element_id'], 'layer': element['layer'], 'rings': rings})
        for line in frame['observed_lines']:
            segments = project_ground(line['geometry_city']['coordinates'], ego, calibration)
            if segments:
                observations.append({'ids': line['candidate_ids'], 'track_id': line['track_id'], 'segments': segments})
        edges = [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]
        for obj in objects:
            if int(obj['frame_index']) != frame['frame_index']:
                continue
            corners = box_corners(obj)
            segments = [p for a, b in edges if (p := project_segment(corners[a], corners[b], ego, calibration))]
            if segments:
                boxes.append({'instance_token': obj['instance_token'], 'category': obj['category'],
                              'segments': segments, 'source': 'nuScenes annotation; lidar-frame timestamp'})
        recorded_route = [f['ego']['xy'] for f in scene['replay'] if f['frame_index'] >= frame['frame_index']]
        result.append({'frame_index': frame['frame_index'], 'sample_token': row['sample_token'],
                       'sample_data_token': sample['token'], 'filename': sample['filename'],
                       'width': sample['width'], 'height': sample['height'],
                       'timestamp_ns': str(sample['timestamp']*1000), 'delta_from_frame_ms': delta,
                       'image_available': (root/sample['filename']).is_file(),
                       'calibration_token': sample['calibrated_sensor_token'], 'ego_pose_token': sample['ego_pose_token'],
                       'surfaces': surfaces, 'lines': lines, 'observations': observations, 'boxes': boxes,
                       'recorded_route': project_ground(recorded_route, ego, calibration)})
        checks.append({'frame': frame['frame_index'], 'sensor_pose_timestamp_dimensions_valid': True})
    if len(result) != len(scene['replay']) or len(result) != len(samples):
        raise ValueError('Missing camera metadata')
    return {'dataset_id': 'nuscenes', 'channel': 'CAM_FRONT', 'views': result, 'checks': checks,
            'available_images': sum(v['image_available'] for v in result),
            'route_type': 'recorded_ego_path_not_planned',
            'projection_scope': '2D map uses z=0; camera-time ego pose; boxes retain annotation time; no occlusion test or physical alignment validation.',
            'source_files': [{'path': p, 'sha256': sha256((root/p).read_bytes()).hexdigest()} for p in files]}
