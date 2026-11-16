"""Native development check using trusted local ScenarioNet data; no real Planning."""
import argparse
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.recorded_takeover import RecordedTakeoverSession
from map_update.simulation_bridge import feature_sha256


def comparable(observation):
    result = deepcopy(observation)
    for row in result['objects'].values():
        row.pop('native_id')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database', type=Path, required=True, help='Trusted pickle database only')
    p.add_argument('--manifest', type=Path, help='Optional frozen development sample manifest')
    p.add_argument('--index', type=int, default=0)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    if args.manifest:
        manifest = json.loads(args.manifest.read_text())
        for row in manifest['development']:
            if sha256((args.database / row['file']).read_bytes()).hexdigest() != row['sha256']:
                raise ValueError('Frozen sample checksum mismatch: ' + row['file'])
    args.out.mkdir(parents=True, exist_ok=False)
    report = dict(passed=False, purpose='development_interface_check_not_Planning_evaluation',
                  sample_source='user_selected_trusted_ScenarioNet_database', checks=[], branches=[],
                  input_sha256={p.name:sha256(p.read_bytes()).hexdigest() for p in sorted(args.database.glob('*.pkl'))})
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        report['checks'].append(name)
    baseline = {}
    session = None
    try:
        session = RecordedTakeoverSession(args.database, args.index)
        report.update(scene_id=session.scenario['id'], length=session.scenario['length'],
                      dataset=session.scenario['metadata'].get('dataset'),
                      scenario_format=session.scenario['version'])
        max_error = 0.
        physical_map_hash = feature_sha256(session.scenario['map_features'])
        report['physical_map_sha256'] = physical_map_hash
        while True:
            baseline[session.step] = comparable(session.observe())
            expected = {k for k,t in session.scenario['tracks'].items()
                        if t['type'] in {'VEHICLE','PEDESTRIAN','CYCLIST'} and session._valid(k,session.step)}
            if set(baseline[session.step]['objects']) != expected:
                raise AssertionError('Valid recorded participant missing from native replay')
            max_error = max(max_error, session.replay_error())
            if session.step == session.scenario['length']-1:
                break
            session.advance({}, observation_step=session.step)
        check('all_valid_recorded_participants_present_each_frame', True)
        check('full_record_replay_position_error_below_1mm', max_error < .001)
        report['baseline_max_position_error_m'] = max_error
        try:
            session.advance({}, observation_step=session.step)
        except ValueError:
            check('record_end_stops_without_extra_tick', session.stopped == 'recording_end')
        else:
            raise AssertionError('record end allowed advance')
        session.close();session = None
        for count in (1, 2, 0):
            label = f'{count}_actor' if count else 'sdc_actor'
            session = RecordedTakeoverSession(args.database, args.index)
            for _ in range(10):
                session.advance({}, observation_step=session.step)
            before = session.observe()
            check(f'{label}_branch_reconstructs_original_start', comparable(before) == baseline[10])
            candidates = [k for k,v in before['objects'].items()
                          if v['type'] == 'VEHICLE' and k != session.sdc
                          and math.hypot(*v['velocity_m_s']) > 1
                          and all(session._valid(k, t) for t in range(10, 31))]
            check('moving_valid_vehicle_candidates_exist', len(candidates) >= count)
            keys = candidates[:count] if count else [session.sdc]
            session.takeover(keys)
            after = session.observe()
            for key in keys:
                expected = dict(before['objects'][key], mode='external')
                check(f'{label}_{key}_identity_pose_velocity_continuity', after['objects'][key] == expected)
            for batch, step in [({}, 10), ({k:[0.,-1.] for k in keys}, 9)]:
                try: session.advance(batch, observation_step=step)
                except ValueError: pass
                else: raise AssertionError('bad action batch accepted')
                check('rejected_batch_does_not_advance_clock', session.step == 10)
            try: session.return_to_replay(keys)
            except ValueError: pass
            else: raise AssertionError('live teleport back permitted')
            max_error = 0.; contacts = []
            for _ in range(20):
                observation = session.advance({k:[0.,-1.] for k in keys}, observation_step=session.step)
                max_error = max(max_error, session.replay_error())
                contacts.extend(dict(step=session.step, actor=k, contacts=v)
                                for k,v in session.contacts().items() if v)
            check(f'{label}_physical_map_unchanged', feature_sha256(session.scenario['map_features']) == physical_map_hash)
            check(f'{label}_background_stays_strict_replay', max_error < .001)
            deviation = {k:math.dist(observation['objects'][k]['position_m'],baseline[30]['objects'][k]['position_m']) for k in keys}
            check(f'{label}_external_brake_changes_trajectory', all(v > .01 for v in deviation.values()))
            report['branches'].append(dict(controlled=keys, takeover_step=10, final_step=30,
                action_source='test_only_constant_brake_not_Planning', deviation_from_log_m=deviation,
                background_max_position_error_m=max_error, contacts=contacts,
                collision_interpretation='strict replay is nonreactive; no automatic planner fault attribution'))
            session.close(); session = None
        report['passed'] = True
    finally:
        if session is not None: session.close()
        (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('passed','scene_id','checks')},ensure_ascii=False))

if __name__ == '__main__': main()
