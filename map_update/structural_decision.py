"""Close map-update decisions from the supplied recording, without revisits.

Local curve residuals remain diagnostics. A material structural change needs a
resolved physical target and edit operation, then either a supported discrete
road property change or sustained spatial displacement of the same feature.
These engineering tolerances are not fitted to dataset names or change labels.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass
from math import hypot, isfinite

from .geometry import point_segment_distance, validate_geometry


@dataclass(frozen=True)
class MaterialityPolicy:
    distance_tolerance_m: float = .5
    minimum_contiguous_length_m: float = 3.
    sample_spacing_m: float = .5

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
                raise ValueError('Invalid materiality policy: ' + name)
        if self.sample_spacing_m > self.minimum_contiguous_length_m:
            raise ValueError('Sampling spacing exceeds the required affected length')


def _policy(value):
    return MaterialityPolicy(**value) if isinstance(value, dict) else value or MaterialityPolicy()


def _line(geometry):
    if isinstance(geometry, (list, tuple)):
        geometry = {'type': 'LineString', 'coordinates': [list(p) for p in geometry]}
    points = validate_geometry(geometry, line_only=True)['coordinates']
    points = [p for i, p in enumerate(points) if i == 0 or p != points[i-1]]
    if len(points) < 2:
        raise ValueError('No nonzero line segment')
    return points


def _sample_line(points, spacing):
    lengths = [hypot(b[0]-a[0], b[1]-a[1]) for a, b in zip(points, points[1:])]
    arc = [0.]
    for length in lengths:
        arc.append(arc[-1] + length)
    positions = sorted(set(arc + [i * spacing for i in range(int(arc[-1] / spacing) + 1)]))
    sampled, segment = [], 0
    for position in positions:
        while segment < len(lengths)-1 and position > arc[segment+1]:
            segment += 1
        fraction = (position - arc[segment]) / lengths[segment]
        a, b = points[segment], points[segment+1]
        sampled.append([a[k] + fraction * (b[k]-a[k]) for k in (0, 1)])
    return positions, sampled


def _directed_profile(source, reference, policy):
    arc, points = _sample_line(source, policy.sample_spacing_m)
    distances = []

    def measure(point):
        distance = min(point_segment_distance(point, a, b) for a, b in zip(reference, reference[1:]))
        distances.append(distance)
        return distance

    tolerance = policy.distance_tolerance_m + 1e-9
    sampled = [measure(point) for point in points]
    intervals = []

    def certify(a, b, da, db, length, depth=0):
        # Distance to a polyline is 1-Lipschitz along each source segment.
        # Merely testing endpoints aliases intervening dips in the reference.
        # This lower bound proves EVERY point in the interval exceeds tolerance.
        if (da + db - length) / 2 > tolerance:
            intervals.append(length)
        elif (da + db + length) / 2 <= tolerance:
            intervals.append(0.)
        elif depth >= 8:
            # Uncertified submillimetre-to-2mm transition intervals are excluded,
            # never bridged into a spurious long run. This is conservative.
            intervals.append(0.)
        else:
            middle = [(a[k]+b[k])/2 for k in (0, 1)]
            dm = measure(middle)
            certify(a, middle, da, dm, length/2, depth+1)
            certify(middle, b, dm, db, length/2, depth+1)

    for i in range(len(arc)-1):
        certify(points[i], points[i+1], sampled[i], sampled[i+1], arc[i+1]-arc[i])
    runs, run = [], 0.
    for length in intervals:
        if length:
            run += length
        elif run:
            runs.append(run)
            run = 0.
    if run:
        runs.append(run)
    # A closed crossing perimeter has no privileged start vertex.
    if source[0] == source[-1] and len(runs) > 1 and intervals[0] and intervals[-1]:
        runs[0] += runs.pop()
    return {'total_length_m': arc[-1], 'sample_count': len(distances),
            'displaced_length_m': sum(intervals),
            'max_contiguous_displaced_length_m': max(runs, default=0.),
            'maximum_offset_m': max(distances, default=0.),
            'minimum_offset_m': min(distances, default=0.),
            'interval_certification': 'one_lipschitz_lower_bound_with_adaptive_subdivision',
            'maximum_unresolved_interval_m': policy.sample_spacing_m / 256}


def geometry_materiality(prior_geometry, proposed_geometry, policy=None):
    """Measure spatial extent, rather than promoting one residual to structure.

