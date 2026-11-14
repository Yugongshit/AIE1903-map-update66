"""Real physics -> scoped measurements -> versioned occupancy; no user planner."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from map_update.simulation_platform_data import load_scene, scene_database
from map_update.simulation_session import SimulationSession
from map_update.simulation_traffic import cyclic_traffic, vehicle_perception
from map_update.simulation_live_map import ObservedStateMap
from map_update.simulation_bridge import feature_sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--seconds',type=int,default=120)
    parser.add_argument('--count',type=int,default=8)
    args=parser.parse_args()
    if args.seconds<120:parser.error('At least 120 seconds required for stop/recovery')
    args.out.mkdir(parents=True,exist_ok=False)
    key='nuscenes-scene0980';payload=load_scene(ROOT,key)
    config=cyclic_traffic(payload,dict(vehicle_count=args.count,speed_spread=.3,stop_duration_s=60))
    session=SimulationSession(args.out/'frames',width=480,height=320)
    report=dict(passed=False,configuration=config['traffic'],transitions=[],seconds=args.seconds)
    try:
        session.open(scene_database(ROOT,key,'updated'),interactive=config)
        roads=session.env.engine.data_manager.current_scenario['map_features']
        physical_hash=feature_sha256(roads)
        views={identifier:ObservedStateMap(session.source_features,physical_hash) for identifier in session.interactive.ids}
        seen={}; observers=set(); cleared=set(); first_stationary=None; first_publish=None
        for i in range(args.seconds*10):
            status=session.interactive_tick({},capture=False)
            traffic=status['interactive'];step=status['step'];t=traffic['sim_time_s']
            if t>=30 and traffic['vehicles']['P1']['speed_m_s']<=.25 and first_stationary is None:first_stationary=t
            for identifier,view in views.items():
                view.update(step,t,vehicle_perception(traffic,identifier))
                assert view.snapshot('baseline')['events']==[]
                layer=view.snapshot()
                for event in layer['events']:
                    identity=(identifier,event['actor_id']);state=event['status']
                    if seen.get(identity)!=state:
                        report['transitions'].append(dict(observer=identifier,actor=event['actor_id'],status=state,time_s=t,step=step))
                        seen[identity]=state
                    if event['actor_id']=='P1' and state=='active':
                        observers.add(identifier)
                        if first_publish is None:first_publish=t
                    if event['actor_id']=='P1' and state=='cleared':cleared.add(identifier)
            if (i+1)%300==0:print(json.dumps({'time_s':t,'observers_of_P1':sorted(observers),'observed_cleared':sorted(cleared)}),flush=True)
        report.update(physical_map_unchanged=physical_hash==feature_sha256(session.env.engine.data_manager.current_scenario['map_features']),
            physical_map_sha256=physical_hash,stationary_at_s=first_stationary,first_published_s=first_publish,
            observers_of_P1=sorted(observers),observers_confirming_departure=sorted(cleared),
            ego_events=views['ego'].snapshot()['events'],collision_events=sum(v['collision_event_count'] for v in traffic['vehicles'].values()),
            final_versions={k:v.snapshot()['state_revision'] for k,v in views.items()},
            scope='Transport and example client validated separately; no actual Planning algorithm connected')
        report['passed']=bool(observers and cleared and first_publish>=first_stationary+2.-1e-6
            and not report['ego_events'] and report['physical_map_unchanged'] and report['collision_events']==0)
    finally:
        session.close();(args.out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
