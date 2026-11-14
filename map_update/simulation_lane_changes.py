"""Conservative low-speed passing on verified source dashed boundaries.

Background policy only. Source neighbours are recovered from shared annotated
segments, never from nearby centre lines alone. The cropped graph cannot close
routes from the adjacent through lanes, so a pass returns before the lane end.
This is an IDM incentive/gap baseline, not a calibrated full MOBIL model.
"""
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
import json
import math

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
from .simulation_path_geometry import PathGeometry
from .simulation_planner import _angle
from .simulation_interactive_scenarios import vehicle_footprint
from .simulation_following import current_leader, idm_acceleration, project_actors


@lru_cache(maxsize=2)
def _raw_map(filename):
    return json.loads(Path(filename).read_text())


def source_neighbours(features, source_file=None):
    source_file=source_file or Path(__file__).resolve().parents[1]/'AIE1903 原始资料/maps/expansion/singapore-onenorth.json'
    raw=_raw_map(str(source_file));nodes={n['token']:(n['x'],n['y']) for n in raw['node']}
    polygons={p['token']:p for p in raw['polygon']};groups=defaultdict(list)
    for lane in raw['lane']:
        key=lane['token']
        if key not in features or lane['lane_type']!='CAR':continue
        # Never apply annotations to a different or modified physical geometry.
        poly=polygons[lane['polygon_token']]
        source=Polygon([nodes[n] for n in poly['exterior_node_tokens']])
        if not features[key].get('polygon') or source.hausdorff_distance(Polygon(features[key]['polygon']))>.01:continue
        for side in ('left','right'):
            seq=lane[side+'_lane_divider_segments']
            for a,b in zip(seq,seq[1:]):
                if a['node_token']==b['node_token']:continue
                if a['segment_type']!='DOUBLE_DASHED_WHITE':continue
                groups[tuple(sorted((a['node_token'],b['node_token'])))].append((key,side))
    shared=defaultdict(list)
    for edge,rows in groups.items():
        for a,side in rows:
            for b,other_side in rows:
                if a<b and side!=other_side:shared[a,b].append(LineString([nodes[n] for n in edge]))
    result=[]
    for (a,b),segments in sorted(shared.items()):
        ga=PathGeometry(features[a]['polyline']);gb=PathGeometry(features[b]['polyline'])
        if min(ga.length,gb.length)<55:continue
        p=ga.point_at(ga.length/2);lat,_,_,heading=gb.project(p)
        if not 2.<lat<5. or abs(_angle(ga.project(p)[3]-heading))>.2:continue
        surface=unary_union([Polygon(features[x]['polygon']) for x in (a,b)])
        dashed=unary_union(segments)
        for src,dst,gs,gt in ((a,b,ga,gb),(b,a,gb,ga)):
            result.append(dict(source=src,target=dst,source_geometry=gs,target_geometry=gt,
                               surface=surface,dashed=dashed))
    return result


def passing_path(neighbour, start_s, base_path):
    """Two quintic lane changes with a 10–26 m target-lane segment; body stays on road."""
    src=neighbour['source_geometry'];dst=neighbour['target_geometry']
    distance=min(60.,src.length-6-start_s);change=17.
    if start_s<6 or distance<44:return None
    path=[]
    for i in range(math.ceil(distance*2)+1):
        d=min(distance,i*.5);p=src.point_at(start_s+d);lat,ts,q,heading=dst.project(p)
        if not 2.<lat<5. or ts<4 or ts>dst.length-4 or abs(_angle(src.project(p)[3]-heading))>.2:return None
        # Every lateral crossing must lie on the proven dashed boundary.
        section=LineString([p,q])
        if not section.intersects(neighbour['dashed'].buffer(.15)):return None
        t=min(1.,d/change,(distance-d)/change);blend=10*t**3-15*t**4+6*t**5
        path.append([p[j]+blend*(q[j]-p[j]) for j in (0,1)])
    geometry=PathGeometry(path)
    for p in path:
        body=vehicle_footprint(p,geometry.project(p)[3],margin=.1)
        if not neighbour['surface'].buffer(.05).covers(body):return None
    base=PathGeometry(base_path);s0=base.project(path[0])[1];s1=base.project(path[-1])[1]
    remain=(s0-s1)%base.length
    # Retain the rest of the directed cycle after the completed pass.
    path += [list(base.point_at((s1+d)%base.length)) for d in range(1,int(remain)+1)]
    path.append(path[0])
    return path,geometry.length,distance-change,math.ceil(distance*2)+1


