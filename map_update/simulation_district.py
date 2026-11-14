"""Wider source-map traffic coverage; no invented roads or trained behavior claim."""
import math
import random
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from .simulation_interactive_scenarios import vehicle_footprint
from .simulation_planner import _angle
from .simulation_path_geometry import PathGeometry

def select_routes(candidates, limit=8):
    """Cover new source lanes before adding similar cycles; retain the source ego area."""
    routes=[candidates[0]];used=set(routes[0]['lanes']);remaining=candidates[1:]
    while remaining and len(routes)<limit:
        best=max(remaining,key=lambda r:(len(set(r['lanes'])-used),r['distance'],-r['length']))
        if not set(best['lanes'])-used:break
        routes.append(best);used.update(best['lanes']);remaining.remove(best)
    return routes


def coverage(routes, points):
    lanes=sorted({k for r in routes for k in r['lanes']})
    geometry=unary_union([LineString(points[k]) for k in lanes])
    return dict(unique_lane_length_m=sum(LineString(points[k]).length for k in lanes),
                bounds_city_m=list(geometry.bounds),source_lane_count=len(lanes))


def distributed_construction(routes, occupied, options, junctions, features):
    """Sample count once, then spread actual fixed bodies across all selected roads."""
    requested=random.Random(f"{options['seed']}:construction:count").randint(options['construction_count_min'],options['construction_count_max'])
    rng=random.Random(f"{options['seed']}:district:construction")
    from shapely.geometry import Polygon
    crossings=[Polygon(f['polygon']).buffer(2.) for f in features.values() if f.get('type')=='CROSSWALK']
    candidates=[]
    for route in routes:
        geom=PathGeometry(route['path']);positions=list(range(0,int(geom.length),3));rng.shuffle(positions)
        candidates.append([(geom,s) for s in positions])
    # Round-robin routes avoids exhausting the first route before touching others.
    ordered=[row[i] for i in range(max(map(len,candidates))) for row in candidates if i<len(row)]
    specs=[];shapes=[]
    for geom,s in ordered:
        pos=list(geom.point_at(s));heading=geom.project(pos)[3]
        if any(abs(_angle(geom.project(geom.point_at((s+d)%geom.length))[3]-heading))>.35 for d in (-4,4)):continue
        if any(math.dist(pos,j['position_city_m'])<j['radius_m']+8 for j in junctions):continue
        shape=vehicle_footprint(pos,heading,1.,2.5,margin=.5)
        if any(shape.intersects(c) for c in crossings):continue
        if any(shape.distance(o)<4 for o in occupied) or any(shape.distance(o)<20 for o in shapes):continue
        index=len(specs)+1
        specs.append(dict(object_id=f'construction-C{index}',event_id=f'construction-{index}',type='TRAFFIC_BARRIER',
            position_city_m=pos,heading_rad=heading,length_m=1.,width_m=2.5,height_m=1.,synthetic=True))
        shapes.append(shape)
        if len(specs)>=requested:break
    return specs,dict(requested_count=requested,placed_count=len(specs),capacity_limited=len(specs)<requested,
        candidate_scope='all_selected_source_routes',reason='insufficient_safe_initial_sites' if len(specs)<requested else None,synthetic=True)


class BackgroundActivity:
    """Private seeded cruise variation and brief stops for background AI only."""
    def __init__(self, seed, identifiers):
        self.rows={};self.started=0;self.completed=0
        for key in identifiers:
            rng=random.Random(f'{seed}:{key}:activities')
            self.rows[key]=dict(rng=rng,factor=rng.uniform(.7,1.15),speed_at=rng.uniform(15,45),
                                stop_at=rng.uniform(25,100),until=None)

    def advance(self, time_s, cars, modes, junctions, crossings):
        for key,row in self.rows.items():
            rng=row['rng'];car=cars[key]
            if modes.get(key)!='ai':
                if row['until'] is not None:self.completed+=1
                row['until']=None;row['stop_at']=time_s+rng.uniform(45,120)
                continue
            if time_s>=row['speed_at']:
                row['factor']=rng.uniform(.65,1.15);row['speed_at']=time_s+rng.uniform(20,50)
            if row['until'] is not None:
                if time_s>=row['until']:
                    row['until']=None;self.completed+=1;row['stop_at']=time_s+rng.uniform(60,150)
            elif time_s>=row['stop_at']:
                point=Point(car['position_city_m'])
                safe=all(point.distance(Point(j['position_city_m']))>j['radius_m']+15 for j in junctions)
                safe=safe and all(point.distance(c)>10 for c in crossings)
                if safe and car['speed_m_s']>.5:
                    row['until']=time_s+rng.uniform(3,9);self.started+=1
                else:row['stop_at']=time_s+5

    def factor(self, key):return self.rows.get(key,{}).get('factor',1.)
    def stopping(self, key):return self.rows.get(key,{}).get('until') is not None
