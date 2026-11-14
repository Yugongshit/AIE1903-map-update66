"""Native expanded-district acceptance, including scale, continuity and cost."""
import argparse
import json
import math
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from shapely.geometry import Point
from map_update.simulation_platform_data import load_scene,scene_database
from map_update.simulation_traffic import cyclic_traffic,vehicle_perception
from map_update.simulation_session import SimulationSession
from map_update.simulation_bridge import feature_sha256


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--seconds',type=int,default=180)
    p.add_argument('--obstacles-min',type=int,default=6);p.add_argument('--obstacles-max',type=int,default=10)
    p.add_argument('--cars',type=int,default=24);p.add_argument('--people',type=int,default=16);p.add_argument('--seed',type=int,default=0)
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    config=cyclic_traffic(load_scene(ROOT,'nuscenes-scene0980'),dict(route_mode='network',network_extent='district',vehicle_count=args.cars,
        pedestrians=True,pedestrian_count=args.people,seed=args.seed,construction=True,construction_count_min=args.obstacles_min,construction_count_max=args.obstacles_max,
        following_model='idm',driver_style='mixed',speed_spread=.3,stop_enabled=False,background_activity=True,incidents=True,recurring_construction=True))
    session=SimulationSession(args.out/'frames',width=480,height=320);report=dict(passed=False,config=config['traffic'],coverage=config['traffic_area']);trace=[]
    try:
        session.open(scene_database(ROOT,'nuscenes-scene0980'),interactive=config)
        rt=session.interactive;map_hash=feature_sha256(session.source_features)
        safe=rt.population.walking_network.surface.buffer(-.35);outside=0;max_step=0;minimum=math.inf
        previous={a['vehicle_id']:a['position_city_m'] for a in rt.actors()};began=time.perf_counter()
        for tick in range(args.seconds*10):
            status=session.interactive_tick({},capture=False);physics=status['interactive']
            actors=rt.actors();positions=[]
            for a in actors:
                key=a['vehicle_id'];pos=a['position_city_m']
                if key in previous:max_step=max(max_step,math.dist(previous[key],pos))
                if a.get('type')=='PEDESTRIAN':
                    outside+=int(not safe.covers(Point(pos)));positions.append(pos)
            if len(positions)>1:minimum=min(minimum,*(math.dist(a,b) for i,a in enumerate(positions) for b in positions[i+1:]))
            previous={a['vehicle_id']:a['position_city_m'] for a in actors}
            if (tick+1)%300==0:
                cars=physics['vehicles'];row=dict(time_s=(tick+1)/10,wall_s=round(time.perf_counter()-began,2),population=physics['population'],
                    minimum_car_distance_m=min(v.get('route',{}).get('travelled_m',0) for k,v in cars.items() if k!='ego'),
                    collisions=sum(v['collision_event_count'] for v in cars.values()),
                    contacts={k:v['last_collision'] for k,v in cars.items() if v['last_collision']},
                    stopped={k:v['stopped_reason'] for k,v in cars.items() if k!='ego' and v['stopped_reason']},
                    junctions=physics['junction_control'],walker_status={w.identifier:w.phase for w in rt.population.walkers})
                trace.append(row);print(json.dumps(row),flush=True)
                encoded=json.dumps(vehicle_perception(physics,'ego'))
                assert all(x not in encoded for x in ('stop_at','speed_at','wait_until','walk_style','brief_stops','background_activity'))
        report.update(trace=trace,wall_s=time.perf_counter()-began,max_step_m=max_step,minimum_pedestrian_separation_m=minimum,outside_walkable_steps=outside,
            spawned_vehicles=len(rt.ids)-1,spawned_pedestrians=len(rt.population.walkers),initial_construction=config['construction_generation'],
            population=rt.population.report(),car_distances_m={k:v['route']['travelled_m'] for k,v in physics['vehicles'].items() if k!='ego'},
            pedestrian_distances_m={w.identifier:w.distance for w in rt.population.walkers},
            pedestrian_results=[w.report() for w in rt.population.walkers],
            physical_map_unchanged=map_hash==feature_sha256(session.source_features))
        report['passed']=bool(report['spawned_vehicles']==args.cars and report['spawned_pedestrians']==args.people
            and trace[-1]['collisions']==0 and outside==0 and max_step<1. and minimum>.85 and report['physical_map_unchanged']
            and min(report['car_distances_m'].values())>5 and sum(x>10 for x in report['pedestrian_distances_m'].values())>=args.people*.8
            and report['population']['brief_stops_completed']>0)
    finally:
        session.close();(args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
