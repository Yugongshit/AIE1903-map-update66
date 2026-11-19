"""Immutable planning-map revisions with explicit provenance and atomic patches."""
from copy import deepcopy

from .evidence_intake import require_identity
from .simulation_bridge import _feature, _topology_issues, feature_sha256, json_value, LANE_TYPES, LINE_TYPES


class MapVersions:
    def __init__(self, features):
        self.current = json_value(features)
        self.version = feature_sha256(self.current)
        self.revisions = [dict(version=self.version, parent=None, content_sha256=self.version,
                               operation='initial', provenance=None, changes=[])]
        self.snapshots = {self.version: deepcopy(self.current)}

    def prepare(self, operations):
        if not isinstance(operations, list) or not 1 <= len(operations) <= 1000:
            raise ValueError('Map patch requires 1 to 1000 operations')
        candidate = deepcopy(self.current)
        touched, changes = set(), []
        for operation in operations:
            if not isinstance(operation, dict):
                raise ValueError('Each map operation must be an object')
            if set(operation) - {'operation', 'feature_id', 'before_sha256', 'feature', 'source_event_id'}:
                raise ValueError('Unknown map operation fields')
            key = operation.get('feature_id')
            require_identity(key, 'feature_id')
            require_identity(operation.get('source_event_id'), 'source_event_id')
            if key in touched:
                raise ValueError('Each feature can be touched once per transaction')
            touched.add(key)
            before = candidate.get(key)
            kind = operation.get('operation')
            if kind == 'add':
                if before is not None or operation.get('before_sha256') is not None:
                    raise ValueError('Added feature must have a new identity')
            elif kind in ('replace', 'delete'):
                if before is None or operation.get('before_sha256') != feature_sha256(before):
                    raise ValueError('Feature precondition hash mismatch')
                if before.get('type') not in LINE_TYPES | LANE_TYPES:
                    raise ValueError('Unsupported source feature type')
            else:
                raise ValueError('Expected add, replace or delete')
            if kind == 'delete':
                if 'feature' in operation:
                    raise ValueError('Deleted feature cannot carry replacement geometry')
                candidate.pop(key)
                after = None
            else:
                after = json_value(_feature(operation.get('feature')))
                if before is not None and ((before['type'] in LANE_TYPES) != (after['type'] in LANE_TYPES)):
                    raise ValueError('Cannot replace a lane with a marking or vice versa')
                if before == after:
                    raise ValueError('Replacement has no change')
                candidate[key] = after
            changes.append(dict(feature_id=key, operation=kind, before=deepcopy(before), after=after,
                                source_event_id=operation['source_event_id']))
        old_issues = _topology_issues(self.current)
        issues = _topology_issues(candidate)
        affected = {issue for issue in issues if issue[0] in touched or issue[2] in touched}
        if issues - old_issues or affected:
            raise ValueError('Patch introduces or touches inconsistent lane topology')
        return candidate, changes

    def publish(self, candidate, changes, provenance, *, restored_from=None):
        digest = feature_sha256(candidate)
        version = f'map-{len(self.revisions):06d}-{digest[:16]}'
        revision = dict(version=version, parent=self.version, content_sha256=digest,
                        operation='rollback' if restored_from else 'patch',
                        restored_from=restored_from, provenance=deepcopy(provenance),
                        changes=deepcopy(changes))
        self.revisions.append(revision)
        self.current, self.version = deepcopy(candidate), version
        self.snapshots[version] = deepcopy(candidate)
        return deepcopy(revision)


def provenance(value):
    if not isinstance(value, dict) or set(value) != {'source', 'reason', 'synthetic'}:
        raise ValueError('Provenance requires source, reason and synthetic')
    require_identity(value['source'], 'source')
    require_identity(value['reason'], 'reason')
    if type(value['synthetic']) is not bool:
        raise ValueError('synthetic must be explicit boolean')
    return deepcopy(value)
