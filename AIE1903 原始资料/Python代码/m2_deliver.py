"""Full-scene YOLOP M2 v1 delivery. No GT/change annotations read."""
import json,time,hashlib,shutil,zipfile
from collections import Counter
from pathlib import Path
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image,ImageDraw
from m1_prepare import ROOT,read,write
from m2_extract import Ground,nearest_distance
from m2_yolop_trial import nms
VERSION='1.0.0';RUN='yolop640_v1';CAMERAS=['ring_front_center','ring_front_left','ring_front_right']

def thin(mask):
 a=np.pad(mask.astype(np.uint8),1)
 for _ in range(48):
  changed=0
  for phase in [0,1]:
   p=[a[:-2,1:-1],a[:-2,2:],a[1:-1,2:],a[2:,2:],a[2:,1:-1],a[2:,:-2],a[1:-1,:-2],a[:-2,:-2]]
   count=sum(p);trans=sum(((p[i]==0)&(p[(i+1)%8]==1)).astype(np.uint8) for i in range(8))
   test=(p[0]*p[2]*p[4]==0)&(p[2]*p[4]*p[6]==0) if phase==0 else (p[0]*p[2]*p[6]==0)&(p[0]*p[4]*p[6]==0)
   drop=(a[1:-1,1:-1]==1)&(count>=2)&(count<=6)&(trans==1)&test
   changed+=int(drop.sum());a[1:-1,1:-1][drop]=0
  if changed==0:break
 return a[1:-1,1:-1]

def paths(mask):
 points={tuple(v) for v in np.argwhere(thin(mask))};graph={}
 for y,x in sorted(points):
  neighbors=[]
  for dy,dx in [(-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)]:
   v=(y+dy,x+dx)
   if v not in points:continue
   if dy and dx and ((y+dy,x) in points or (y,x+dx) in points):continue
   neighbors.append(v)
  graph[y,x]=neighbors
 visited=set();curves=[]
 starts=sorted(graph,key=lambda v:(len(graph[v])==2,v))
 for start in starts:
  for nxt in graph[start]:
   edge=tuple(sorted([start,nxt]))
   if edge in visited:continue
   visited.add(edge);path=[start,nxt];prev,cur=start,nxt
   while len(graph[cur])==2:
    options=[v for v in graph[cur] if v!=prev];node=options[0];edge=tuple(sorted([cur,node]))
    if edge in visited:break
    visited.add(edge);path.append(node);prev,cur=cur,node
   if len(path)>=12:
    curve=np.array(path)[::4,::-1].astype(float)
    if not np.array_equal(curve[-1],np.array(path[-1])[::-1]):curve=np.vstack([curve,np.array(path[-1])[::-1]])
    curves.append(curve)
 return curves

