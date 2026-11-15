"""Seeded open-road background trips, with explicit boundary vehicle lifecycles.

No road connections are guessed. Each new goal is selected from the directed
source graph. A control slot can receive a NEW physical actor after a boundary
exit; actor instance IDs, sensor histories and visual interpolation distinguish
that lifecycle from moving an existing vehicle to another location.
"""
from collections import Counter
from copy import deepcopy
import heapq
from bisect import bisect_right
import math
from pathlib import Path
import random
import numpy as np
import shapely
from shapely.geometry import LineString, Point

from .simulation_interactive_scenarios import _graph, vehicle_footprint
from .simulation_path_geometry import PathGeometry
from .simulation_planner import _angle


class JourneyNetwork:
    def __init__(self, features, obstacles=(), blocked_lanes=()):
        from .simulation_lane_changes import _raw_map
        raw=_raw_map(str(Path(__file__).resolve().parents[1]/'AIE1903 原始资料/maps/expansion/singapore-onenorth.json'))
        points,graph=_graph(features);self.geometries={k:PathGeometry(p) for k,p in points.items()}
        self.regular={r['token'] for r in raw['lane'] if r['lane_type']=='CAR'} & set(graph)
        reverse={k:[] for k in graph}
        for k,targets in graph.items():
            for t in targets:reverse[t].append(k)
        connectivity=raw['connectivity']
        # Proven clipped source edges, not arbitrary interior dead ends.
        entries={k for k in graph if not reverse[k] and any(t not in graph for t in connectivity.get(k,{}).get('incoming',[]))}
        exits={k for k in graph if not graph[k] and any(t not in graph for t in connectivity.get(k,{}).get('outgoing',[]))}
        blocked=set(blocked_lanes)
        if obstacles:
            keys=sorted(graph);lines=np.array([LineString(points[k]) for k in keys],dtype=object)
            distances=shapely.distance(lines[:,None],np.array(obstacles,dtype=object)[None,:])
            blocked={key for key,d in zip(keys,distances.min(axis=1)) if d<1.35}
        graph={k:[t for t in v if t not in blocked] for k,v in graph.items() if k not in blocked}
        exits-=blocked
        # Remove interior cul-de-sacs and chains leading only to those ends.
        viable=set(graph)
        while True:
            dead={k for k in viable if k not in exits and not any(t in viable for t in graph[k])}
            if not dead:break
            viable-=dead
        self.graph={k:[t for t in graph[k] if t in viable] for k in sorted(viable)}
        self.points={k:points[k] for k in self.graph}
        self.entries=sorted(k for k in entries & viable if self.geometries[k].length>15)
        self.exits=exits & viable;self.regular &= viable
        self.blocked=blocked;self.bounds=list(shapely.union_all([LineString(p) for p in self.points.values()]).bounds)
        self.length=sum(self.geometries[k].length for k in self.graph)
        from .simulation_network import junction_sites, merge_junction_regions
        self.junctions=merge_junction_regions(junction_sites([{'lanes':list(self.graph)}],self.points,self.graph,geometric_conflicts=True))
        self.roads=[dict(lanes=[k],path=self.points[k],length=self.geometries[k].length) for k in self.graph]

    def trip(self, start, progress, rng, recent=(), actors=()):
        """Reachable independent destination, with private seeded route preferences."""
        if start not in self.graph:raise ValueError('Journey start is not on the usable source graph')
        costs={k:self.geometries[k].length*rng.uniform(.8,1.25) for k in self.graph}
        best={start:0.};parents={};pending=[(0.,start)]
        while pending:
            cost,k=heapq.heappop(pending)
            if cost!=best[k]:continue
            for target in self.graph[k]:
                next_cost=cost+costs[k]
                if next_cost<best.get(target,math.inf):
                    best[target]=next_cost;parents[target]=k;heapq.heappush(pending,(next_cost,target))
        candidates=[k for k in best if k!=start and (k in self.exits or k in self.regular and self.geometries[k].length>=25) and best[k]>40]
        if not candidates:
            candidates=[k for k in best if k!=start and (k in self.exits or self.geometries[k].length>10)]
        if not candidates:
            return [start],min(self.geometries[start].length-5,max(progress+8,self.geometries[start].length*.7))
        fresh=[k for k in candidates if k not in recent]
        if fresh:candidates=fresh
        cells=Counter((int(a['position_city_m'][0]//100),int(a['position_city_m'][1]//100)) for a in actors if a.get('type')=='VEHICLE')
        preferred=rng.uniform(150,450)
        weights=[]
        for k in candidates:
            x,y=self.geometries[k].point_at(self.geometries[k].length/2)
            weights.append((1.5 if k in self.exits else 1.)/((1+abs(best[k]-preferred)/250)*(1+cells[int(x//100),int(y//100)]*.15)))
        goal=rng.choices(candidates,weights=weights,k=1)[0];route=[goal]
        while route[-1]!=start:route.append(parents[route[-1]])
        route.reverse();length=self.geometries[goal].length
        return route,(length-5 if goal in self.exits else rng.uniform(max(8,length*.4),length-8))

    def path(self, lanes, end, *, with_lane_ends=False):
        from .simulation_path_geometry import join_source_centerlines
        parts=[self.points[key] for key in lanes[:-1]]
        geom=self.geometries[lanes[-1]]
        last=[list(geom.point_at(d)) for d in range(math.ceil(end))]
        last.append(list(geom.point_at(end)))
        points,ends=join_source_centerlines([*parts,last])
        return (points,ends[:-1]) if with_lane_ends else points


def journey_traffic(payload, options):
    from .simulation_district import density_spawns, distributed_construction
    ego=payload['tracks'][payload['ego_track_id']]
    fixed=[vehicle_footprint(a['position'],a['heading'],a['length'],a['width'],margin=0.) for a in payload.get('static_overview_actors',[])]
    network=JourneyNetwork(payload['maps']['updated'],fixed)
    occupied=[*fixed,vehicle_footprint(ego['position'][0],ego['heading'][0])]
    desired=round(options['density_veh_km']*network.length/1000) if options['density_veh_km'] else options['vehicle_count']
    count=max(1,min(128,desired)) if options['vehicle_count'] else 0
    # Distinct source lanes, no closed-route length duplication.
    roads=[r for r in network.roads if r['lanes'][0] in network.regular and r['length']>15]
    placements,lane_counts=density_spawns(roads,network.points,occupied,network.junctions,count,options['seed'],parallel_clearance=True,end_clearance={k:20 for k in network.exits})
    rng=random.Random(f"{options['seed']}:journey:speeds")
    missions=[]
    for i,(road,_,pos,heading) in enumerate(placements):
        missions.append(dict(vehicle_id=f'P{i+1}',position_scenario_m=pos,heading_rad=heading,
                             goal_scenario_m=pos,journey_lane=roads[road]['lanes'][0],
                             cruise_speed_m_s=max(.5,min(3.,options['speed_m_s']*(1+rng.uniform(-options['speed_spread'],options['speed_spread']))))))
    specs,generation=distributed_construction(roads,occupied,options,network.junctions,payload['maps']['updated']) if options['construction'] else ([],None)
    return dict(missions=missions,traffic=dict(options,vehicle_count=count,profile='synthetic_journeys_v1',synthetic=True),
                constructions=specs,construction_generation=generation,journey_blocked_lanes=sorted(network.blocked),
                traffic_area=dict(selection='source_journeys',network_extent='district',source_ego_unchanged=True,
                    route_count=0,route_length_m=network.length,unique_lane_length_m=network.length,source_lane_count=len(network.graph),
                    bounds_city_m=network.bounds,initial_lane_counts=lane_counts,spawned_vehicle_count=count,
                    requested_vehicle_count=desired,vehicle_limit_reached=desired>128,requested_density_veh_km=options['density_veh_km'],
                    actual_density_veh_km=count*1000/network.length,entry_count=len(network.entries),exit_count=len(network.exits),
                    route_distance_to_recorded_ego_m=min(LineString(p).distance(Point(ego['position'][0][:2])) for p in network.points.values()),
                    source_scope='reachable_source_graph_with_explicit_cut_boundary_portals'))


class Journeys:
    def __init__(self, runtime, network):
        self.runtime=runtime;self.network=network;self.rows={};self.completed=0;self.exited=0;self.entered=0
        self.entry_uses=Counter();self.visited=set();self.transitions=set();self.lifecycle=[];self.waiting_entries=set()
        for key in runtime.ids[1:]:
            self.rows[key]=dict(generation=1,rng=random.Random(f"{runtime.traffic['seed']}:{key}:journeys:1"),recent=[],goals=0)

    def assign(self, key, lane, progress, actors=()):
        from .simulation_traffic import CycleFollower
        rt=self.runtime;row=self.rows[key]
        lanes,end=self.network.trip(lane,progress,row['rng'],row['recent'],actors)
        path,lane_ends=self.network.path(lanes,end,with_lane_ends=True)
        row.update(lanes=lanes,end=end,lane_ends=lane_ends,recent=(row['recent']+[lanes[-1]])[-5:])
        old=rt.controllers.get(key);distance=old.distance if old and hasattr(old,'distance') else 0.
        prior=rt.journey_paths.pop(key,None)
        if prior:
            rt.path_geometries.pop(id(prior),None);rt.junction_coordinator._path_geometry.pop(id(prior),None)
        controller=CycleFollower(path,rt.missions[key]['cruise_speed_m_s'],closed=False);controller.distance=distance;controller.last_s=progress
        rt.journey_paths[key]=path;rt.path_geometries[id(path)]=controller.geometry;rt.controllers[key]=controller
        rt.missions[key]['goal_scenario_m']=path[-1];rt.missions[key]['patrol_goals_scenario_m']=[path[-1]]

    def entry(self, actors):
        # Balance independent entry flows, subject to actual current safe space.
        for lane in sorted(self.network.entries,key=lambda k:(self.entry_uses[k],k)):
            geom=self.network.geometries[lane];p=list(geom.point_at(6.));h=geom.project(p)[3]
            body=vehicle_footprint(p,h)
            safe=True
            for actor in actors:
                if math.dist(p,actor['position_city_m'])>50:continue
                other=vehicle_footprint(actor['position_city_m'],actor['heading_rad'],actor['length_m'],actor['width_m'],margin=.3)
                if body.distance(other)<max(6.,actor['speed_m_s']*3.):safe=False;break
            if safe:return lane,p,h
        return None

    def update(self, actors, time_s):
        rt=self.runtime;cars={a['vehicle_id']:a for a in actors if a['vehicle_id'] in self.rows}
        self.waiting_entries=set();changed=False
        for key,row in self.rows.items():
            if rt.modes[key]!='ai' or key in rt.lane_changes.active:continue
            own=cars[key];controller=rt.controllers[key];geom=controller.geometry
            lateral,progress,_,heading=controller.project_pose(own['position_city_m'])
            if lateral>2.5 or abs(_angle(own['heading_rad']-heading))>math.pi/2:continue
            # Record source lanes actually reached, not merely intended routes.
            current=row['lanes'][min(len(row['lanes'])-1,bisect_right(row['lane_ends'],progress))]
            self.visited.add(current)
            if row.get('current')!=current:
                if row.get('current') and current in self.network.graph[row['current']]:self.transitions.add((row['current'],current))
                row['current']=current
            goal=row['lanes'][-1]
            if geom.length-progress>2.:continue
            if goal in self.network.exits:
                # End only at a verified source cut. External-owned cars never recycle.
                if own['speed_m_s']>.7:continue
                if rt.population and key in rt.population.incidents.active:continue
                placement=self.entry(rt.actors())
                if not placement:self.waiting_entries.add(key);continue
                lane,p,h=placement;old_instance=self.instance_id(key)
                rt.replace_background_vehicle(key,p,h,row['generation']+1);changed=True
                row['generation']+=1;row['recent']=[];row.pop('current',None)
                row['rng']=random.Random(f"{rt.traffic['seed']}:{key}:journeys:{row['generation']}")
                self.entry_uses[lane]+=1;self.exited+=1;self.entered+=1;self.completed+=1;row['goals']+=1
                self.lifecycle.append(dict(time_s=time_s,slot=key,exited_instance=old_instance,entered_instance=self.instance_id(key),exit_lane=goal,entry_lane=lane,synthetic=True))
                self.lifecycle=self.lifecycle[-128:]
                self.assign(key,lane,6.,rt.actors())
            else:
                self.completed+=1;row['goals']+=1
                self.assign(key,goal,self.network.geometries[goal].project(own['position_city_m'])[1],actors)

        return changed

    def instance_id(self,key):return f"traffic-{key}-g{self.rows[key]['generation']}"

    def report(self):
        return dict(mode='independent_reachable_destinations',completed_trips=self.completed,boundary_exits=self.exited,
                    boundary_entries=self.entered,waiting_for_safe_entry=list(self.waiting_entries),
                    visited_source_lanes=len(self.visited),distinct_source_transitions=len(self.transitions),
                    entry_count=len(self.network.entries),exit_count=len(self.network.exits),
                    lifecycle=deepcopy(self.lifecycle),synthetic=True,trained_model=False)