Both directions are sampled at <= 0.5 m by default, retaining line vertices.
Adaptive subdivision and a distance-function Lipschitz bound certify the whole
affected interval exceeds tolerance; unresolved tiny intervals never bridge runs.
One large isolated sample never supplies the required length. Either direction may
show a sustained change (for example a supported extension of the same line).
The caller must establish identity, operation and complete comparable geometry.
"""
    policy = _policy(policy)
    try:
        prior, proposed = _line(prior_geometry), _line(proposed_geometry)
    except (ValueError, TypeError, KeyError, IndexError):
        return {'material': False, 'reason_codes': ['COMPARABLE_GEOMETRY_UNAVAILABLE'],
                'metrics': None, 'policy': asdict(policy)}
    forward = _directed_profile(prior, proposed, policy)
    reverse = _directed_profile(proposed, prior, policy)
    longest = max(forward['max_contiguous_displaced_length_m'], reverse['max_contiguous_displaced_length_m'])
    maximum = max(forward['maximum_offset_m'], reverse['maximum_offset_m'])
    material = longest + 1e-9 >= policy.minimum_contiguous_length_m
    reason = ('SUSTAINED_SAME_TARGET_GEOMETRY_CHANGE' if material else
              'GEOMETRY_WITHIN_MATERIAL_DISTANCE_TOLERANCE' if maximum <= policy.distance_tolerance_m + 1e-9 else
              'SPATIAL_EXTENT_BELOW_MATERIAL_LENGTH')
    return {'material': material, 'reason_codes': [reason],
            'metrics': {'max_contiguous_displaced_length_m': longest, 'maximum_offset_m': maximum,
                        'prior_to_proposed': forward, 'proposed_to_prior': reverse},
            'policy': asdict(policy), 'method': 'bidirectional_sampled_point_to_segment_spatial_extent',
            'calibrated_probability': False, 'requires_verified_same_target': True}


_GEOMETRY_OPERATIONS = {'geometry_change', 'geometry_changed', 'road_geometry_change',
                        'lane_geometry_change', 'building_geometry_change'}
_ADDITION_OPERATIONS = {'add', 'new_lane', 'lane_added', 'new_road', 'road_added', 'building_added', 'new_building'}
_DELETION_OPERATIONS = {'delete', 'lane_removed', 'road_removed', 'building_removed'}
_TOPOLOGY_OPERATIONS = {'topology_change', 'lane_topology_change', 'road_topology_change', 'connectivity_changed'}
_LANE_USE_OPERATIONS = {'lane_use_change', 'lane_type_changed', 'lane_use_changed'}
_MARKING_OPERATIONS = {'marking_change', 'marking_changed', 'regulated_marking_change'}


def _canonical_operation(operation):
    for name, aliases in [('geometry_change', _GEOMETRY_OPERATIONS), ('add', _ADDITION_OPERATIONS),
                          ('delete', _DELETION_OPERATIONS), ('marking_change', _MARKING_OPERATIONS),
                          ('topology_change', _TOPOLOGY_OPERATIONS), ('lane_use_change', _LANE_USE_OPERATIONS)]:
        if operation in aliases:
            return name
    return operation


def final_structural_decision(event, layer, edit_gate, policy=None):
    """Return a completed action for this recording, never an open review queue.