def main():
 m1=next((ROOT/'m1_outputs').iterdir());records=[json.loads(v) for v in (m1/'frames.jsonl').read_text(encoding='utf-8').splitlines()];scene=(ROOT/records[0]['lidar_source']).parents[2]
 out=ROOT/'deliveries'/f'{scene.name}_M2_v1';out.mkdir(parents=True,exist_ok=True)
 for d in ['masks','overlays','cache','tables']: (out/d).mkdir(exist_ok=True)
 cal=read(m1/'calibration.json');prior=read(ROOT/'m2_inputs/prior_map.json');ground=Ground(scene);model=ROOT/'model_sources/YOLOP/weights/yolop-640-640.onnx'
 modelhash=hashlib.sha256(model.read_bytes()).hexdigest();inputhash=hashlib.sha256((m1/'frames.jsonl').read_bytes()).hexdigest();priorhash=hashlib.sha256((ROOT/'m2_inputs/prior_map.json').read_bytes()).hexdigest()
 signature=hashlib.sha256((modelhash+inputhash+priorhash+hashlib.sha256(Path(__file__).read_bytes()).hexdigest()).encode()).hexdigest()
 write(out/'run.json',{'schema_version':VERSION,'run_id':RUN,'scene_id':scene.name,'status':'RUNNING','signature':signature,'model_sha256':modelhash,'m1_frames_sha256':inputhash,'prior_sha256':priorhash,'inference_gt_read':False})
 write(out/'calibration.json',cal);write(out/'prior_map.json',prior)
 if (ROOT/'m2_inputs/provenance.json').exists():shutil.copy2(ROOT/'m2_inputs/provenance.json',out/'prior_provenance.json')
 opts=ort.SessionOptions();opts.intra_op_num_threads=4;opts.inter_op_num_threads=1;session=ort.InferenceSession(str(model),sess_options=opts,providers=['CPUExecutionProvider']);cv2.setNumThreads(1)
 session.run(None,{'images':np.zeros((1,3,640,640),np.float32)})
 bounds=[]
 for lane in prior['lane_segments']:
  for side in ['left','right']:
   xy=np.array([[v['x'],v['y']] for v in lane[side+'_lane_boundary']])
   if len(xy)>1:bounds.append((lane['id'],side,xy,lane[side+'_lane_mark_type']))
 started=time.perf_counter();qa_indices=set(np.linspace(0,len(records)-1,5,dtype=int))
 for i,r in enumerate(records):
  ego=np.array(r['ego_pose_city'])[:2,3];local=[b for b in bounds if nearest_distance(ego[None],b[2])[0]<=60]
  for camera in CAMERAS:
   view_id=f"{scene.name}:{r['timestamp_ns']}:{camera}";stem=f'{i:04d}_{camera}';cache=out/'cache'/f'{stem}.json'
   if cache.exists() and read(cache).get('signature')==signature:continue
   ci=cal[camera];inf=r['images'][camera];timestamp=inf['timestamp_ns']
   with Image.open(ROOT/inf['path']) as im:rgb=np.array(im.convert('RGB'))
   h,w=rgb.shape[:2];ratio=min(640/h,640/w);nw,nh=round(w*ratio),round(h*ratio);left,top=(640-nw)//2,(640-nh)//2
   canvas=np.full((640,640,3),114,np.uint8);canvas[top:top+nh,left:left+nw]=cv2.resize(rgb,(nw,nh),interpolation=cv2.INTER_AREA)
   x=(canvas.astype(np.float32)/255-np.array([.485,.456,.406],np.float32))/np.array([.229,.224,.225],np.float32)
   t=time.perf_counter();det,da,ll=session.run(None,{'images':np.ascontiguousarray(x.transpose(2,0,1)[None])});seconds=time.perf_counter()-t
   assert np.isfinite(det).all() and np.isfinite(da).all() and np.isfinite(ll).all()
   road=da[0].argmax(0).astype(np.uint8)[top:top+nh,left:left+nw];raw=ll[0].argmax(0).astype(np.uint8)[top:top+nh,left:left+nw]
   boxes=nms(det);boxes[:,[0,2]]=(boxes[:,[0,2]]-left)/ratio;boxes[:,[1,3]]=(boxes[:,[1,3]]-top)/ratio;boxes[:,[0,2]]=np.clip(boxes[:,[0,2]],0,w);boxes[:,[1,3]]=np.clip(boxes[:,[1,3]],0,h)
   vehicles=[];blocked=np.zeros_like(raw)
   for j,b in enumerate(boxes):
    x1,y1,x2,y2=b[:4];ix1,iy1=int(np.floor(x1*nw/w)),int(np.floor(y1*nh/h));ix2,iy2=int(np.ceil(x2*nw/w)),int(np.ceil(y2*nh/h));blocked[iy1:iy2,ix1:ix2]=1
    vehicles.append({'schema_version':VERSION,'run_id':RUN,'detection_id':f'{view_id}:vehicle:{j:03d}','view_id':view_id,'frame_id':r['timestamp_ns'],'scene_id':scene.name,'camera':camera,'image_timestamp_ns':timestamp,'bbox_xyxy_px':b[:4].tolist(),'score':float(b[4]),'class_name':'vehicle','track_id':None,'geometry_city':None,'quality_flags':['untracked_2d_detection','coarse_vehicle_class']})
   filtered=raw*(blocked==0);maskrefs={}
   for name,mask in [('road',road),('lane_raw',raw),('lane_vehicle_filtered',filtered)]:
    p=out/'masks'/f'{stem}_{name}.png';Image.fromarray(cv2.resize(mask,(w,h),interpolation=cv2.INTER_NEAREST)*255).save(p);maskrefs[name]=str(p.relative_to(out)).replace('\\','/')
   pose=np.array(inf['pose_city'])@np.array(ci['ego_from_camera']);k0=ci['intrinsics_original'];k={'fx':k0['fx_px']*w/k0['width_px'],'fy':k0['fy_px']*h/k0['height_px'],'cx':k0['cx_px']*w/k0['width_px'],'cy':k0['cy_px']*h/k0['height_px']}
   curves=paths(filtered);observations=[];matches=[];overlay=Image.fromarray(rgb);draw=ImageDraw.Draw(overlay)
   for j,curve in enumerate(curves):
    pixels=curve*np.array([w/nw,h/nh]);xyz=ground.project(pixels,pose,k);valid=np.isfinite(xyz).all(1)
    # Retain the longest contiguous valid portion; never bridge invalid ground samples.
    groups=np.split(np.arange(len(xyz)),np.flatnonzero(np.diff(valid.astype(int)))+1);groups=[g for g in groups if len(g)>=3 and valid[g].all()];g=max(groups,key=len) if groups else np.array([],dtype=int)
    # Break large metric jumps rather than inventing a continuous ground curve.
    if len(g):
     splits=np.split(g,np.flatnonzero(np.linalg.norm(np.diff(xyz[g,:2],axis=0),axis=1)>3)+1);g=max(splits,key=len)
    geo=xyz[g] if len(g)>=3 else None;length=float(np.linalg.norm(np.diff(geo[:,:2],axis=0),axis=1).sum()) if geo is not None else None
    if length is not None and not .75<=length<=60:geo=None;length=None;g=np.array([],dtype=int)
    ix=np.clip(curve[:,0].astype(int),0,nw-1);iy=np.clip(curve[:,1].astype(int),0,nh-1);foreground=ll[0,1,top:top+nh,left:left+nw];score=float(np.mean(foreground[iy,ix]))
    samplecolor=rgb[np.clip(pixels[:,1].astype(int),0,h-1),np.clip(pixels[:,0].astype(int),0,w-1)];hsv=cv2.cvtColor(samplecolor[None],cv2.COLOR_RGB2HSV)[0];yellow=(hsv[:,0]>=12)&(hsv[:,0]<=40)&(hsv[:,1]>=65)&(hsv[:,2]>=90);white=(hsv[:,1]<65)&(hsv[:,2]>=155);color='yellow' if yellow.mean()>.5 else 'white' if white.mean()>.5 else 'unknown'
    oid=f'{view_id}:line:{j:03d}';flags=['model_candidate','curb_confusion_possible','no_independent_accuracy','not_temporally_fused'];status='projected' if geo is not None else 'projection_failed'
    if len(g)<len(pixels):flags.append('ground_curve_partial_or_invalid')
    observation={'schema_version':VERSION,'run_id':RUN,'observation_id':oid,'view_id':view_id,'frame_id':r['timestamp_ns'],'scene_id':scene.name,'clip_id':r['clip_id'],'camera':camera,'image_timestamp_ns':timestamp,'element_type':'lane_boundary_candidate','pixel_curve_xy':pixels.tolist(),'geometry_city':geo.tolist() if geo is not None else None,'geometry_pixel_indices':g.tolist() if geo is not None else [],'length_m':length,'model_score_mean':score,'color':color,'marking_pattern':'unknown','visibility':'unknown','projection_status':status,'quality_flags':flags}
    observations.append(observation);candidates=[]
    if geo is not None:
     direction=geo[-1,:2]-geo[0,:2];norm=np.linalg.norm(direction)
     if norm>.1:
      for eid,side,line,mark in local:
       v=line[-1]-line[0];n=np.linalg.norm(v)
       if n<.1:continue
       angle=float(np.degrees(np.arccos(np.clip(abs(np.dot(direction/norm,v/n)),0,1))))
       if angle>25:continue
       ds=nearest_distance(geo[:,:2],line);distance=float(np.mean(ds));coverage=float(np.mean(ds<=3))
       if distance<=3 and coverage>=.5:candidates.append({'map_element_id':eid,'boundary_side':side,'mean_distance_m':distance,'direction_difference_deg':angle,'sample_fraction_within_3m':coverage})
    candidates.sort(key=lambda c:(c['mean_distance_m'],c['map_element_id'],c['boundary_side']));candidates=candidates[:3]
    mstatus='projection_failed' if geo is None else 'unmatched' if not candidates else 'ambiguous' if len(candidates)>1 and candidates[1]['mean_distance_m']-candidates[0]['mean_distance_m']<.25 else 'matched'
    matches.append({'schema_version':VERSION,'run_id':RUN,'observation_id':oid,'view_id':view_id,'status':mstatus,'candidates':candidates,'best_map_element_id':candidates[0]['map_element_id'] if mstatus=='matched' else None,'visibility':'unknown','change_type':None})
    draw.line([tuple(v) for v in pixels],fill=(0,255,150) if mstatus=='matched' else (255,150,0),width=3)
   for b in boxes:draw.rectangle(tuple(b[:4]),outline=(255,255,0),width=3)
   overlay_ref=None
   if i in qa_indices:
    overlay.thumbnail((1000,750));p=out/'overlays'/f'{stem}.jpg';overlay.save(p,quality=90);overlay_ref=str(p.relative_to(out)).replace('\\','/')
   view={'schema_version':VERSION,'run_id':RUN,'view_id':view_id,'frame_id':r['timestamp_ns'],'scene_id':scene.name,'camera':camera,'image_timestamp_ns':timestamp,'source_image_ref':inf['path'].replace('\\','/'),'image_size_wh':[w,h],'offset_ms':r['time_offsets_ms'][camera],'city_from_ego_at_image':inf['pose_city'],'masks':maskrefs,'overlay':overlay_ref,'inference_seconds':seconds,'visibility':'unknown','visibility_reason':'model masks and vehicle boxes are hints, not verified visibility','observation_count':len(observations),'detection_count':len(vehicles),'status':'processed'}
   write(cache,{'signature':signature,'view':view,'observations':observations,'matches':matches,'detections':vehicles})
  if (i+1)%25==0 or i+1==len(records):print(f'{i+1}/{len(records)} frames complete ({(i+1)*3} views), elapsed {time.perf_counter()-started:.0f}s',flush=True)
 allviews=[];allobs=[];allmatches=[];allvehicles=[]
 for i,r in enumerate(records):
  for c in CAMERAS:
   result=read(out/'cache'/f'{i:04d}_{c}.json');assert result['signature']==signature
   allviews.append(result['view']);allobs.extend(result['observations']);allmatches.extend(result['matches']);allvehicles.extend(result['detections'])
 frames=[{'schema_version':VERSION,'run_id':RUN,'frame_id':r['timestamp_ns'],'scene_id':scene.name,'clip_id':r['clip_id'],'timestamp_ns':r['timestamp_ns'],'city_from_ego_at_lidar':r['ego_pose_city'],'source_lidar_ref':r['lidar_source'].replace('\\','/'),'source_city_points_ref':r['lidar_xyz_city'].replace('\\','/'),'view_ids':[v['view_id'] for v in allviews if v['frame_id']==r['timestamp_ns']],'source_refs_root':'Argoverse workspace; external to this package'} for r in records]
 for name,rows in [('frames',frames),('views',allviews),('observations',allobs),('matches',allmatches),('detections',allvehicles)]:
  with (out/'tables'/f'{name}.jsonl').open('w',encoding='utf-8') as f:
   for row in rows:f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
 summary={'schema_version':VERSION,'run_id':RUN,'scene_id':scene.name,'frames':len(frames),'views':len(allviews),'observations':len(allobs),'projected_observations':sum(o['geometry_city'] is not None for o in allobs),'match_status_counts':dict(Counter(m['status'] for m in allmatches)),'vehicle_detections':len(allvehicles),'mean_inference_seconds':float(np.mean([v['inference_seconds'] for v in allviews])),'elapsed_seconds':time.perf_counter()-started,'coverage':'Entire representative clip, three front cameras only','limitations':['No crosswalk perception or solid/dashed classification','Curb confusion and side-view false negatives','Visibility unknown: no deletion evidence','IDs are per-view observations, not tracks','No change inference or accuracy evaluation','Source RGB, LiDAR and City clouds are external references; masks and tables are self-contained']}
 write(out/'summary.json',summary);run=read(out/'run.json');run['status']='COMPLETED';write(out/'run.json',run);print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
