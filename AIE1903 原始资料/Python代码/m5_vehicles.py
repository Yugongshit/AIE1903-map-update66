"""M5: YOLOP 2D boxes + LiDAR association + ego-motion compensated City tracking."""
import json,time,csv,argparse,hashlib
from collections import defaultdict,Counter
from pathlib import Path
import numpy as np
from scipy.spatial import cKDTree
from scipy.optimize import linear_sum_assignment
from PIL import Image,ImageDraw
from m3_changes import ROOT,read,rows,write
from m2_extract import Ground
RULES={'version':'m5_vehicle_v1','lidar_range_m':60.,'minimum_ego_distance_m':2.5,'point_height_above_ground_m':[.25,3.5],'minimum_cluster_points':8,'cluster_voxel_m':.3,'cluster_connectivity_m':.8,'max_cluster_voxels':2500,'measurement_sigma_base_m':.6,'acceleration_noise_mps2':3.,'mahalanobis_gate_squared':9.21,'maximum_prediction_s':.5,'terminate_after_missing_s':.8,'confirm_after_hits':3,'two_d_iou_gate':.2,'two_d_max_gap_s':.4,'cross_camera_overlap_fraction':.25,'cross_camera_center_distance_m':.75,'position_definition':'median of associated visible LiDAR returns, not amodal vehicle box center','source_detector':'YOLOP640 vehicle class only'}

def iou(a,b):
 lo=np.maximum(a[:2],b[:2]);hi=np.minimum(a[2:],b[2:]);wh=np.maximum(hi-lo,0);intersection=float(np.prod(wh));union=float(np.prod(np.maximum(a[2:]-a[:2],0))+np.prod(np.maximum(b[2:]-b[:2],0))-intersection)
 return intersection/max(union,1e-9)

def connected_components(points):
 _,voxel_ids=np.unique(np.floor(points/.3).astype(np.int32),axis=0,return_inverse=True);n=int(voxel_ids.max())+1
 sums=np.zeros((n,3));np.add.at(sums,voxel_ids,points);counts=np.bincount(voxel_ids);centers=sums/counts[:,None]
 if n>RULES['max_cluster_voxels']:return [],'TOO_MANY_POINTS_AMBIGUOUS'
 parent=np.arange(n)
 def find(x):
  while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
  return x
 for a,b in sorted(cKDTree(centers).query_pairs(.8)):
  a,b=find(a),find(b)
  if a!=b:parent[b]=a
 roots=np.array([find(i) for i in range(n)])[voxel_ids];groups=[np.flatnonzero(roots==r) for r in np.unique(roots)]
 return groups,None

def associate_detection(d,uv,depth,xyz,point_ids):
 x1,y1,x2,y2=d['bbox_xyxy_px'];w,h=x2-x1,y2-y1
 if w<12 or h<12:return None,'BOX_TOO_SMALL'
 inside=(uv[:,0]>=x1+.1*w)&(uv[:,0]<=x2-.1*w)&(uv[:,1]>=y1+.2*h)&(uv[:,1]<=y2-.05*h)
 ids=np.flatnonzero(inside)
 if len(ids)<8:return None,'INSUFFICIENT_NON_GROUND_POINTS'
 groups,error=connected_components(xyz[ids])
 if error:return None,error
 options=[]
 for group in groups:
  if len(group)<8:continue
  index=ids[group];points=xyz[index];qlo,qhi=np.percentile(points,[5,95],axis=0);extent=qhi-qlo
  if np.linalg.norm(extent[:2])<.4 or np.linalg.norm(extent[:2])>15 or extent[2]<.15:continue
  center=np.median(points,axis=0);pixelcenter=np.median(uv[index],axis=0);normcenter=np.linalg.norm((pixelcenter-[x1+.5*w,y1+.65*h])/[w,h])
  if normcenter>.6:continue
  options.append((float(np.median(depth[index])),float(normcenter),index,center,qlo,qhi))
 if not options:return None,'NO_PLAUSIBLE_FOREGROUND_CLUSTER'
 # Choose closest substantial component; background leakage is still a risk.
 options.sort(key=lambda r:(r[0],r[1]));distance,normcenter,index,center,qlo,qhi=options[0]
 return {'position_city':center.tolist(),'point_count':len(index),'source_point_indices':point_ids[index].tolist(),'depth_m':distance,'bbox_city_minmax':[qlo.tolist(),qhi.tolist()],'cluster_count':len(options),'projected_center_offset_normalized':normcenter,'quality_flags':['visible_surface_center_not_box_center','background_contamination_possible','coarse_vehicle_class']},None

