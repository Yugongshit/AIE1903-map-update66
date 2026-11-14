"""Native multi-site generation, individual clearance and observation evidence."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.simulation_platform_data import load_scene, scene_database
from map_update.simulation_session import SimulationSession
from map_update.simulation_traffic import cyclic_traffic, vehicle_perception
from map_update.simulation_live_map import ObservedStateMap
from map_update.simulation_bridge import feature_sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--cars', type=int, default=8)
    parser.add_argument('--sites', type=int, help='Fix count for a capacity stress test')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    options = dict(seed=args.seed, vehicle_count=args.cars, area='near_recording',
                   following_model='idm', driver_style='mixed', speed_spread=.3,
                   construction=True, stop_enabled=False)
    if args.sites:
        options.update(construction_count_min=args.sites, construction_count_max=args.sites)
    config = cyclic_traffic(load_scene(ROOT, 'nuscenes-scene0980'), options)
    session = SimulationSession(args.out/'frames', width=480, height=320)
    report = dict(passed=False, config=config['traffic'], generation=config['construction_generation'],
                  sites=config['constructions'], scope='evaluation_truth_not_planning_input')
    try:
        session.open(scene_database(ROOT, 'nuscenes-scene0980', 'updated'), interactive=config)
        events = {e.spec['object_id']: e for e in session.interactive.construction_events}
        initial = {key: list(e.obj.position[:2]) for key,e in events.items()}
        masses = {key: float(e.obj.body.getMass()) for key,e in events.items()}
        map_hash = feature_sha256(session.source_features)
        views = {key: ObservedStateMap(session.source_features, map_hash) for key in session.interactive.ids}
        seen, cleared, removed = {}, {}, {}
        braking, recovered = set(), set()
        max_step, drift = 0., 0.
        previous = session.interactive.report()['vehicles']
        trace = hashlib.sha256()
        for i in range(2400):
            status = session.interactive_tick({}, capture=False)
            traffic, step = status['interactive'], status['step']
            t = traffic['sim_time_s']
            for key,e in events.items():
                if e.obj is None:
                    removed.setdefault(key, t)
                else:
                    drift = max(drift, math.dist(initial[key], e.obj.position[:2]))
            for key,car in traffic['vehicles'].items():
                max_step = max(max_step, math.dist(car['position_city_m'],previous[key]['position_city_m']))
                blocker = (car.get('braking') or {}).get('vehicle_id')
                if blocker in events:
                    braking.add(key)
                if len(removed)==len(events) and key in braking and car['speed_m_s']>1.:
                    recovered.add(key)
                view = views[key]
                perception = vehicle_perception(traffic, key, clearance_queries=view.clearance_queries())
                assert not any(k in json.dumps(perception) for k in ('duration_s', 'construction_generation', 'construction_count_min'))
                view.update(step, t, perception)
                assert view.snapshot('baseline')['events']==[]
                for entry in view.snapshot()['events']:
                    actor = entry['actor_id']
                    if actor not in events:
                        continue
                    if entry['status']=='active':
                        seen.setdefault(actor, dict(observer=key,time_s=t))
                    elif entry['status']=='cleared':
                        assert actor in removed and t >= removed[actor]+1.9
                        cleared.setdefault(actor, dict(observer=key,time_s=t))
            trace.update(json.dumps({k:[v['position_city_m'],v['speed_m_s']] for k,v in traffic['vehicles'].items()},sort_keys=True).encode())
            previous = traffic['vehicles']
            if (i+1)%600==0:
                print(json.dumps(dict(time_s=t,sites=len(events),seen=len(seen),cleared=len(cleared))),flush=True)
        report.update(masses=masses, max_barrier_drift_m=drift, max_step_distance_m=max_step,
                      sampled_clearance_s={k:e.duration_s for k,e in events.items()},
                      first_absent_physics_frame_s=removed, final_events=[e.report() for e in events.values()],
                      first_observed=seen, first_cleared=cleared, braking_vehicles=sorted(braking),
                      recovered_vehicles=sorted(recovered), final_time_s=t,
                      collision_events=sum(v['collision_event_count'] for v in traffic['vehicles'].values()),
                      physical_map_unchanged=map_hash==feature_sha256(session.source_features),trace_sha256=trace.hexdigest())
        report['passed'] = bool(len(events)>=2 and set(events)==set(seen)==set(cleared)==set(removed)
            and all(e.duration_s<=removed[k]<=e.duration_s+.100001 for k,e in events.items())
            and all(m==0 for m in masses.values()) and drift<1e-6 and max_step<1.
            and braking and braking<=recovered and report['collision_events']==0 and report['physical_map_unchanged'])
    finally:
        session.close()
        (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
