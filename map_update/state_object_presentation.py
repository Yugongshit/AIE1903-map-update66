"""Annotation-backed display models for already-decided map-state events.

This is a post-prediction presentation adapter. It neither detects objects nor
changes an event's classification, lifecycle, geometry, or map write decision.
Object identity comes from the retained observation's instance token and frame;
geometry is only a consistency check, never a nearest-object identity lookup.
"""

from collections import Counter, defaultdict
from copy import deepcopy
import csv
from hashlib import sha256
import json
from math import isfinite, radians
from pathlib import Path

from .scene import footprint


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                             ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _shape(category):
    exact = {'movable_object.barrier': 'barrier', 'movable_object.trafficcone': 'cone',
             'vehicle.car': 'car', 'vehicle.truck': 'truck',
             'vehicle.bicycle': 'bicycle', 'vehicle.motorcycle': 'motorcycle'}
    if category.startswith('vehicle.bus.'):
        return 'bus'
    if category.startswith('human.pedestrian.'):
        return 'pedestrian'
    return exact.get(category, 'other')


def _geometry_matches(a, b):
    """Permit floating-point roundoff, not spatial matching or shape changes."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_geometry_matches(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_geometry_matches(x, y) for x, y in zip(a, b))
    if type(a) in (int, float) and type(b) in (int, float):
        return isfinite(a) and isfinite(b) and abs(a-b) <= 1e-7
    return a == b


def _finite(row, key):
    value = float(row[key])
    if not isfinite(value):
        raise ValueError('nonfinite source dimension or pose')
    return value


def build_state_object_presentation(raw_root, events, state_layer):
    """Resolve semantic display models against exact retained source footprints.

    ``raw_root`` is the original nuScenes scene folder containing ``processed``.
    Each GeometryCollection member is one object in the *same retained frame*,
    ordered by that sample's ``instance_tokens``. Repeated observations in other
    frames do not become additional display objects. Missing or ambiguous joins
    return explicit footprint fallbacks. No input object is mutated.

    Dimensions and heading retain the annotation convention: length is local X,
    width is local Y, and yaw is counterclockwise in city XY. Height is placed
    above the display ground; source global Z does not establish road elevation.
    A missing height is an explicitly marked 1 m schematic height. Generic
    semantic prototypes do not claim recovered surface shape, texture or color.
    """
    root = Path(raw_root)
    source_files, inputs, unavailable = [], {}, []
    for relative in ('processed/objects.csv', 'processed/frames.csv'):
        path = root/relative
        if not path.is_file():
            unavailable.append(relative)
            continue
        source_files.append({'path': relative, 'sha256': sha256(path.read_bytes()).hexdigest()})
        with path.open(encoding='utf-8-sig', newline='') as stream:
            inputs[relative] = list(csv.DictReader(stream))

    objects_by_key, frames_by_id, events_by_id = defaultdict(list), defaultdict(list), defaultdict(list)
    for row in inputs.get('processed/objects.csv', []):
        objects_by_key[(row.get('instance_token'), row.get('frame_index'))].append(row)
    for row in inputs.get('processed/frames.csv', []):
        frames_by_id[row.get('frame_index')].append(row)
    for event in events:
        events_by_id[event.get('event_id')].append(event)

    records, fallbacks = [], []
    features = state_layer.get('features', [])
    feature_ids = Counter(f.get('properties', {}).get('event_id') for f in features)

    def fallback(feature, reason, index=None, instance=None):
        item = {'event_id': feature.get('properties', {}).get('event_id', feature.get('id')),
                'reason_code': reason, 'render_mode': 'source_footprint', 'footprint_index': index}
        if instance is not None:
            item['instance_token'] = instance
        fallbacks.append(item)

    for feature in features:
        properties = feature.get('properties', {})
        event_id = properties.get('event_id')
        if unavailable:
            fallback(feature, 'SOURCE_FILES_UNAVAILABLE')
            continue
        if feature_ids[event_id] != 1 or len(events_by_id[event_id]) != 1:
            fallback(feature, 'AMBIGUOUS_EVENT_IDENTITY')
            continue
        event = events_by_id[event_id][0]
        if event.get('change_type') != 'occupancy':
            fallback(feature, 'NOT_AN_ANNOTATED_OCCUPANCY_EVENT')
            continue
        source_ids = properties.get('source_ids', [])
        frame_ids = [source[6:] for source in source_ids if source.startswith('frame:')]
        tokens = [source[9:] for source in source_ids if source.startswith('instance:')]
        if (len(frame_ids) != 1 or not tokens or len(tokens) != len(set(tokens))
                or event_id not in source_ids):
            fallback(feature, 'SOURCE_IDENTITY_UNAVAILABLE_OR_AMBIGUOUS')
            continue
        frame_id = frame_ids[0]
        stamp = properties.get('last_seen_timestamp_ns')
        samples = [s for s in event.get('samples', [])
                   if s.get('frame_id') == frame_id and s.get('timestamp_ns') == stamp
                   and s.get('observation') == 'supports_change' and s.get('quality', 0) > 0
                   and s.get('source_ids') == source_ids
                   and _geometry_matches(s.get('geometry_city'), feature.get('geometry'))]
        # Identical copies are the same retained observation, not extra evidence.
        samples = list({_digest(s): s for s in samples}.values())
        if len(samples) != 1 or samples[0].get('instance_tokens') != tokens:
            fallback(feature, 'RETAINED_OBSERVATION_IDENTITY_MISMATCH')
            continue
        frame_rows = frames_by_id[frame_id]
        if len(frame_rows) != 1:
            fallback(feature, 'FRAME_SOURCE_UNAVAILABLE_OR_AMBIGUOUS')
            continue
        frame = frame_rows[0]
        try:
            timestamp_matches = str(int(frame['timestamp_us'])*1000) == stamp
        except (KeyError, ValueError, TypeError):
            timestamp_matches = False
        if not timestamp_matches:
            fallback(feature, 'FRAME_TIMESTAMP_MISMATCH')
            continue
        geometry = feature.get('geometry') or {}
        shapes = geometry.get('geometries', []) if geometry.get('type') == 'GeometryCollection' else [geometry]
        if len(shapes) != len(tokens) or any(g.get('type') != 'Polygon' for g in shapes):
            fallback(feature, 'FOOTPRINT_IDENTITY_COUNT_MISMATCH')
            continue
        categories = event.get('evidence', {}).get('categories', [])
        for index, (token, shape) in enumerate(zip(tokens, shapes)):
            rows = objects_by_key[(token, frame_id)]
            if len(rows) != 1:
                fallback(feature, 'OBJECT_SOURCE_UNAVAILABLE_OR_AMBIGUOUS', index, token)
                continue
            row = rows[0]
            if (not row.get('annotation_token') or not row.get('sample_token')
                    or row['sample_token'] != frame.get('sample_token')):
                fallback(feature, 'ANNOTATION_SAMPLE_IDENTITY_MISMATCH', index, token)
                continue
            category = row.get('category', '')
            if not category or category not in categories:
                fallback(feature, 'OBJECT_CATEGORY_MISMATCH', index, token)
                continue
            try:
                x, y = _finite(row, 'x_global_m'), _finite(row, 'y_global_m')
                length, width = _finite(row, 'length_m'), _finite(row, 'width_m')
                yaw = radians(_finite(row, 'yaw_global_deg'))
                if length <= 0 or width <= 0:
                    raise ValueError('nonpositive footprint')
                expected_footprint = footprint(row)
            except (KeyError, ValueError, TypeError):
                fallback(feature, 'INVALID_ANNOTATION_FOOTPRINT', index, token)
                continue
            if not _geometry_matches(expected_footprint, shape):
                fallback(feature, 'ANNOTATION_FOOTPRINT_MISMATCH', index, token)
                continue
            try:
                height = _finite(row, 'height_m')
                if height <= 0:
                    raise ValueError('nonpositive height')
                height_source = 'source_annotation'
            except (KeyError, ValueError, TypeError):
                height, height_source = 1., 'schematic_default'
            record = {'event_id': event_id, 'object_id': 'instance:'+token,
                            'instance_token': token, 'annotation_token': row['annotation_token'],
                            'sample_token': row['sample_token'], 'frame_id': frame_id, 'timestamp_ns': stamp,
                            'category': category, 'semantic_shape': _shape(category),
                            'center_xy': [x, y], 'length_m': length, 'width_m': width,
                            'height_m': height, 'height_source': height_source, 'yaw_rad': yaw,
                            'footprint_index': index, 'footprint_geometry_city': deepcopy(shape),
                            'observation_state': properties.get('state'),
                            'geometry_role': properties.get('geometry_role'),
                            'source_file': 'processed/objects.csv',
                            'appearance_source': 'schematic_category_prototype',
                            'vertical_placement': 'display_ground_relative_not_source_road_elevation'}
            try:
                record['center_xyz'] = [x, y, _finite(row, 'z_global_m')]
            except (KeyError, ValueError, TypeError):
                pass
            records.append(record)

    identities = Counter(r['instance_token'] for r in records)
    snapshots = Counter((r['instance_token'], r['frame_id']) for r in records)
    return {'schema': 'map-state-object-presentation-1.0', 'objects': records, 'fallbacks': fallbacks,
            'summary': {'state_events': len(features), 'matched_events': len({r['event_id'] for r in records}),
                        'objects': len(records), 'distinct_instances': len(identities),
                        'distinct_instance_frames': len(snapshots),
                        'repeated_instance_records': sum(n-1 for n in identities.values()),
                        'fallbacks': len(fallbacks),
                        'categories': dict(sorted(Counter(r['category'] for r in records).items())),
                        'semantic_shapes': dict(sorted(Counter(r['semantic_shape'] for r in records).items())),
                        'annotation_height_objects': sum(r['height_source'] == 'source_annotation' for r in records)},
            'provenance': {'source_files': source_files, 'unavailable_source_files': unavailable,
                           'events_sha256': _digest(events), 'state_layer_sha256': _digest(state_layer),
                           'identity_join': 'retained_state_source_ids + exact_frame + sample_instance_order',
                           'geometry_check': 'source_oriented_box_matches_retained_polygon_within_1e-7_m',
                           'category_source': 'supplied_nuscenes_annotation_table',
                           'sensor_inference_rerun': False, 'predictions_modified': False,
                           'state_lifecycle_modified': False, 'base_map_modified': False,
                           'temporal_scope': 'each_event_last_observed_frame_not_simultaneous_current_scene',
                           'appearance_scope': 'category_prototypes_not_recovered_meshes_or_textures'}}
