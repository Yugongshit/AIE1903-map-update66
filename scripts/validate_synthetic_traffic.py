"""Native cyclic-traffic endurance check; no ego Planning algorithm is installed."""
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
from map_update.simulation_traffic import cyclic_traffic
from map_update.simulation_bridge import feature_sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--stop-duration', type=float, default=15.)
    parser.add_argument('--handover', action='store_true')
    args = parser.parse_args()
    if args.seconds < max(60, 45+args.stop_duration) or (args.handover and args.seconds < 120):
        parser.error('Run at least 60 seconds to include stop and recovery')
    args.out.mkdir(parents=True, exist_ok=False)
    key = 'nuscenes-scene0980'
    config = cyclic_traffic(load_scene(ROOT, key), {'seed': args.seed, 'stop_duration_s': args.stop_duration})
    session = SimulationSession(args.out / 'frames', width=480, height=320)
    report = dict(schema='synthetic-traffic-validation-1.0', passed=False, seed=args.seed,
                  seconds=args.seconds, config=config, handover=args.handover, following_brake_steps=0, off_route_steps=0, collision_events=0, max_step_distance_m=0.,
                  stop_min_speed_m_s=100., recovery_max_speed_m_s=0., checkpoints=[])
    digest = hashlib.sha256()
    try:
        session.open(scene_database(ROOT, key, 'updated'), interactive=config)
        report['physical_map_sha256'] = feature_sha256(session.env.engine.data_manager.current_scenario['map_features'])
        previous = session.interactive.report()['vehicles']
        for index in range(args.seconds*10):
            controls = {}
            if args.handover and 1000 <= index < 1030:
                controls = {'P2': {'mode': 'manual', 'action': [0., -1.]}}
            elif args.handover and index == 1030:
                controls = {'P2': {'mode': 'ai'}}
            status = session.interactive_tick(controls, capture=False)
            cars = status['interactive']['vehicles']
            for identifier, car in cars.items():
                distance = math.dist(car['position_city_m'], previous[identifier]['position_city_m'])
                report['max_step_distance_m'] = max(report['max_step_distance_m'], distance)
            t = status['interactive']['sim_time_s']
            report['following_brake_steps'] += sum(v['stopped_reason']=='nearby_actor_braking' for v in cars.values())
            report['off_route_steps'] += sum(v['stopped_reason']=='cycle_reentry_requires_forward_lane_pose' for v in cars.values())
            if 35 <= t < 30+args.stop_duration:
                report['stop_min_speed_m_s'] = min(report['stop_min_speed_m_s'], cars['P1']['speed_m_s'])
            if 35+args.stop_duration <= t <= 45+args.stop_duration:
                report['recovery_max_speed_m_s'] = max(report['recovery_max_speed_m_s'], cars['P1']['speed_m_s'])
            digest.update(json.dumps({k: [v['position_city_m'], v['speed_m_s']] for k,v in cars.items()},sort_keys=True).encode())
            if (index+1)%300 == 0:
                checkpoint = dict(time_s=t, cars={k:dict(speed_m_s=v['speed_m_s'], reason=v['stopped_reason'],
                    route=v['route'], collisions=v['collision_event_count']) for k,v in cars.items()})
                report['checkpoints'].append(checkpoint)
                print(json.dumps({'time_s':t,'cars':{k:[round(v['speed_m_s'],2),v['stopped_reason'],
                    (v.get('route') or {}).get('completed_laps')] for k,v in cars.items()}}),flush=True)
            previous = cars
        report['physical_map_unchanged'] = report['physical_map_sha256'] == feature_sha256(session.env.engine.data_manager.current_scenario['map_features'])
        report['collision_events'] = sum(v['collision_event_count'] for v in cars.values())
        report['completed_laps'] = {k:(v['route'] or {}).get('completed_laps',0) for k,v in cars.items() if k!='ego'}
        report['trace_sha256'] = digest.hexdigest()
        report['source_hashes'] = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [ROOT/'map_update/simulation_traffic.py', ROOT/'map_update/simulation_interactive.py']}
        session.set_camera({'target':'P1', 'yaw':35., 'pitch':55., 'distance':35.})
        report['passed'] = (report['physical_map_unchanged'] and report['collision_events']==0 and report['off_route_steps']==0 and report['max_step_distance_m']<1.
            and report['stop_min_speed_m_s']<.1 and report['recovery_max_speed_m_s']>1.
            and all(v['speed_m_s']>.5 for k,v in cars.items() if k!='ego')
            and (args.seconds<300 or min(report['completed_laps'].values())>=1))
    finally:
        session.close()
        (args.out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
