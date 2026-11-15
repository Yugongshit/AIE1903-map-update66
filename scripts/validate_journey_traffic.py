"""Native open-road journeys: distribution, physical continuity and new-actor ingress."""
import argparse,json,math,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from map_update.simulation_platform_data import load_scene,scene_database
from map_update.simulation_traffic import cyclic_traffic,vehicle_perception
from map_update.simulation_session import SimulationSession
from map_update.simulation_bridge import feature_sha256


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);p.add_argument('--seconds',type=int,default=180)
    p.add_argument('--initial-config',type=Path,help='Reuse a saved synthetic initial world for traffic-rule comparison');p.add_argument('--cars',type=int,default=96);p.add_argument('--seed',type=int,default=0);p.add_argument('--mixed',action='store_true')
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=False)
    cfg=cyclic_traffic(load_scene(ROOT,'nuscenes-scene0980'),dict(route_mode='journeys',network_extent='district',vehicle_count=args.cars,
        following_model='idm',driver_style='mixed',speed_spread=.3,seed=args.seed,stop_enabled=False,lane_changes=True,
        pedestrians=args.mixed,pedestrian_count=16,background_activity=args.mixed,construction=args.mixed,construction_count_min=6,construction_count_max=10,
        incidents=args.mixed,recurring_construction=args.mixed))
    if args.initial_config:cfg=json.loads(args.initial_config.read_text())
    (args.out/'initial-config.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2)+'\n')
    session=SimulationSession(args.out/'frames',width=480,height=320);report=dict(passed=False,config=cfg['traffic'],coverage=cfg['traffic_area']);trace=[];samples=[]
    try:
        session.open(scene_database(ROOT,'nuscenes-scene0980'),interactive=cfg);rt=session.interactive;original=feature_sha256(session.source_features)
        previous={};max_step=0.;start=time.perf_counter();instance_changes=0
        for tick in range(args.seconds*10):
            result=session.interactive_tick({},capture=False);state=result['interactive']
            for key,v in state['vehicles'].items():
                instance=v.get('actor_instance_id',key);pos=v['position_city_m']
                if key in previous:
                    old_id,old_pos=previous[key]
                    if old_id==instance:max_step=max(max_step,math.dist(pos,old_pos))
                    else:instance_changes+=1
                previous[key]=(instance,pos)
            if tick==0 or (tick+1)%300==0 or tick+1==args.seconds*10:
                vehicles=[v for k,v in state['vehicles'].items() if k!='ego'];pos=[v['position_city_m'] for v in vehicles]
                row=dict(time_s=(tick+1)/10,wall_s=round(time.perf_counter()-start,2),journeys=state['journeys'],lane_changes=state['lane_changes'],
                    collisions=sum(v['collision_event_count'] for v in state['vehicles'].values()),occupied_80m_cells=len({(int(x//80),int(y//80)) for x,y in pos}),
                    outside_old_ring=sum(not(395.85<=x<=611.91 and 1073.32<=y<=1463.36) for x,y in pos),
                    moving=sum(v['speed_m_s']>.3 for v in vehicles),
                    traffic_diagnostics=state.get('traffic_diagnostics'),
                    pedestrian_diagnostics=[e for e in state.get('synthetic_events',[]) if e.get('kind')=='synthetic_pedestrian'],
                    waits={k:v.get('wait_diagnostic') for k,v in state['vehicles'].items()},
                    stopped={k:v['stopped_reason'] for k,v in state['vehicles'].items() if k!='ego' and v['stopped_reason']})
                trace.append(row);(args.out/'progress.json').write_text(json.dumps(row,ensure_ascii=False,indent=2)+'\n');samples.append(dict(time_s=row['time_s'],vehicles=[dict(slot=v['vehicle_id'],instance=v.get('actor_instance_id'),position=v['position_city_m'],speed=v['speed_m_s']) for v in vehicles]))
                print(json.dumps({k:v for k,v in row.items() if k not in ('stopped','journeys','waits','pedestrian_diagnostics')}|{'trips':row['journeys']['completed_trips'],'exits':row['journeys']['boundary_exits']}),flush=True)
                cycles=row['traffic_diagnostics']['waiting_cycles']
                if cycles:
                    involved={k for group in cycles for k in group}
                    # Offline synthetic debugging only; never a Planning input.
                    detail=dict(scope='synthetic_spectator_wait_cycle_debug_only',time_s=row['time_s'],
                        vehicles=state['vehicles'],static_actors=state['static_actors'],pedestrians=row['pedestrian_diagnostics'],
                        paths={k:rt.path_for(k) for k in involved if k in rt.controllers},zones=rt.junctions,
                        reservations={z:{k:{a:b for a,b in h.items() if a!='corridor'} for k,h in holders.items()}
                                      for z,holders in rt.junction_coordinator.reservations.items()})
                    (args.out/f'wait-cycle-{tick+1:06d}.json').write_text(json.dumps(detail,ensure_ascii=False,indent=2))
                encoded=json.dumps(vehicle_perception(state,'ego'))
                assert all(k not in encoded for k in ('exit_lane','entry_lane','recent','lifecycle','journey_lane','patrol_goals','wait_diagnostic','waiting_cycles','waiting_for'))
        report.update(trace=trace,position_samples=samples,max_same_instance_step_m=max_step,slot_instance_changes=instance_changes,
            world_map_unchanged=original==feature_sha256(session.source_features),wire_bytes=len(json.dumps(cfg).encode()),
            all_actors_have_unique_identity=len({v.get('actor_instance_id',k) for k,v in state['vehicles'].items()})==len(state['vehicles']))
        report['initial_config_sha256']=feature_sha256(cfg)
        report['validation_scope']='physical_integrity_only_not_sustained_traffic_acceptance'
        last=trace[-1];window=[r for r in trace if r['time_s']>=last['time_s']-60]
        report['traffic_health']=dict(minimum_moving_last_60s=min(r['moving'] for r in window),
            final_moving=last['moving'],final_stopped_over_30s=len(last['traffic_diagnostics']['stopped_over_30s']),
            final_waiting_cycles=last['traffic_diagnostics']['waiting_cycles'],
            completed_trips_last_60s=last['journeys']['completed_trips']-window[0]['journeys']['completed_trips'],
            final_tracking_failures=[k for k,v in last['waits'].items() if k!='ego' and v.get('controller_status')=='stopped'],
            final_walkers_waiting_over_30s=[{k:w[k] for k in ('event_id','status','stationary_for_s','waiting_for')}
                for w in last['pedestrian_diagnostics'] if w['stationary_for_s']>=30],
            final_walkers_commanded_but_stationary=[w['event_id'] for w in last['pedestrian_diagnostics']
                if w['status'] in ('walking','crossing','passing') and w['stationary_for_s']>=10])
        if len(samples)>1:
            previous_sample={v['instance']:v for v in samples[-2]['vehicles']}
            report['traffic_health']['last_sample_same_instance_under_1m']=[v['slot'] for v in samples[-1]['vehicles']
                if v['instance'] in previous_sample and math.dist(v['position'],previous_sample[v['instance']]['position'])<1.]
            report['traffic_health']['displacement_window_s']=samples[-1]['time_s']-samples[-2]['time_s']
        report['passed']=bool(trace[-1]['collisions']==0 and max_step<1 and report['world_map_unchanged'] and trace[0]['outside_old_ring']>args.cars*.25
            and trace[-1]['journeys']['boundary_exits']>0 and trace[-1]['journeys']['completed_trips']>trace[-1]['journeys']['boundary_exits']
            and trace[-1]['occupied_80m_cells']>=15)
    finally:
        session.close();(args.out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return 0 if report['passed'] else 1
if __name__=='__main__':raise SystemExit(main())
