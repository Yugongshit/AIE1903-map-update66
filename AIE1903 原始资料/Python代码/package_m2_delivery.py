"""Validate complete delivery and create portable ZIP (without raw sensor data)."""
import json,hashlib,zipfile,shutil,re,math
from pathlib import Path
from collections import Counter
import numpy as np
from PIL import Image
ROOT=Path(__file__).resolve().parent
out=next((ROOT/'deliveries').glob('*_M2_v1'))
def read(path):return json.loads(path.read_text(encoding='utf-8'))
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def table(name):return [json.loads(s) for s in (out/'tables'/f'{name}.jsonl').read_text(encoding='utf-8').splitlines()]

def validate_subset(value,schema,path='row'):
 """Offline checker for every assertion keyword used by make_schema's definitions."""
 errors=[]
 if 'oneOf' in schema:
  count=sum(not validate_subset(value,s,path) for s in schema['oneOf'])
  return [] if count==1 else [path+':oneOf']
 types={'null':value is None,'string':isinstance(value,str),'number':isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value),'integer':isinstance(value,int) and not isinstance(value,bool),'array':isinstance(value,list),'object':isinstance(value,dict)}
 if 'type' in schema:
  ts=schema['type'];ts=[ts] if isinstance(ts,str) else ts
  if not any(types[t] for t in ts):return [path+':type']
 if 'const' in schema and value!=schema['const']:errors.append(path+':const')
 if 'enum' in schema and value not in schema['enum']:errors.append(path+':enum')
 if isinstance(value,str) and 'pattern' in schema and not re.search(schema['pattern'],value):errors.append(path+':pattern')
 if isinstance(value,(int,float)):
  if 'minimum' in schema and value<schema['minimum']:errors.append(path+':minimum')
  if 'maximum' in schema and value>schema['maximum']:errors.append(path+':maximum')
 if isinstance(value,list):
  if len(value)<schema.get('minItems',0) or len(value)>schema.get('maxItems',float('inf')):errors.append(path+':array length')
  if 'items' in schema:
   for i,v in enumerate(value):errors.extend(validate_subset(v,schema['items'],path+f'[{i}]'))
 if isinstance(value,dict):
  for k in schema.get('required',[]):
   if k not in value:errors.append(path+':missing '+k)
  props=schema.get('properties',{})
  for k,v in value.items():
   if k in props:errors.extend(validate_subset(v,props[k],path+'.'+k))
   elif isinstance(schema.get('additionalProperties'),dict):errors.extend(validate_subset(v,schema['additionalProperties'],path+'.'+k))
 return errors

