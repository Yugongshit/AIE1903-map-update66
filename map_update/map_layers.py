"""Route change claims by what changes, never by how long they last.

This is an explicit semantic ontology, not a trained classifier or a probability
model. Base structure describes road/lane/building geometry and topology; map
state describes occupancy, traffic and operational accessibility. A long-lasting
closure remains a state claim. A brief observed geometry discrepancy is still a
structural hypothesis, whose truth must be assessed independently.

Mixed events may supply ``claims`` with separate semantic inputs. Alternatively,
``material_change`` and ``operational_status`` declare claim types, accepting a
nonempty string, a dict with ``kind``, or True. Both types in an unsplit event
require separate claims; neither is forced into the other layer. This module
never validates observed geometry, adopts a change, or authorizes a map edit.
"""


_BASE = 'base_map_structure'
_STATE = 'map_state_event'

_SEMANTIC_LAYERS = {
    'structural_marking': _BASE, 'road_structure': _BASE,
    'lane_structure': _BASE, 'building_structure': _BASE,
    'road_geometry': _BASE, 'lane_geometry': _BASE, 'building_geometry': _BASE,
    'road_topology': _BASE, 'lane_topology': _BASE,
    'movable_occupancy': _STATE, 'occupancy': _STATE,
    'traffic_congestion': _STATE, 'congestion': _STATE,
    'vehicle': _STATE, 'barrier': _STATE, 'traffic_cone': _STATE,
    'construction_closure': _STATE, 'road_closure': _STATE,
    'landslide_impact': _STATE, 'operational_status': _STATE,
}
_CHANGE_LAYERS = {
    'line_discrepancy': _BASE, 'marking_change': _BASE, 'marking_changed': _BASE,
    'geometry_change': _BASE, 'geometry_changed': _BASE,
    'road_geometry_change': _BASE, 'lane_geometry_change': _BASE,
    'building_geometry_change': _BASE, 'topology_change': _BASE,
    'road_topology_change': _BASE, 'lane_topology_change': _BASE,
    'new_road': _BASE, 'road_added': _BASE, 'road_removed': _BASE,
    'new_lane': _BASE, 'lane_added': _BASE, 'lane_removed': _BASE,
    'new_building': _BASE, 'building_added': _BASE, 'building_removed': _BASE,
    'occupancy': _STATE, 'congestion': _STATE, 'traffic_congestion': _STATE,
    'vehicle_occupancy': _STATE, 'obstacle_occupancy': _STATE,
    'road_closure': _STATE, 'lane_closure': _STATE, 'construction_closure': _STATE,
    'landslide_impact': _STATE, 'access_restriction': _STATE,
}
_STATE_CATEGORIES = {'vehicle', 'barrier', 'traffic_cone', 'trafficcone', 'pedestrian',
                     'animal', 'congestion', 'traffic_congestion', 'construction_closure',
                     'road_closure', 'landslide_impact'}
_BASE_CATEGORIES = {'road_geometry', 'lane_geometry', 'building_geometry',
                    'road_topology', 'lane_topology', 'lane_marking', 'building'}
_STATE_PREFIXES = ('vehicle.', 'movable_object.', 'human.', 'animal.')


def _text(value, field):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(field + ' must be a string')
    return value.strip().lower().replace(' ', '_')


def _category_rows(event):
    evidence = event.get('evidence', {})
    if not isinstance(evidence, dict):
        raise ValueError('evidence must be an object')
    for field, value in [('source_categories', event.get('source_categories', [])),
                         ('categories', event.get('categories', [])),
                         ('evidence.categories', evidence.get('categories', []))]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError(field + ' must be a sequence of strings')
        for category in sorted({_text(v, field) for v in value if v is not None}):
            yield field, category


def _explicit_kind(value, field):
    if value is None or value is False:
        return None
    if value is True:
        return 'unspecified_' + field
    if isinstance(value, dict):
        value = value.get('kind')
        if not isinstance(value, str) or not value.strip():
            raise ValueError(field + ' object requires a nonempty kind')
    kind = _text(value, field)
    return kind if kind not in {'', 'unknown', 'unspecified', 'none'} else None


def _reliability(event):
    reliability = event.get('reliability')
    return {'status': reliability.get('status', 'not_assessed') if isinstance(reliability, dict) else 'not_assessed',
            'assessed_by_classifier': False,
            'meaning': 'upstream evidence that the claimed change exists; separate from semantic layer routing'}


