"""Native construction -> scoped observations -> map evidence -> clearance."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from map_update.simulation_platform_data import load_scene, scene_database
from map_update.simulation_session import SimulationSession
from map_update.simulation_traffic import cyclic_traffic, vehicle_perception
from map_update.simulation_live_map import ObservedStateMap
from map_update.simulation_bridge import feature_sha256
from map_update.simulation_events import sample_clearance_time
from map_update.simulation_planner import _project, _length


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--seed',type=int,default=0)
    parser.add_argument('--count',type=int,default=8)
    parser.add_argument('--collision-probe',action='store_true')
    parser.add_argument('--incident-response',action='store_true',help='Verify physical collision disables and repairs the driven vehicle')
    args=parser.parse_args()
    if args.incident_response and not args.collision_probe:parser.error('--incident-response requires --collision-probe')
    args.out.mkdir(parents=True,exist_ok=False)
    key='nuscenes-scene0980';payload=load_scene(ROOT,key)
    config=cyclic_traffic(payload,dict(area='near_recording',following_model='idm',driver_style='mixed',
        speed_spread=.3,vehicle_count=args.count,seed=args.seed,incidents=args.incident_response,construction=True,construction_count_min=1,construction_count_max=1,construction_min_s=45.,construction_max_s=120.,stop_enabled=False))
    session=SimulationSession(args.out/'frames',width=480,height=320)
    sampled=sample_clearance_time(config['traffic'])
    report=dict(passed=False,config=config['traffic'],collision_probe=args.collision_probe,transitions=[],
        sampled_clearance_s=sampled, sampling_scope='evaluation_only_not_planning_input')
    trace=hashlib.sha256()
    try:
        session.open(scene_database(ROOT,key,'updated'),interactive=config)
        event=session.interactive.construction_event
        barrier_initial=list(event.obj.position[:2])
        report['barrier_mass']=float(event.obj.body.getMass())
        initial_map=feature_sha256(session.source_features)
        views={key:ObservedStateMap(session.source_features,initial_map) for key in session.interactive.ids}
        first_active={};first_cleared={};last={};braking=set();recovered=set();contact=False
        previous=session.interactive.report()['vehicles'];max_step=0.;barrier_drift=0.
        removed=None;first_contact=None;passed_barrier=False;disabled_seen=False;repair_seen=False
        for i in range(1000 if args.incident_response else 300 if args.collision_probe else 1500):
            controls={}
            if args.collision_probe and not (args.incident_response and first_contact is not None and i*.1-first_contact>5.):
                own=previous['P1'];car=session.interactive.vehicles['P1']
                steer=session.interactive.controllers['P1'](dict(position_runtime_m=own['position_city_m'],
                    heading_rad=own['heading_rad'],speed_m_s=own['speed_m_s'],
                    wheelbase_m=car.FRONT_WHEELBASE+car.REAR_WHEELBASE,max_steering_rad=math.radians(car.max_steering)))[0]
                controls={'P1':dict(mode='external',action=[steer,.4])}
            if args.incident_response and first_contact is not None and i*.1-first_contact>5.:controls={'P1':dict(mode='ai')}
            status=session.interactive_tick(controls,capture=False)
            traffic=status['interactive'];t=traffic['sim_time_s'];step=status['step']
            if event.obj is not None:
                barrier_drift=max(barrier_drift,math.dist(barrier_initial,event.obj.position[:2]))
            else:
                if removed is None:removed=t
            for key,car in traffic['vehicles'].items():
                max_step=max(max_step,math.dist(car['position_city_m'],previous[key]['position_city_m']))
                if car.get('braking',{}):
                    if car['braking']['vehicle_id']=='construction-C1':braking.add(key)
                if t>70 and key in braking and car['speed_m_s']>1.:recovered.add(key)
                if any(c['object_id']=='construction-C1' for c in car.get('current_contacts',[])):
                    contact=True
                    if first_contact is None:first_contact=t
                view=views[key]
                perception=vehicle_perception(traffic,key,clearance_queries=view.clearance_queries())
                view.update(step,t,perception)
                assert view.snapshot('baseline')['events']==[]
                for e in view.snapshot()['events']:
                    if e['actor_id']!='construction-C1':continue
                    if last.get(key)!=e['status']:
                        report['transitions'].append(dict(observer=key,status=e['status'],time_s=t,reason=e.get('clear_reason')))
                        last[key]=e['status']
                    if e['status']=='active':first_active.setdefault(key,t)
                    if e['status']=='cleared':first_cleared.setdefault(key,t)
            trace.update(json.dumps({k:[v['position_city_m'],v['speed_m_s']] for k,v in traffic['vehicles'].items()},sort_keys=True).encode())
            previous=traffic['vehicles']
            if (i+1)%300==0:print(json.dumps(dict(time_s=t,observed=first_active,cleared=first_cleared,braking=sorted(braking))),flush=True)
            if args.incident_response:
                incident=session.interactive.population.incidents
                disabled_seen |= any(e['cause']=='physical_collision' for e in incident.active.values())
                repair_seen |= disabled_seen and 'P1' not in incident.active
            if args.collision_probe:
                route_length=_length(config['cyclic_path'])
                progress=_project(previous['P1']['position_city_m'],config['cyclic_path'])[1]
                obstacle_progress=_project(config['construction']['position_city_m'],config['cyclic_path'])[1]
                passed_barrier |= event.obj is not None and (progress-obstacle_progress+route_length/2)%route_length-route_length/2>0
                if first_contact is not None and t-first_contact>=(50. if args.incident_response else 5.):break
        report.update(removed_at_s=removed,barrier_drift_m=barrier_drift,first_active=first_active,first_cleared=first_cleared,
            braking_vehicles=sorted(braking),recovered_vehicles=sorted(recovered),physical_contact_observed=contact,
            passed_through_barrier=passed_barrier,first_contact_s=first_contact,final_time_s=t,
            collision_events=sum(v['collision_event_count'] for v in traffic['vehicles'].values()),max_step_distance_m=max_step,
            physical_map_unchanged=initial_map==feature_sha256(session.source_features),trace_sha256=trace.hexdigest(),
            scope='Native background traffic and causal state baseline; no classmate Planning algorithm',
            final_event=event.report(),incident_response=dict(enabled=args.incident_response,disabled_seen=disabled_seen,repair_seen=repair_seen))
        report['passed']=bool((not args.incident_response or (disabled_seen and repair_seen)) and report['barrier_mass']==0 and (args.collision_probe or (sampled<=removed<=sampled+.100001)) and barrier_drift<1e-6 and report['physical_map_unchanged'] and max_step<1.
            and ((contact and not passed_barrier and t-first_contact>=5.) if args.collision_probe else (first_active and first_cleared and braking and recovered
                and report['collision_events']==0 and all(t>=removed+1.9 for t in first_cleared.values()))))
    finally:
        session.close()
        (args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
