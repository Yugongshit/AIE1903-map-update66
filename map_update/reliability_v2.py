"""Local geometric reliability from frozen observations, without change truth.

This is a robust, quality-weighted engineering estimator, not a trained or
calibrated probability model. A supported conflict says observed geometry
differs from the selected reference; it does not establish permanence, visibility
of missing paint, or permission to edit the base map.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from math import isfinite, sqrt
from statistics import median


@dataclass(frozen=True)
class ReliabilityPolicy:
    candidate_distance_m: float = 3.0
    direction_deg: float = 25.0
    ambiguity_gap_m: float = .25
    minimum_curve_length_m: float = 1.5
    full_quality_length_m: float = 3.0
    minimum_quality: float = .35
    anchor_spacing_m: float = 5.0
    residual_tolerance_m: float = .5
    maximum_mad_m: float = .35
    temporal_block_s: float = .5
    minimum_blocks: int = 3
    minimum_span_s: float = 1.0
    minimum_support_fraction: float = .7

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                raise ValueError('Invalid reliability policy: '+name)
        if type(self.minimum_blocks) is not int or self.minimum_blocks < 2:
            raise ValueError('minimum_blocks must be an integer >= 2')
        if self.minimum_quality > 1 or self.minimum_support_fraction > 1:
            raise ValueError('Quality and support fractions must be <= 1')


def _id(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:16]


def canonical_boundaries(prior):
    """Merge exact XY geometry, including reversal, only with equal paint type."""
    grouped = {}
    for lane in prior['lane_segments']:
        for side in ('left', 'right'):
            points = [(float(p['x']), float(p['y'])) for p in lane[side+'_lane_boundary']]
            points = tuple(p for i, p in enumerate(points) if i == 0 or p != points[i-1])
            if len(points) < 2 or not all(isfinite(x) for p in points for x in p):
                continue
            points = min(points, points[::-1])
            marking = lane.get(side+'_lane_mark_type', 'UNKNOWN')
            key = (points, marking)
            if key not in grouped:
                grouped[key] = {'boundary_id': 'boundary-'+_id([prior['scene_id'], key]), 'points': points,
                                'marking_type': marking, 'aliases': []}
            grouped[key]['aliases'].append({'map_element_id': lane['id'], 'boundary_side': side,
                                            'physical_boundary_id': grouped[key]['boundary_id']})
    result = list(grouped.values())
    for row in result:
        row['aliases'].sort(key=lambda r: (str(r['map_element_id']), r['boundary_side']))
        row['physical_boundary_id'] = row['boundary_id']
        row['coordinates'] = [list(p) for p in row['points']]
    return sorted(result, key=lambda r: r['boundary_id'])


def _polyline(points):
    import numpy as np
    p = np.asarray(points, dtype=float)[:, :2]
    v = np.diff(p, axis=0)
    lengths = np.linalg.norm(v, axis=1)
    return p, v, lengths, np.r_[0., np.cumsum(lengths)]


def _sample(points, spacing=.5):
    import numpy as np
    p, _, _, arc = _polyline(points)
    ts = np.unique(np.r_[np.arange(0, arc[-1], spacing), arc[-1]])
    return np.column_stack([np.interp(ts, arc, p[:, k]) for k in (0, 1)])


def _distance(points, line):
    import numpy as np
    p, v, lengths, _ = _polyline(line)
    u = np.clip(((points[:, None]-p[:-1])*v).sum(2)/np.maximum(lengths**2, 1e-12), 0, 1)
    return np.linalg.norm(points[:, None]-(p[:-1]+u[:, :, None]*v), axis=2).min(1)


def _anchors(line, spacing):
    import numpy as np
    p, v, lengths, arc = _polyline(line)
    positions = np.unique(np.r_[np.arange(spacing/2, arc[-1], spacing), arc[-1]/2])
    result = []
    for s in positions:
        i = min(int(np.searchsorted(arc, s, side='right'))-1, len(v)-1)
        tangent = v[i]/lengths[i]
        result.append((float(s), p[i]+(s-arc[i])*tangent, tangent))
    return result


def _fixed_residual(line, anchor):
    """Intersect observation with the same reference normal in every frame."""
    import numpy as np
    _, point, tangent = anchor
    normal = np.array([-tangent[1], tangent[0]])
    p = np.asarray(line)[:, :2]
    along = (p-point)@tangent
    values = []
    for i, (a, b) in enumerate(zip(along, along[1:])):
        if a*b <= 0 and abs(b-a) > 1e-10:
            q = p[i]+(-a/(b-a))*(p[i+1]-p[i])
            values.append(float((q-point)@normal))
    if not values or max(values)-min(values) > .25:
        return None
    return median(values)


def _match(geo, boundaries, policy):
    import numpy as np
    points = _sample(geo)
    direction = np.asarray(geo[-1])[:2]-np.asarray(geo[0])[:2]
    norm = np.linalg.norm(direction)
    if norm <= 1e-8:
        return []
    lo, hi = points.min(0), points.max(0)
    found = []
    for boundary in boundaries:
        line = np.asarray(boundary['points'])
        separation = np.maximum(np.maximum(line.min(0)-hi, lo-line.max(0)), 0)
        if np.linalg.norm(separation) > policy.candidate_distance_m:
            continue
        v = line[-1]-line[0]
        den = norm*np.linalg.norm(v)
        if den <= 1e-8:
            continue
        angle = float(np.degrees(np.arccos(np.clip(abs(direction@v)/den, 0, 1))))
        if angle > policy.direction_deg:
            continue
        distance = float(_distance(points, line).mean())
        if distance <= policy.candidate_distance_m:
            found.append((distance, boundary['boundary_id'], boundary))
    return sorted(found, key=lambda r: (r[0], r[1]))


def _quality(o, policy):
    import numpy as np
    if o.get('alignment_valid') is False:
        return None, 0., 'EXPLICIT_ALIGNMENT_FAILURE'
    geo = o.get('geometry_city')
    if geo is None or len(geo) < 2:
        return None, 0., 'NO_USABLE_CITY_GEOMETRY'
    geo = np.asarray(geo, dtype=float)
    if geo.ndim != 2 or geo.shape[1] < 2 or not np.isfinite(geo).all():
        return None, 0., 'INVALID_CITY_GEOMETRY'
    geo = geo[np.r_[True, np.linalg.norm(np.diff(geo[:, :2], axis=0), axis=1) > 1e-8]]
    if len(geo) < 2:
        return None, 0., 'DEGENERATE_GEOMETRY'
    length = float(np.linalg.norm(np.diff(geo[:, :2], axis=0), axis=1).sum())
    if length < policy.minimum_curve_length_m:
        return None, 0., 'LOCAL_CURVE_TOO_SHORT'
    pixels = len(o.get('pixel_curve_xy') or [])
    fraction = min(1., len(o.get('geometry_pixel_indices') or [])/pixels) if pixels else 0.
    score = o.get('model_score_mean')
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not isfinite(score) or not 0 <= score <= 1:
        return None, 0., 'MODEL_SCORE_UNAVAILABLE'
    quality = sqrt(score*fraction)*min(length/policy.full_quality_length_m, 1.)
    return geo.tolist(), quality, None if quality >= policy.minimum_quality else 'LOW_LOCAL_GEOMETRIC_QUALITY'


def _structural_hypothesis(marking, assessment):
    """Identify the property being questioned without confirming a map edit."""
    if marking == 'NONE':
        target, operation = 'paint_presence', 'marking_presence_change'
        reasons = ['PRIOR_BOUNDARY_IS_NOT_KNOWN_PAINTED',
                   'VISIBLE_CURVE_MAY_INDICATE_PAINT_PRESENCE_NOT_OLD_PAINT_DISPLACEMENT']
        required = ['observed_curve_is_paint_not_curb_or_projection_artifact',
                    'paint_absence_to_presence_at_verified_local_reference']
    elif marking in ('UNKNOWN', None, ''):
        target, operation = 'unspecified_marking_semantics', 'unspecified'
        reasons = ['PRIOR_MARKING_SEMANTICS_UNKNOWN', 'CHANGE_PROPERTY_CANNOT_BE_RESOLVED']
        required = ['prior_and_observed_marking_semantics', 'verified_change_property']
    else:
        target, operation = 'boundary_geometry', 'geometry_change'
        reasons = ['PRIOR_ATTRIBUTE_DESCRIBES_PAINTED_MARKING',
                   'LOCAL_GEOMETRIC_RELATION_DOES_NOT_CONFIRM_PHYSICAL_MAP_CHANGE']
        required = ['same_physical_painted_boundary_correspondence',
                    'physical_alignment_and_observed_edit_geometry']
    return {'target_property': target, 'operation': operation,
            'status': 'unconfirmed', 'confirmed': False,
            'assessment': assessment, 'scope': 'observed_local_segment',
            'reference_marking_type': marking,
            'reason_codes': reasons, 'required_evidence': required,
            'base_map_write_eligible': False}


def _event(boundary, rows, frame_index, policy):
    # Anchor selection uses only whether a curve crosses a predefined normal,
    # never truth labels, discrepancy magnitude, or a candidate identifier.
    choices = []
    for anchor in _anchors(boundary['points'], policy.anchor_spacing_m):
        selected = [(r, _fixed_residual(r['geometry'], anchor)) for r in rows]
        selected = [(r, x) for r, x in selected if x is not None]
        choices.append((len({r['frame_id'] for r, _ in selected}), anchor, selected))
    _, anchor, selected = max(choices, key=lambda x: (x[0], -x[1][0]))
    by_frame = defaultdict(list)
    for row, residual in selected:
        by_frame[row['frame_id']].append((row, residual))
    frame_rows = []
    for fid, values in sorted(by_frame.items(), key=lambda p: int(p[0])):
        residual = median(x for _, x in values)
        disagreement = max(x for _, x in values)-min(x for _, x in values)
        quality = max(r['quality'] for r, _ in values)/(1+disagreement/policy.residual_tolerance_m)
        frame_rows.append({'timestamp_ns': frame_index[fid]['timestamp_ns'], 'frame_id': fid,
                           'residual_m': residual, 'quality': quality,
                           'source_ids': sorted({r['observation_id'] for r, _ in values})})
    blocks = defaultdict(list)
    block_ns = round(policy.temporal_block_s*1e9)
    for r in frame_rows:
        blocks[int(r['timestamp_ns'])//block_ns].append(r)
    samples = []
    for _, members in sorted(blocks.items()):
        residual = median(r['residual_m'] for r in members)
        relation = ('offset_positive' if residual > policy.residual_tolerance_m else
                    'offset_negative' if residual < -policy.residual_tolerance_m else 'within_tolerance')
        samples.append({'timestamp_ns': members[-1]['timestamp_ns'], 'frame_id': members[-1]['frame_id'],
                        'visit_id': rows[0]['visit_id'], 'observation': 'supports_change' if abs(residual) > policy.residual_tolerance_m else 'supports_prior',
                        'quality': median(r['quality'] for r in members), 'residual_m': residual,
                        'measured_local_relation': relation,
                        'source_ids': sorted({s for r in members for s in r['source_ids']})})
    residuals = [s['residual_m'] for s in samples]
    center = median(residuals) if residuals else None
    mad = median(abs(x-center) for x in residuals) if residuals else None
    span = (int(frame_rows[-1]['timestamp_ns'])-int(frame_rows[0]['timestamp_ns']))/1e9 if frame_rows else 0.
    weight = sum(s['quality'] for s in samples)
    positive = sum(s['quality'] for s in samples if s['residual_m'] > policy.residual_tolerance_m)
    negative = sum(s['quality'] for s in samples if s['residual_m'] < -policy.residual_tolerance_m)
    conflict_fraction = max(positive, negative)/weight if weight else 0.
    consistent_fraction = (weight-positive-negative)/weight if weight else 0.
    reasons = []
    if len(samples) < policy.minimum_blocks or span < policy.minimum_span_s:
        reasons.append('INSUFFICIENT_TEMPORALLY_SEPARATED_LOCAL_OBSERVATIONS')
    if mad is None or mad > policy.maximum_mad_m:
        reasons.append('UNSTABLE_OR_UNAVAILABLE_FIXED_ANCHOR_RESIDUAL')
    marking = boundary['marking_type']
    if marking in ('NONE', 'UNKNOWN', None, ''):
        reasons.append('PRIOR_EXPLICITLY_UNPAINTED_REQUIRES_INSERTION_VERIFICATION' if marking == 'NONE' else 'PRIOR_MARKING_SEMANTICS_UNKNOWN')
    status = 'insufficient_geometry'
    if not reasons:
        if abs(center) > policy.residual_tolerance_m and conflict_fraction >= policy.minimum_support_fraction:
            status = 'supported_conflict'
            reasons.append('REPEATED_SIGNED_LOCAL_OFFSET_FROM_REFERENCE')
        elif abs(center) <= policy.residual_tolerance_m and consistent_fraction >= policy.minimum_support_fraction:
            status = 'consistent_with_map'
            reasons.append('OBSERVED_LOCAL_MARKING_WITHIN_ENGINEERING_TOLERANCE')
        else:
            reasons.append('CONFLICTING_LOCAL_OBSERVATIONS')
    if selected:
        representative = min(selected, key=lambda p: (abs(p[1]-(center or 0)), -p[0]['quality'], p[0]['observation_id']))[0]
    else:
        representative = max(rows, key=lambda r: (r['quality'], r['observation_id']))
    reliability_quality = (median(s['quality'] for s in samples) if samples else 0.)
    if status == 'insufficient_geometry':
        for sample in samples:
            sample['observation'] = 'unknown'
    hypothesis = _structural_hypothesis(marking, status)
    # A failed change assessment does not erase the measured geometric relation.
    # These are separate evidence dimensions, not independent probabilities.
    observation_evidence = {
        'scope': 'fixed_anchor_recorded_local_measurements_not_change_confirmation',
        'geometry_measurement': {
            'status': 'available' if samples else 'unavailable',
            'comparison_reference': 'prior_geometric_boundary_not_verified_observed_paint_identity',
            'measured_block_count': len(samples),
            'relation_counts': dict(Counter(s['measured_local_relation'] for s in samples)),
            'median_residual_m': center, 'residual_tolerance_m': policy.residual_tolerance_m},
        'temporal_support': {
            'status': 'sufficient' if len(samples) >= policy.minimum_blocks and span >= policy.minimum_span_s else 'insufficient',
            'distinct_frames': len(frame_rows), 'temporal_blocks': len(samples), 'span_s': span},
        'local_consistency': {
            'status': 'unavailable' if mad is None else 'stable' if mad <= policy.maximum_mad_m else 'unstable',
            'mad_residual_m': mad, 'same_sign_conflict_fraction': conflict_fraction,
            'within_tolerance_fraction': consistent_fraction},
        'reference_semantics': {
            'status': 'unpainted_or_logical_boundary' if marking == 'NONE' else
                      'unknown' if marking in ('UNKNOWN', None, '') else 'painted_prior_attribute',
            'marking_type': marking, 'candidate_target_property': hypothesis['target_property']},
        'paint_identity': {'status': 'unverified',
                           'reason': 'curve_detection_quality_does_not_verify_paint_vs_curb_identity'},
        'alignment': {'status': 'unverified'},
        'visibility': {'status': 'unverified', 'effective_coverage': None},
        'change_assessment': {'status': status, 'confirmed_map_change': False},
    }
    return {'event_id': 'argo-local-'+_id([rows[0]['visit_id'], boundary['boundary_id']]),
            'change_type': 'geometry_change' if status == 'supported_conflict' else 'line_discrepancy',
            'semantic_kind': 'structural_marking', 'scene_id': rows[0]['scene_id'], 'visit_id': rows[0]['visit_id'],
            'map_aliases': boundary['aliases'], 'canonical_boundary_id': boundary['boundary_id'],
            'physical_boundary_id': boundary['boundary_id'], 'physical_target_unambiguous': True,
            'map_object_identity_unambiguous': len(boundary['aliases']) == 1,
            'prior_marking_type': marking,
            'structural_hypothesis': hypothesis,
            'observation_evidence': observation_evidence,
            'geometry_city': {'type': 'LineString', 'coordinates': [p[:2] for p in representative['geometry']]},
            'representative_source_id': representative['observation_id'],
            'reliability': {'status': status, 'score': reliability_quality, 'score_is_probability': False,
                            'reason_codes': reasons, 'effective_coverage': None, 'alignment_verified': None,
                            'scope': 'observed local geometric consistency; no absence/deletion or permanence claim'},
            'samples': samples, 'observation_count': len(rows), 'anchor_observation_count': len(selected),
            'distinct_frame_count': len(frame_rows), 'temporal_block_count': len(samples), 'span_s': span,
            'fixed_anchor': {'arc_length_m': anchor[0], 'xy_m': anchor[1].tolist(),
                             'selection': 'maximum distinct-frame intersections over prior-defined anchors; offline window'},
            'median_residual_m': center, 'mad_residual_m': mad, 'same_sign_conflict_fraction': conflict_fraction,
            'local_consistency_fraction': consistent_fraction, 'base_map_write_eligible': False,
            'warnings': ['uncalibrated_engineering_model', 'projection_bias_can_mimic_map_offset',
                         'generic_curb_risk_not_object_specific_verification', 'partial_observation_not_complete_replacement_geometry']}


def evaluate_argoverse(prior, observations, frames, policy=None):
    """Return local geometric events; never read labels or classify duration."""
    policy = ReliabilityPolicy(**policy) if isinstance(policy, dict) else policy or ReliabilityPolicy()
    frame_index = {str(f['frame_id']): f for f in frames}
    if len(frame_index) != len(frames):
        raise ValueError('Duplicate frame identity')
    boundaries = canonical_boundaries(prior)
    grouped, decisions, seen = defaultdict(list), [], {}
    for o in sorted(observations, key=lambda r: (int(r['frame_id']), r['observation_id'])):
        oid = o['observation_id']
        if oid in seen:
            if o != seen[oid]:
                raise ValueError('Conflicting duplicate observation identity')
            continue
        seen[oid] = o
        fid = str(o['frame_id'])
        if fid not in frame_index or o['scene_id'] != prior['scene_id'] or frame_index[fid]['clip_id'] != o['clip_id']:
            raise ValueError('Observation/frame/prior attribution mismatch')
        geo, quality, reason = _quality(o, policy)
        d = {'observation_id': oid, 'frame_id': fid, 'quality': quality, 'status': 'insufficient_geometry',
             'reason_codes': [], 'match_candidates': []}
        if reason:
            d['reason_codes'].append(reason)
        else:
            matches = _match(geo, boundaries, policy)
            d['match_candidates'] = [{'boundary_id': bid, 'distance_m': distance, 'map_aliases': b['aliases']} for distance, bid, b in matches]
            if not matches:
                d['reason_codes'].append('NO_UNAMBIGUOUS_REFERENCE_FOR_LOCAL_RESIDUAL')
            elif len(matches) > 1 and matches[1][0]-matches[0][0] < policy.ambiguity_gap_m:
                d['reason_codes'].append('DISTINCT_PHYSICAL_REFERENCE_AMBIGUITY')
            else:
                b = matches[0][2]
                d.update(status='local_geometry_available', canonical_boundary_id=b['boundary_id'])
                grouped[(o['scene_id'], o['clip_id'], b['boundary_id'])].append({
                    'observation_id': oid, 'frame_id': fid, 'scene_id': o['scene_id'],
                    'visit_id': o['scene_id']+':'+o['clip_id'], 'quality': quality, 'geometry': geo})
        decisions.append(d)
    lookup = {b['boundary_id']: b for b in boundaries}
    events = [_event(lookup[key[2]], rows, frame_index, policy) for key, rows in sorted(grouped.items())]
    event_lookup = {(e['visit_id'], e['canonical_boundary_id']): e for e in events}
    for decision in decisions:
        if 'canonical_boundary_id' not in decision:
            continue
        original = seen[decision['observation_id']]
        event = event_lookup[(original['scene_id']+':'+original['clip_id'], decision['canonical_boundary_id'])]
        decision['event_id'] = event['event_id']
        decision['local_reliability_status'] = event['reliability']['status']
        decision['included_at_fixed_anchor'] = any(decision['observation_id'] in s['source_ids'] for s in event['samples'])
    return {'policy': asdict(policy), 'events': events, 'observation_decisions': decisions,
            'summary': {'input_observations': len(observations), 'unique_observations': len(seen),
                        'raw_boundary_references': 2*len(prior['lane_segments']), 'canonical_physical_boundaries': len(boundaries),
                        'observation_statuses': dict(Counter(d['status'] for d in decisions)),
                        'observation_reasons': dict(Counter(r for d in decisions for r in d['reason_codes'])),
                        'event_statuses': dict(Counter(e['reliability']['status'] for e in events)), 'events': len(events)},
            'diagnostics': {'gt_accessed': False, 'trained': False, 'probability_calibrated': False,
                            'visibility_unknown_blocks': ['absence', 'deletion', 'whole_feature_replacement'],
                            'policy_basis': 'engineering tolerances and temporally separated local evidence; no ground-truth tuning',
                            'scope': 'single-visit local geometry; inputs and outputs remain separate from duration and map writes'}}
