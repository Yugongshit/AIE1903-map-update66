"""Native stage-two traffic acceptance: motion, events, perception and recovery."""
import argparse
import json
import math
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from map_update.simulation_platform_data import load_scene,scene_database
from map_update.simulation_session import SimulationSession
from map_update.simulation_traffic import cyclic_traffic,vehicle_perception
from map_update.simulation_live_map import ObservedStateMap
from map_update.simulation_bridge import feature_sha256


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--seconds',type=int,default=600)
    p.add_argument('--seed',type=int,default=0);p.add_argument('--cars',type=int,default=8)
    p.add_argument('--network-only',action='store_true')
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    options=dict(route_mode='network',seed=args.seed,vehicle_count=args.cars,following_model='idm',driver_style='mixed',speed_spread=.3,
        stop_enabled=False,construction=not args.network_only,pedestrians=not args.network_only,
        incidents=not args.network_only,recurring_construction=not args.network_only,construction_count_min=2,construction_count_max=3)
    config=cyclic_traffic(load_scene(ROOT,'nuscenes-scene0980'),options)
    report=dict(passed=False,config=config['traffic'],route_lengths=[r['length'] for r in config['routes']],junctions=config['junctions'])
    session=SimulationSession(args.out/'frames',width=480,height=320)
    try:
        session.open(scene_database(ROOT,'nuscenes-scene0980','updated'),interactive=config)
        initial_map=feature_sha256(session.source_features)
        previous={a['vehicle_id']:a for a in session.interactive.actors()}
        views={key:ObservedStateMap(session.source_features,initial_map) for key in session.interactive.ids}
        seen=set();active=set();cleared=set();max_step=0.;ped_step=0.
        event_spawns={};trace=[]
        for i in range(args.seconds*10):
            status=session.interactive_tick({},capture=False);traffic=status['interactive'];t=traffic['sim_time_s']
            allactors={a['vehicle_id']:a for a in session.interactive.actors()}
            for key,a in allactors.items():
                if key in previous:
                    delta=math.dist(a['position_city_m'],previous[key]['position_city_m'])
                    if key.startswith('walker-'):ped_step=max(ped_step,delta)
                    elif key in traffic['vehicles']:max_step=max(max_step,delta)
                elif key.startswith('construction-R'):
                    distance=min(math.dist(a['position_city_m'],v['position_city_m']) for v in traffic['vehicles'].values())
                    event_spawns[key]=dict(time_s=t,minimum_observer_distance_m=distance)
            for key,view in views.items():
                perception=vehicle_perception(traffic,key,clearance_queries=view.clearance_queries())
                assert not any(k in json.dumps(perception) for k in ('until','next_spawn','junction_control','completed_crossings','construction_generation'))
                seen.update(a['vehicle_id'] for a in perception['current_objects'] if a.get('synthetic'))
                view.update(status['step'],t,perception)
                for e in view.snapshot()['events']:
                    if e['status']=='active':active.add(e['actor_id'])
                    if e['status']=='cleared':cleared.add(e['actor_id'])
            previous=allactors
            if (i+1)%300==0:
                row=dict(time_s=t,population=traffic['population'],junctions=traffic['junction_control'],
                    distances={k:round(v.get('route',{}).get('travelled_m',0),1) for k,v in traffic['vehicles'].items() if v.get('route')},
                    reasons={k:v['stopped_reason'] for k,v in traffic['vehicles'].items()},
                    collisions=sum(v['collision_event_count'] for v in traffic['vehicles'].values()))
                trace.append(row);print(json.dumps(row),flush=True)
        collisions=sum(v['collision_event_count'] for v in traffic['vehicles'].values())
        travelled={k:v.get('route',{}).get('travelled_m',0) for k,v in traffic['vehicles'].items() if v.get('route')}
        population=traffic['population'] or {}
        report.update(final_time_s=t,collision_events=collisions,max_car_step_m=max_step,max_pedestrian_step_m=ped_step,
            travelled_m=travelled,final_population=population,junction_control=traffic['junction_control'],trace=trace,
            observed_synthetic=sorted(seen),observed_active=sorted(active),observed_cleared=sorted(cleared),
            recurring_spawns=event_spawns,physical_map_unchanged=initial_map==feature_sha256(session.source_features),
            final_events=traffic['synthetic_events'])
        report['passed']=bool(collisions==0 and max_step<1. and ped_step<.5 and report['physical_map_unchanged']
            and all(v>args.seconds*.2 for v in travelled.values()) and traffic['junction_control']['passes']>0
            and (args.network_only or (population['pedestrian_spawned']>0 and population['completed_crossings']>0
                and population['incident_completed']>0 and population['recurring_spawned']>0 and cleared
                and all(e['minimum_observer_distance_m']>=54.6 for e in event_spawns.values()))))
    finally:
        session.close();(args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
