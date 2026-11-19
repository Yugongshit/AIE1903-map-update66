"""Reconstruct a recorded run by verified command replay into a new session."""
from copy import deepcopy

from .experiment_records import read_journal


def binding(service):
    current = service.snapshot()
    return {key:current[key] for key in ('session_id', 'map_version', 'state_version')} | {
        'observation_step': current['step']}


def physical_state(observation):
    return dict(step=observation['step'], signals=observation['signals'],
                objects={key:{field:value for field,value in row.items() if field != 'native_id'}
                         for key,row in observation['objects'].items()})


def verify_state(actual, expected):
    # Repeatability is checked at every recorded boundary; failure stops recovery.
    if actual.get('scene_id') != expected.get('scene_id') or actual.get('physical_map_version') != expected.get('physical_map_version'):
        raise ValueError('Recovery scene or physical map differs from the recorded world')
    if physical_state(actual) != physical_state(expected):
        raise ValueError('Native state differs from the recorded result; recovery stopped')


def latest_run(path):
    records = read_journal(path)
    starts = [index for index,row in enumerate(records) if row['event'] == 'open']
    if not starts:
        raise ValueError('Journal has no session')
    run = records[starts[-1]:]
    source = next((row['data'] for row in run if row['event'] == 'world_source'), None)
    if source is None or source['database'] is None:
        raise ValueError('Journal lacks a replayable world source')
    return run, source


def recover_commands(service, records):
    source = next((row['data'] for row in records if row['event']=='world_source'), {})
    if source.get('source_sha256') != getattr(service.session, 'source_sha256', None):
        raise ValueError('Source recording checksum differs from the journal')
    verify_state(service.snapshot(), records[0]['data'])
    pending = {}
    accepted = {'takeover': 'takeover_intent', 'advance': 'advance_intent',
                'evidence_accepted': 'evidence_intent', 'map_published': 'map_intent',
                'resumed': 'resume_intent'}
    for row in records[1:]:
        kind, data = row['event'], row['data']
        if kind.endswith('_intent'):
            pending[kind] = data
        elif kind in accepted:
            intent = pending.pop(accepted[kind], None)
            if intent is None:
                raise ValueError('Result lacks its preceding command intent')
            if kind == 'takeover':
                service.takeover(dict(binding(service), actor_ids=intent['actor_ids'],
                                      observation_scope=intent.get('observation_scope', {'kind':'global'}),
                                      coverage_policy=intent.get('coverage_policy', 'map_envelope')))
                expected = data
            elif kind == 'advance':
                service.advance(dict(binding(service), actions=intent['actions'],
                                     control_tokens=dict(service.leases)))
                expected = data
            elif kind == 'evidence_accepted':
                body = deepcopy(intent)
                body.update(binding(service))
                service.evidence_update(body)
                expected = data['observation']
            elif kind == 'map_published':
                body = deepcopy(intent['body'])
                body.update(binding(service))
                getattr(service, intent['command'])(body)
                expected = data['observation']
            else:
                expected = data
            verify_state(service.snapshot(), expected)
        elif kind == 'structure_evidence_accepted':
            body = deepcopy(data)
            body.update(binding(service))
            service.structure_evidence(body)
        elif kind == 'plan_accepted':
            plan = data['plan']
            service.plan_submit(dict(binding(service), plan=plan,
                                     control_token=service.leases[plan['actor_id']]))
        elif kind == 'task_set':
            service.task_set(dict(binding(service), actor_id=data['actor_id'],
                                  goal_m=data['task']['goal_m'],
                                  control_token=service.leases[data['actor_id']]))
    if pending:
        raise ValueError('Journal contains unresolved command intents; exact recovery is unavailable')
    service.pending.clear()
    service.deadline = service.clock() + service.timeout if service.leases else None
    service._record('recovered', dict(source_session_id=records[0]['data']['session_id'],
                                     observation=service.snapshot()))
    return service