def _result(event, matches, reasons, basis, status, layers, split=False, claims=None):
    return {
        'schema': 'semantic-map-layer-classification-1.0',
        'model': 'explicit_semantic_ontology',
        'predicted_layer': layers[0] if len(layers) == 1 and status == 'routed' else 'unresolved',
        'candidate_layers': layers,
        'classification_status': status, 'classification_basis': basis,
        'matched_evidence': matches, 'reasons': reasons,
        'confidence': {'kind': 'ontology_rule_match', 'value': None, 'calibrated': False,
                       'meaning': 'an explicit semantic mapping, not a measured probability of correct classification or a verified change'},
        'parameters_learned_from_data': False,
        'classification_accuracy_measured': False,
        'observation_reliability': _reliability(event),
        'conditional_on_change': True,
        'change_adopted': False, 'base_map_write_eligible': False,
        'adoption_status': 'not_decided_by_layer_classifier',
        'requires_claim_split': split, 'mixed_layers': len(layers) > 1,
        'claims': claims or [],
    }


def classify_map_layer(event):
    """Classify one claim or explicitly separated claims without duration features.

    Recognized input fields: semantic_kind, change_type, source_categories,
    categories, evidence.categories, material_change, operational_status, claims.
    Upstream reliability.status is reported separately, never used as a layer
    label. Generic add/delete or a geometry shape alone is insufficient to infer
    the subject. Dataset names, temporal labels and GT metadata are not consumed.
    """
    if not isinstance(event, dict):
        raise ValueError('event must be an object')
    if 'claims' in event:
        claims = event['claims']
        if not isinstance(claims, list) or not claims:
            raise ValueError('claims must be a nonempty list of separately described claims')
        results, ids = [], set()
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict) or 'claims' in claim:
                raise ValueError('each claim must be an object without nested claims')
            cid = claim.get('claim_id', 'claim-' + str(index + 1))
            if not isinstance(cid, str) or not cid or cid in ids:
                raise ValueError('claim_id must be a unique nonempty string')
            ids.add(cid)
            # Parent semantic labels are not inherited: claims may describe
            # different subjects. Reliability remains explicit per claim.
            results.append({'claim_id': cid, **classify_map_layer(claim)})
        layers = sorted({layer for row in results for layer in row['candidate_layers']})
        unresolved = any(row['classification_status'] != 'routed' for row in results)
        status = 'partially_routed' if unresolved else 'split_claims_routed' if len(layers) > 1 else 'routed'
        return _result(event, [], ['EXPLICIT_CLAIMS_CLASSIFIED'] +
                       (['SOME_CLAIMS_REQUIRE_MORE_SEMANTIC_DETAIL'] if unresolved else []),
                       'explicit_separate_claims', status, layers,
                       split=any(row['requires_claim_split'] for row in results), claims=results)

    matches, ignored = [], []

    def match(field, value, layer, reason):
        matches.append({'field': field, 'value': value, 'layer': layer, 'reason': reason})

    for field, mapping in [('semantic_kind', _SEMANTIC_LAYERS), ('change_type', _CHANGE_LAYERS)]:
        value = _text(event.get(field), field)
        if value in mapping:
            match(field, value, mapping[value], 'DECLARED_SEMANTIC_SUBJECT')
        elif value and value != 'unknown':
            ignored.append(field)
    for field, value in _category_rows(event):
        if value in _STATE_CATEGORIES or value.startswith(_STATE_PREFIXES):
            match(field, value, _STATE, 'OBSERVED_OBJECT_OR_OPERATIONAL_CATEGORY')
        elif value in _BASE_CATEGORIES:
            match(field, value, _BASE, 'OBSERVED_STRUCTURAL_CATEGORY')
    for field, layer in [('material_change', _BASE), ('operational_status', _STATE)]:
        value = _explicit_kind(event.get(field), field)
        if value:
            match(field, value, layer, 'EXPLICIT_TYPED_CLAIM')
    layers = sorted({row['layer'] for row in matches})
    if not layers:
        return _result(event, matches, ['NO_RECOGNIZED_SEMANTIC_SUBJECT'] +
                       (['UNSUPPORTED_SEMANTIC_VALUE'] if ignored else []),
                       'insufficient_semantic_evidence', 'unresolved', layers)
    if len(layers) > 1:
        return _result(event, matches,
                       ['STRUCTURE_AND_OPERATIONAL_EVIDENCE_COEXIST',
                        'SEPARATE_MATERIAL_CHANGE_FROM_OPERATIONAL_IMPACT',
                        'DO_NOT_FORCE_ONE_LAYER_FOR_MIXED_OR_CONFLICTING_CLAIMS'],
                       'mixed_or_conflicting_semantic_evidence', 'split_required', layers, split=True)
    basis = 'explicit_typed_claim' if any(row['reason'] == 'EXPLICIT_TYPED_CLAIM' for row in matches) else 'semantic_ontology'
    reason = 'STRUCTURAL_HYPOTHESIS_ROUTES_TO_BASE_MAP' if layers[0] == _BASE else 'OCCUPANCY_OR_OPERATIONAL_STATUS_ROUTES_TO_STATE_LAYER'
    return _result(event, matches, [reason, 'SEMANTIC_ROUTING_DOES_NOT_VERIFY_OBSERVED_CHANGE'],
                   basis, 'routed', layers)
