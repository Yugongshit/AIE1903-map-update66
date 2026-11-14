"""Native lane-density and physically driven lane-change acceptance."""
import argparse
import json
import math
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from map_update.simulation_platform_data import load_scene,scene_database
from map_update.simulation_traffic import cyclic_traffic,vehicle_perception
from map_update.simulation_session import SimulationSession
from map_update.simulation_bridge import feature_sha256


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--seconds',type=int,default=180);p.add_argument('--density',type=float,default=24);p.add_argument('--seed',type=int,default=0)
    p.add_argument('--mixed',action='store_true');p.add_argument('--passing-fixture',action='store_true')
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    payload=load_scene(ROOT,'nuscenes-scene0980')
    config=cyclic_traffic(payload,dict(route_mode='network',network_extent='district',density_veh_km=args.density,
        lane_changes=True,following_model='idm',driver_style='mixed',speed_spread=.5,seed=args.seed,
        stop_enabled=False,pedestrians=args.mixed,pedestrian_count=16,background_activity=args.mixed,
        incidents=args.mixed,construction=args.mixed,construction_count_min=6,construction_count_max=10,recurring_construction=args.mixed))
    if args.passing_fixture:
        # Explicit controlled initial condition, not a representative frequency claim.
        from map_update.simulation_lane_changes import source_neighbours
        n=next(n for n in source_neighbours(payload['maps']['updated']) if n['source'].startswith('313a6405'))
        route=next(i+1 for i,r in enumerate(config['routes']) if n['source'] in r['lanes'])
        config['missions']=config['missions'][:2];config['traffic']['vehicle_count']=2;config['traffic']['density_veh_km']=0
        config['traffic_area'].update(spawned_vehicle_count=2,requested_density_veh_km=0,actual_density_veh_km=2000/config['traffic_area']['unique_lane_length_m'],initial_lane_counts={n['source']:2})
        for m,s,speed in zip(config['missions'],(7.,23.),(3.,.5)):
            geom=n['source_geometry'];pos=list(geom.point_at(s));m.update(position_scenario_m=pos,heading_rad=geom.project(pos)[3],cruise_speed_m_s=speed,route_id=f'route-{route}')
    session=SimulationSession(args.out/'frames',width=480,height=320);report=dict(passed=False,options=config['traffic'],coverage=config['traffic_area'],fixture=args.passing_fixture);trace=[];trajectories={}
    try:
        session.open(scene_database(ROOT,'nuscenes-scene0980'),interactive=config);rt=session.interactive
        map_hash=feature_sha256(session.source_features);previous={a['vehicle_id']:a['position_city_m'] for a in rt.actors()};max_step=0;off_surface=0;began=time.perf_counter()
        for tick in range(args.seconds*10):
            status=session.interactive_tick({},capture=False);physics=status['interactive']
            for a in rt.actors():
                key=a['vehicle_id'];pos=a['position_city_m']
                if key in previous:max_step=max(max_step,math.dist(pos,previous[key]))
                previous[key]=pos
                if args.passing_fixture and key in ('P1','P2') and tick%10==0:
                    trajectories.setdefault(key,[]).append(dict(time_s=(tick+1)/10,position_city_m=pos,heading_rad=a['heading_rad']))
                if key in rt.lane_changes.active:
                    from map_update.simulation_interactive_scenarios import vehicle_footprint
                    body=vehicle_footprint(pos,a['heading_rad'],a['length_m'],a['width_m'],margin=0.)
                    off_surface+=int(not rt.lane_changes.active[key]['surface'].buffer(.1).covers(body))
            if (tick+1)%300==0:
                row=dict(time_s=(tick+1)/10,wall_s=round(time.perf_counter()-began,2),lane_changes=physics['lane_changes'],
                    collisions=sum(v['collision_event_count'] for v in physics['vehicles'].values()),
                    stopped={k:v['stopped_reason'] for k,v in physics['vehicles'].items() if k!='ego' and v['stopped_reason']},
                    car_distances={k:round(v['route']['travelled_m'],2) for k,v in physics['vehicles'].items() if k!='ego'})
                trace.append(row);print(json.dumps(row),flush=True)
                assert 'source_dashed' not in json.dumps(vehicle_perception(physics,'ego'))
        report.update(trace=trace,trajectories=trajectories,max_step_m=max_step,passing_body_outside_surface_steps=off_surface,physical_map_unchanged=map_hash==feature_sha256(session.source_features),wire_bytes=len(json.dumps({'command':'open','interactive':config}).encode()),
                      lane_changes=rt.lane_changes.report(),final_collision_count=sum(v['collision_event_count'] for v in physics['vehicles'].values()))
        if len(trace)>=2:
            progress={key:round(value-trace[-2]['car_distances'][key],3) for key,value in trace[-1]['car_distances'].items()}
            report['progress_last_30s_m']=progress
            report['stagnant_last_30s']=[key for key,value in progress.items() if value<1.]
        report['acceptance_scope']='collision_continuity_map_integrity_and_fixture_pass_not_city_traffic_quality'
        report['passed']=(report['final_collision_count']==0 and off_surface==0 and max_step<1 and report['physical_map_unchanged'] and (not args.passing_fixture or rt.lane_changes.completed>0))
    finally:
        session.close();(args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
