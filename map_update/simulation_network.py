"""Source-map background routes and conservative unsignalised junctions.

This is a simulation traffic baseline, never an ego Planning implementation.
"""
import math
import random
from copy import deepcopy
from shapely.geometry import LineString, Point, Polygon
from .simulation_interactive_scenarios import _graph, _cycle, vehicle_footprint
from .simulation_planner import _length, _point_at, _project, _angle


def route_path(route, features):
    """Expand source lane references in the worker; avoid repeated wire geometry."""
    if 'path' in route:return route['path']
    path=[]
    for lane in route['lanes']:
        for p in features[lane]['polyline']:
            point=list(p[:2])
            if not path or point!=path[-1]:path.append(point)
    if path[-1]!=path[0]:path.append(path[0])
    return path


def source_cycles(payload, expanded=False):
    points, graph = _graph(payload['maps']['updated'])
    ego = payload['tracks'][payload['ego_track_id']]
    occupied = [vehicle_footprint(ego['position'][0], ego['heading'][0])]
    occupied += [vehicle_footprint(a['position'],a['heading'],a['length'],a['width'],margin=0.)
                 for a in payload.get('static_overview_actors',[])]
    seen, candidates = set(), []
    for lane in sorted(graph):
        cycle = _cycle(lane,points,graph)
        if not cycle or tuple(sorted(cycle[:-1])) in seen:
            continue
        seen.add(tuple(sorted(cycle[:-1])))
        path=[]
        for key in cycle[:-1]:
            for p in points[key]:
                if not path or p!=path[-1]:path.append(p)
        if path[-1]!=path[0]:path.append(path[0])
        line=LineString(path)
        if 200<=line.length<=(1400 if expanded else 800) and line.is_simple and min(line.distance(o) for o in occupied)>=(2.1 if expanded else 4.):
            candidates.append(dict(lanes=cycle[:-1],path=path,length=line.length,
                distance=line.distance(Point(ego['position'][0][:2]))))
    return sorted(candidates,key=lambda c:(c['distance'],c['length'],c['lanes'])),occupied,points,graph


def junction_sites(routes, points, graph):
    used={lane for r in routes for lane in r['lanes']}
    incoming={lane:[] for lane in used}
    for lane in used:
        for target in graph[lane]:
            if target in used:incoming[target].append(lane)
    positions=[]
    for lane in sorted(used):
        if len(incoming[lane])>1:positions.append(points[lane][0])
        if len([t for t in graph[lane] if t in used])>1:positions.append(points[lane][-1])
    # Overlapping approach envelopes are one conservative reservation zone.
    groups=[]
    for p in positions:
        matching=[g for g in groups if any(math.dist(p,q)<14. for q in g)]
        merged=[p]
        for g in matching:merged.extend(g);groups.remove(g)
        groups.append(merged)
    result=[]
    for group in groups:
        center=[sum(p[i] for p in group)/len(group) for i in (0,1)]
        radius=6.+max(math.dist(center,p) for p in group)
        result.append(dict(junction_id=f'junction-{len(result)+1}',position_city_m=center,radius_m=radius,
                           rule='synthetic_unsignalised_exclusive_reservation'))
    return result


