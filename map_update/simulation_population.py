"""Bounded continuous synthetic events and velocity-controlled pedestrian bodies.

Future event draws stay private. Walkers use an approximate rigid-body velocity
servo, not human biomechanics. Collision disable/repair is an abstract state,
not a damage or tow-truck simulation.
"""
import math
import random
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from .simulation_walking import WalkingNetwork
from .simulation_interactive_scenarios import vehicle_footprint
from .simulation_planner import _length, _point_at, _project, _angle
from .simulation_events import ConstructionEvent, sample_clearance_time


class Walker:
    def __init__(self, runtime, network, node, identifier):
        from metadrive.component.traffic_participants.pedestrian import PedestrianBoundingBox
        self.runtime, self.network, self.identifier = runtime, network, identifier
        self.rng = random.Random(f"{runtime.traffic['seed']}:{identifier}:walk")
        self.speed = self.rng.uniform(.8, 1.4)
        self.walk_style='baseline';self.trip_range=(35,180);self.rest_range=(2,12)
        if runtime.traffic.get('network_extent')=='district':
            self.walk_style=random.Random(f"{runtime.traffic['seed']}:{identifier}:style").choice(('commuter','stroller','errands'))
            speed_range,self.trip_range,self.rest_range={'commuter':((1.1,1.6),(70,250),(1,5)),
                'stroller':((.65,1.),(25,100),(8,25)),'errands':((.85,1.3),(35,140),(3,12))}[self.walk_style]
            self.speed=self.rng.uniform(*speed_range)
        self.wait_until = self.rng.uniform(0., 4.)
        self.phase = 'resting'; self.crossings = 0; self.trips = 0; self.distance = 0.
        self.recent = [node]; self.visited_crosswalks = set(); self.crossing = None
        self.crossing_distance = 0.; self.approved_crossing = None
        self.route = network.trip(node, self.rng, distance_range=self.trip_range); self.target = 1; self.detour = []
        self.blocked_since = None; self.reroutes = 0; self.changed_destinations = 0
        self.stalled_for=0.;self.physics_replans=0;self.local_blockages=[]
        a = network.points[node]; b = network.points[self.route[1]]
        self.previous = list(a); self.corridor = Point(a).buffer(.8)
        offset = runtime.metadata.get('old_origin_in_current_coordinate', [0, 0])
        self.obj = runtime.env.engine.spawn_object(PedestrianBoundingBox, name=identifier, random_seed=0,
            position=[a[i]+offset[i] for i in (0, 1)], heading_theta=math.atan2(b[1]-a[1], b[0]-a[0]),
            length=.6, width=.6, height=1.7)
        self.native_id = self.obj.id

    def position(self):
        offset = self.runtime.metadata.get('old_origin_in_current_coordinate', [0, 0])
        return [float(self.obj.position[i])-offset[i] for i in (0, 1)]

    def safe_crossing(self, key, actors):
        corridor = self.network.crosswalks[key].buffer(.3)
        x0, y0, x1, y1 = corridor.bounds
        duration = math.hypot(x1-x0, y1-y0)/self.speed
        for actor in actors:
            if actor['vehicle_id'] == self.identifier or actor.get('type')=='PEDESTRIAN': continue
            # Pedestrians use the local body-avoidance stage; reserving an entire
            # crosswalk for each person deadlocks opposite curb approaches.
            shape = vehicle_footprint(actor['position_city_m'], actor['heading_rad'], actor['length_m'], actor['width_m'], margin=.3)
            if corridor.intersects(shape): return False
            if actor.get('type') == 'VEHICLE':
                reach = actor['speed_m_s']*(duration+2.)+2.
                if shape.distance(corridor) < reach:
                    heading = [math.cos(actor['heading_rad']), math.sin(actor['heading_rad'])]
                    sweep = LineString([actor['position_city_m'], [actor['position_city_m'][i]+heading[i]*reach for i in (0, 1)]]).buffer(actor['width_m']/2+.5)
                    if sweep.intersects(corridor): return False
        return True

    def advance(self, time_s, actors):
        pos = self.position(); moved = math.dist(pos, self.previous)
        self.distance += moved; self.previous = pos
        self.stalled_for=(getattr(self,'stalled_for',0.)+.1) if self.phase in ('walking','crossing','passing') and moved<.005 else 0.
        crossing = self.network.crossing_at(pos)
        if crossing != self.crossing:
            if self.crossing and self.crossing_distance > 2.:
                self.crossings += 1; self.visited_crosswalks.add(self.crossing)
            self.crossing_distance = 0.; self.crossing = crossing
            if not crossing: self.approved_crossing = None
        if crossing: self.crossing_distance += moved
        if time_s < self.wait_until:
            self.obj.set_velocity([0., 0.]); return
        if self.detour and math.dist(pos, self.detour[0]) < .06: self.detour.pop(0)
        target = self.detour[0] if self.detour else self.network.points[self.route[self.target]]
        if not self.detour and math.dist(pos, target) < .06:
            self.target += 1
            if self.target == len(self.route):
                self.trips += 1; node = self.route[-1]
                self.route = self.network.trip(node, self.rng, self.recent, self.trip_range)
                if self.local_blockages:
                    safe_route=self.network.reroute(pos,self.route[-1],self.rng,unary_union(self.local_blockages),self.recent)
                    if safe_route:self.route=safe_route
                self.recent = (self.recent+[node])[-4:]; self.target = 1
                self.phase = 'resting'; self.wait_until = time_s+self.rng.uniform(*self.rest_range)
                self.obj.set_velocity([0., 0.]); return
            target = self.network.points[self.route[self.target]]
        if self.stalled_for>=3.:
            # A source-map path can meet an engine collider (for example a curb).
            # Use failed current motion as local blockage evidence; turn away
            # along walkable space, never disable collision or move the body.
            delta=[target[i]-pos[i] for i in (0,1)];size=max(math.hypot(*delta),1e-6)
            direction=[v/size for v in delta]
            blocked=Point([pos[i]+1.2*direction[i] for i in (0,1)]).buffer(.8)
            self.local_blockages=(self.local_blockages+[blocked])[-32:]
            route=self.network.reroute(pos,self.route[-1],self.rng,unary_union(self.local_blockages),self.recent,avoid_direction=direction)
            if route:
                self.changed_destinations+=int(route[-1]!=self.route[-1]);self.route=route;self.target=0;self.detour=[]
                self.physics_replans+=1;self.phase='replanning_physical_blockage'
            else:self.phase='blocked_by_physics'
            self.stalled_for=0.;self.obj.set_velocity([0.,0.]);return
        upcoming = crossing or self.network.crossing_at(target)
        self.corridor = self.network.crosswalks[upcoming].buffer(.8) if upcoming else LineString([pos, target]).buffer(.8)
        if upcoming and self.approved_crossing != upcoming:
            if not self.safe_crossing(upcoming, actors):
                self.phase = 'waiting_gap'; self.obj.set_velocity([0., 0.]); return
            self.approved_crossing = upcoming
        # Current-body local avoidance. No intention or future driver action access.
        delta = [target[i]-pos[i] for i in (0, 1)]; distance = math.hypot(*delta)
        ahead = [pos[i]+delta[i]*min(1., .8/max(distance, 1e-6)) for i in (0, 1)]
        swept = LineString([pos, ahead]).buffer(.45)
        for actor in actors:
            if actor['vehicle_id'] == self.identifier: continue
            if math.dist(pos, actor['position_city_m']) > 8: continue
            shape = vehicle_footprint(actor['position_city_m'], actor['heading_rad'], actor['length_m'], actor['width_m'], margin=.15)
            if swept.intersects(shape):
                if self.blocked_since is None: self.blocked_since = time_s
                if actor.get('type') == 'PEDESTRIAN' and time_s-self.blocked_since > 4+int(self.identifier.split('-')[-1])*3:
                    # A narrow sidewalk may have no room to pass. Replan around
                    # the current body, or abandon this destination and walk
                    # toward a different reachable place; never count arrival.
                    obstacle = unary_union([Point(actor['position_city_m']).buffer(.95),*self.local_blockages])
                    route = self.network.reroute(pos, self.route[-1], self.rng, obstacle, self.recent)
                    if route:
                        self.changed_destinations += int(route[-1] != self.route[-1])
                        self.route = route; self.target = 0; self.detour = []; self.reroutes += 1
                    self.blocked_since = time_s
                if actor.get('type') == 'PEDESTRIAN' and not self.detour:
                    # Pass on the right when the actual sidewalk is wide enough.
                    # Both walkers use their own heading, hence opposite sides.
                    ux, uy = delta[0]/max(distance, 1e-6), delta[1]/max(distance, 1e-6)
                    for width in (.9, 1.2):
                        side = [pos[0]+uy*width, pos[1]-ux*width]
                        forward = [side[0]+ux*min(2., distance), side[1]+uy*min(2., distance)]
                        detour = [side, forward]
                        if not self.network.motion_area.covers(LineString([pos, *detour, target])): continue
                        first_step = LineString([pos, side]).buffer(.45)
                        others = [a for a in actors if a['vehicle_id'] != self.identifier and math.dist(pos, a['position_city_m']) < 8]
                        if any(first_step.intersects(vehicle_footprint(a['position_city_m'], a['heading_rad'], a['length_m'], a['width_m'], margin=.15)) for a in others): continue
                        self.detour = detour; break
                self.phase = 'yielding'; self.obj.set_velocity([0., 0.]); return
        if not self.network.motion_area.covers(LineString([pos, target])):
            self.phase = 'blocked_off_walkway'; self.obj.set_velocity([0., 0.]); return
        self.blocked_since = None
        self.phase = 'crossing' if crossing else ('passing' if self.detour else 'walking')
        self.obj.set_heading_theta(math.atan2(delta[1], delta[0]))
        self.obj.set_velocity(delta, min(self.speed, distance/.1))

    def report(self):
        return dict(event_id=self.identifier, kind='synthetic_pedestrian', object_id=self.identifier,
            status=self.phase, completed_crossings=self.crossings, completed_trips=self.trips,
            physics_blockage_replans=self.physics_replans, walk_style=self.walk_style, travelled_m=round(self.distance, 2), reroutes=self.reroutes, changed_destinations=self.changed_destinations, distinct_crosswalks=len(self.visited_crosswalks), synthetic=True,
            execution_model='source_walkway_trips_rigid_body_velocity_servo_no_biomechanics')


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
        from .simulation_district import BackgroundActivity
        self.activity=BackgroundActivity(runtime.traffic['seed'],runtime.ids[1:]) if runtime.traffic.get('background_activity') else None
        self.rng=random.Random(f"{runtime.traffic['seed']}:recurring:sites")
        self.next_spawn=self.rng.uniform(45.,70.);self.serial=len(runtime.construction_events)
        self.spawned=0;self.deferred=0;self.clearances=0;self.collision_counts={key:0 for key in runtime.ids}
        paths=list({str(runtime.path_for(k)):runtime.path_for(k) for k in runtime.ids[1:]}.values())
        if not paths: paths=list(runtime.route_paths.values()) or [runtime.cyclic_path]
        self.paths=paths
        if runtime.traffic['pedestrians']:
            actors=runtime.actors()
            occupied=[vehicle_footprint(a['position_city_m'],a['heading_rad'],a['length_m'],a['width_m'],margin=.5) for a in actors]
            static=[vehicle_footprint(a['position_city_m'],a['heading_rad'],a['length_m'],a['width_m']) for a in actors if a.get('type') not in ('VEHICLE','PEDESTRIAN')]
            self.walking_network=WalkingNetwork(runtime.source_features,paths,static,expanded=runtime.traffic.get('network_extent')=='district')
            nodes=self.walking_network.spawn_nodes(runtime.traffic['pedestrian_count'],random.Random(f"{runtime.traffic['seed']}:pedestrian:spawn"),occupied)
            for i,node in enumerate(nodes):
                self.walkers.append(Walker(runtime,self.walking_network,node,f'walker-{i+1}'))
        self.requested_walkers=runtime.traffic['pedestrian_count'] if runtime.traffic['pedestrians'] else 0

    def advance(self, time_s, actors):
        rt=self.runtime
        for walker in self.walkers:walker.advance(time_s,actors)
        cars={a['vehicle_id']:a for a in actors if a['vehicle_id'] in rt.ids}
        if self.activity:
            crossings=list(self.walking_network.crosswalks.values()) if hasattr(self,'walking_network') else []
            self.activity.advance(time_s,cars,rt.modes,rt.junctions,crossings)
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
        return dict(brief_stops_started=self.activity.started if self.activity else 0,
            brief_stops_completed=self.activity.completed if self.activity else 0,
            brief_stops_active=sum(self.activity.stopping(k) for k in self.activity.rows) if self.activity else 0,
            pedestrian_occupied_cells=len({(int(w.position()[0]//80),int(w.position()[1]//80)) for w in self.walkers}),
            pedestrian_requested=self.requested_walkers,pedestrian_spawned=len(self.walkers),
            pedestrian_capacity_limited=len(self.walkers)<self.requested_walkers,
            completed_crossings=sum(w.crossings for w in self.walkers),
            completed_walking_trips=sum(w.trips for w in self.walkers),
            pedestrian_travelled_m=round(sum(w.distance for w in self.walkers),2),
            distinct_crosswalks=len(set().union(*(w.visited_crosswalks for w in self.walkers))),
            incident_started=self.incidents.started,incident_completed=self.incidents.completed,
            collision_disables=self.incidents.collision_disables,construction_cleared=self.clearances,recurring_spawned=self.spawned,
            recurring_deferred=self.deferred,recurring_spawn_rule='outside_all_controlled_vehicle_55m_with_clear_footprint',
            synthetic=True)

    def close(self):
        for walker in self.walkers:
            self.runtime.env.engine.clear_objects([walker.native_id],force_destroy=True)
        self.walkers=[]
