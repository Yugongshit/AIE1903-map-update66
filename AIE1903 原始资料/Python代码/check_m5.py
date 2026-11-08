"""Validate M5 lineage, observed/predicted distinction and tracking regressions."""
import json,hashlib,shutil,zipfile
from collections import defaultdict,Counter
import numpy as np
from m5_vehicles import ROOT,Tracker,associate_detection,read,rows,write

def regression():
 t0=1_000_000_000_000_000_000;tracker=Tracker();ids=[]
 for i in range(15):
  m={'measurement_id':f'test{i}','position_city':[i*.1,0.,1.],'point_count':100,'depth_m':10.,'track_ids_2d':['test2d']}
  samples,mapping=tracker.step([m],t0+i*100_000_000,'clip1');ids.append(mapping[m['measurement_id']])
 assert len(set(ids))==1 and abs(samples[0]['speed_mps']-1)<.2
 samples,_=tracker.step([],t0+1_500_000_000,'clip1');assert len(samples)==1 and samples[0]['observed'] is False and samples[0]['measurement_position_city'] is None and samples[0]['measurement_id'] is None
 samples,_=tracker.step([],t0+2_400_000_000,'clip1');assert not samples and tracker.finished[0]['end_reason']=='lost'
 m={'measurement_id':'aftergap','position_city':[2.4,0.,1.],'point_count':100,'depth_m':10.,'track_ids_2d':['test2d']};samples,mapping=tracker.step([m],t0+2_500_000_000,'clip1');assert mapping['aftergap']!=ids[0]
 previous=mapping['aftergap'];samples,mapping=tracker.step([{**m,'measurement_id':'clip2'}],t0+2_600_000_000,'clip2');assert mapping['clip2']!=previous
 rng=np.random.default_rng(42);points=np.vstack([rng.normal([10,0,1.5],[.2,.2,.2],(30,3)),rng.normal([20,0,1.5],[.2,.2,.2],(30,3))]);uv=np.tile([50.,65.],(60,1));depth=np.r_[np.full(30,10.),np.full(30,20.)]
 result,error=associate_detection({'bbox_xyxy_px':[0,0,100,100]},uv,depth,points,np.arange(60));assert not error and max(result['source_point_indices'])<30
 result,error=associate_detection({'bbox_xyxy_px':[0,0,100,100]},uv[:2],depth[:2],points[:2],np.arange(2));assert result is None and error=='INSUFFICIENT_NON_GROUND_POINTS'