def make_schema():
 string={'type':'string'};number={'type':'number'};null={'type':'null'};timestamp={'type':'string','pattern':'^[0-9]+$'}
 def arr(item,n=None):
  d={'type':'array','items':item}
  if n is not None:d.update(minItems=n,maxItems=n)
  return d
 xy=arr(number,2);xyz=arr(number,3);matrix=arr(arr(number,4),4)
 def record(required,props):return {'type':'object','required':['schema_version','run_id']+required,'properties':{'schema_version':{'const':'1.0.0'},'run_id':string,**props},'additionalProperties':True}
 curve={'type':'array','items':xy,'minItems':3};city={'oneOf':[{'type':'array','items':xyz,'minItems':3},null]}
 defs={
 'frames':record(['scene_id','clip_id','frame_id','timestamp_ns','view_ids','city_from_ego_at_lidar'],{'scene_id':string,'clip_id':string,'frame_id':timestamp,'timestamp_ns':timestamp,'view_ids':arr(string,3),'city_from_ego_at_lidar':matrix}),
 'views':record(['view_id','scene_id','frame_id','camera','image_timestamp_ns','offset_ms','image_size_wh','masks','visibility','status'],{'view_id':string,'scene_id':string,'frame_id':timestamp,'camera':string,'image_timestamp_ns':timestamp,'offset_ms':number,'image_size_wh':arr({'type':'integer','minimum':1},2),'city_from_ego_at_image':matrix,'masks':{'type':'object','required':['road','lane_raw','lane_vehicle_filtered'],'additionalProperties':string},'visibility':{'enum':['visible','occluded','unknown']},'status':{'const':'processed'}}),
 'observations':record(['observation_id','view_id','frame_id','scene_id','camera','image_timestamp_ns','element_type','pixel_curve_xy','geometry_city','geometry_pixel_indices','projection_status','model_score_mean','color','marking_pattern','visibility','quality_flags'],{'observation_id':string,'view_id':string,'frame_id':timestamp,'scene_id':string,'camera':string,'image_timestamp_ns':timestamp,'element_type':{'const':'lane_boundary_candidate'},'pixel_curve_xy':curve,'geometry_city':city,'geometry_pixel_indices':arr({'type':'integer','minimum':0}),'length_m':{'type':['number','null']},'projection_status':{'enum':['projected','projection_failed']},'model_score_mean':number,'color':{'enum':['white','yellow','unknown']},'marking_pattern':{'enum':['solid','dashed','unknown']},'visibility':{'enum':['visible','occluded','unknown']},'quality_flags':arr(string)}),
 'matches':record(['observation_id','view_id','status','candidates','best_map_element_id','visibility','change_type'],{'observation_id':string,'view_id':string,'status':{'enum':['matched','ambiguous','unmatched','projection_failed']},'candidates':{'type':'array','maxItems':3,'items':{'type':'object','required':['map_element_id','boundary_side','mean_distance_m','direction_difference_deg','sample_fraction_within_3m'],'properties':{'map_element_id':string,'boundary_side':{'enum':['left','right']},'mean_distance_m':{'type':'number','minimum':0,'maximum':3},'direction_difference_deg':{'type':'number','minimum':0,'maximum':25},'sample_fraction_within_3m':{'type':'number','minimum':.5,'maximum':1}}}},'best_map_element_id':{'type':['string','null']},'visibility':{'enum':['visible','occluded','unknown']},'change_type':null}),
 'detections':record(['detection_id','view_id','frame_id','scene_id','camera','image_timestamp_ns','bbox_xyxy_px','score','class_name','track_id','geometry_city'],{'detection_id':string,'view_id':string,'frame_id':timestamp,'scene_id':string,'camera':string,'image_timestamp_ns':timestamp,'bbox_xyxy_px':arr(number,4),'score':{'type':'number','minimum':0,'maximum':1},'class_name':{'const':'vehicle'},'track_id':null,'geometry_city':null})}
 return {'$schema':'https://json-schema.org/draft/2020-12/schema','$comment':'Validate a row with $ref #/$defs/<table_name>, not as a combined table. Semantic/version upgrades follow README.','$defs':defs}