def network_traffic(payload, options):
    from .simulation_following import driver_parameters
    from .simulation_events import construction_specs
    expanded=options.get('network_extent')=='district'
    candidates,occupied,points,graph=source_cycles(payload,expanded)
    if not candidates:raise ValueError('No safe source road routes')
    routes=[candidates[0]]
    for candidate in candidates[1:]:
        if any(set(candidate['lanes'])&set(r['lanes']) for r in routes):
            routes.append(candidate)
        if len(routes)>=3:break
    if expanded:
        from .simulation_district import select_routes
        routes=select_routes(candidates)
    if len(routes)<2:raise ValueError('Multi-route traffic requires at least two connected safe source cycles')
    junctions=junction_sites(routes,points,graph)
    rng=random.Random(f"{options['seed']}:network:spawns")
    missions=[]
    for index in range(options['vehicle_count']):
        route_index=index%len(routes)
        route=routes[route_index];path=route['path']
        starts=[s*3. for s in range(int(route['length']/3))];rng.shuffle(starts)
        for progress in starts:
            pos=list(_point_at(path,progress));heading=_project(pos,path)[3]
            shape=vehicle_footprint(pos,heading)
            if any(shape.distance(o)<7. for o in occupied):continue
            if any(math.dist(pos,j['position_city_m'])<j['radius_m']+8. for j in junctions):continue
            break
        else:raise ValueError('Not enough safe multi-route spawn space; lower vehicle count')
        occupied.append(shape)
        identifier=f'P{index+1}'
        goal=list(_point_at(path,(progress+route['length']/2)%route['length']))
        missions.append(dict(vehicle_id=identifier,position_scenario_m=pos,heading_rad=heading,
            goal_scenario_m=goal,patrol_goals_scenario_m=[goal,pos],patrol_loop=True,
            cruise_speed_m_s=max(.5,min(3.,options['speed_m_s']*(1+rng.uniform(-options['speed_spread'],options['speed_spread'])))),
            cycle_follower=True,route_id=f'route-{route_index+1}',
            source_route_length_m=route['length'],synthetic_actor=True,
            initial_clearance_checked=True,mission_origin='synthetic_multi_source_cycles',uses_recorded_trajectory=False,
            driver_parameters=driver_parameters(options['seed'],identifier,options['driver_style'])))
    specs,generation=construction_specs(routes[0]['path'],missions,occupied,options) if options['construction'] and not expanded else ([],None)
    if expanded and options['construction']:
        from .simulation_district import distributed_construction
        specs,generation=distributed_construction(routes,occupied,options,junctions,payload['maps']['updated'])
    from .simulation_district import coverage
    return dict(missions=missions,cyclic_path=None if expanded else routes[0]['path'],routes=[{k:v for k,v in r.items() if k!='path'} for r in routes],junctions=junctions,
        constructions=specs,construction=deepcopy(specs[0]) if specs else None,construction_generation=generation,
        traffic=dict(options,profile='synthetic_network_v1',synthetic=True),
        traffic_area=dict(**coverage(routes,points), network_extent=options.get('network_extent','local'),selection='source_network',source_ego_unchanged=True,route_count=len(routes),
            route_length_m=sum(r['length'] for r in routes),route_distance_to_recorded_ego_m=routes[0]['distance'],
            route_lengths_m=[r['length'] for r in routes],source_scope='connected_source_cycles_not_arbitrary_city_OD'))


def crossing_sites(features, paths, occupied, count, seed):
    """Use long axes of real crosswalk polygons; do not invent sidewalks."""
    candidates=[]
    for key,f in sorted(features.items()):
        if f.get('type')!='CROSSWALK' or not f.get('polygon'):continue
        poly=Polygon([p[:2] for p in f['polygon']])
        if not poly.is_valid or poly.area<1.:continue
        if not any(poly.intersects(LineString(path)) for path in paths):continue
        rect=list(poly.minimum_rotated_rectangle.exterior.coords)
        edges=[(math.dist(rect[i],rect[i+1]),rect[i],rect[i+1]) for i in range(4)]
        _,a,b=max(edges,key=lambda e:e[0]);direction=[b[i]-a[i] for i in (0,1)]
        size=math.hypot(*direction);direction=[v/size for v in direction]
        center=list(poly.centroid.coords)[0]
        axis=LineString([[center[i]-direction[i]*100 for i in (0,1)], [center[i]+direction[i]*100 for i in (0,1)]])
        segment=poly.intersection(axis)
        if segment.geom_type!='LineString' or not 4.<segment.length<40.:continue
        ends=[list(segment.coords[0]),list(segment.coords[-1])]
        corridor=LineString(ends).buffer(.7)
        if any(corridor.intersects(o) for o in occupied):continue
        candidates.append(dict(source_crosswalk_id=key,endpoints=ends))
    random.Random(f'{seed}:pedestrian:sites').shuffle(candidates)
    selected=[]
    for candidate in candidates:
        line=LineString(candidate['endpoints'])
        if any(line.distance(LineString(c['endpoints']))<1.5 for c in selected):continue
        selected.append(candidate)
        if len(selected)>=count:break
    return [dict(c,pedestrian_id=f'walker-W{i+1}') for i,c in enumerate(selected)]


