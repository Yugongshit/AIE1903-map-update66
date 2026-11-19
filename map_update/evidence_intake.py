"""Validate append-only upstream road-state evidence before atomic publication."""
from copy import deepcopy
from math import isfinite

from .state_events import build_state_event_layer, _geometry as validate_state_geometry


def require_identity(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(field + ' must be a nonempty string of at most 256 characters')


def prepare_evidence(existing, events, *, cutoff_ns, coordinate_frame):
    """Return a detached candidate store; never change already accepted evidence."""
    if not isinstance(events, list) or not 1 <= len(events) <= 100:
        raise ValueError('events must contain between 1 and 100 road-state events')
    candidate = deepcopy(existing)
    seen = set()
    reference = {'frame': coordinate_frame, 'units': 'm'}
    for event in events:
        if not isinstance(event, dict):
            raise ValueError('Each event must be an object')
        allowed = {'event_id', 'source_object_id', 'change_type', 'map_layer', 'reliability',
                   'coordinate_reference', 'score_semantics', 'unknown_fields', 'map_aliases',
                   'ttl_s', 'expires_at_timestamp_ns', 'samples'}
        if set(event) - allowed:
            raise ValueError('Unknown event fields')
        for field in ('event_id', 'source_object_id', 'change_type', 'score_semantics'):
            require_identity(event.get(field), field)
        eid = event['event_id']
        if eid in seen:
            raise ValueError('Duplicate event_id in one submission')
        seen.add(eid)
        if event.get('map_layer') != {'predicted_layer': 'map_state_event'}:
            raise ValueError('This endpoint accepts road-state evidence only')
        if event.get('reliability') != {'status': 'supported_conflict'}:
            raise ValueError('Upstream supported_conflict classification is required')
        if event.get('coordinate_reference') != reference:
            raise ValueError('Evidence coordinates must match the current observation frame')
        unknown = event.get('unknown_fields')
        if not isinstance(unknown, list) or any(not isinstance(item, str) or not item for item in unknown):
            raise ValueError('unknown_fields must be an explicit string list')
        aliases = event.get('map_aliases', [])
        if not isinstance(aliases, list):
            raise ValueError('map_aliases must be a list')
        for alias in aliases:
            if not isinstance(alias, dict) or set(alias) != {'map_element_id'}:
                raise ValueError('Map aliases require map_element_id')
            require_identity(alias['map_element_id'], 'map_element_id')
        if 'ttl_s' in event:
            ttl = event['ttl_s']
            if type(ttl) not in (int, float) or not isfinite(ttl) or ttl < 0:
                raise ValueError('ttl_s must be finite and nonnegative')
        if 'expires_at_timestamp_ns' in event:
            _timestamp(event['expires_at_timestamp_ns'])
        samples = event.get('samples')
        if not isinstance(samples, list) or not 1 <= len(samples) <= 10000:
            raise ValueError('Each event requires 1 to 10000 samples')
        last = -1
        for sample in samples:
            if not isinstance(sample, dict):
                raise ValueError('Each sample must be an object')
            fields = {'timestamp_ns', 'observation', 'quality', 'fully_observed',
                      'source_ids', 'geometry_city', 'event_id', 'map_element_id', 'visit_id', 'frame_id'}
            if set(sample) - fields:
                raise ValueError('Unknown sample fields')
            if 'geometry_city' in sample:
                validate_state_geometry(sample['geometry_city'])
            stamp = _timestamp(sample.get('timestamp_ns'))
            if stamp < last or stamp > cutoff_ns:
                raise ValueError('Samples must be ordered and cannot be in the future')
            last = stamp
            sources = sample.get('source_ids')
            if not isinstance(sources, list) or not sources:
                raise ValueError('Every sample requires evidence source_ids')
            for source in sources:
                require_identity(source, 'source_id')
            if 'quality' not in sample:
                raise ValueError('Every sample requires an explicit quality score')
            for field in ('visit_id', 'frame_id'):
                if field in sample:
                    require_identity(sample[field], field)
        old = existing.get(eid)
        if old is not None:
            old_metadata = {k:v for k,v in old.items() if k != 'samples'}
            metadata = {k:v for k,v in event.items() if k != 'samples'}
            if metadata != old_metadata or samples[:len(old['samples'])] != old['samples']:
                raise ValueError('Existing event metadata and samples are immutable; append observations')
        candidate[eid] = deepcopy(event)
    # Reuse the existing geometry, score, conflicting evidence and clearance rules.
    build_state_event_layer(list(candidate.values()), str(cutoff_ns))
    return candidate


def _timestamp(value):
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdecimal():
        raise ValueError('timestamp_ns must be a nonnegative decimal string')
    if len(value) > 24:
        raise ValueError('timestamp_ns is out of range')
    return int(value)


def road_state_layer(events, *, cutoff_ns, coordinate_frame):
    layer = build_state_event_layer(list(events.values()), str(cutoff_ns))
    layer['coordinate_reference'] = {'frame': coordinate_frame, 'units': 'm'}
    layer['geometry_semantics'] = 'Current session coordinate frame in metres.'
    layer['time_basis'] = 'simulation_time_ns'
    layer['policy']['temporal_scope'] = 'accepted_evidence_up_to_current_simulation_frame'
    # Keep upstream score semantics and unknowns alongside each published event.
    properties = [row['properties'] for row in layer['features']] + layer['cleared_events']
    for row in properties:
        event = events[row['event_id']]
        row.update(source_object_id=event['source_object_id'],
                   score_semantics=event['score_semantics'],
                   unknown_fields=deepcopy(event['unknown_fields']))
    return layer
