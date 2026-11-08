"""Source-map backdrops for the composite structure/state presentation.

These are presentation derivatives of the nuScenes reference and frozen Argoverse
M2 prior. No M8/current-GT map is opened. Lane fills connect supplied boundary
vertices without interpolation and remain explicitly marked as derived geometry.
"""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

from .geometry import validate_geometry
from .scene import inspect_scene


def _hash(path):
    return sha256(path.read_bytes()).hexdigest()


def _bounds(elements):
    points = []
    for element in elements:
        geometry = element['geometry_city']
        points.extend(geometry['coordinates'] if geometry['type'] == 'LineString'
                      else [point for ring in geometry['coordinates'] for point in ring])
    return [min(p[0] for p in points), min(p[1] for p in points),
            max(p[0] for p in points), max(p[1] for p in points)] if points else None


def _nuscenes(raw, delivery):
    scene = inspect_scene(raw, delivery)
    reference = scene['reference_map']
    elements = []
    for original in reference['elements']:
        element = deepcopy(original)
        element.update(source_ids=[original['element_id']], geometry_origin='source_map_geometry')
        elements.append(element)
    path = raw/'maps/expansion'/(reference['map_id']+'.json')
    digest = _hash(path)
    if digest != reference['source_sha256']:
        raise ValueError('nuScenes reference map changed during context extraction')
    return {'dataset_id': 'nuscenes', 'scene_id': scene['scene_id'], 'map_id': reference['map_id'],
            'map_version': reference['version'], 'map_sha256': digest,
            'coordinate_reference': deepcopy(reference['coordinate_reference']),
            'bounds_xyxy': _bounds(elements), 'elements': elements,
            'summary': {'elements': len(elements), 'layers': dict(Counter(e['layer'] for e in elements))},
            'provenance': {'source_files': [{'path': path.relative_to(raw).as_posix(), 'sha256': digest, 'role': 'reference_map'}],
                           'map_sha256': digest, 'map_role': reference['map_role'],
                           'selection_method': reference['selection_method'],
                           'selection_bounds_xyxy': deepcopy(reference['bounds_xyxy']),
                           'selection_input_files': deepcopy(scene['source_files']),
                           'geometry_transform': 'none; source geometry retained without clipping',
                           'source_only': True, 'gt_current_map_used': False}}