def main():
 assert read(out/'run.json')['status']=='COMPLETED'
 tables={n:table(n) for n in ['frames','views','observations','matches','detections']};frames=tables['frames'];views=tables['views'];obs=tables['observations'];matches=tables['matches'];vehicles=tables['detections']
 errors=[]
 def check(test,message):
  if not test:errors.append(message)
 check(len(frames)==582,'expected 582 frames');check(len(views)==1746,'expected 1746 views')
 fi={r['frame_id']:r for r in frames};vi={r['view_id']:r for r in views};oi={r['observation_id']:r for r in obs};mi={r['observation_id']:r for r in matches};di={r['detection_id']:r for r in vehicles}
 check(len(fi)==len(frames) and len(vi)==len(views) and len(oi)==len(obs) and len(mi)==len(matches) and len(di)==len(vehicles),'duplicate IDs')
 check(set(oi)==set(mi),'match references do not cover observations')
 schema=make_schema();write(out/'schema.json',schema)
 try:
  from jsonschema import Draft202012Validator
 except ImportError:Draft202012Validator=None
 if Draft202012Validator:
  for name,rows in tables.items():
   validator=Draft202012Validator({'$schema':schema['$schema'],'$defs':schema['$defs'],'$ref':f'#/$defs/{name}'})
   for i,row in enumerate(rows):
    for e in validator.iter_errors(row):errors.append(f'{name}:{i}:{e.message}')
 else:
  for name,rows in tables.items():
   for i,row in enumerate(rows):errors.extend(validate_subset(row,schema['$defs'][name],f'{name}:{i}'))
 prior=read(out/'prior_map.json');mapids={r['id'] for r in prior['lane_segments']};maskcount=0
 for v in views:
  check(v['frame_id'] in fi,'view missing frame');check(abs(v['offset_ms'])<=50,'sync outside 50ms');check(v['visibility']=='unknown','unexpected visibility')
  check(abs((int(v['image_timestamp_ns'])-int(v['frame_id']))/1e6-v['offset_ms'])<1e-6,'offset sign/time mismatch')
  for p in v['masks'].values():
   path=out/p;check(path.is_file(),f'missing mask {p}')
   with Image.open(path) as image:
    check(list(image.size)==v['image_size_wh'],f'mask size {p}');check(set(np.unique(np.asarray(image)))<={0,255},f'mask values {p}')
   maskcount+=1
 for frame in frames:check(set(frame['view_ids'])=={v['view_id'] for v in views if v['frame_id']==frame['frame_id']},'frame-view mismatch')
 cal=read(out/'calibration.json');reprojection_error=0.
 for o in obs:
  check(o['view_id'] in vi,'observation view missing');v=vi[o['view_id']];w,h=v['image_size_wh'];uv=np.array(o['pixel_curve_xy']);check(np.isfinite(uv).all() and (uv>=0).all() and (uv[:,0]<w).all() and (uv[:,1]<h).all(),'invalid pixel curve')
  g=o['geometry_city'];inds=o['geometry_pixel_indices'];check((g is None)==(o['projection_status']=='projection_failed'),'geometry status mismatch')
  if g is not None:
   p=np.array(g);check(np.isfinite(p).all() and len(p)==len(inds)>=3,'invalid City geometry');check(all(0<=i<len(uv) for i in inds),'geometry pixel indices invalid');length=float(np.linalg.norm(np.diff(p[:,:2],axis=0),axis=1).sum());check(abs(length-o['length_m'])<1e-6,'length mismatch')
   # Project retained City points back independently to original image pixels.
   c=cal[v['camera']];pose=np.array(v['city_from_ego_at_image'])@np.array(c['ego_from_camera']);pc=(p-pose[:3,3])@pose[:3,:3];intr=c['intrinsics_original'];xy=pc[:,:2]/pc[:,2,None]*[intr['fx_px'],intr['fy_px']]+[intr['cx_px'],intr['cy_px']];xy*=np.array([w,h])/[intr['width_px'],intr['height_px']]
   diff=float(np.max(np.abs(xy-uv[inds])));reprojection_error=max(reprojection_error,diff);check(diff<.001,'City-to-pixel consistency failed')
  check(o['frame_id']==v['frame_id'] and o['image_timestamp_ns']==v['image_timestamp_ns'],'observation timing mismatch')
 for m in matches:
  check(m['observation_id'] in oi,'match unknown observation');check(all(c['map_element_id'] in mapids and c['mean_distance_m']<=3 and c['direction_difference_deg']<=25 and c['sample_fraction_within_3m']>=.5 for c in m['candidates']),'invalid match')
  check((m['best_map_element_id'] is not None)==(m['status']=='matched'),'best match nullability');check(m['change_type'] is None,'unexpected change label')
 for d in vehicles:
  check(d['view_id'] in vi,'vehicle view missing');w,h=vi[d['view_id']]['image_size_wh'];x1,y1,x2,y2=d['bbox_xyxy_px'];check(0<=x1<=x2<=w and 0<=y1<=y2<=h,'invalid vehicle box')
 countobs=Counter(o['view_id'] for o in obs);countdet=Counter(d['view_id'] for d in vehicles)
 for v in views:check(v['observation_count']==countobs[v['view_id']] and v['detection_count']==countdet[v['view_id']],'view counts disagree')
 report={'status':'PASS' if not errors else 'FAIL','frames':len(frames),'views':len(views),'observations':len(obs),'matches':len(matches),'detections':len(vehicles),'masks_checked':maskcount,'max_reprojection_error_px':reprojection_error,'schema_validation_engine':'jsonschema' if Draft202012Validator else 'offline checker for assertion keywords used in this schema','errors':errors[:100],'error_count':len(errors),'note':'Data contract, links, coordinates, counts and masks only; no independent model accuracy evaluation.'}
 write(out/'validation.json',report);print(json.dumps(report,indent=2));assert not errors
 shutil.copy2(ROOT/'m2_delivery_contract.md',out/'README.md');shutil.copy2(ROOT/'read_m2_delivery.py',out/'read_delivery.py')
 write(out/'examples.json',{name:rows[0] if rows else None for name,rows in tables.items()})
 # Package contains derived data only. Cache and raw source references are excluded.
 files=sorted(p for p in out.rglob('*') if p.is_file() and 'cache' not in p.relative_to(out).parts and p.name!='file_manifest.json')
 manifest=[{'path':p.relative_to(out).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files];write(out/'file_manifest.json',{'files':manifest,'source_data_included':False,'hash_algorithm':'sha256'})
 zipfile_path=out.with_suffix('.zip')
 with zipfile.ZipFile(zipfile_path,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
  for p in files+[out/'file_manifest.json']:archive.write(p,arcname=out.name+'/'+p.relative_to(out).as_posix())
 with zipfile.ZipFile(zipfile_path) as archive:assert archive.testzip() is None
 print('DELIVERY',zipfile_path,'BYTES',zipfile_path.stat().st_size)
if __name__=='__main__':main()