class JunctionCoordinator:
    """One approach head at a time, using present positions and source routes.

    No signal timings are inferred from the dataset. External cars are physical
    occupants but receive no commands from this background traffic controller.
    """
    def __init__(self, sites):
        self.sites=sites;self.holders={};self.waiting={};self.passes=0;self.yield_ticks=0
        self._path_geometry={}

    def update(self, actors, paths, time_s):
        cars={a['vehicle_id']:a for a in actors if a.get('type')=='VEHICLE'}
        stopped={}
        projections={}
        def project(identifier,path):
            key=(identifier,id(path))
            if key not in projections:projections[key]=self._path_geometry[id(path)][2].project(cars[identifier]['position_city_m'])
            return projections[key]
        for path in paths.values():
            if id(path) not in self._path_geometry:
                from .simulation_path_geometry import PathGeometry
                geom=PathGeometry(path)
                self._path_geometry[id(path)]=(geom.length,{z['junction_id']:geom.project(z['position_city_m']) for z in self.sites},geom)
        for zone in self.sites:
            key=zone['junction_id'];center=zone['position_city_m'];radius=zone['radius_m']
            inside={k for k,a in cars.items() if math.dist(a['position_city_m'],center)<radius+a['length_m']/2+.5}
            approaches={}
            for identifier,path in paths.items():
                own=cars[identifier]
                if math.dist(own['position_city_m'],center)>=radius+24.:continue
                length,targets,_=self._path_geometry[id(path)];lat,target,_,_=targets[key]
                _,progress,_,_=project(identifier,path)
                ahead=(target-progress)%length
                if lat<=radius and ahead<radius+20. and math.dist(own['position_city_m'],center)<radius+24.:
                    approaches[identifier]=ahead
                    self.waiting.setdefault((key,identifier),time_s)
            holder=self.holders.get(key)
            if holder:
                identifier=holder['vehicle_id']
                if identifier in inside:holder['entered']=True
                if identifier not in cars or (holder['entered'] and identifier not in inside):
                    self.passes+=int(holder['entered']);self.holders.pop(key);holder=None
                elif not holder['entered'] and time_s-holder['since']>20.:
                    self.holders.pop(key);holder=None
            heads=[]
            for identifier,ahead in approaches.items():
                own=cars[identifier];path=paths[identifier];progress=project(identifier,path)[1]
                length=self._path_geometry[id(path)][0]
                blocked=False
                for other in approaches:
                    if other==identifier:continue
                    lat,s,_,heading=project(other,path)
                    delta=(s-progress)%length
                    if lat<1.5 and abs(_angle(cars[other]['heading_rad']-heading))<.6 and 0<delta<ahead:
                        blocked=True;break
                if not blocked:heads.append(identifier)
            if not holder and not inside and heads:
                identifier=min(heads,key=lambda k:(self.waiting[(key,k)],approaches[k],k))
                holder=dict(vehicle_id=identifier,since=time_s,entered=False);self.holders[key]=holder
            for identifier,ahead in approaches.items():
                if identifier not in inside and (not holder or holder['vehicle_id']!=identifier):
                    stopped[identifier]=key;self.yield_ticks+=1
            for pair in list(self.waiting):
                if pair[0]==key and pair[1] not in approaches:self.waiting.pop(pair)
        return stopped