``no_update`` is a map-maintenance action, not a ground-truth negative label.
An ``update`` action recommends an eligible structural patch; execution remains
separate and is never claimed here. Trusted reference replay is a distinct path.
"""
    policy = _policy(policy)
    layer_name = layer.get('predicted_layer')
    structural_scope = (layer_name == 'base_map_structure'
                        or layer.get('conditional_layer_prediction') == 'base_map_structure'
                        or 'base_map_structure' in layer.get('candidate_layers', []))
    result = {'schema': 'closed-structural-decision-1.0', 'processing_status': 'complete',
              'action': 'no_update', 'material_change_supported': False,
              'reason_codes': [], 'revisit_required': False, 'manual_review_required': False,
              'evidence_scope': 'supplied_recording', 'decision_is_ground_truth': False,
              'structural_scope': structural_scope, 'map_patch_executed': False,
              'local_discrepancy_detected': event.get('model_change_decision', {}).get('detected', False),
              'edit_contract_passed': bool(edit_gate.get('eligible')),
              'policy': asdict(policy), 'parameters_tuned_on_ground_truth': False}
    reliability = event.get('reliability', {})
    if layer_name == 'map_state_event':
        result['action'] = 'state_event' if reliability.get('status') == 'supported_conflict' else 'no_update'
        result['reason_codes'] = ['OBSERVED_STATE_EVENT_SEPARATE_FROM_ROAD_STRUCTURE'] if result['action'] == 'state_event' else ['STATE_EVENT_NOT_SUPPORTED_BY_SUPPLIED_RECORD']
        return result
    if not structural_scope:
        result['reason_codes'] = ['NO_STRUCTURAL_CLAIM_IN_SUPPLIED_RECORD']
        return result

    hypothesis = event.get('structural_hypothesis', {})
    operation = _canonical_operation(hypothesis.get('operation', event.get('change_type')))
    gate_operation = _canonical_operation(edit_gate.get('operation', event.get('change_type')))
    operation_matches_contract = operation == gate_operation
    resolved = event.get('operation_resolved') is True or hypothesis.get('operation_resolved') is True
    # Geometry-reference ambiguity and observed-object identity are different.
    # A nearest parallel map line cannot prove an observation is that old line.
    identity = (event.get('target_identity_verified') is True
                or event.get('physical_target_verified') is True
                or event.get('map_object_identity_verified') is True)
    if operation in _ADDITION_OPERATIONS:
        identity = event.get('novelty_verified') is True and bool(event.get('new_element_id'))
    reasons = result['reason_codes']
    if reliability.get('status') != 'supported_conflict':
        reasons.append('NO_SUPPORTED_STRUCTURAL_CONFLICT_IN_SUPPLIED_RECORD')
    if not identity:
        reasons.append('STRUCTURAL_TARGET_IDENTITY_NOT_ESTABLISHED')
    if not resolved:
        reasons.append('STRUCTURAL_EDIT_OPERATION_NOT_ESTABLISHED')
    if not operation_matches_contract:
        reasons.append('STRUCTURAL_OPERATION_DIFFERS_FROM_WRITE_CONTRACT')
    reference_remains = bool(event.get('same_window_reference_observations'))
    prior_presence_contradicts_operation = reference_remains and operation in (_GEOMETRY_OPERATIONS | _DELETION_OPERATIONS)
    if prior_presence_contradicts_operation:
        reasons.append('PRIOR_REFERENCE_STILL_OBSERVED_NO_MOVEMENT_ESTABLISHED')
    elif reference_remains and operation not in (_ADDITION_OPERATIONS | _TOPOLOGY_OPERATIONS | _LANE_USE_OPERATIONS | _MARKING_OPERATIONS):
        reasons.append('PRIOR_REFERENCE_STILL_OBSERVED_LOCAL_DIFFERENCE_NOT_RESOLVED_AS_ADDITION')

    material, analysis = False, None
    if operation in _GEOMETRY_OPERATIONS:
        analysis = geometry_materiality(event.get('prior_geometry_city'),
                                       event.get('proposed_geometry_city', event.get('geometry_city')), policy)
        reasons += analysis['reason_codes']
        complete = event.get('replacement_geometry_verified') is True
        if not complete:
            reasons.append('LOCAL_CURVE_IS_NOT_COMPLETE_SAME_TARGET_GEOMETRY')
        material = analysis['material'] and complete
    elif operation in _ADDITION_OPERATIONS:
        material = event.get('novelty_verified') is True and event.get('replacement_geometry_verified') is True
        reasons.append('VERIFIED_NEW_STRUCTURAL_ELEMENT' if material else 'NEW_STRUCTURAL_ELEMENT_NOT_ESTABLISHED')
    elif operation in _DELETION_OPERATIONS:
        material = (event.get('effective_visibility_verified') is True
                    and edit_gate.get('fully_observed_delete_frames', 0) > 0)
        reasons.append('VERIFIED_STRUCTURAL_ELEMENT_REMOVAL' if material else 'VISIBLE_STRUCTURAL_ABSENCE_NOT_ESTABLISHED')
    elif operation in _TOPOLOGY_OPERATIONS | _LANE_USE_OPERATIONS:
        property_key = 'topology_change_verified' if operation in _TOPOLOGY_OPERATIONS else 'lane_use_change_verified'
        material = event.get(property_key) is True
        reasons.append('VERIFIED_DISCRETE_ROAD_PROPERTY_CHANGE' if material else 'DISCRETE_ROAD_PROPERTY_CHANGE_NOT_ESTABLISHED')
    elif operation in _MARKING_OPERATIONS:
        # A regulated marking attribute changes road meaning even with zero shift.
        material = (event.get('marking_property_verified') is True
                    and str(event.get('marking_type', '')).upper() not in {'', 'UNKNOWN', 'NONE'}
                    and event.get('marking_type') is not None
                    and str(event.get('prior_marking_type', '')).upper() not in {'', 'UNKNOWN'}
                    and event.get('prior_marking_type') is not None
                    and event.get('marking_type') != event.get('prior_marking_type'))
        reasons.append('VERIFIED_MARKING_PROPERTY_CHANGE' if material else 'REGULATED_MARKING_CHANGE_NOT_ESTABLISHED')
    else:
        reasons.append('LOCAL_CURVE_DIFFERENCE_DOES_NOT_ESTABLISH_ROAD_STRUCTURE_CHANGE')

    supported = (material and identity and resolved and operation_matches_contract and reliability.get('status') == 'supported_conflict'
                 and not prior_presence_contradicts_operation)
    result.update(material_change_supported=bool(supported), operation=operation,
                  target_identity_verified=identity, operation_resolved=resolved,
                  geometry_materiality=deepcopy(analysis))
    if supported and edit_gate.get('eligible'):
        result['action'] = 'update'
        reasons.append('MATERIAL_STRUCTURAL_EDIT_SUPPORTED_BY_SUPPLIED_RECORD')
    else:
        reasons.append('CLOSED_WITHOUT_STRUCTURAL_UPDATE')
        if supported:
            reasons.append('EDIT_CONTRACT_NOT_SATISFIED_BY_SUPPLIED_RECORD')
    result['reason_codes'] = list(dict.fromkeys(reasons))
    return result
