"""Bounded continuous synthetic events and velocity-controlled pedestrian bodies.

Future event draws stay private. Walkers use an approximate rigid-body velocity
servo, not human biomechanics. Collision disable/repair is an abstract state,
not a damage or tow-truck simulation.
"""
import math
import random
from shapely.geometry import LineString, Point
from .simulation_network import crossing_sites
from .simulation_interactive_scenarios import vehicle_footprint
from .simulation_planner import _length, _point_at, _project, _angle
from .simulation_events import ConstructionEvent, sample_clearance_time


class Walker:
    def __init__(self, runtime, spec):
        from metadrive.component.traffic_participants.pedestrian import PedestrianBoundingBox
        self.runtime, self.spec = runtime, spec
        self.identifier=spec['pedestrian_id']
        self.rng=random.Random(f"{runtime.traffic['seed']}:{self.identifier}:walk")
        self.speed=self.rng.uniform(.8,1.4);self.target=1;self.wait_until=self.rng.uniform(3.,12.)
        self.phase='waiting';self.crossings=0
        a,b=spec['endpoints'];self.corridor=LineString([a,b]).buffer(.8)
        offset=runtime.metadata.get('old_origin_in_current_coordinate',[0,0])
        self.obj=runtime.env.engine.spawn_object(PedestrianBoundingBox,name=self.identifier,random_seed=0,
            position=[a[i]+offset[i] for i in (0,1)],heading_theta=math.atan2(b[1]-a[1],b[0]-a[0]),
            length=.6,width=.6,height=1.7)
        self.native_id=self.obj.id

    def position(self):
        offset=self.runtime.metadata.get('old_origin_in_current_coordinate',[0,0])
        return [float(self.obj.position[i])-offset[i] for i in (0,1)]

    def advance(self, time_s, actors):
        pos=self.position();target=self.spec['endpoints'][self.target];distance=math.dist(pos,target)
        if self.phase=='crossing' and distance<.16:
            self.crossings+=1;self.phase='waiting';self.target=1-self.target
            self.wait_until=time_s+self.rng.uniform(5.,18.)
        if self.phase=='waiting' and time_s>=self.wait_until:
            duration=math.dist(*self.spec['endpoints'])/self.speed
            safe=True
            for actor in actors:
                if actor['vehicle_id']==self.identifier:continue
                shape=vehicle_footprint(actor['position_city_m'],actor['heading_rad'],actor['length_m'],actor['width_m'],margin=.3)
                if self.corridor.intersects(shape):safe=False;break
                if actor.get('type')=='VEHICLE':
                    # Constant-current-velocity screening, not a future action oracle.
                    reach=actor['speed_m_s']*(duration+2.)+5.
                    if shape.distance(self.corridor)<reach:
                        heading=[math.cos(actor['heading_rad']),math.sin(actor['heading_rad'])]
                        sweep=LineString([actor['position_city_m'],[actor['position_city_m'][i]+heading[i]*reach for i in (0,1)]]).buffer(actor['width_m']/2+.5)
                        if sweep.intersects(self.corridor):safe=False;break
            if safe:self.phase='crossing'
        if self.phase=='crossing':
            target=self.spec['endpoints'][self.target];delta=[target[i]-pos[i] for i in (0,1)]
            distance=math.hypot(*delta)
            self.obj.set_heading_theta(math.atan2(delta[1],delta[0]))
            self.obj.set_velocity(delta,min(self.speed,distance/.1))
        else:self.obj.set_velocity([0.,0.])

    def report(self):
        return dict(event_id=self.identifier,kind='synthetic_pedestrian',object_id=self.identifier,
            status=self.phase,completed_crossings=self.crossings,synthetic=True,
            source_crosswalk_id=self.spec['source_crosswalk_id'],execution_model='rigid_body_velocity_servo_no_biomechanics')


class IncidentScheduler:
    def __init__(self, seed):
        self.rng=random.Random(f'{seed}:incidents');self.next_at=self.rng.uniform(20.,40.)
        self.active={};self.completed=0;self.started=0;self.collision_disables=0

    def advance(self, time_s, cars, modes, junctions, crossings):
        for key,event in list(self.active.items()):
            if time_s>=event['until']:
                self.active.pop(key);self.completed+=1
                self.next_at=time_s+self.rng.uniform(25.,55.)
            elif cars[key]['speed_m_s']<.25:event['status']='disabled'
        if not self.active and time_s>=self.next_at:
            choices=[key for key,car in sorted(cars.items()) if key!='ego' and modes[key]=='ai'
                and all(math.dist(car['position_city_m'],j['position_city_m'])>j['radius_m']+15 for j in junctions)]
            choices=[key for key in choices if all(Point(cars[key]['position_city_m']).distance(c)>10. for c in crossings)]
            if choices:
                self.disable(self.rng.choice(choices),time_s,'temporary_breakdown')
            else:self.next_at=time_s+2.

    def disable(self, identifier, time_s, cause):
        if identifier in self.active:return
        self.active[identifier]=dict(status='braking',cause=cause,started_at_s=time_s,
            until=time_s+self.rng.uniform(12.,25.) if cause=='temporary_breakdown' else time_s+self.rng.uniform(25.,45.))
        self.started+=1;self.collision_disables+=int(cause=='physical_collision')

    def report(self):
        return [dict(event_id='incident:'+key,kind='synthetic_vehicle_incident',object_id=key,
            status=e['status'],cause=e['cause'],started_at_s=e['started_at_s'],synthetic=True,
            execution_model='abstract_disable_repair_no_damage_mesh') for key,e in sorted(self.active.items())]


