"""Per-observation map discrepancy candidates from frozen M2; no GT read."""
import json,csv,hashlib,zipfile
from pathlib import Path
from collections import Counter,defaultdict
import numpy as np
ROOT=Path(__file__).resolve().parent
RULES={'version':'m3_rules_v1','sample_spacing_m':.5,'min_observed_length_m':3.,'min_projected_fraction':.5,'min_model_score':.6,'max_geometry_difference_m':1.5,'attribute_geometry_max_m':.75,'min_map_overlap_length_m':3.,'addition_min_distance_any_boundary_m':3.,'local_map_radius_m':30.,'strict_direction_deg':20.,'addition_parallel_direction_deg':25.,'addition_parallel_min_overlap_m':3.,'deletion_enabled':False,'topology_enabled':False,'lane_use_enabled':False,'multiframe_confirmation':False}
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def rows(p):return [json.loads(s) for s in p.read_text(encoding='utf-8').splitlines()]
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
def lengths(line):return np.r_[0,np.cumsum(np.linalg.norm(np.diff(line,axis=0),axis=1))]
def sample(line,spacing=.5):
 line=np.asarray(line,float);s=lengths(line);keep=np.r_[True,np.diff(s)>1e-8];line,s=line[keep],s[keep]
 if len(line)<2:return line
 t=np.unique(np.r_[np.arange(0,s[-1],spacing),s[-1]])
 return np.column_stack([np.interp(t,s,line[:,i]) for i in range(line.shape[1])])
def project_to_line(points,line):
 a=line[:-1];v=np.diff(line,axis=0);seg=np.linalg.norm(v,axis=1);den=np.maximum(seg*seg,1e-12)
 u=np.clip(((points[:,None]-a)*v).sum(2)/den,0,1);d=np.linalg.norm(points[:,None]-(a+u[:,:,None]*v),axis=2);idx=d.argmin(1)
 return d[np.arange(len(points)),idx],lengths(line)[idx]+u[np.arange(len(points)),idx]*seg[idx]
def clip_line(line,start,end):
 s=lengths(line);t=np.unique(np.r_[start,s[(s>start)&(s<end)],end])
 return np.column_stack([np.interp(t,s,line[:,i]) for i in range(line.shape[1])])
def compare(observed,line):
 p=sample(observed[:,:2]);dist,along=project_to_line(p,line);start,end=float(along.min()),float(along.max());clipped=clip_line(line,start,end)
 v=observed[-1,:2]-observed[0,:2];w=clipped[-1]-clipped[0];den=np.linalg.norm(v)*np.linalg.norm(w)
 angle=float(np.degrees(np.arccos(np.clip(abs(np.dot(v,w))/den,0,1)))) if den>1e-8 else None
 if end-start<1e-8:return {'difference_m':None,'overlap_m':0.,'angle_deg':angle,'map_geometry_xy':clipped.tolist(),'obs_to_map_m':float(dist.mean()),'map_to_obs_m':None}
 q=sample(clipped);reverse,_=project_to_line(q,observed[:,:2]);difference=.5*(float(dist.mean())+float(reverse.mean()))
 return {'difference_m':difference,'overlap_m':end-start,'angle_deg':angle,'map_geometry_xy':clipped.tolist(),'obs_to_map_m':float(dist.mean()),'map_to_obs_m':float(reverse.mean())}