def target_gap_safe(own, actors, geometry, *, rear_brake=2.):
    """Present target-lane front/rear gaps and bounded induced rear braking."""
    _,progress,_,_=geometry.project(own['position_city_m'])
    for other in actors:
        if other['vehicle_id']==own['vehicle_id']:continue
        if math.dist(own['position_city_m'],other['position_city_m'])>40:continue
        lat,s,_,h=geometry.project(other['position_city_m'])
        if lat>(other.get('width_m',2.)+own['width_m'])/2+.3:continue
        delta=s-progress;gap=abs(delta)-(own['length_m']+other.get('length_m',1.))/2
        speed=other.get('speed_m_s',0.)
        if abs(_angle(other.get('heading_rad',h)-h))>1. and abs(delta)<12:return False
        required=3.+(own['speed_m_s'] if delta>=0 else speed)*1.3
        closing=max(0.,(own['speed_m_s']-speed) if delta>=0 else (speed-own['speed_m_s']))
        if gap<required+closing**2/(2*rear_brake):return False
    return True


class LaneChanges:
    def __init__(self, features, route_paths, missions, enabled=False):
        self.enabled=enabled;self.neighbours=source_neighbours(features) if enabled else []
        geometries=[PathGeometry(p) for p in route_paths.values()]
        self.neighbours=[n for n in self.neighbours if any(
            g.project(n['source_geometry'].point_at(n['source_geometry'].length/2))[0]<.05 for g in geometries)]
        self.route_paths=route_paths;self.missions=missions;self.active={};self.cooldown={};self.next_check={}
        self.started=self.completed=self.gap_rejections=self.cancelled=0;self.last_completed=None;self.yielding=set()

    def report(self):
        return dict(enabled=self.enabled,legal_directed_sections=len(self.neighbours),started=self.started,
                    completed=self.completed,cancelled=self.cancelled,active=list(self.active),gap_rejections=self.gap_rejections,
                    last_completed_vehicle=self.last_completed,model='source_dashed_IDM_incentive_returning_pass',calibrated=False)

    def cancel(self, runtime, key):
        active=self.active.pop(key,None)
        if active:
            runtime.path_overrides.pop(key,None)
            runtime.path_geometries.pop(id(active['path']),None)
            runtime.junction_coordinator._path_geometry.pop(id(active['path']),None)
            self.cancelled+=1

    def update(self, runtime, actors, time_s):
        self.yielding=set()
        if not self.enabled:return
        cars={a['vehicle_id']:a for a in actors if a.get('type')=='VEHICLE'}
        projections={}
        for key in runtime.ids[1:]:
            own=cars[key];active=self.active.get(key)
            if runtime.modes[key]!='ai':continue
            if active:
                controller=runtime.controllers[key]
                progress=controller.geometry.project(own['position_city_m'])[1]
                if active['merge_start']-.5<progress<active['end']-5 and not target_gap_safe(own,actors,active['source_geometry']):
                    self.yielding.add(key)
                if active['end']<progress<active['end']+10:
                    distance=controller.distance
                    runtime.path_overrides.pop(key,None);runtime.controllers[key]=runtime._controller(key)
                    runtime.controllers[key].distance=distance
                    runtime.path_geometries.pop(id(active['path']),None)
                    runtime.junction_coordinator._path_geometry.pop(id(active['path']),None)
                    self.active.pop(key);self.completed+=1;self.last_completed=key;self.cooldown[key]=time_s+45
                continue
            if time_s<self.cooldown.get(key,0) or time_s<self.next_check.get(key,2):continue
            self.next_check[key]=time_s+2
            base=runtime.path_for(key);mission=self.missions[key]
            if not base or own['speed_m_s']<.2:continue
            if id(base) not in projections:
                projections[id(base)]=project_actors(base,list(cars.values()),runtime.path_geometries[id(base)])
            leader=current_leader(own,actors,base,projections[id(base)])
            # Pass only for a current slower vehicle, not to create arbitrary swerving.
            if not leader or not 8.<leader['gap_m']<30. or leader['speed_m_s']>own['speed_m_s']-.25:continue
            params=mission['driver_parameters'];desired=runtime.controllers[key].speed
            gain=idm_acceleration(own['speed_m_s'],desired,None,params)-idm_acceleration(own['speed_m_s'],desired,leader,params)
            if gain<.12:continue
            for neighbour in self.neighbours:
                src=neighbour['source_geometry'];lat,s,_,h=src.project(own['position_city_m'])
                if lat>.6 or abs(_angle(own['heading_rad']-h))>.2:continue
                built=passing_path(neighbour,s,base)
                if not built:continue
                path,end,merge_start,point_count=built
                # Reserve overlapping maneuvers, re-evaluate current merge gaps.
                corridor=LineString(path[:point_count]).buffer(1.4)
                if any(corridor.intersects(a['corridor']) for a in self.active.values()):continue
                if not target_gap_safe(own,actors,neighbour['target_geometry']):self.gap_rejections+=1;continue
                from .simulation_traffic import CycleFollower
                controller=CycleFollower(path,desired);controller.distance=runtime.controllers[key].distance
                runtime.path_overrides[key]=path;runtime.path_geometries[id(path)]=controller.geometry;runtime.controllers[key]=controller
                self.active[key]=dict(path=path,end=end,corridor=corridor,target=neighbour['target'],source_geometry=src,merge_start=merge_start,surface=neighbour['surface']);self.started+=1
                break
