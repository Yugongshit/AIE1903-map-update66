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


def junction_sites(routes, points, graph, *, geometric_conflicts=False):
    used={lane for r in routes for lane in r['lanes']}
    incoming={lane:[] for lane in used}
    for lane in used:
        for target in graph[lane]:
            if target in used:incoming[target].append(lane)
    positions=[]
    for lane in sorted(used):
        if len(incoming[lane])>1:positions.append(points[lane][0])
        if len([t for t in graph[lane] if t in used])>1:positions.append(points[lane][-1])
    if geometric_conflicts:
        # Crossing connectors need a shared reservation even when their graph
        # nodes are distinct. Topological split/merge points alone leave the
        # middle of a large junction unprotected (opposing turns can deadlock).
        import shapely
        keys = sorted(used)
        lines = [LineString(points[k]) for k in keys]
        tree = shapely.STRtree(lines)
        for i, line in enumerate(lines):
            for j in sorted(tree.query(line, predicate='intersects')):
                if j <= i:
                    continue
                overlap = line.intersection(lines[j])
                hits = [overlap] if overlap.geom_type == 'Point' else list(overlap.geoms) if overlap.geom_type == 'MultiPoint' else []
                for hit in hits:
                    p = list(hit.coords[0])
                    # An ordinary end-to-start connection is not a conflict.
                    if all(min(math.dist(p, points[key][end]) for end in (0, -1)) < .25 for key in (keys[i], keys[j])):
                        continue
                    positions.append(p)
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
        if geometric_conflicts:result[-1]['boundary_city_m']=[list(p) for p in shapely.MultiPoint(group).convex_hull.buffer(6.).exterior.coords]
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
    from .simulation_district import density_spawns, coverage
    area=coverage(routes,points)
    requested=(max(1,min(64,round(options['density_veh_km']*area['unique_lane_length_m']/1000)))
               if options.get('density_veh_km') else options['vehicle_count'])
    # Counts passed to the native worker are the actual validated mission count.
    options=dict(options,vehicle_count=requested)
    if options.get('density_veh_km'):
        placements,lane_counts=density_spawns(routes,points,occupied,junctions,requested,options['seed'])
    else:
        placements=[];lane_counts={};legacy_speeds=[]
        rng=random.Random(f"{options['seed']}:network:spawns")
        for index in range(requested):
            route_index=index%len(routes);route=routes[route_index];path=route['path']
            starts=[s*3. for s in range(int(route['length']/3))];rng.shuffle(starts)
            for progress in starts:
                pos=list(_point_at(path,progress));heading=_project(pos,path)[3]
                shape=vehicle_footprint(pos,heading)
                if any(shape.distance(o)<7. for o in occupied):continue
                if any(math.dist(pos,j['position_city_m'])<j['radius_m']+8. for j in junctions):continue
                break
            else:raise ValueError('Not enough safe multi-route spawn space; lower vehicle count or use district lane density')
            occupied.append(shape);placements.append((route_index,progress,pos,heading))
            legacy_speeds.append(max(.5,min(3.,options['speed_m_s']*(1+rng.uniform(-options['speed_spread'],options['speed_spread'])))))
    rng=random.Random(f"{options['seed']}:network:speeds")
    missions=[]
    for index,(route_index,progress,pos,heading) in enumerate(placements):
        route=routes[route_index];identifier=f'P{index+1}'
        goal=list(_point_at(route['path'],(progress+route['length']/2)%route['length']))
        missions.append(dict(vehicle_id=identifier,position_scenario_m=pos,heading_rad=heading,
            goal_scenario_m=goal,patrol_goals_scenario_m=[goal,pos],patrol_loop=True,
            cruise_speed_m_s=(max(.5,min(3.,options['speed_m_s']*(1+rng.uniform(-options['speed_spread'],options['speed_spread']))))
                              if options.get('density_veh_km') else legacy_speeds[index]),
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
        traffic_area=dict(**area, requested_density_veh_km=options.get('density_veh_km',0), actual_density_veh_km=requested*1000/area['unique_lane_length_m'], initial_lane_counts=lane_counts, spawned_vehicle_count=requested, network_extent=options.get('network_extent','local'),selection='source_network',source_ego_unchanged=True,route_count=len(routes),
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


def merge_junction_regions(sites):
    """Merge envelopes too close to hold a vehicle safely between reservations.

    Keep a polygon for entry/exit tests; a large enclosing circle can capture an
    unrelated road or parked ego and block the whole intersection forever.
    """
    from shapely.ops import unary_union
    groups = [((Polygon(z['boundary_city_m']) if z.get('boundary_city_m') else Point(z['position_city_m']).buffer(z['radius_m'])), [z['junction_id']]) for z in sites]
    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            # Passenger-car body plus the outlet gap and next stop-line margin.
            matches = [j for j in range(i+1, len(groups)) if groups[i][0].distance(groups[j][0]) < 9.]
            if not matches:
                continue
            members = [groups[i], *[groups[j] for j in matches]]
            groups[i] = (unary_union([g for g, _ in members]).convex_hull, [k for _, ids in members for k in ids])
            for j in reversed(matches): groups.pop(j)
            changed = True
            break
    result = []
    for polygon, ids in groups:
        center = list(polygon.centroid.coords[0])
        result.append(dict(junction_id=ids[0], position_city_m=center,
            radius_m=max(math.dist(center, p) for p in polygon.exterior.coords),
            boundary_city_m=[list(p) for p in polygon.exterior.coords], merged_junction_ids=ids,
            rule='synthetic_unsignalised_conflict_reservation'))
    return result


class JunctionCoordinator:
    """Background reservations over source conflict regions, with actual entry lines.

    This is a synthetic traffic rule, not inferred traffic lights or ego Planning.
    Present external bodies block only paths they physically intersect.
    """
    def __init__(self, sites):
        self.sites=sites;self.holders={};self.waiting={};self.passes=0;self.yield_ticks=0
        self._path_geometry={};self.blockers={};self.reservations={}
        self.regions={z['junction_id']: Polygon(z['boundary_city_m']) if z.get('boundary_city_m')
                      else Point(z['position_city_m']).buffer(z['radius_m']) for z in sites}

    def release_actor(self, identifier):
        """A reused control slot must not inherit its retired actor's grant."""
        for key, reservations in self.reservations.items():
            reservations.pop(identifier,None)
            if self.holders.get(key,{}).get('vehicle_id')==identifier:
                self.holders.pop(key,None)
        self.waiting={pair:stamp for pair,stamp in self.waiting.items() if pair[1]!=identifier}
        self.blockers.pop(identifier,None)

    def update(self, actors, paths, time_s, *, own_projections=None):
        from shapely.ops import substring
        from .simulation_path_geometry import PathGeometry
        cars={a['vehicle_id']:a for a in actors if a.get('type')=='VEHICLE'}
        bodies={k:vehicle_footprint(a['position_city_m'],a['heading_rad'],a['length_m'],a.get('width_m',2.),margin=.3) for k,a in cars.items()}
        stopped={};self.blockers={};projections={}
        own_projections=own_projections or {}
        def project(identifier,path):
            key=(identifier,id(path))
            if key not in projections:
                projections[key]=own_projections[identifier] if identifier in own_projections and paths.get(identifier) is path else self._path_geometry[id(path)][2].project(cars[identifier]['position_city_m'])
            return projections[key]
        for path in paths.values():
            if id(path) in self._path_geometry:continue
            geom=PathGeometry(path);line=LineString(path);targets={}
            for key,region in self.regions.items():
                intervals=geom.region_intervals(region)
                if math.dist(path[0],path[-1])<.01 and len(intervals)>1 and intervals[0][0]<.01 and intervals[-1][1]>geom.length-.01:
                    first,last=intervals[0],intervals[-1]
                    intervals=[(last[0]-geom.length,first[1]),*intervals[1:-1],(last[0],geom.length+first[1])]
                targets[key]=intervals
            self._path_geometry[id(path)]=(geom.length,targets,geom,line)
        for zone in self.sites:
            key=zone['junction_id'];center=zone['position_city_m'];radius=zone['radius_m'];region=self.regions[key]
            inside={k for k,a in cars.items() if math.dist(a['position_city_m'],center)<radius+a['length_m'] and bodies[k].intersects(region)}
            approaches={};corridors={};movements={};exit_spaces={};exit_allowance={};exit_tails={};terminal_outlets=set()
            for identifier,path in paths.items():
                own=cars[identifier]
                if math.dist(own['position_city_m'],center)>=radius+24.:continue
                length,targets,geom,line=self._path_geometry[id(path)]
                _,progress,_,_=project(identifier,path)
                intervals=targets[key]
                # A car past the region's exit is departing, not approaching.
                # Rear clearance belongs to the physical occupancy/release test.
                upcoming=[(a,b) for a,b in intervals if b>progress]
                if not upcoming and intervals and math.dist(path[0],path[-1])<.01:
                    upcoming=[(a+length,b+length) for a,b in intervals]
                if not upcoming:continue
                entry,leave=upcoming[0];ahead=entry-progress
                if ahead>=20.:continue
                approaches[identifier]=ahead
                self.waiting.setdefault((key,identifier),time_s)
                # Include downstream clearing space, using current geometry only.
                closed=math.dist(path[0],path[-1])<.01
                end=leave+own['length_m']+2.
                begin=max(0.,min(progress,entry)-own['length_m']/2)
                pieces=[substring(line,begin,min(length,end))]
                if closed and end>length:pieces.append(substring(line,0.,min(length,end-length)))
                from shapely.ops import unary_union
                corridors[identifier]=unary_union(pieces).buffer(own.get('width_m',2.)/2+.4)
                exit_begin=max(0.,leave-own['length_m']/2)
                exit_pieces=[substring(line,min(length,exit_begin),min(length,end))]
                if closed and end>length:
                    exit_pieces.append(substring(line,max(0.,exit_begin-length),min(length,end-length)))
                exit_spaces[identifier]=unary_union(exit_pieces).buffer(own.get('width_m',2.)/2+.3)
                if not closed and end>length:
                    # A destination marks the centre, not the front of the car.
                    goal_body=vehicle_footprint(path[-1],geom.heading_at(length),own['length_m'],own.get('width_m',2.),margin=.3)
                    exit_spaces[identifier]=exit_spaces[identifier].union(goal_body)
                at=lambda value:geom.point_at(value%length if closed else value)
                heading_at=lambda value:geom.heading_at(value%length if closed else value)
                movements[identifier]=(at(entry),at(leave),heading_at(entry),heading_at(leave))
                # Reserve finite storage between this exit and the next stop
                # line. An empty outlet is not a separate free slot for every
                # car that is still traversing the junction.
                if not closed and leave<length-.01:
                    next_entries=[a for other,spans in targets.items() if other!=key
                                  for a,b in spans if a>leave+.01]
                    limit=min(length,leave+50.,min(next_entries)-2. if next_entries else length)
                    outlet=substring(line,leave,max(leave,limit)).buffer(own.get('width_m',2.)/2+.3)
                    tails=[]
                    for other in cars:
                        if other==identifier or not outlet.intersects(bodies[other]):continue
                        lateral,s,_,heading=geom.project_near(cars[other]['position_city_m'],leave,window=50.)
                        if s>leave and abs(_angle(cars[other]['heading_rad']-heading))<.6:
                            tails.append((s-cars[other]['length_m']/2,other))
                    if tails:limit=min(limit,min(tails)[0])
                    exit_allowance[identifier]=max(0.,limit-leave)
                    exit_tails[identifier]={v for s,v in tails if s<=limit+.01}
                    # A short terminal journey is not a physically truncated
                    # road. Permit one terminal movement with actual body
                    # clearance; it must clear before another gets that slot.
                    if not next_entries and length-leave<own['length_m']+2. and not tails:
                        terminal_outlets.add(identifier)
            reservations=self.reservations.setdefault(key,{})
            for identifier,holder in list(reservations.items()):
                if identifier in inside:holder['entered']=True
                if identifier not in cars or (holder['entered'] and identifier not in inside) or (not holder['entered'] and time_s-holder['since']>20.):
                    self.passes+=int(holder['entered']);reservations.pop(identifier)
            def same_stream(identifier, holder):
                # Same entry/exit stream can follow under IDM; independent
                # streams can proceed only when their swept regions are disjoint.
                a,b=movements[identifier],holder['movement']
                return all(math.dist(a[i],b[i])<1. for i in (0,1)) and all(abs(_angle(a[i]-b[i]))<.2 for i in (2,3))
            def compatible(identifier, holder):
                return same_stream(identifier,holder) or not corridors[identifier].intersects(holder['corridor'])
            def same_outlet(identifier, holder):
                a,b=movements[identifier],holder['movement']
                return math.dist(a[1],b[1])<1. and abs(_angle(a[3]-b[3]))<.2
            occupancy={k:{v for v in inside if v!=k and corridors[k].intersects(bodies[v])
                          and not (v in reservations and cars[v]['speed_m_s']>.3 and same_stream(k,reservations[v]))} for k in approaches}
            # Do not admit a car into a junction with no downstream space for
            # its body. Otherwise a stopped exit queue can spill into crossing
            # streams and form a network-wide wait cycle. Current bodies only.
            exit_blocked={k:{v for v in cars if v!=k and cars[v]['speed_m_s']<.3
                            and exit_spaces[k].intersects(bodies[v])} for k in approaches}
            for identifier in approaches:occupancy[identifier].update(exit_blocked[identifier])
            heads=[]
            for identifier,ahead in approaches.items():
                if identifier in inside or occupancy[identifier]:continue
                path=paths[identifier];progress=project(identifier,path)[1];length=self._path_geometry[id(path)][0]
                blocked=False
                for other in approaches:
                    if other==identifier:continue
                    lat,s,_,heading=project(other,path);delta=s-progress
                    if math.dist(path[0],path[-1])<.01:delta%=length
                    if lat<1.5 and abs(_angle(cars[other]['heading_rad']-heading))<.6 and 0<delta<ahead:
                        blocked=True;break
                if not blocked:heads.append(identifier)
            # An unentered grant cannot survive a new queue head ahead of it.
            # Otherwise the follower waits for its leader while that leader
            # yields to the follower's stale reservation.
            for identifier,holder in list(reservations.items()):
                if not holder['entered'] and identifier not in heads:
                    reservations.pop(identifier)
            capacity_blocked={}
            for identifier in sorted(heads,key=lambda k:(self.waiting[(key,k)],approaches[k],k)):
                if identifier in reservations and reservations[identifier]['entered']:continue
                if identifier in exit_allowance:
                    reserved={k for k,h in reservations.items() if k!=identifier and same_outlet(identifier,h)}
                    remaining=exit_allowance[identifier]-sum(cars[k]['length_m']+2. for k in reserved)
                    limited=bool(reserved) if identifier in terminal_outlets else remaining<cars[identifier]['length_m']+2.
                    if limited:
                        reservations.pop(identifier,None)
                        capacity_blocked[identifier]=reserved|exit_tails[identifier]
                        continue
                if identifier in reservations:continue
                if all(compatible(identifier,holder) for holder in reservations.values()):
                    reservations[identifier]=dict(vehicle_id=identifier,since=time_s,entered=False,
                        corridor=corridors[identifier],movement=movements[identifier])
            if reservations:self.holders[key]=min(reservations.values(),key=lambda h:(h['since'],h['vehicle_id']))
            else:self.holders.pop(key,None)
            for identifier,ahead in approaches.items():
                own=cars[identifier];speed=max(0.,own.get('speed_m_s',0.))
                stop_distance=own['length_m']/2+2.+speed+speed*speed/(2*1.4)
                if identifier not in inside and ahead<=stop_distance and identifier not in reservations:
                    stopped[identifier]=key;self.yield_ticks+=1
                    waiting=occupancy[identifier] | {k for k,h in reservations.items() if not compatible(identifier,h)}
                    waiting.update(capacity_blocked.get(identifier,()))
                    self.blockers[identifier]=dict(junction_id=key,waiting_for=sorted(waiting),
                        exit_blocked_by=sorted(exit_blocked[identifier]),exit_capacity_limited=identifier in capacity_blocked,
                        exit_available_m=exit_allowance.get(identifier),terminal_destination=identifier in terminal_outlets)
            for pair in list(self.waiting):
                if pair[0]==key and pair[1] not in approaches:self.waiting.pop(pair)
        return stopped