def main():
 source=next((ROOT/'deliveries').glob('*_M2_v1'));prior=read(source/'prior_map.json');obs=rows(source/'tables/observations.jsonl');matches={r['observation_id']:r for r in rows(source/'tables/matches.jsonl')};frames=rows(source/'tables/frames.jsonl')
 out=ROOT/'deliveries'/source.name.replace('_M2_v1','_M3_v1');out.mkdir(exist_ok=True)
 boundaries={}
 for lane in prior['lane_segments']:
  for side in ['left','right']:
   line=np.array([[p['x'],p['y']] for p in lane[side+'_lane_boundary']],float)
   if len(line)>1:boundaries[(lane['id'],side)]=(line,lane[side+'_lane_mark_type'])
 decisions=[];candidates=[];seen=defaultdict(Counter)
 for o in obs:
  m=matches[o['observation_id']];oid=o['observation_id'];key=(o['frame_id'],m.get('best_map_element_id'),m['candidates'][0]['boundary_side'] if m['candidates'] else None)
  if m['status']=='matched':seen[key]['matched']+=1
  for c in m['candidates']:
   if m['status']=='ambiguous':seen[o['frame_id'],c['map_element_id'],c['boundary_side']]['ambiguous']+=1
  decision={'schema_version':'1.0.0','run_id':'m3_rules_v1','source_run_id':o['run_id'],'scene_id':o['scene_id'],'clip_id':o['clip_id'],'frame_id':o['frame_id'],'view_id':o['view_id'],'camera':o['camera'],'image_timestamp_ns':o['image_timestamp_ns'],'observation_id':oid,'m2_match_status':m['status'],'status':'insufficient_evidence','reason_codes':[],'candidate_id':None,'change_type':None,'metrics':None,'attribute_assessment':'unknown','visibility':o['visibility']}
  decision['quality_flags']=list(o['quality_flags']);geo=np.array(o['geometry_city']) if o['geometry_city'] is not None else None
  reasons=decision['reason_codes']
  if geo is None:reasons.append('NO_USABLE_CITY_GEOMETRY')
  else:
   if o['length_m']<RULES['min_observed_length_m']:reasons.append('CURVE_TOO_SHORT')
   if len(o['geometry_pixel_indices'])/len(o['pixel_curve_xy'])<RULES['min_projected_fraction']:reasons.append('PROJECTED_FRACTION_TOO_LOW')
   if o['model_score_mean']<RULES['min_model_score']:reasons.append('MODEL_SCORE_TOO_LOW')
  if m['status']=='ambiguous':reasons.append('MAP_IDENTITY_AMBIGUOUS')
  change=None;map_id=None;side=None;metrics=None
  if not reasons and m['status']=='matched':
   map_id=m['best_map_element_id'];side=m['candidates'][0]['boundary_side'];line,mark=boundaries[map_id,side];metrics=compare(geo,line);decision['metrics']=metrics
   if metrics['overlap_m']<RULES['min_map_overlap_length_m']:reasons.append('MAP_OVERLAP_TOO_SHORT')
   if metrics['angle_deg'] is None or metrics['angle_deg']>RULES['strict_direction_deg']:reasons.append('DIRECTION_INCONSISTENT')
   if mark in ['NONE','UNKNOWN']:reasons.append('MAP_BOUNDARY_NOT_KNOWN_PAINTED_MARKING')
   if not reasons:
    if metrics['difference_m']>RULES['max_geometry_difference_m']:change='geometry_changed'
    else:
     decision['status']='no_discrepancy_detected';reasons.append('GEOMETRY_WITHIN_THRESHOLD')
     mapcolor='yellow' if 'YELLOW' in mark else 'white' if 'WHITE' in mark else 'unknown'
     if o['color']!='unknown' and mapcolor!='unknown':
      decision['attribute_assessment']='color_matches' if o['color']==mapcolor else 'color_differs'
      if o['color']!=mapcolor and metrics['difference_m']<=RULES['attribute_geometry_max_m']:
       change='marking_changed';decision['quality_flags'].append('color_only_heuristic_not_solid_dashed');metrics.update(observed_color=o['color'],prior_color=mapcolor)
     else:decision['attribute_assessment']='not_evaluable_unknown_color_or_pattern'
  elif not reasons and m['status']=='unmatched':
   # Conservative unmatched handling: reject proximity to ANY prior boundary,
   # irrespective of M2 direction gating, rather than calling every unmatched curve added.
   p=sample(geo[:,:2]);distances=[(float(project_to_line(p,line)[0].min()),eid,s,line) for (eid,s),(line,mark) in boundaries.items()]
   nearest=min(distances,key=lambda v:v[0]);metrics={'minimum_distance_to_any_prior_boundary_m':nearest[0],'nearest_prior_element_id':nearest[1],'nearest_boundary_side':nearest[2]};decision['metrics']=metrics
   if nearest[0]<=RULES['addition_min_distance_any_boundary_m']:reasons.append('UNMATCHED_BUT_NEAR_PRIOR_BOUNDARY')
   else:
    # A remote parallel counterpart might be a large geometry edit, not an insertion.
    alternatives=[]
    for (eid,s),(line,mark) in boundaries.items():
     result=compare(geo,line)
     if result['angle_deg'] is not None and result['angle_deg']<=RULES['addition_parallel_direction_deg'] and result['overlap_m']>=RULES['addition_parallel_min_overlap_m']:alternatives.append(eid+':'+s)
    metrics['remote_parallel_counterparts']=alternatives
    if alternatives:reasons.append('ADDITION_VS_LARGE_GEOMETRY_EDIT_UNRESOLVED')
    else:change='added'
  if change:
   cid=f"m3:{oid}:{change}";decision.update(status='candidate',candidate_id=cid,change_type=change);reasons[:]=['SINGLE_FRAME_DIFFERENCE_PENDING_M4']
   candidates.append({'schema_version':'1.0.0','run_id':'m3_rules_v1','source_run_id':o['run_id'],'candidate_id':cid,'scene_id':o['scene_id'],'clip_id':o['clip_id'],'frame_id':o['frame_id'],'view_id':o['view_id'],'observation_id':oid,'camera':o['camera'],'image_timestamp_ns':o['image_timestamp_ns'],'change_type':change,'map_element_id':map_id,'boundary_side':side,'geometry_city':o['geometry_city'],'pixel_curve_xy':o['pixel_curve_xy'],'difference_value':metrics['difference_m'] if change=='geometry_changed' else 1 if change=='marking_changed' else None,'difference_unit':'m' if change=='geometry_changed' else 'categorical' if change=='marking_changed' else None,'metrics':metrics,'geometry_usable':True,'observation_valid':None,'visibility':'unknown','status':'pending_multiframe_confirmation','evidence_score':None,'quality_flags':decision['quality_flags']+['not_confirmed_change','thresholds_not_independently_calibrated']})
  decisions.append(decision)
 mapchecks=[]
 for f in frames:
  xy=np.array(f['city_from_ego_at_lidar'])[:2,3]
  for (eid,side),(line,mark) in boundaries.items():
   if project_to_line(xy[None],line)[0][0]>RULES['local_map_radius_m']:continue
   counts=seen[f['frame_id'],eid,side]
   mapchecks.append({'schema_version':'1.0.0','run_id':'m3_rules_v1','frame_id':f['frame_id'],'scene_id':f['scene_id'],'map_element_id':eid,'boundary_side':side,'matched_observation_count':counts['matched'],'ambiguous_observation_count':counts['ambiguous'],'visibility':'unknown','deletion_assessment':'not_evaluable','reason':'NO_VERIFIED_VISIBILITY_DENOMINATOR'})
 for name,data in [('decisions',decisions),('change_candidates',candidates),('map_checks',mapchecks)]:
  with (out/f'{name}.jsonl').open('w',encoding='utf-8') as f:
   for r in data:f.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n')
 with (out/'change_candidates.csv').open('w',encoding='utf-8-sig',newline='') as f:
  fields=['candidate_id','frame_id','camera','change_type','map_element_id','boundary_side','difference_value','status'];w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(candidates)
 write(out/'rules.json',RULES)
 write(out/'provenance.json',{'source_m2_package':source.name,'source_files_sha256':{name:hashlib.sha256((source/name).read_bytes()).hexdigest() for name in ['prior_map.json','tables/frames.jsonl','tables/observations.jsonl','tables/matches.jsonl']},'source_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'gt_read':False,'source_map':'M2 exported stale prior, not original TBV current map'})
 summary={'input_observations':len(obs),'frames':len(frames),'views':len(rows(source/'tables/views.jsonl')),'views_with_observations':len(set(o['view_id'] for o in obs)),'candidates':len(candidates),'candidate_types':dict(Counter(c['change_type'] for c in candidates)),'decision_status':dict(Counter(d['status'] for d in decisions)),'reasons':dict(Counter(r for d in decisions for r in d['reason_codes'])),'map_boundary_frame_checks':len(mapchecks),'confirmed_changes':0,'deletion_enabled':False,'gt_read':False,'limitations':['Single-frame candidates only; M4 not run','No verified visibility, so no deletion conclusions','Color-only attribute evidence is heuristic; solid/dashed unknown','No topology, lane-use or crosswalk change perception','Remote parallel alternatives suppress added hypotheses conservatively','M2 curb confusion and false negatives propagate']}
 write(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
