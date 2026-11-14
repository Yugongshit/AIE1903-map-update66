"""Native pedestrian-trip acceptance; private goals are never planner inputs."""
import argparse
import json
import math
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from shapely.geometry import Point
from map_update.simulation_platform_data import load_scene, scene_database
from map_update.simulation_session import SimulationSession
from map_update.simulation_traffic import cyclic_traffic, vehicle_perception


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True); p.add_argument('--seconds', type=int, default=300)
    p.add_argument('--cars', type=int, default=0); p.add_argument('--seed', type=int, default=0)
    args = p.parse_args(); args.out.mkdir(parents=True, exist_ok=False)
    config = cyclic_traffic(load_scene(ROOT, 'nuscenes-scene0980'), dict(route_mode='network', vehicle_count=args.cars,
        seed=args.seed, pedestrians=True, pedestrian_count=3, stop_enabled=False, following_model='idm', driver_style='mixed', speed_spread=.3))
    session = SimulationSession(args.out/'frames', width=480, height=320)
    result = dict(passed=False, config=config['traffic']); traces=[]
    try:
        session.open(scene_database(ROOT, 'nuscenes-scene0980', 'updated'), interactive=config)
        rt = session.interactive; walkers = rt.population.walkers
        previous = {w.identifier:w.position() for w in walkers}; maximum=0.; outside=0; separation=math.inf
        safe_area=rt.population.walking_network.surface.buffer(-.35)
        for i in range(args.seconds*10):
            status = session.interactive_tick({}, capture=False)
            for w in walkers:
                position = w.position(); maximum=max(maximum,math.dist(previous[w.identifier],position));previous[w.identifier]=position
                outside += int(not safe_area.covers(Point(position)))
            positions=[w.position() for w in walkers]
            separation=min(separation, *(math.dist(a,b) for j,a in enumerate(positions) for b in positions[j+1:]))
            if (i+1)%300 == 0:
                row=dict(time_s=(i+1)/10, walkers=[dict(w.report(),position=w.position()) for w in walkers])
                traces.append(row); print(json.dumps(row),flush=True)
                for key in rt.ids:
                    encoded=json.dumps(vehicle_perception(status['interactive'],key))
                    assert all(secret not in encoded for secret in ('completed_trips','visited_crosswalks','wait_until','recent','walking_network'))
        collisions=sum(v['collision_event_count'] for v in status['interactive']['vehicles'].values())
        result.update(trace=traces, max_step_m=maximum, outside_walkable_steps=outside, collisions=collisions, minimum_pedestrian_separation_m=separation,
            vehicles=list(rt.ids), population=rt.population.report())
        result['passed']=bool(len(walkers)==3 and maximum<.2 and outside==0 and collisions==0
            and separation>.85 and all(w.trips>=1 and w.distance>args.seconds*.4 for w in walkers)
            and all(end['travelled_m']-before['travelled_m']>10 for end,before in zip(traces[-1]['walkers'],traces[-3]['walkers'])))
    finally:
        session.close(); (args.out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return 0 if result['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
