"""Explicit, auditable XY/time conversion for upstream road-state evidence."""
from copy import deepcopy
import math

from .evidence_intake import require_identity, _timestamp
from .planning_contract import number, xy
from .state_events import _geometry as validate_state_geometry


def adapt_events(events, transform, source_sha256):
    fields = {'source_frame', 'target_frame', 'rotation_rad', 'translation_m', 'scale',
              'source_time_origin_ns', 'target_time_origin_ns', 'drop_z'}
    if not isinstance(transform, dict) or set(transform) != fields:
        raise ValueError('Transform must specify both frames, XY transform, both time origins and drop_z')
    require_identity(transform['source_frame'], 'source_frame')
    require_identity(transform['target_frame'], 'target_frame')
    angle = number(transform['rotation_rad'], 'rotation_rad')
    translation = xy(transform['translation_m'])
    scale = number(transform['scale'], 'scale')
    if scale <= 0 or type(transform['drop_z']) is not bool:
        raise ValueError('Positive scale and explicit drop_z required')
    source_origin = _timestamp(transform['source_time_origin_ns'])
    target_origin = _timestamp(transform['target_time_origin_ns'])
    if not isinstance(events, list):
        raise ValueError('Expected events list')

    def timestamp(value):
        converted = _timestamp(value)-source_origin+target_origin
        if converted < 0:
            raise ValueError('Transformed evidence predates the simulation origin')
        return str(converted)

    def geometry(value):
        result = deepcopy(value)
        if not isinstance(result, dict):
            raise ValueError('Expected source geometry')
        if result.get('type') == 'GeometryCollection':
            result['geometries'] = [geometry(child) for child in result['geometries']]
            return result
        kind = result.get('type')
        if kind not in ('LineString','Polygon'):
            raise ValueError('Supported evidence shapes are LineString, Polygon and GeometryCollection')
        rings = [result['coordinates']] if kind == 'LineString' else result['coordinates']
        converted = []
        c,s = math.cos(angle),math.sin(angle)
        for ring in rings:
            points = []
            for point in ring:
                if not isinstance(point,list) or len(point) not in (2,3):
                    raise ValueError('Source point must contain XY or XYZ')
                if len(point)==3 and not transform['drop_z']:
                    raise ValueError('XYZ input requires explicit drop_z=true')
                values = [number(v,'source coordinate') for v in point]
                x,y = values[:2]
                points.append([scale*(c*x-s*y)+translation[0], scale*(s*x+c*y)+translation[1]])
            converted.append(points)
        result['coordinates'] = converted[0] if kind=='LineString' else converted
        validate_state_geometry(result)
        return result

    result = deepcopy(events)
    for event in result:
        reference = event.get('coordinate_reference')
        if not isinstance(reference,dict) or reference.get('frame') != transform['source_frame']:
            raise ValueError('Source coordinate frame mismatch')
        event['coordinate_reference'] = dict(frame=transform['target_frame'],units='m')
        if 'expires_at_timestamp_ns' in event:
            event['expires_at_timestamp_ns'] = timestamp(event['expires_at_timestamp_ns'])
        for sample in event.get('samples',[]):
            sample['timestamp_ns'] = timestamp(sample['timestamp_ns'])
            if 'geometry_city' in sample:
                sample['geometry_city'] = geometry(sample['geometry_city'])
            sources=sample.get('source_ids')
            if not isinstance(sources,list) or not sources:
                raise ValueError('Preserve explicit original evidence source_ids')
            sample['source_ids'] = sources + ['adapted-source-sha256:'+source_sha256]
    return result
