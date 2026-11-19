"""Connect a trusted trajectory/controller worker to the synchronous HTTP service."""
import argparse
import json
import os
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.planner_process import PlannerProcess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8094')
    parser.add_argument('--actors', nargs='+', required=True)
    parser.add_argument('--tasks', type=Path, help='JSON mapping each controlled actor to a goal XY')
    parser.add_argument('--steps', type=int, default=10)
    parser.add_argument('--timeout', type=float, default=10.)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--worker', nargs=argparse.REMAINDER, required=True)
    args = parser.parse_args()
    key = os.environ.get('RECORDED_SERVICE_KEY')
    if not key or not args.worker or args.steps <= 0:
        parser.error('Require RECORDED_SERVICE_KEY, a worker command and positive steps')
    args.out.mkdir(parents=True, exist_ok=False)

    def call(path, body=None):
        request = urllib.request.Request(args.url.rstrip('/')+path,
            data=None if body is None else json.dumps(body).encode(),
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            return json.load(response)

    def bound(observation):
        return dict(session_id=observation['session_id'], map_version=observation['map_version'],
                    state_version=observation['state_version'], observation_step=observation['step'])

    worker = None
    result = dict(completed=False, steps=0, implementations=[])
    try:
        observation = call('/observation')
        claim = call('/takeover', dict(bound(observation), actor_ids=args.actors,
                                      observation_scope={'kind':'global'}))
        tokens = claim['control_tokens']
        if args.tasks:
            tasks = json.loads(args.tasks.read_text())
            if not isinstance(tasks, dict) or set(tasks) != set(args.actors):
                raise ValueError('Tasks must specify one goal per controlled actor')
            for actor_id, goal in tasks.items():
                call('/tasks', dict(bound(call('/observation')), actor_id=actor_id, goal_m=goal,
                                    control_token=tokens[actor_id]))
        with (args.out/'worker.log').open('x') as errors:
            worker = PlannerProcess(args.worker, errors, args.timeout)
            for _ in range(args.steps):
                observation = call('/observation')
                response = worker.compute(dict(schema='recorded-worker-request-1',
                    observation=observation, map=call('/map'), tasks=call('/tasks'), controlled_actor_ids=args.actors))
                if not isinstance(response['plans'], list) or len(response['plans']) != len(args.actors):
                    raise ValueError('Worker must return exactly one plan per actor')
                if {p['actor_id'] for p in response['plans']} != set(args.actors):
                    raise ValueError('Worker plan actor set mismatch')
                for plan in response['plans']:
                    call('/plans', dict(bound(observation), plan=plan, control_token=tokens[plan['actor_id']]))
                call('/advance', dict(bound(observation), actions=response['actions'], control_tokens=tokens,
                                      plan_ids={p['actor_id']:p['plan_id'] for p in response['plans']}))
                result['steps'] += 1
                if response['implementation'] not in result['implementations']:
                    result['implementations'].append(response['implementation'])
        result['completed'] = True
    except Exception as error:
        result['error'] = str(error)
        raise
    finally:
        if worker:
            worker.close()
        (args.out/'client-result.json').write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