def _argoverse(raw):
    packages = list((raw/'deliveries').glob('*_M2_v1'))
    if len(packages) != 1:
        raise ValueError('Exactly one frozen Argoverse M2 package is required')
    package = packages[0]
    manifest_path = package/'file_manifest.json'
    entries = {r['path'].replace('\\', '/'): r['sha256'] for r in json.loads(manifest_path.read_text())['files']}
    sources = [{'path': manifest_path.relative_to(raw).as_posix(), 'sha256': _hash(manifest_path), 'role': 'source_manifest'}]
    for name in ('prior_map.json', 'run.json'):
        path = package/name
        if _hash(path) != entries.get(name):
            raise ValueError('Argoverse context source manifest mismatch: '+name)
        sources.append({'path': path.relative_to(raw).as_posix(), 'sha256': _hash(path), 'role': 'frozen_prior' if name == 'prior_map.json' else 'source_identity'})
    prior = json.loads((package/'prior_map.json').read_text())
    run = json.loads((package/'run.json').read_text())
    scene = prior['scene_id']
    if package.name != scene+'_M2_v1' or run['scene_id'] != scene:
        raise ValueError('Argoverse map, run and package scene identities differ')
    if prior['role'] != 'stale_prior_from_official_loader' or not prior['coordinates'].startswith('City XY'):
        raise ValueError('Expected the supplied stale prior with city XY coordinates')
    elements, boundaries, source_ids = [], {}, set()
    for lane in prior['lane_segments']:
        eid = lane['id']
        if eid in source_ids:
            raise ValueError('Duplicate source lane identity')
        source_ids.add(eid)
        sides = {}
        for side in ('left', 'right'):
            points = [[p['x'], p['y']] for p in lane[side+'_lane_boundary']]
            sides[side] = points
            validate_geometry({'type': 'LineString', 'coordinates': points}, line_only=True)
            ordered = tuple(map(tuple, points))
            key = min(ordered, ordered[::-1])
            alias = {'map_element_id': eid, 'boundary_side': side, 'marking_type': lane[side+'_lane_mark_type']}
            if key not in boundaries:
                digest = sha256(json.dumps(key, separators=(',', ':')).encode()).hexdigest()[:20]
                boundaries[key] = {'element_id': 'boundary:'+digest, 'layer': 'lane_boundary',
                                   'geometry_city': {'type': 'LineString', 'coordinates': deepcopy(points)},
                                   'source_ids': [], 'map_aliases': [], 'geometry_origin': 'source_boundary_vertices'}
            boundaries[key]['map_aliases'].append(alias)
            if eid not in boundaries[key]['source_ids']:
                boundaries[key]['source_ids'].append(eid)
        ring = deepcopy(sides['left']+sides['right'][::-1])
        if ring[0] != ring[-1]:
            ring.append(deepcopy(ring[0]))
        geometry = {'type': 'Polygon', 'coordinates': [ring]}
        validate_geometry(geometry)
        elements.append({'element_id': eid, 'layer': 'lane', 'geometry_city': geometry, 'source_ids': [eid],
                         'geometry_origin': 'display_fill_from_source_left_and_reversed_right_boundaries',
                         'properties': {k: deepcopy(lane[k]) for k in ('lane_type', 'is_intersection', 'predecessors', 'successors')}})
    elements.extend(boundaries.values())
    for crossing in prior['pedestrian_crossings']:
        if crossing['id'] in source_ids:
            raise ValueError('Duplicate source crossing identity')
        source_ids.add(crossing['id'])
        geometry = {'type': 'Polygon', 'coordinates': [deepcopy(crossing['points'])]}
        validate_geometry(geometry)
        elements.append({'element_id': crossing['id'], 'layer': 'ped_crossing', 'geometry_city': geometry,
                         'source_ids': [crossing['id']], 'geometry_origin': 'source_crossing_polygon'})
    digest = _hash(package/'prior_map.json')
    return {'dataset_id': 'argoverse', 'scene_id': scene, 'map_id': scene+':M2_prior',
            'map_version': 'M2_v1-'+digest[:12], 'map_sha256': digest,
            'coordinate_reference': {'frame': 'Argoverse supplied city XY', 'scene_id': scene, 'units': 'm'},
            'bounds_xyxy': _bounds(elements), 'elements': elements,
            'summary': {'elements': len(elements), 'layers': dict(Counter(e['layer'] for e in elements)),
                        'source_lanes': len(prior['lane_segments']), 'source_boundary_references': 2*len(prior['lane_segments']),
                        'physical_boundaries': len(boundaries), 'source_crossings': len(prior['pedestrian_crossings'])},
            'provenance': {'source_files': sources, 'map_sha256': digest, 'map_role': prior['role'],
                           'source_coordinate_description': prior['coordinates'], 'source_only': True,
                           'gt_current_map_used': False, 'selection_method': 'all supplied M2 prior lanes and crossings',
                           'boundary_deduplication': 'exact XY vertex sequence or its reversal; every source side/attribute alias retained',
                           'lane_fill_derivation': 'left boundary followed by reversed right boundary, closed; no interpolation or geometry repair',
                           'not_supplied_layers': ['drivable_area']}}


def build_map_contexts(raw_root, delivery_root):
    """Return source-backed map contexts; callers must match dataset scene/hash.

    Coordinates retain each dataset's source metric frame. These GeoJSON-shaped
    geometries are not asserted to be WGS84 longitude/latitude. Prediction and
    evaluation results remain unchanged: this function only creates view data.
    """
    raw, delivery = Path(raw_root).resolve(), Path(delivery_root).resolve()
    return {'nuscenes': _nuscenes(raw, delivery), 'argoverse': _argoverse(raw)}
