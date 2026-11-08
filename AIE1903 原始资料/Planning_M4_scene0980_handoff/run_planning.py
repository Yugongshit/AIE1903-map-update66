"""Offline scene-0980 static planning handoff. No upstream code is executed.

python run_planning.py --desktop <source directory> --output <delivery directory>
python run_planning.py --inputs <delivery/inputs> --output <new delivery directory>
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import heapq
import io
import json
import math
import platform
import shutil
import sys
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

# Optional project-local installation; standard environments use requirements.txt.
local_packages = Path(__file__).resolve().parents[1] / 'tmp' / 'planning_runtime'
if local_packages.is_dir():
    sys.path.insert(0, str(local_packages))
import numpy as np
import shapely
from shapely.geometry import Point, Polygon, LineString, box, mapping
from shapely.ops import unary_union
from shapely.prepared import prep
from PIL import Image, ImageDraw, ImageFont

SCENE = 'scene-0980'
RUN = 'm4_scene0980_static_v1'
PARAMS = {
    'centerline_resolution_m': 0.75,
    'connector_resolution_m': 0.25,
    'ego_length_m': 4.8,
    'ego_width_m': 1.9,
    'ego_reference': 'assumed geometric center at supplied ego translation',
    'obstacle_margin_m': 0.30,
    'road_margin_m': 0.10,
    'max_curvature_per_m': 0.20,
    'response_time_s': 0.50,
    'braking_deceleration_mps2': 3.0,
    'stopping_extra_margin_m': 2.0,
    'join_lookahead_m': [6.0, 10.0, 14.0],
    'max_lane_match_distance_m': 3.0,
    'max_lane_match_heading_deg': 45.0,
    'max_successor_gap_m': 1.0,
    'goal_region_xy': [378.0, 1125.0, 410.0, 1140.0],
    'roi_xy': [320.0, 1060.0, 520.0, 1340.0],
    'goal_selection': 'fixed map-space destination region downstream of the recorded corridor; all policies share it',
    'fixed_candidate_penalty_per_m': 2.0,
    'suspected_change_penalty_per_m': 3.0,
    'uncertain_penalty_per_m': 1.0,
    'minimum_object_age_s': 0.0,
    'temporal_mode': 'current annotation snapshot; prefix-only candidate evidence; final offline M3 available at scene end',
    'dynamic_prediction': False,
    'lane_change_edges': False,
    'signal_state_available': False,
    'observed_free_space_available': False,
}
POLICIES = ['map_only', 'fixed_candidate_penalty', 'reliability_aware']

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')

def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))

def write_csv(path, records):
    if not records:
        return
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(records[0]))
        w.writeheader()
        w.writerows(records)

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def angle_diff(a, b):
    return (a-b+math.pi) % (2*math.pi)-math.pi

def sample_arcs(parts):
    points = []
    maximum_error = 0.0
    for part in parts:
        pose = np.asarray(part['start_pose'], dtype=float)
        if not points or np.linalg.norm(np.array(points[-1][:2])-pose[:2]) > 1e-7:
            points.append(pose.tolist())
        for shape, length in zip(part['shape'], part['segment_length']):
            curvature = {'L': 1., 'R': -1., 'S': 0.}[shape] / part['radius']
            origin = pose.copy()
            count = max(1, math.ceil(length/PARAMS['centerline_resolution_m']))
            for distance in np.linspace(0, length, count+1)[1:]:
                theta = origin[2]+curvature*distance
                if abs(curvature) < 1e-12:
                    xy = origin[:2]+distance*np.array([math.cos(origin[2]), math.sin(origin[2])])
                else:
                    xy = origin[:2]+np.array([math.sin(theta)-math.sin(origin[2]), -math.cos(theta)+math.cos(origin[2])])/curvature
                pose = np.array([*xy, theta])
                if np.linalg.norm(pose[:2]-np.array(points[-1][:2])) > 1e-6:
                    points.append(pose.tolist())
        maximum_error = max(maximum_error, float(np.linalg.norm(pose[:2]-np.array(part['end_pose'][:2]))))
    return points, maximum_error

def footprint(pose, length=None, width=None):
    length = length or PARAMS['ego_length_m']
    width = width or PARAMS['ego_width_m']
    x, y, yaw = pose
    c, s = math.cos(yaw), math.sin(yaw)
    return Polygon([(x+c*a-s*b, y+s*a+c*b) for a,b in [(-length/2,-width/2), (length/2,-width/2), (length/2,width/2), (-length/2,width/2)]])

def edge_sweep(a, b):
    # Conservative enclosure for linear center / shortest-angle interpolation.
    delta = abs(angle_diff(b[2], a[2]))
    radius = math.hypot(PARAMS['ego_length_m'], PARAMS['ego_width_m'])/2
    hull = unary_union([footprint(a), footprint(b)]).convex_hull
    return hull.buffer(radius*delta/2) if delta > 1e-8 else hull

def path_length(poses):
    return sum(math.hypot(b[0]-a[0], b[1]-a[1]) for a,b in zip(poses,poses[1:]))

def max_curvature(poses):
    values = [abs(angle_diff(b[2], a[2]))/math.hypot(b[0]-a[0], b[1]-a[1]) for a,b in zip(poses,poses[1:]) if math.hypot(b[0]-a[0], b[1]-a[1]) > 1e-5]
    return max(values, default=0.)

def hermite(a, b):
    p0, p1 = np.array(a[:2]), np.array(b[:2])
    length = float(np.linalg.norm(p1-p0))
    if length < 0.1:
        return [list(a), list(b)]
    m0 = length*np.array([math.cos(a[2]), math.sin(a[2])])
    m1 = length*np.array([math.cos(b[2]), math.sin(b[2])])
    poses=[]
    for u in np.linspace(0, 1, max(3, math.ceil(length/PARAMS['connector_resolution_m']))+1):
        xy=(2*u**3-3*u**2+1)*p0+(u**3-2*u**2+u)*m0+(-2*u**3+3*u**2)*p1+(u**3-u**2)*m1
        d=(6*u*u-6*u)*p0+(3*u*u-4*u+1)*m0+(-6*u*u+6*u)*p1+(3*u*u-2*u)*m1
        poses.append([float(xy[0]),float(xy[1]),math.atan2(d[1],d[0])])
    return poses

def prefix(poses, length):
    if not poses:
        return []
    result=[poses[0]]
    remaining=length
    for a,b in zip(poses,poses[1:]):
        d=math.hypot(b[0]-a[0],b[1]-a[1])
        if d >= remaining and d>0:
            u=remaining/d
            result.append([a[0]+u*(b[0]-a[0]),a[1]+u*(b[1]-a[1]),a[2]+u*angle_diff(b[2],a[2])])
            return result
        result.append(b)
        remaining-=d
    return result

def prepare_inputs(desktop, output, input_root=None):
    dest=output/'inputs'
    dest.mkdir(parents=True, exist_ok=True)
    if input_root:
        for p in input_root.rglob('*'):
            if p.is_file():
                q=dest/p.relative_to(input_root)
                q.parent.mkdir(parents=True, exist_ok=True)
                if p.resolve()!=q.resolve():
                    shutil.copyfile(p,q)
        return dest
    sources={}
    with zipfile.ZipFile(desktop/'maps.zip') as z:
        (dest/'base_map.json').write_bytes(z.read('maps/expansion/singapore-onenorth.json'))
    required=['frames.csv','objects.csv','scene0980_unified_candidates_v1.csv','lane_impact_frames_v2.csv','lane_impact_events_v2.csv','road_perception/scene0980_discrepancy_evidence_v1.json','tracking_report.json','scene0980_delivery_validation_v1.json']
    with zipfile.ZipFile(desktop/'processed.zip') as z:
        for n in required:
            (dest/Path(n).name).write_bytes(z.read('processed/'+n))
        evidence=json.loads(z.read('processed/road_perception/scene0980_discrepancy_evidence_v1.json'))
        for ev in evidence['events']:
            for row in ev['frame_evidence']:
                name=Path(row['source_csv']).name
                (dest/name).write_bytes(z.read('processed/road_perception/'+name))
    with zipfile.ZipFile(desktop/'M3_scene0980_proposal_supplement.zip') as z:
        (dest/'real_scene_handoff.json').write_bytes(z.read('M3_scene0980_proposal_supplement/results/real_scene_handoff.json'))
    for name in ['maps.zip','processed.zip','M3_scene0980_proposal_supplement.zip']:
        sources[name]={'sha256':sha(desktop/name),'bytes':(desktop/name).stat().st_size}
    sources['member2']={'used':False,'reason':'different Argoverse scene and truncated archive; no result files verified'}
    dump(dest/'source_archives.json',sources)
    return dest

class MapGraph:
    def __init__(self, data):
        self.raw=data
        self.nodes={}
        self.adj=defaultdict(list)
        self.lanes={}
        self.lines={}
        self.lane_polygons={}
        self.static_cache={}
        self.goal=box(*PARAMS['goal_region_xy'])
        self.roi=box(*PARAMS['roi_xy'])
        ns={n['token']:(n['x'],n['y']) for n in data['node']}
        polys={}
        invalid=[]
        for p in data['polygon']:
            ext=[ns[n] for n in p['exterior_node_tokens']]
            holes=[[ns[n] for n in h['node_tokens']] for h in p.get('holes',[])]
            g=Polygon(ext,holes)
            if not g.is_valid:
                invalid.append(p['token'])
                g=shapely.make_valid(g)
            polys[p['token']]=g
        road_tokens={t for r in data['drivable_area'] for t in r['polygon_tokens']}
        self.road=unary_union([polys[t] for t in road_tokens if polys[t].intersects(self.roi)]).intersection(self.roi)
        self.safe_road=self.road.buffer(-PARAMS['road_margin_m'])
        self.prepared_road=prep(self.safe_road)
        records={r['token']:r for layer in ['lane','lane_connector'] for r in data[layer]}
        max_error=0.
        for token,parts in data['arcline_path_3'].items():
            if not parts or token not in records:
                continue
            points,error=sample_arcs(parts)
            max_error=max(max_error,error)
            line=LineString([p[:2] for p in points])
            if not line.intersects(self.roi):
                continue
            self.lanes[token]=points
            self.lines[token]=line
            self.lane_polygons[token]=polys[records[token]['polygon_token']]
            for i,p in enumerate(points):
                node=(token,i)
                self.nodes[node]=p
                if i:
                    self.adj[(token,i-1)].append(node)
        missing=[]
        bad_gaps=[]
        for token,conn in data['connectivity'].items():
            for target in conn['outgoing']:
                if target not in records:
                    missing.append({'from':token,'to':target})
                if token not in self.lanes or target not in self.lanes:
                    continue
                a=(token,len(self.lanes[token])-1);b=(target,0)
                gap=math.dist(self.nodes[a][:2],self.nodes[b][:2])
                yaw_gap=abs(angle_diff(self.nodes[a][2],self.nodes[b][2]))
                if gap>PARAMS['max_successor_gap_m'] or yaw_gap>math.radians(15):
                    bad_gaps.append({'from':token,'to':target,'distance_m':gap,'heading_gap_deg':math.degrees(yaw_gap)})
                    continue
                self.adj[a].append(b)
        self.audit={'full_map_lane_count':len(data['lane']),'full_map_connector_count':len(data['lane_connector']),'local_lane_and_connector_count':len(self.lanes),'local_nodes':len(self.nodes),'local_edges':sum(map(len,self.adj.values())),'dangling_successor_references':missing,'excluded_local_connectors':bad_gaps,'invalid_polygon_tokens_repaired':invalid,'max_arc_endpoint_error_m':max_error,'centerline_reference':'official nuScenes L/R/S arc definitions; endpoints numerically checked'}

    def match_lane(self, pose):
        p=Point(pose[:2]);candidates=[]
        for token,line in self.lines.items():
            dist=line.distance(p)
            if dist>PARAMS['max_lane_match_distance_m']:
                continue
            s=line.project(p)
            pts=self.lanes[token]
            i=min(range(len(pts)),key=lambda j:math.dist(pose[:2],pts[j][:2]))
            angle=abs(angle_diff(pts[i][2],pose[2]))
            if angle>math.radians(PARAMS['max_lane_match_heading_deg']):
                continue
            in_polygon=self.lane_polygons[token].buffer(.15).covers(p)
            if not in_polygon:
                continue
            candidates.append((dist+0.5*angle,token,i,dist,angle,s))
        return min(candidates) if candidates else None

    def starts(self, pose, match):
        if match is None:
            return []
        _,token,i,*_=match
        p=Point(pose[:2]);s=self.lines[token].project(p)
        # Only follow official successor chains. Do not seed parallel lanes.
        initial=(token,i)
        q=[(0.,initial,[token])];seen={};targets=[]
        while q:
            dist,node,chain=heapq.heappop(q)
            if dist>16 or node in seen:
                continue
            seen[node]=dist
            for target in PARAMS['join_lookahead_m']:
                if abs(dist-target)<.42:
                    targets.append((node,chain))
            for nxt in self.adj[node]:
                length=math.dist(self.nodes[node][:2],self.nodes[nxt][:2])
                heapq.heappush(q,(dist+length,nxt,chain+([nxt[0]] if nxt[0]!=chain[-1] else [])))
        results=[]
        for node,chain in targets:
            poses=hermite(pose,self.nodes[node])
            corridor=unary_union([self.lane_polygons[t] for t in chain]).buffer(.10)
            # Centers must stay in the matched forward corridor; the vehicle is
            # checked separately against physical drivable boundaries.
            if max_curvature(poses)>PARAMS['max_curvature_per_m']:
                continue
            if not corridor.covers(LineString([p[:2] for p in poses])):
                continue
            results.append((node,poses,chain))
        return results

    def edge(self,a,b):
        key=(a,b)
        if key not in self.static_cache:
            pa,pb=self.nodes[a],self.nodes[b]
            distance=math.dist(pa[:2],pb[:2])
            k=abs(angle_diff(pb[2],pa[2]))/distance if distance>1e-5 else 0.
            geom=edge_sweep(pa,pb)
            self.static_cache[key]=(distance,geom,self.prepared_road.covers(geom) and k<=PARAMS['max_curvature_per_m'])
        return self.static_cache[key]

def make_obstacles(rows):
    result=[]
    for r in rows:
        pose=[float(r['x_global_m']),float(r['y_global_m']),math.radians(float(r['yaw_global_deg']))]
        poly=footprint(pose,float(r['length_m']),float(r['width_m']))
        result.append({'instance_token':r['instance_token'],'annotation_token':r['annotation_token'],'category':r['category'],'frame_index':int(r['frame_index']),'time_s':float(r['time_s']),'geometry':poly,'buffered':poly.buffer(PARAMS['obstacle_margin_m']),'source':'nuScenes annotation snapshot'})
    return result

def validate_geometry(graph,poses,obstacles):
    if len(poses)<2:
        return {'feasible':False,'reasons':['EMPTY_PATH'],'length_m':0.,'min_obstacle_clearance_m':None,'first_collision_distance_m':None,'max_curvature_per_m':None,'colliding_instance_tokens':[]}
    sweeps=[edge_sweep(a,b) for a,b in zip(poses,poses[1:])]
    geometry=unary_union(sweeps)
    intruders=[o['instance_token'] for o in obstacles if geometry.intersects(o['buffered'])]
    out_area=float(geometry.difference(graph.safe_road).area)
    k=max_curvature(poses)
    reasons=[]
    if out_area>1e-7: reasons.append('OUTSIDE_DRIVABLE_AREA_WITH_MARGIN')
    if intruders: reasons.append('BUFFERED_OBSTACLE_INTERSECTION')
    if k>PARAMS['max_curvature_per_m']+1e-8: reasons.append('CURVATURE_LIMIT')
    first=None;s=0.
    for geom,a,b in zip(sweeps,poses,poses[1:]):
        if any(geom.intersects(o['buffered']) for o in obstacles):
            first=s;break
        s+=math.dist(a[:2],b[:2])
    return {'feasible':not reasons,'reasons':reasons,'length_m':path_length(poses),'min_obstacle_clearance_m':min((geometry.distance(o['geometry']) for o in obstacles),default=None),'first_collision_distance_m':first,'max_curvature_per_m':k,'outside_drivable_area_m2':out_area,'colliding_instance_tokens':intruders}

def astar(graph,starts,obstacles,regions,policy,ignore_obstacles=False):
    obs_union=unary_union([o['buffered'] for o in obstacles]) if obstacles and not ignore_obstacles else None
    obs_prepared=prep(obs_union) if obs_union is not None else None
    region_union=unary_union(regions) if regions else None
    region_prepared=prep(region_union) if region_union is not None else None
    penalty={'map_only':0.,'fixed_candidate_penalty':PARAMS['fixed_candidate_penalty_per_m'],'reliability_aware':PARAMS['uncertain_penalty_per_m']}[policy]
    gscore={};parent={};origin={};queue=[];counter=0;blocked=Counter();expanded=0
    for node,poses,chain in starts:
        report=validate_geometry(graph,poses,[] if ignore_obstacles else obstacles)
        if not report['feasible']:
            blocked.update(report['reasons']);continue
        length=report['length_m']
        affected=region_prepared is not None and region_prepared.intersects(LineString([p[:2] for p in poses]))
        cost=length*(1+penalty*bool(affected))
        if cost>=gscore.get(node,float('inf')):continue
        gscore[node]=cost;parent[node]=None;origin[node]=(poses,chain)
        heuristic=graph.goal.distance(Point(graph.nodes[node][:2]))
        heapq.heappush(queue,(cost+heuristic,counter,node,cost));counter+=1
    found=None
    while queue:
        _,_,node,cost=heapq.heappop(queue)
        if cost!=gscore.get(node):continue
        expanded+=1
        if graph.goal.covers(Point(graph.nodes[node][:2])):
            found=node;break
        for nxt in graph.adj[node]:
            length,geom,ok=graph.edge(node,nxt)
            if not ok:
                blocked['MAP_OR_CURVATURE_EDGE_REJECTED']+=1;continue
            if obs_prepared is not None and obs_prepared.intersects(geom):
                blocked['OBSTACLE_EDGE_REJECTED']+=1;continue
            affected=region_prepared is not None and region_prepared.intersects(geom)
            new=cost+length*(1+penalty*bool(affected))
            if new+1e-10>=gscore.get(nxt,float('inf')):continue
            gscore[nxt]=new;parent[nxt]=node
            heuristic=graph.goal.distance(Point(graph.nodes[nxt][:2]))
            heapq.heappush(queue,(new+heuristic,counter,nxt,new));counter+=1
    if found is None:
        return {'poses':[],'lane_ids':[],'cost':None,'expanded_nodes':expanded,'rejections':dict(blocked),'reason':'NO_PATH_IN_CHECKED_DIRECTED_CENTERLINE_GRAPH'}
    nodes=[];n=found
    while n is not None:
        nodes.append(n);n=parent[n]
    nodes.reverse();poses,chain=origin[nodes[0]];poses=list(poses)
    for node in nodes[1:]:
        p=graph.nodes[node]
        if math.dist(p[:2],poses[-1][:2])>1e-6:poses.append(p)
    lanes=list(chain)
    for n in nodes:
        if n[0]!=lanes[-1]:lanes.append(n[0])
    return {'poses':poses,'lane_ids':lanes,'cost':gscore[found],'expanded_nodes':expanded,'rejections':dict(blocked),'reason':None}

def load_lane_evidence(inputs):
    source=json.loads((inputs/'scene0980_discrepancy_evidence_v1.json').read_text(encoding='utf-8'))
    result=[]
    for event in source['events']:
        for row in event['frame_evidence']:
            filename=Path(row['source_csv']).name
            pts=read_csv(inputs/filename)
            if not pts:continue
            # Input exports have x_city/y_city or explicitly named global fields.
            keys=list(pts[0])
            xkey=next((k for k in keys if k in ['global_x_m','x_city_m','x_global_m','x_m','x_city','x']),None)
            ykey=next((k for k in keys if k in ['global_y_m','y_city_m','y_global_m','y_m','y_city','y']),None)
            if xkey is None or ykey is None:
                raise ValueError(f'Unknown lane coordinate fields: {keys}')
            xy=[[float(p[xkey]),float(p[ykey])] for p in pts]
            result.append({'candidate_id':event['event_id'],'frame_index':int(row['frame']),'time_s':float(row['time_s']),'geometry':LineString(xy).buffer(.5),'source_file':'inputs/'+filename,'status':'CANDIDATE_UNVERIFIED'})
    return result

def frame_evidence(frame, lane_evidence, occupancy, graph, final_m3, scene_end):
    idx=int(frame['frame_index']);t=float(frame['time_s'])
    records=[];regions=[]
    for e in lane_evidence:
        if e['frame_index']<=idx:
            records.append({k:v for k,v in e.items() if k!='geometry'})
            regions.append(e['geometry'])
    # Current-frame lane occupancy only. No whole-scene max, median, or future persistence.
    for row in occupancy:
        if int(row['frame_index'])!=idx:continue
        token=row['lane_token']
        if token not in graph.lane_polygons:continue
        regions.append(graph.lane_polygons[token])
        records.append({'candidate_id':'S0980-OCC-'+row['event_id'],'frame_index':idx,'time_s':t,'source_file':'inputs/lane_impact_frames_v2.csv','status':'GEOMETRIC_OCCUPANCY_NOT_LANE_CLOSURE','lane_token':token})
    m3=[]
    if t>=scene_end-1e-6:
        for case in final_m3['cases']:
            m3.append({'record_id':case['record_id'],'final_decision':case['final_decision'],'effective_coverage':case['effective_coverage'],'available_at_time_s':scene_end,'evidence_window_end_s':case['last_time_s'],'map_token':case.get('map_token'),'source_file':'inputs/real_scene_handoff.json'})
    return records,regions,m3

def render_overview(graph,frame,objects,records,path):
    w,h=1450,1050
    im=Image.new('RGB',(w,h),'#f4f6f8');draw=ImageDraw.Draw(im)
    bounds=(360,1110,480,1300);left,top,pw,ph=50,100,920,900
    scale=min(pw/(bounds[2]-bounds[0]),ph/(bounds[3]-bounds[1]))
    def pt(xy):return (left+(xy[0]-bounds[0])*scale,top+(bounds[3]-xy[1])*scale)
    try:
        font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',19)
        small=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',15)
        title=ImageFont.truetype('C:/Windows/Fonts/arialbd.ttf',26)
    except OSError:font=small=title=ImageFont.load_default()
    geoms=list(graph.road.geoms) if hasattr(graph.road,'geoms') else [graph.road]
    for g in geoms:
        if g.geom_type!='Polygon':continue
        draw.polygon([pt(p) for p in g.exterior.coords], fill='#e1e5e8')
        for hole in g.interiors:draw.polygon([pt(p) for p in hole.coords],fill='#f4f6f8')
    for line in graph.lines.values():draw.line([pt(p) for p in line.coords],fill='#a4adb4',width=1)
    draw.polygon([pt(p) for p in graph.goal.exterior.coords],outline='#217550',width=3)
    for ob in objects:
        draw.polygon([pt(p) for p in ob['buffered'].exterior.coords],fill='#efb8ae')
        draw.polygon([pt(p) for p in ob['geometry'].exterior.coords],fill='#bd5348')
    colors={'map_only':'#2674bf','fixed_candidate_penalty':'#db922e','reliability_aware':'#8a43b5'}
    for record in records:
        poses=record['route']['poses_xy_yaw_rad']
        if poses:draw.line([pt(p[:2]) for p in poses],fill=colors[record['policy']],width=3)
    selected=records[-1]
    stop=selected['stopping']['poses_xy_yaw_rad']
    if stop:draw.line([pt(p[:2]) for p in stop],fill='#15926f',width=5)
    ego=[float(frame['ego_x_m']),float(frame['ego_y_m']),math.radians(float(frame['ego_yaw_deg']))]
    draw.polygon([pt(p) for p in footprint(ego).exterior.coords],fill='#202c3a')
    draw.text((45,25),f"scene-0980 | frame {int(frame['frame_index']):02d} | t={float(frame['time_s']):.3f}s",font=title,fill='#182b3b')
    x,y=990,110
    texts=['OFFLINE STATIC SNAPSHOT','Current GT boxes frozen in time','No dynamic safety claim','', 'Grey: prior road / lane graph','Red: annotated obstacles + margin','Green box: common goal region','Green path: stopping candidate','Black: assumed ego footprint','']
    for text in texts:draw.text((x,y),text,font=small,fill='#253b4b');y+=27
    for r in records:
        draw.text((x,y),r['policy'],font=font,fill=colors[r['policy']]);y+=29
        draw.text((x,y),'static route: '+str(r['static_route_geometry_feasible']),font=small,fill='#253b4b');y+=26
        draw.text((x,y),'length: '+str(round(r['route']['length_m'],1))+' m',font=small,fill='#253b4b');y+=37
    for text in ['Observed free space: UNKNOWN','Execution authorized: FALSE','Decision: '+selected['behavior'],'M3 records used: '+str(len(selected['m3_records_used'])),'','All values are model-conditional.']:
        draw.text((x,y),text,font=small,fill='#a44436' if 'FALSE' in text or 'UNKNOWN' in text else '#253b4b');y+=27
    path.parent.mkdir(parents=True,exist_ok=True);im.save(path)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--desktop',type=Path,default=Path.home()/'Desktop')
    parser.add_argument('--inputs',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();out=args.output.resolve();out.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter();inputs=prepare_inputs(args.desktop,out,args.inputs)
    data=json.loads((inputs/'base_map.json').read_text(encoding='utf-8'))
    m3=json.loads((inputs/'real_scene_handoff.json').read_text(encoding='utf-8'))
    if sha(inputs/'base_map.json')!=m3['source_map']['sha256']:
        raise ValueError('M3 source map hash differs from supplied map')
    frames=read_csv(inputs/'frames.csv');objects=read_csv(inputs/'objects.csv')
    by_frame=defaultdict(list)
    for row in objects:by_frame[int(row['frame_index'])].append(row)
    occupancy=read_csv(inputs/'lane_impact_frames_v2.csv')
    event_to_lane={r['event_id']:r['lane_token'] for r in read_csv(inputs/'lane_impact_events_v2.csv')}
    for r in occupancy:r['lane_token']=event_to_lane[r['event_id']]
    lane_evidence=load_lane_evidence(inputs);graph=MapGraph(data)
    scene_end=float(frames[-1]['time_s'])
    all_records=[];flat=[];features=[];obstacle_features=[];evidence_out=[]
    selected_frames={0,10,18,20,24,27,30,33,40}
    for fi,frame in enumerate(frames):
        idx=int(frame['frame_index']);t=float(frame['time_s'])
        pose=[float(frame['ego_x_m']),float(frame['ego_y_m']),math.radians(float(frame['ego_yaw_deg']))]
        speed=None
        if fi:
            prev=frames[fi-1]
            speed=math.hypot(pose[0]-float(prev['ego_x_m']),pose[1]-float(prev['ego_y_m']))/(t-float(prev['time_s']))
        obs=make_obstacles(by_frame[idx])
        evidence,regions,m3_used=frame_evidence(frame,lane_evidence,occupancy,graph,m3,scene_end)
        for e in evidence:evidence_out.append({'decision_frame':idx,**e})
        for ob in obs:
            obstacle_features.append({'type':'Feature','geometry':mapping(ob['geometry']),'properties':{k:v for k,v in ob.items() if k not in ['geometry','buffered']}})
        match=graph.match_lane(pose);starts=graph.starts(pose,match)
        # Nominal route does not make a feasibility claim; used to expose rejects
        # and obtain an independently checked stopping fallback.
        nominal=astar(graph,starts,[],[],'map_only',ignore_obstacles=True)
        nominal_check=validate_geometry(graph,nominal['poses'],obs)
        records=[]
        for policy in POLICIES:
            begin=time.perf_counter()
            planned=astar(graph,starts,obs,regions,policy)
            check=validate_geometry(graph,planned['poses'],obs)
            if planned['poses'] and not check['feasible']:
                raise AssertionError('A* returned a path failing full-path validation')
            route=planned['poses']
            d_brake=None;stop_poses=[];stop_check=None;stop_reason=None
            stop_source=route or nominal['poses']
            if speed is None:
                stop_reason='INITIAL_SPEED_NOT_AVAILABLE_FROM_PAST_FRAMES'
            else:
                d_brake=speed*PARAMS['response_time_s']+speed*speed/(2*PARAMS['braking_deceleration_mps2'])+PARAMS['stopping_extra_margin_m']
                if not stop_source:
                    stop_reason='NO_FORWARD_REFERENCE_FOR_STOPPING'
                elif path_length(stop_source)+1e-7<d_brake:
                    stop_reason='REFERENCE_SHORTER_THAN_REQUIRED_STOPPING_DISTANCE'
                else:
                    stop_poses=prefix(stop_source,d_brake)
                    stop_check=validate_geometry(graph,stop_poses,obs)
                    if not stop_check['feasible']:stop_reason='STOPPING_SWEEP_FAILS_STATIC_GEOMETRY'
            static_ok=bool(route) and check['feasible']
            stop_ok=stop_check['feasible'] if stop_check is not None else None
            if static_ok:
                behavior='VERIFY_REQUIRED'
            elif stop_ok:
                behavior='STOP_CANDIDATE_UNVERIFIED'
            else:
                behavior='NO_VERIFIED_PLAN'
            reasons=['OBSERVED_FREE_SPACE_UNAVAILABLE','TRAFFIC_SIGNAL_AND_FULL_RULE_STATE_UNAVAILABLE','STATIC_ANNOTATION_SNAPSHOT_ONLY','EGO_DIMENSIONS_AND_REFERENCE_ASSUMED']
            if not static_ok:reasons.append(planned['reason'] or 'STATIC_GEOMETRY_REJECTED')
            if stop_reason:reasons.append(stop_reason)
            if not m3_used:reasons.append('FINAL_OFFLINE_M3_NOT_YET_AVAILABLE')
            else:reasons.append('M3_ALL_REVIEW_UNITS_INSUFFICIENT_EVIDENCE')
            rid=f'{RUN}-f{idx:03d}-{policy}'
            record={'schema_version':'1.0.0','run_id':RUN,'planning_id':rid,'scene_id':SCENE,'frame_index':idx,'timestamp_us':int(frame['timestamp_us']),'time_s':t,'coordinate_frame':'nuScenes singapore-onenorth global XY in metres; yaw radians CCW from +X','map_version':data['version'],'map_sha256':sha(inputs/'base_map.json'),'policy':policy,'experiment_type':'ANNOTATION_BACKED_OFFLINE_STATIC_SNAPSHOT','ego':{'pose_xy_yaw_rad':pose,'speed_mps':speed,'speed_source':'backward position difference' if speed is not None else 'unavailable'},'destination_region_xy':PARAMS['goal_region_xy'],'matched_lane_id':match[1] if match else None,'lane_match_distance_m':match[3] if match else None,'obstacle_annotation_count':len(obs),'behavior':behavior,'static_route_geometry_feasible':static_ok,'observed_space_verified':None,'execution_authorized':False,'full_traffic_rule_compliance_verified':None,'reasons':reasons,'route':{'poses_xy_yaw_rad':route,'lane_ids':planned['lane_ids'],'length_m':check['length_m'],'cost':planned['cost'],'validation':check,'search_rejections':planned['rejections'],'expanded_nodes':planned['expanded_nodes']},'nominal_map_route':{'poses_xy_yaw_rad':nominal['poses'],'lane_ids':nominal['lane_ids'],'validation_against_current_obstacles':nominal_check,'executable':False},'stopping':{'required_distance_m':d_brake,'static_geometry_feasible':stop_ok,'reason':stop_reason,'poses_xy_yaw_rad':stop_poses,'validation':stop_check,'observed_space_verified':None,'execution_authorized':False,'model':'v*t_response + v^2/(2*a) + margin; current snapshot obstacles are frozen'},'candidate_ids_used':sorted({e['candidate_id'] for e in evidence}),'candidate_evidence_count':len(evidence),'candidate_evidence_max_time_s':max((e['time_s'] for e in evidence),default=None),'m3_records_used':m3_used,'m3_final_review_file':'inputs/real_scene_handoff.json','m3_available_at_time_s':scene_end,'m2_behavior_used':False,'m2_reason':'different dataset/scene; incomplete supplied archive','map_edit_requested':False,'permanent_lane_closure_claimed':False,'runtime_ms':(time.perf_counter()-begin)*1000}
            records.append(record);all_records.append(record)
            flat.append({'planning_id':rid,'frame_index':idx,'time_s':t,'policy':policy,'behavior':behavior,'static_route_geometry_feasible':static_ok,'route_length_m':check['length_m'],'route_min_clearance_m':check['min_obstacle_clearance_m'],'nominal_route_geometry_feasible':nominal_check['feasible'],'nominal_first_collision_distance_m':nominal_check['first_collision_distance_m'],'stop_static_geometry_feasible':stop_ok,'required_stopping_distance_m':d_brake,'observed_space_verified':'UNKNOWN','execution_authorized':False,'candidate_count':len(record['candidate_ids_used']),'m3_records_used':len(m3_used),'runtime_ms':record['runtime_ms'],'reason':';'.join(reasons)})
            for kind,pp in [('planned_route',route),('nominal_map_route',nominal['poses']),('stopping_candidate',stop_poses)]:
                if len(pp)>1:features.append({'type':'Feature','geometry':mapping(LineString([p[:2] for p in pp])),'properties':{'planning_id':rid,'scene_id':SCENE,'frame_index':idx,'time_s':t,'policy':policy,'kind':kind,'execution_authorized':False,'coordinates':'local metric city XY; not longitude/latitude'}})
        if idx in selected_frames:render_overview(graph,frame,obs,records,out/'figures'/f'frame_{idx:03d}.png')
        print(f'frame {idx:02d}: match={match[1] if match else None} starts={len(starts)} static={[r["static_route_geometry_feasible"] for r in records]} stop={records[-1]["stopping"]["static_geometry_feasible"]}',flush=True)
    with (out/'planning_results.jsonl').open('w',encoding='utf-8') as f:
        for r in all_records:f.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n')
    write_csv(out/'planning_summary.csv',flat)
    dump(out/'routes_city.geojson',{'type':'FeatureCollection','coordinate_system_note':'metric nuScenes city XY; this is not RFC7946 longitude/latitude','features':features})
    dump(out/'obstacles_city.geojson',{'type':'FeatureCollection','coordinate_system_note':'metric nuScenes city XY','features':obstacle_features})
    with (out/'candidate_evidence_used.jsonl').open('w',encoding='utf-8') as f:
        for e in evidence_out:f.write(json.dumps(e,ensure_ascii=False)+'\n')
    dump(out/'map_graph_audit.json',graph.audit)
    summary={'scene_id':SCENE,'run_id':RUN,'snapshot_count':len(frames),'duration_s':scene_end,'planning_records':len(all_records),'policy_count':len(POLICIES),'per_policy':{},'observed_space_verified_records':0,'execution_authorized_records':0,'important_result':'Static feasibility is conditional on frozen annotation boxes and prior map. Missing observation coverage prevents executable/safety claims. Policy differences are measured, not assumed.'}
    for policy in POLICIES:
        rs=[r for r in all_records if r['policy']==policy]
        summary['per_policy'][policy]={'snapshots':len(rs),'static_route_geometry_feasible':sum(r['static_route_geometry_feasible'] for r in rs),'static_stopping_geometry_feasible':sum(r['stopping']['static_geometry_feasible'] is True for r in rs),'static_stopping_geometry_infeasible':sum(r['stopping']['static_geometry_feasible'] is False for r in rs),'static_stopping_not_evaluable':sum(r['stopping']['static_geometry_feasible'] is None for r in rs),'behaviors':dict(Counter(r['behavior'] for r in rs)),'mean_runtime_ms':float(np.mean([r['runtime_ms'] for r in rs]))}
    dump(out/'summary.json',summary)
    dump(out/'run_manifest.json',{'run_id':RUN,'schema_version':'1.0.0','parameters':PARAMS,'policies':POLICIES,'python':platform.python_version(),'numpy':np.__version__,'shapely':shapely.__version__,'map_sha256':sha(inputs/'base_map.json'),'m3_map_hash_match':True,'input_hashes':{str(p.relative_to(out)):sha(p) for p in sorted(inputs.rglob('*')) if p.is_file()},'total_runtime_s':time.perf_counter()-started,'used_member2':False,'source_provenance':json.loads((inputs/'source_archives.json').read_text(encoding='utf-8'))})
    shutil.copyfile(Path(__file__),out/'run_planning.py')
    (out/'requirements.txt').write_text('numpy>=1.26\nshapely>=2.0\nPillow>=10\n',encoding='utf-8')
    dump(out/'m5_handoff.json',{'schema_version':'1.0.0','scene_id':SCENE,'run_id':RUN,'map_version':data['version'],'map_sha256':sha(inputs/'base_map.json'),'planning_records_file':'planning_results.jsonl','planning_summary_file':'planning_summary.csv','summary_file':'summary.json','routes_file':'routes_city.geojson','obstacles_file':'obstacles_city.geojson','evidence_file':'candidate_evidence_used.jsonl','reliability_review_file':'inputs/real_scene_handoff.json','join_keys':['scene_id','map_sha256','frame_index','timestamp_us','candidate_ids_used'],'primary_policy':'reliability_aware','map_updates_authorized_by_this_package':False,'integration_rules':['Treat routes as static candidates, not executed vehicle trajectories.','Keep unknown visibility as null; never convert it to clear or zero risk.','Planning rejection does not prove a permanent map change or lane closure.','Use map/observation evidence, not planner detours, to decide map updates.','M3 review is available only at scene end in this replay; other frames do not consume its final results.','Preserve GT annotation fallback provenance and all rejected / unevaluable records.','All GeoJSON coordinates are metric city XY, not WGS84.'],'record_count':len(all_records),'primary_policy_record_count':len(frames)})
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