class Tracker:
 def __init__(self):self.active={};self.finished=[];self.counter=0
 def predict(self,track,t):
  dt=(t-track['state_ns'])/1e9;F=np.array([[1,0,dt,0],[0,1,0,dt],[0,0,1,0],[0,0,0,1.]])
  G=np.array([[dt*dt/2,0],[0,dt*dt/2],[dt,0],[0,dt]])
  track['x']=F@track['x'];track['P']=F@track['P']@F.T+G@G.T*9;track['state_ns']=t
 def step(self,measurements,t,clip):
  for tid in list(self.active):
   track=self.active[tid]
   if track['clip']!=clip or (t-track['last_observed_ns'])/1e9>.8:self.finish(tid,'clip_boundary' if track['clip']!=clip else 'lost')
  tids=list(self.active)
  for tid in tids:self.predict(self.active[tid],t)
  H=np.array([[1.,0,0,0],[0,1.,0,0]])
  costs=np.full((len(tids),len(measurements)),1e6)
  for i,tid in enumerate(tids):
   tr=self.active[tid]
   for j,m in enumerate(measurements):
    sigma=.6+.01*m['depth_m']+1/np.sqrt(m['point_count']);R=np.eye(2)*sigma*sigma;delta=np.array(m['position_city'][:2])-H@tr['x'];S=H@tr['P']@H.T+R;mah=float(delta@np.linalg.solve(S,delta))
    if mah<=9.21 and np.linalg.norm(delta)<=2.5+35*(t-tr['last_observed_ns'])/1e9:
     shared=bool(set(m['track_ids_2d'])&tr['two_d_ids']);costs[i,j]=np.sqrt(mah)+.2*np.linalg.norm(delta)+(0 if shared else .5)
  matched={};used=set()
  if costs.size:
   ri,ci=linear_sum_assignment(costs)
   for i,j in zip(ri,ci):
    if costs[i,j]>=1e6:continue
    tid=tids[i];tr=self.active[tid];m=measurements[j];sigma=.6+.01*m['depth_m']+1/np.sqrt(m['point_count']);R=np.eye(2)*sigma*sigma;S=H@tr['P']@H.T+R;K=np.linalg.solve(S,H@tr['P']).T
    tr['x']+=K@(np.array(m['position_city'][:2])-H@tr['x']);I=np.eye(4)-K@H;tr['P']=I@tr['P']@I.T+K@R@K.T
    tr['last_observed_ns']=t;tr['z']=m['position_city'][2];tr['hits']+=1;tr['two_d_ids'].update(m['track_ids_2d']);matched[tid]=j;used.add(j)
  for j,m in enumerate(measurements):
   if j in used:continue
   self.counter+=1;tid=f'vehicle3d_{self.counter:05d}';self.active[tid]={'x':np.array([*m['position_city'][:2],0.,0.]),'P':np.diag([2.,2.,100.,100.]),'z':m['position_city'][2],'state_ns':t,'last_observed_ns':t,'start_ns':t,'hits':1,'clip':clip,'two_d_ids':set(m['track_ids_2d']),'samples':[]};matched[tid]=j
  output=[];mapping={}
  for tid,tr in self.active.items():
   measured=tid in matched;missing=(t-tr['last_observed_ns'])/1e9
   if not measured and missing>.5:continue
   m=measurements[matched[tid]] if measured else None;speed=float(np.linalg.norm(tr['x'][2:]));confirmed=tr['hits']>=3
   velocity_usable=bool(measured and tr['hits']>=5 and (t-tr['start_ns'])/1e9>=1 and np.linalg.eigvalsh(tr['P'][:2,:2]).max()<=4 and speed<=35)
   row={'track_id':tid,'clip_id':clip,'timestamp_ns':str(t),'position_city':[float(tr['x'][0]),float(tr['x'][1]),tr['z']],'measurement_position_city':m['position_city'] if measured else None,'velocity_city_xy_mps':tr['x'][2:].tolist(),'speed_mps':speed,'velocity_usable_for_behavior_candidate':velocity_usable,'heading_rad':float(np.arctan2(tr['x'][3],tr['x'][2])) if speed>=1 and velocity_usable else None,'observed':measured,'track_state':'confirmed' if confirmed else 'tentative','measurement_id':m['measurement_id'] if measured else None,'point_count':m['point_count'] if measured else 0,'position_covariance_xy':tr['P'][:2,:2].tolist(),'quality_flags':['visible_surface_position','no_independent_3d_or_identity_gt']+([] if measured else ['prediction_not_measurement'])+([] if velocity_usable else ['velocity_not_ready_for_behavior'])}
   output.append(row);tr['samples'].append(row)
   if measured:mapping[m['measurement_id']]=tid
  return output,mapping
 def finish(self,tid,reason):
  tr=self.active.pop(tid);self.finished.append({'track_id':tid,'clip_id':tr['clip'],'start_ns':str(tr['start_ns']),'last_observed_ns':str(tr['last_observed_ns']),'end_ns':tr['samples'][-1]['timestamp_ns'] if tr['samples'] else str(tr['state_ns']),'observed_samples':tr['hits'],'sample_count':len(tr['samples']),'confirmed':tr['hits']>=3,'end_reason':reason,'class_name':'vehicle','position_definition':RULES['position_definition']})

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int);args=parser.parse_args()
 source=next((ROOT/'deliveries').glob('*_M2_v1'));frames=rows(source/'tables/frames.jsonl');frames=frames[:args.limit] if args.limit else frames;views={r['view_id']:r for r in rows(source/'tables/views.jsonl')};detbyview=defaultdict(list)
 for d in rows(source/'tables/detections.jsonl'):detbyview[d['view_id']].append(d)
 cal=read(source/'calibration.json');scene=(ROOT/frames[0]['source_lidar_ref']).parents[2];ground=Ground(scene)
 out=ROOT/('m5_pilot' if args.limit else 'deliveries')/(scene.name+('_M5_pilot' if args.limit else '_M5_v1'));out.mkdir(parents=True,exist_ok=True);(out/'qa').mkdir(exist_ok=True)
 tracker=Tracker();twod=defaultdict(dict);two_counter=0;detection_rows=[];measurements_all=[];track_samples=[];frame_reports=[];started=time.perf_counter();qaindices=set(np.linspace(0,len(frames)-1,4,dtype=int))
 for i,frame in enumerate(frames):
  t=int(frame['timestamp_ns']);clip=frame['clip_id']
  with np.load(ROOT/frame['source_city_points_ref']) as data:allpoints=data['xyz_city'].astype(float)
  ego=np.array(frame['city_from_ego_at_lidar'])[:3,3];distance=np.linalg.norm(allpoints[:,:2]-ego[:2],axis=1);valid=(distance>2.5)&(distance<=60);source_indices=np.flatnonzero(valid);points=allpoints[valid];height=points[:,2]-ground.sample(points[:,:2]);good=np.isfinite(height)&(height>=.25)&(height<=3.5);points=points[good];source_indices=source_indices[good]
  localized=[];framedets=[];qa_data=[]
  for vid in frame['view_ids']:
   view=views[vid];camera=view['camera'];ds=detbyview[vid];state=twod[camera]
   for key in list(state):
    if state[key]['clip']!=clip or (t-state[key]['timestamp'])/1e9>.4:del state[key]
   keys=list(state);cost=np.full((len(keys),len(ds)),1e6)
   for a,key in enumerate(keys):
    for b,d in enumerate(ds):
     val=iou(np.array(state[key]['box']),np.array(d['bbox_xyxy_px']))
     if val>=.2:cost[a,b]=1-val
   links={}
   if cost.size:
    aa,bb=linear_sum_assignment(cost)
    for a,b in zip(aa,bb):
     if cost[a,b]<1e6:links[b]=keys[a]
   ci=cal[camera];pose=np.array(view['city_from_ego_at_image'])@np.array(ci['ego_from_camera']);pc=(points-pose[:3,3])@pose[:3,:3];intr=ci['intrinsics_original'];w,h=view['image_size_wh']
   with np.errstate(divide='ignore',invalid='ignore'):uv=pc[:,:2]/pc[:,2,None]*[intr['fx_px'],intr['fy_px']]+[intr['cx_px'],intr['cy_px']]
   uv*=np.array([w,h])/[intr['width_px'],intr['height_px']];mask=np.isfinite(uv).all(1)&(pc[:,2]>.5)&(uv[:,0]>=0)&(uv[:,0]<w)&(uv[:,1]>=0)&(uv[:,1]<h)
   for j,d in enumerate(ds):
    if j not in links:two_counter+=1;links[j]=f'vehicle2d_{two_counter:06d}'
    tid2=links[j];state[tid2]={'clip':clip,'timestamp':t,'box':d['bbox_xyxy_px']};local,reason=associate_detection(d,uv[mask],pc[mask,2],points[mask],source_indices[mask])
    row={**d,'schema_version':'1.0.0','run_id':'m5_vehicle_v1','source_run_id':d['run_id'],'clip_id':clip,'lidar_timestamp_ns':str(t),'track_id_2d':tid2,'track_id':None,'measurement_id':None,'localization_status':'localized_3d' if local else 'two_d_only','localization_reason':reason,'position_city':local['position_city'] if local else None,'point_count':local['point_count'] if local else 0,'source_point_indices':local['source_point_indices'] if local else [],'source_city_points_ref':frame['source_city_points_ref'],'quality_flags':['coarse_vehicle_class','2d_track_iou_only','no_independent_detection_gt']+local['quality_flags'] if local else ['coarse_vehicle_class','2d_track_iou_only','no_reliable_3d_position']}
    framedets.append(row)
    if local:localized.append({'local':local,'detection':row,'track_id_2d':tid2,'camera':camera})
   if i in qaindices:qa_data.append((view,[r for r in framedets if r['camera']==camera]))
  groups=[]
  for item in sorted(localized,key=lambda v:-v['local']['point_count']):
   target=None
   for group in groups:
    if item['camera'] in {g['camera'] for g in group}:continue
    okay=True
    for other in group:
     shared=len(set(item['local']['source_point_indices'])&set(other['local']['source_point_indices']))/min(item['local']['point_count'],other['local']['point_count']);dist=np.linalg.norm(np.array(item['local']['position_city'][:2])-other['local']['position_city'][:2])
     if shared<.25 and dist>.75:okay=False;break
    if okay:target=group;break
   if target is None:groups.append([item])
   else:target.append(item)
  measurements=[]
  for j,group in enumerate(groups):
   ids=sorted({p for g in group for p in g['local']['source_point_indices']});center=np.median(allpoints[ids],axis=0);mid=f"measurement:{frame['frame_id']}:{j:03d}"
   m={'measurement_id':mid,'timestamp_ns':str(t),'clip_id':clip,'position_city':center.tolist(),'point_count':len(ids),'source_point_indices':ids,'depth_m':min(g['local']['depth_m'] for g in group),'detection_ids':[g['detection']['detection_id'] for g in group],'track_ids_2d':[g['track_id_2d'] for g in group],'cameras':sorted(g['camera'] for g in group),'source_city_points_ref':frame['source_city_points_ref'],'position_definition':RULES['position_definition'],'track_id':None}
   measurements.append(m)
   for g in group:g['detection']['measurement_id']=mid
  samples,mapping=tracker.step(measurements,t,clip)
  for sample in samples:sample.update(schema_version='1.0.0',run_id='m5_vehicle_v1',scene_id=scene.name)
  for m in measurements:m['track_id']=mapping[m['measurement_id']];m.update(schema_version='1.0.0',run_id='m5_vehicle_v1',scene_id=scene.name)
  for d in framedets:
   if d['measurement_id']:d['track_id']=mapping[d['measurement_id']]
   if d['localization_status']=='two_d_only':d['geometry_city']=None
   else:d['geometry_city']=d['position_city']
  detection_rows.extend(framedets);measurements_all.extend(measurements);track_samples.extend(samples);frame_reports.append({'frame_id':frame['frame_id'],'clip_id':clip,'detections':len(framedets),'localized_detections':len(localized),'merged_measurements':len(measurements),'active_samples':len(samples)})
  for view,ds in qa_data:
   with Image.open(ROOT/view['source_image_ref']) as image:im=image.convert('RGB')
   draw=ImageDraw.Draw(im)
   ci=cal[view['camera']];pose=np.array(view['city_from_ego_at_image'])@np.array(ci['ego_from_camera']);intr=ci['intrinsics_original']
   for d in ds:
    color=(0,255,100) if d['position_city'] is not None else (255,160,0);draw.rectangle(tuple(d['bbox_xyxy_px']),outline=color,width=3);draw.text(tuple(d['bbox_xyxy_px'][:2]),d['track_id'] or d['track_id_2d'],fill=color)
    if d['source_point_indices']:
     p=(allpoints[d['source_point_indices']]-pose[:3,3])@pose[:3,:3];pix=p[:,:2]/p[:,2,None]*[intr['fx_px'],intr['fy_px']]+[intr['cx_px'],intr['cy_px']];pix*=np.array(im.size)/[intr['width_px'],intr['height_px']]
     for u,v in pix[::3]:draw.ellipse((u-1,v-1,u+1,v+1),fill=(0,255,255))
   im.thumbnail((1000,750));im.save(out/'qa'/f"{i:04d}_{view['camera']}.jpg",quality=90)
  if (i+1)%25==0 or i+1==len(frames):print(f'{i+1}/{len(frames)} frames; {len(measurements_all)} 3D measurements; {len(track_samples)} samples',flush=True)
 for tid in list(tracker.active):tracker.finish(tid,'end_of_clip' if not args.limit else 'pilot_boundary')
 for name,data in [('detections',detection_rows),('measurements',measurements_all),('track_samples',track_samples),('tracks',tracker.finished),('frame_reports',frame_reports)]:
  with (out/f'{name}.jsonl').open('w',encoding='utf-8') as f:
   for r in data:f.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n')
 write(out/'rules.json',RULES)
 summary={'frames':len(frames),'detection_count':len(detection_rows),'localization_status':dict(Counter(r['localization_status'] for r in detection_rows)),'two_d_only_reasons':dict(Counter(r['localization_reason'] for r in detection_rows if r['localization_reason'])),'merged_3d_measurements':len(measurements_all),'tracks':len(tracker.finished),'confirmed_tracks':sum(t['confirmed'] for t in tracker.finished),'two_d_track_ids':two_counter,'track_samples':len(track_samples),'observed_samples':sum(s['observed'] for s in track_samples),'predicted_samples':sum(not s['observed'] for s in track_samples),'elapsed_seconds':time.perf_counter()-started,'gt_read':False,'limitations':['Coarse vehicle class, no car/truck/bus distinction','Visible point median is not amodal box center','Potential background contamination and ID switches','Only three front cameras; short and fragmented trajectories possible','Predicted samples are not measurements','No independent 3D/tracking GT, no accuracy or IDF1 claim']};write(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
 write(out/'provenance.json',{'source_m2_package':source.name,'input_files_sha256':{n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in ['tables/frames.jsonl','tables/views.jsonl','tables/detections.jsonl','calibration.json']},'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'gt_read':False})
if __name__=='__main__':main()
