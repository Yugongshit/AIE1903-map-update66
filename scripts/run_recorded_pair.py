"""Run paired planning inputs against identical source worlds and mode rules."""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.recorded_service import RecordedService
from map_update.recorded_takeover import RecordedTakeoverSession
from map_update.recorded_recovery import binding, physical_state
from map_update.planner_process import PlannerProcess
from map_update.experiment_records import summarize_journal


def run(args, label, patch):
    directory = args.out / label
    directory.mkdir()
    with (directory/'events.jsonl').open('x') as journal, (directory/'worker.log').open('x') as errors:
        session = RecordedTakeoverSession(args.database, args.index)
        worker = None
        try:
            for _ in range(args.start_step):
                session.advance({}, observation_step=session.step)
            service = RecordedService(session, journal, action_timeout=args.timeout)
            if patch is not None:
                service.map_patch(dict(binding(service), request_id='paired-map', **patch))
            service.takeover(dict(binding(service), actor_ids=args.actors))
            if args.tasks:
                tasks = json.loads(args.tasks.read_text())
                if not isinstance(tasks, dict) or set(tasks) != set(args.actors):
                    raise ValueError('Tasks must specify one goal per controlled actor')
                for actor_id, goal in tasks.items():
                    service.task_set(dict(binding(service), actor_id=actor_id, goal_m=goal,
                                          control_token=service.leases[actor_id]))
            initial = physical_state(service.snapshot())
            errors_m, implementations = [], set()
            worker = PlannerProcess(args.worker, errors, args.timeout)
            for _ in range(args.steps):
                observation = service.snapshot()
                response = worker.compute(dict(schema='recorded-worker-request-1', observation=observation,
                    map=service.read('/map'), tasks=service.read('/tasks'), controlled_actor_ids=args.actors))
                implementations.add(response['implementation'])
                if not isinstance(response['plans'], list):
                    raise ValueError('Worker plans must be a list')
                if len(response['plans']) != len(args.actors) or {p['actor_id'] for p in response['plans']} != set(args.actors):
                    raise ValueError('Worker must return one plan for each controlled actor')
                for plan in response['plans']:
                    service.plan_submit(dict(binding(service), plan=plan,
                                             control_token=service.leases[plan['actor_id']]))
                actual = service.advance(dict(binding(service), actions=response['actions'],
                    control_tokens=dict(service.leases),
                    plan_ids={p['actor_id']:p['plan_id'] for p in response['plans']}))
                for plan in response['plans']:
                    sample = next((p for p in plan['points'] if abs(p['time_offset_s']-.1)<1e-8), None)
                    if sample is not None and plan['actor_id'] in actual['objects']:
                        errors_m.append(math.dist(sample['position_m'], actual['objects'][plan['actor_id']]['position_m']))
            return dict(initial=initial, physical_map_version=service.physical_map_version,
                        planning_map_version=service.map_version, worker_implementations=sorted(implementations),
                        tracking_error_samples=len(errors_m),
                        tracking_error_mean_m=sum(errors_m)/len(errors_m) if errors_m else None,
                        report=summarize_journal(directory/'events.jsonl'))
        finally:
            if worker:
                worker.close()
            session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--index', type=int, default=0)
    parser.add_argument('--start-step', type=int, default=10)
    parser.add_argument('--steps', type=int, default=5)
    parser.add_argument('--actors', nargs='+', required=True)
    parser.add_argument('--tasks', type=Path)
    parser.add_argument('--patch', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--timeout', type=float, default=30.)
    parser.add_argument('--worker', nargs=argparse.REMAINDER, required=True)
    args = parser.parse_args()
    if args.steps <= 0 or args.start_step < 0 or not args.worker:
        parser.error('Require positive steps, nonnegative start and a worker command')
    args.out.mkdir(parents=True, exist_ok=False)
    result = dict(schema='recorded-map-pair-1', completed=False, branches={})
    try:
        patch = json.loads(args.patch.read_text())
        for label, modifications in [('original', None), ('updated', patch)]:
            result['branches'][label] = run(args, label, modifications)
        original, updated = result['branches']['original'], result['branches']['updated']
        if original['initial'] != updated['initial'] or original['physical_map_version'] != updated['physical_map_version']:
            raise ValueError('Paired initial worlds differ')
        if original['planning_map_version'] == updated['planning_map_version']:
            raise ValueError('Paired experiment did not change the planning map')
        result['completed'] = True
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        (args.out/'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(dict(completed=True, output=str(args.out/'comparison.json'))))


if __name__ == '__main__':
    main()
