"""Replay attached map-state events without turning missing data into clearance.

Geometry uses the source city-coordinate system. The FeatureCollection is a
GeoJSON-shaped interchange artifact, not a claim of WGS84 longitude/latitude.
Only an explicitly supplied expiry/TTL can expire evidence; there is no default.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from math import isfinite

from .geometry import validate_geometry


def _time(value):
    if not isinstance(value, str) or not value or not value.isascii() or not value.isdecimal():
        raise ValueError('Exact timestamp_ns must be a nonnegative decimal string')
    return int(value)


def _geometry(value):
    if isinstance(value, dict) and value.get('type') == 'GeometryCollection':
        children = value.get('geometries')
        if not isinstance(children, list) or not children:
            raise ValueError('Observed GeometryCollection must contain source geometries')
        for child in children:
            _geometry(child)
    else:
        validate_geometry(value)
    return deepcopy(value)


def _samples(event):
    grouped, frame_times = defaultdict(list), {}
    aliases = {a['map_element_id'] for a in event.get('map_aliases', [])}
    for row in event.get('samples', []):
        stamp = _time(row['timestamp_ns'])
        if row.get('event_id', event['event_id']) != event['event_id']:
            raise ValueError('A sample cannot clear or update a different event identity')
        if row.get('map_element_id') is not None and row['map_element_id'] not in aliases:
            raise ValueError('Sample target differs from this event aliases')
        if row.get('observation') not in {'supports_change', 'supports_prior', 'unknown'}:
            raise ValueError('Unsupported state-event observation')
        quality = row.get('quality', 0.)
        if type(quality) not in (int, float) or not isfinite(quality) or not 0 <= quality <= 1:
            raise ValueError('State-event quality must be finite and within [0,1]')
        if 'fully_observed' in row and type(row['fully_observed']) is not bool:
            raise ValueError('fully_observed must be explicit boolean')
        identity = row.get('visit_id'), row.get('frame_id')
        if identity[1] is not None:
            if identity in frame_times and frame_times[identity] != stamp:
                raise ValueError('One event frame has conflicting timestamps')
            frame_times[identity] = stamp
        if row not in grouped[stamp]:
            grouped[stamp].append(row)
    return grouped


def _expiry(event, last_seen):
    values = []
    if event.get('expires_at_timestamp_ns') is not None:
        values.append(_time(event['expires_at_timestamp_ns']))
    if event.get('ttl_s') is not None:
        ttl = event['ttl_s']
        if type(ttl) not in (int, float) or not isfinite(ttl) or ttl < 0:
            raise ValueError('Explicit ttl_s must be finite and nonnegative')
        if last_seen is not None:
            values.append(last_seen+round(ttl*1_000_000_000))
    return min(values) if values else None


def _reduce(event, grouped, cutoff):
    geometry, last_seen, last_recorded, cleared_at, observed_quality = None, None, None, None, None
    state, last_observed_state, last_sources, clearance_sources = 'unknown', None, [], []
    history = []
    for stamp, rows in sorted(grouped.items()):
        if stamp > cutoff:
            continue
        last_recorded = stamp
        positive = [r for r in rows if r['observation'] == 'supports_change' and r.get('quality', 0) > 0]
        recovery = [r for r in rows if r['observation'] == 'supports_prior' and r.get('quality', 0) > 0 and r.get('fully_observed') is True]
        source_ids = sorted({source for row in rows for source in row.get('source_ids', [])})
        if positive:
            # Preserve a real observed shape, never construct an averaged shape.
            chosen = max(positive, key=lambda r: r['quality'])
            geometry = _geometry(chosen.get('geometry_city'))
            last_seen, observed_quality, last_sources = stamp, chosen['quality'], deepcopy(chosen.get('source_ids', []))
        if positive and recovery:
            state, reason = 'unknown', 'CONTRADICTORY_SIMULTANEOUS_OBSERVATIONS'
            cleared_at, clearance_sources = None, []
        elif positive:
            state, last_observed_state, cleared_at = 'observed_active', 'active', None
            clearance_sources = []
            reason = 'OBSERVED_EVENT_SUPPORT'
        elif recovery:
            state, last_observed_state, cleared_at = 'cleared', 'cleared', stamp
            clearance_sources = sorted({source for row in recovery for source in row.get('source_ids', [])})
            reason = 'EXPLICIT_FULLY_OBSERVED_EVENT_RECOVERY'
        elif state == 'cleared':
            # Missing data cannot recreate an event already explicitly cleared.
            reason = 'NO_NEW_POSITIVE_OBSERVATION_AFTER_CLEARANCE'
        else:
            state, reason = 'unknown', 'MISSING_OR_UNVERIFIED_OBSERVATION_IS_NOT_CLEARANCE'
        history.append({'timestamp_ns': str(stamp), 'state': state, 'reason_code': reason, 'source_ids': source_ids})
    expires = _expiry(event, last_seen)
    if state != 'cleared' and last_recorded is not None:
        if expires is not None and cutoff >= expires:
            state = 'stale'
        elif cutoff > last_recorded:
            state = 'unknown'
    properties = {'event_id': event['event_id'], 'change_type': event.get('change_type'),
                  'map_aliases': deepcopy(event.get('map_aliases', [])), 'state': state,
                  'validity': {'observed_active': 'observed_at_last_seen', 'unknown': 'unverified',
                               'stale': 'expired', 'cleared': 'explicitly_cleared'}[state],
                  'last_observed_state': last_observed_state,
                  'last_seen_timestamp_ns': str(last_seen) if last_seen is not None else None,
                  'last_recorded_timestamp_ns': str(last_recorded) if last_recorded is not None else None,
                  'expires_at_timestamp_ns': str(expires) if expires is not None else None,
                  'ttl_s': event.get('ttl_s'), 'cleared_at_timestamp_ns': str(cleared_at) if cleared_at is not None else None,
                  'clearance_observed': state == 'cleared', 'observed_quality': observed_quality,
                  'current_quality': observed_quality if state == 'observed_active' else None,
                  'score_is_probability': False, 'source_ids': last_sources,
                  'clearance_source_ids': clearance_sources,
                  'coordinate_reference': deepcopy(event.get('coordinate_reference')),
                  'geometry_role': 'last_observed_event_geometry_not_verified_current_extent',
                  'history': history, 'base_map_modified': False}
    return {'type': 'Feature', 'id': event['event_id'], 'geometry': geometry, 'properties': properties}


def build_state_event_layer(events, as_of_timestamp_ns=None):
    """Build state features plus explicit-clearance records at a replay cutoff.

    Eligible events have ``map_layer.predicted_layer == 'map_state_event'`` and
    ``reliability.status == 'supported_conflict'``. Each positive sample must
    carry its own observed geometry; event-level geometry is never used to leak
    a future observation into an earlier replay. Recovery requires the same
    event ID, positive quality and ``fully_observed=True``. Unknown/stale shapes
    remain as explicitly unverified historical state; cleared tags are removed
    from features and retained in ``cleared_events`` for audit.
    """
    identities = {}
    for event in events:
        eid = event.get('event_id')
        if not isinstance(eid, str) or not eid:
            raise ValueError('State events require a stable nonempty event_id')
        if eid in identities and identities[eid] != event:
            raise ValueError('Conflicting copies of one event identity')
        identities[eid] = event
    selected, excluded = [], []
    for event in identities.values():
        if event.get('map_layer', {}).get('predicted_layer') != 'map_state_event':
            excluded.append({'event_id': event['event_id'], 'reason': 'NOT_CLASSIFIED_AS_MAP_STATE_EVENT'})
        elif event.get('reliability', {}).get('status') != 'supported_conflict':
            excluded.append({'event_id': event['event_id'], 'reason': 'OBSERVED_STATE_CONFLICT_NOT_SUPPORTED'})
        else:
            selected.append((event, _samples(event)))
    cutoff = _time(as_of_timestamp_ns) if as_of_timestamp_ns is not None else max(
        (stamp for _, grouped in selected for stamp in grouped), default=None)
    features, cleared, unobserved, refs = [], [], [], []
    for event, grouped in selected:
        reference = event.get('coordinate_reference')
        if reference is not None and reference not in refs:
            refs.append(reference)
        feature = _reduce(event, grouped, cutoff) if cutoff is not None else None
        if feature and feature['properties']['state'] == 'cleared':
            cleared.append(feature['properties'])
        elif feature and feature['geometry'] is not None:
            features.append(feature)
        else:
            unobserved.append({'event_id': event['event_id'], 'reason': 'NO_SUPPORTED_GEOMETRY_AT_REPLAY_CUTOFF'})
    if len(refs) > 1:
        raise ValueError('State layer cannot mix coordinate references')
    if refs and any(event.get('coordinate_reference') is None for event, _ in selected):
        raise ValueError('State layer cannot mix known and unspecified coordinate references')
    counts = Counter(feature['properties']['state'] for feature in features)
    return {'type': 'FeatureCollection', 'schema': 'map-state-event-layer-1.0',
            'as_of_timestamp_ns': str(cutoff) if cutoff is not None else None,
            'coordinate_reference': deepcopy(refs[0]) if refs else None,
            'geometry_semantics': 'Source city-coordinate geometry; not asserted WGS84 longitude/latitude.',
            'features': features, 'cleared_events': cleared, 'unobserved_events': unobserved, 'excluded_events': excluded,
            'summary': {'input_events': len(identities), 'eligible_events': len(selected), 'features': len(features),
                        'observed_active': counts['observed_active'], 'unknown': counts['unknown'], 'stale': counts['stale'],
                        'cleared': len(cleared), 'unobserved': len(unobserved), 'excluded': len(excluded)},
            'policy': {'identity_scope': 'event_id_not_shared_lane', 'missing_data': 'unknown_not_clearance',
                       'clearance': 'explicit_fully_observed_same_event_recovery', 'default_ttl_s': None,
                       'expiry': 'explicit_ttl_or_absolute_expiry_only; expires_quality_not_observed_clearance',
                       'cleared_tags': 'removed_from_features_retained_in_cleared_events',
                       'temporal_scope': 'replay_of_observations_for_offline_classified_events', 'base_map_modified': False}}