class TrafficPopulation:
    def __init__(self, runtime):
        self.runtime=runtime;self.walkers=[];self.incidents=IncidentScheduler(runtime.traffic['seed'])
        self.rng=random.Random(f"{runtime.traffic['seed']}:recurring:sites")
        self.next_spawn=self.rng.uniform(45.,70.);self.serial=len(runtime.construction_events)
        self.spawned=0;self.deferred=0;self.clearances=0;self.collision_counts={key:0 for key in runtime.ids}
        paths=list({str(runtime.path_for(k)):runtime.path_for(k) for k in runtime.ids[1:]}.values())
        self.paths=paths
        if runtime.traffic['pedestrians']:
            actors=runtime.actors()
            occupied=[vehicle_footprint(a['position_city_m'],a['heading_rad'],a['length_m'],a['width_m'],margin=.5) for a in actors]
            sites=crossing_sites(runtime.source_features,paths,occupied,runtime.traffic['pedestrian_count'],runtime.traffic['seed'])
            for spec in sites:
                self.walkers.append(Walker(runtime,spec))
        self.requested_walkers=runtime.traffic['pedestrian_count'] if runtime.traffic['pedestrians'] else 0

    def advance(self, time_s, actors):
        rt=self.runtime
        for walker in self.walkers:walker.advance(time_s,actors)
        cars={a['vehicle_id']:a for a in actors if a['vehicle_id'] in rt.ids}
        if rt.traffic['incidents']:
            self.incidents.advance(time_s,cars,rt.modes,rt.junctions,[w.corridor for w in self.walkers])
        if rt.traffic['recurring_construction'] and time_s>=self.next_spawn:
            active=[e for e in rt.construction_events if e.obj is not None]
            if len(active)<rt.traffic['construction_count_max'] and self.spawn_construction(time_s,actors):
                self.next_spawn=time_s+self.rng.uniform(30.,60.)
            else:
                self.deferred+=1;self.next_spawn=time_s+5.
        # Keep a bounded spectator event history. Sensor evidence is independent.
        removed=[e for e in rt.construction_events if e.obj is None]
        if len(removed)>64:
            old=set(removed[:-64]);rt.construction_events=[e for e in rt.construction_events if e not in old]

    def spawn_construction(self, time_s, actors):
        rt=self.runtime
        occupied=[vehicle_footprint(a['position_city_m'],a['heading_rad'],a['length_m'],a['width_m'],margin=.5) for a in actors]
        candidates=[(p,s*3.) for p in self.paths for s in range(int(_length(p)/3))]
        self.rng.shuffle(candidates)
        for path,progress in candidates:
            pos=list(_point_at(path,progress));heading=_project(pos,path)[3]
            if any(math.dist(pos,a['position_city_m'])<=55. for a in actors if a['vehicle_id'] in rt.ids):continue
            if any(math.dist(pos,j['position_city_m'])<j['radius_m']+15. for j in rt.junctions):continue
            if any(Point(pos).distance(w.corridor)<8. for w in self.walkers):continue
            if any(abs(_angle(_project(_point_at(path,(progress+d)%_length(path)),path)[3]-heading))>.35 for d in (-4,4)):continue
            shape=vehicle_footprint(pos,heading,1.,2.5,margin=.5)
            if any(shape.distance(o)<8. for o in occupied):continue
            self.serial+=1
            spec=dict(object_id=f'construction-R{self.serial}',event_id=f'recurring-{self.serial}',type='TRAFFIC_BARRIER',
                position_city_m=pos,heading_rad=heading,length_m=1.,width_m=2.5,height_m=1.,synthetic=True)
            event=ConstructionEvent(rt.env.engine,rt.metadata,spec,time_s+sample_clearance_time(rt.traffic,spec['event_id']))
            event.started_at_s=time_s
            rt.construction_events.append(event);self.spawned+=1
            return True
        return False

    def after_step(self, time_s):
        rt=self.runtime
        if not rt.traffic['incidents']:return
        for key in rt.ids:
            count=rt.collision_event_count[key]
            if count>self.collision_counts[key]:
                self.incidents.disable(key,time_s,'physical_collision')
            self.collision_counts[key]=count

    def report(self):
        return dict(pedestrian_requested=self.requested_walkers,pedestrian_spawned=len(self.walkers),
            pedestrian_capacity_limited=len(self.walkers)<self.requested_walkers,
            completed_crossings=sum(w.crossings for w in self.walkers),
            incident_started=self.incidents.started,incident_completed=self.incidents.completed,
            collision_disables=self.incidents.collision_disables,construction_cleared=self.clearances,recurring_spawned=self.spawned,
            recurring_deferred=self.deferred,recurring_spawn_rule='outside_all_controlled_vehicle_55m_with_clear_footprint',
            synthetic=True)

    def close(self):
        for walker in self.walkers:
            self.runtime.env.engine.clear_objects([walker.native_id],force_destroy=True)
        self.walkers=[]