def main():
 regression();out=next((ROOT/'deliveries').glob('*_M5_v1'));source=ROOT/'deliveries'/read(out/'provenance.json')['source_m2_package'];ds=rows(out/'detections.jsonl');ms=rows(out/'measurements.jsonl');ss=rows(out/'track_samples.jsonl');ts=rows(out/'tracks.jsonl');fr=rows(out/'frame_reports.jsonl');frames=rows(source/'tables/frames.jsonl');source_ds=rows(source/'tables/detections.jsonl')
 assert len(fr)==582 and len(ds)==len(source_ds) and {d['detection_id'] for d in ds}=={d['detection_id'] for d in source_ds}
 mids={m['measurement_id']:m for m in ms};tids={t['track_id']:t for t in ts};dids={d['detection_id']:d for d in ds};framebyid={f['frame_id']:f for f in frames};bytrack=defaultdict(list);byframe=defaultdict(list)
 assert len(mids)==len(ms) and len(tids)==len(ts) and len(dids)==len(ds)
 for s in ss:
  assert s['track_id'] in tids and s['clip_id']==tids[s['track_id']]['clip_id'];assert np.isfinite(s['position_city']).all() and np.isfinite(s['velocity_city_xy_mps']).all();assert abs(np.linalg.norm(s['velocity_city_xy_mps'])-s['speed_mps'])<1e-9;assert np.linalg.eigvalsh(s['position_covariance_xy']).min()>=-1e-9
  if s['observed']:
   assert s['measurement_id'] in mids and s['measurement_position_city']==mids[s['measurement_id']]['position_city'] and s['track_id']==mids[s['measurement_id']]['track_id']
  else:assert s['measurement_id'] is None and s['measurement_position_city'] is None and not s['velocity_usable_for_behavior_candidate']
  if s['heading_rad'] is not None:assert s['velocity_usable_for_behavior_candidate'] and s['speed_mps']>=1
  bytrack[s['track_id']].append(s)
 for tid,group in bytrack.items():
  time_values=[int(s['timestamp_ns']) for s in group];assert time_values==sorted(set(time_values));last=None
  for s in group:
   if s['observed']:last=int(s['timestamp_ns'])
   else:assert last is not None and (int(s['timestamp_ns'])-last)/1e9<=.5+1e-9
  assert sum(s['observed'] for s in group)==tids[tid]['observed_samples'] and len(group)==tids[tid]['sample_count']
 assert sum(s['observed'] for s in ss)==len(ms)
 assert len({s['measurement_id'] for s in ss if s['observed']})==len(ms)
 for m in ms:byframe[m['timestamp_ns']].append(m)
 max_error=0.;points_checked=0
 for frame_id,measurements in byframe.items():
  with np.load(ROOT/framebyid[frame_id]['source_city_points_ref']) as data:points=data['xyz_city']
  for m in measurements:
   ids=m['source_point_indices'];assert len(ids)==m['point_count'] and len(ids)==len(set(ids)) and all(0<=i<len(points) for i in ids)
   center=np.median(points[ids].astype(float),axis=0);error=float(np.max(np.abs(center-m['position_city'])));max_error=max(max_error,error);assert error<1e-8;points_checked+=len(ids)
   assert m['track_id'] in tids and all(dids[d]['measurement_id']==m['measurement_id'] for d in m['detection_ids'])
 for d in ds:
  if d['localization_status']=='two_d_only':assert d['position_city'] is None and d['measurement_id'] is None and d['track_id'] is None and d['track_id_2d']
  else:assert d['measurement_id'] in mids and d['track_id']==mids[d['measurement_id']]['track_id'] and d['point_count']==len(d['source_point_indices'])>=8
 for key in ['source_city_points_ref']:assert all(key in m for m in ms)
 two=Counter((d['view_id'],d['track_id_2d']) for d in ds);assert max(two.values())==1
 report={'status':'PASS','detections':len(ds),'measurements':len(ms),'tracks':len(ts),'track_samples':len(ss),'associated_point_references_checked':points_checked,'max_median_position_recompute_error_m':max_error,'regression_tests':['constant_velocity_tracking','prediction_is_not_measurement','long_gap_track_termination','clip_id_isolation','foreground_vs_background_cluster','insufficient_points_retained_as_2d'],'errors':[],'note':'Consistency checks, not independent vehicle localization or identity accuracy.'};write(out/'validation.json',report);print(json.dumps(report,indent=2))
 for filename,sourcefile in [('frames.jsonl',source/'tables/frames.jsonl'),('views.jsonl',source/'tables/views.jsonl'),('calibration.json',source/'calibration.json')]:shutil.copy2(sourcefile,out/filename)
 shutil.copy2(ROOT/'M5处理说明.md',out/'README.md')
 with (out/'track_samples.csv').open('w',encoding='utf-8-sig',newline='') as f:
  import csv
  fields=['track_id','timestamp_ns','observed','track_state','x_m','y_m','z_m','vx_mps','vy_mps','speed_mps','velocity_usable_for_behavior_candidate','measurement_id'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
  for s in ss:w.writerow({**{k:s[k] for k in ['track_id','timestamp_ns','observed','track_state','speed_mps','velocity_usable_for_behavior_candidate','measurement_id']},**dict(zip(['x_m','y_m','z_m'],s['position_city'])),**dict(zip(['vx_mps','vy_mps'],s['velocity_city_xy_mps']))})
 # Standalone XY track graphic; source trajectories are not assumed to be GT.
 from PIL import Image,ImageDraw
 image=Image.new('RGB',(1200,900),'white');draw=ImageDraw.Draw(image);positions=np.array([s['position_city'][:2] for s in ss if s['observed']]);lo=positions.min(0)-3;hi=positions.max(0)+3;scale=min(1100/(hi[0]-lo[0]),750/(hi[1]-lo[1]))
 def pix(p):return (50+(p[0]-lo[0])*scale,830-(p[1]-lo[1])*scale)
 for i,(tid,group) in enumerate(bytrack.items()):
  if not tids[tid]['confirmed']:continue
  measured=[s for s in group if s['observed']];color=((i*83)%180,(i*137)%180,(i*59)%180)
  if len(measured)>1:draw.line([pix(s['position_city']) for s in measured],fill=color,width=2)
 ego=[np.array(f['city_from_ego_at_lidar'])[:2,3] for f in frames];draw.line([pix(p) for p in ego],fill='black',width=4);draw.text((30,20),'M5 observed confirmed track states in City XY; black = ego',fill='black');draw.text((30,42),'Colors are predicted track IDs, not independently verified identities. Unit: meters; equal XY scale.',fill='black');image.save(out/'track_overview.png')
 files=sorted(p for p in out.rglob('*') if p.is_file() and p.name!='file_manifest.json');write(out/'file_manifest.json',{'files':[{'path':p.relative_to(out).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]})
 with zipfile.ZipFile(out.with_suffix('.zip'),'w',zipfile.ZIP_DEFLATED) as z:
  for p in files+[out/'file_manifest.json']:z.write(p,out.name+'/'+p.relative_to(out).as_posix())
 with zipfile.ZipFile(out.with_suffix('.zip')) as z:assert z.testzip() is None
 print('ZIP',out.with_suffix('.zip'),out.with_suffix('.zip').stat().st_size)
if __name__=='__main__':main()
