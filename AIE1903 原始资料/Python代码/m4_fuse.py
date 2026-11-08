"""Fuse M3 candidates; keep visibility-based evidence unavailable when unknown."""
import json,csv,hashlib,shutil,zipfile,copy
from collections import defaultdict,Counter
from pathlib import Path
import numpy as np
from m3_changes import ROOT,read,rows,write,sample,project_to_line

RULES={'version':'m4_fusion_v1','minimum_repeated_frames':5,'minimum_duration_s':1.,'minimum_valid_frames':5,'minimum_visibility_support_ratio':.7,'addition_cluster_distance_m':3.,'addition_direction_difference_deg':20.,'map_object_grouping':['scene_id','clip_id','change_type','map_element_id','boundary_side'],'score_definition':'S=N_support/N_valid using verified visible frames only; null if denominator unknown','gt_used':False}

def geometry_distance(a,b):
 a=sample(np.array(a)[:,:2]);b=sample(np.array(b)[:,:2])
 return .5*(float(project_to_line(a,b)[0].mean())+float(project_to_line(b,a)[0].mean()))

def group_candidates(candidates):
 mapped=defaultdict(list);added=[]
 for c in sorted(candidates,key=lambda r:(r['scene_id'],r['clip_id'],r['frame_id'],r['candidate_id'])):
  if c['map_element_id'] is not None:mapped[c['scene_id'],c['clip_id'],c['change_type'],c['map_element_id'],c['boundary_side']].append(c)
  else:
   match=None
   for group in added:
    rep=group[0]
    if (rep['scene_id'],rep['clip_id'],rep['change_type'])!=(c['scene_id'],c['clip_id'],c['change_type']):continue
    # Complete-link distance prevents chaining separate additions into one region.
    a=np.array(c['geometry_city']);v=a[-1,:2]-a[0,:2]
    okay=True
    for old in group:
     b=np.array(old['geometry_city']);w=b[-1,:2]-b[0,:2];den=np.linalg.norm(v)*np.linalg.norm(w)
     angle=np.degrees(np.arccos(np.clip(abs(np.dot(v,w))/den,0,1))) if den>1e-8 else 180
     if angle>RULES['addition_direction_difference_deg'] or geometry_distance(c['geometry_city'],old['geometry_city'])>RULES['addition_cluster_distance_m']:okay=False;break
    if okay:match=group;break
   if match is None:added.append([c])
   else:match.append(c)
 return list(mapped.values())+added

def fuse(group,frame_records,mapchecks):
 first=group[0];times=sorted({c['frame_id'] for c in group},key=int);start,end=times[0],times[-1]
 window=[r for r in frame_records if r['scene_id']==first['scene_id'] and r['clip_id']==first['clip_id'] and int(start)<=int(r['frame_id'])<=int(end)]
 frame_set={r['frame_id'] for r in window};byframe=defaultdict(list)
 for c in group:byframe[c['frame_id']].append(c)
 eligible=[r for r in mapchecks if r['scene_id']==first['scene_id'] and r['frame_id'] in frame_set and r['map_element_id']==first['map_element_id'] and r['boundary_side']==first['boundary_side']]
 visible={r['frame_id'] for r in eligible if r['visibility']=='visible'};unknown={r['frame_id'] for r in eligible if r['visibility']=='unknown'}
 checked={r['frame_id'] for r in eligible};missing=frame_set-checked
 supported={t for t in visible if any(c.get('observation_valid') is True for c in byframe[t])}
 denominator_known=not unknown and not missing and first['map_element_id'] is not None
 valid_count=len(visible) if denominator_known else None
 score=len(supported)/valid_count if valid_count else None
 support_duration=(int(end)-int(start))/1e9
 reasons=[]
 if len(times)<RULES['minimum_repeated_frames']:reasons.append('INSUFFICIENT_DISTINCT_SUPPORT_FRAMES')
 if support_duration<RULES['minimum_duration_s']:reasons.append('SUPPORT_DURATION_TOO_SHORT')
 if valid_count is None:reasons.append('VISIBILITY_DENOMINATOR_UNKNOWN')
 elif valid_count<RULES['minimum_valid_frames']:reasons.append('INSUFFICIENT_VERIFIED_VISIBLE_FRAMES')
 if score is None:reasons.append('VISIBILITY_SUPPORT_SCORE_UNAVAILABLE')
 elif score<RULES['minimum_visibility_support_ratio']:reasons.append('VISIBILITY_SUPPORT_RATIO_TOO_LOW')
 # Pick an actually observed medoid curve; do not fabricate an average geometry.
 distances=np.array([[geometry_distance(a['geometry_city'],b['geometry_city']) for b in group] for a in group]);rep=group[int(np.argmin(distances.sum(1)))]
 points=np.concatenate([np.array(c['geometry_city']) for c in group]);center=np.median(points,axis=0)
 flags=sorted({flag for c in group for flag in c['quality_flags'] if flag!='not_temporally_fused'})
 flags+=['repeated_detection_is_not_independent_truth','representative_observed_geometry_not_reconstructed_map']
 if 'curb_confusion_possible' in flags:reasons.append('UPSTREAM_CURB_CONFUSION_RISK_UNRESOLVED')
 fingerprint='|'.join([first['scene_id'],first['clip_id'],first['change_type'],str(first['map_element_id']),str(first['boundary_side']),start])
 cid='m4:'+hashlib.sha256(fingerprint.encode()).hexdigest()[:16]
 values=[c['difference_value'] for c in group if c['difference_value'] is not None]
 result={'schema_version':'1.0.0','run_id':'m4_fusion_v1','change_id':cid,'scene_id':first['scene_id'],'clip_id':first['clip_id'],'change_type':first['change_type'],'map_element_id':first['map_element_id'],'boundary_side':first['boundary_side'],'start_ns':start,'end_ns':end,'support_duration_s':support_duration,'source_candidate_ids':[c['candidate_id'] for c in group],'source_observation_ids':[c['observation_id'] for c in group],'source_m3_run_id':first['run_id'],'source_m2_run_id':first['source_run_id'],'observation_count':len(group),'distinct_support_frame_count':len(times),'camera_names':sorted({c['camera'] for c in group}),'candidate_window_frame_count':len(window),'observed_continuity':len(times)/len(window) if window else None,'maximum_support_gap_s':max([(int(b)-int(a))/1e9 for a,b in zip(times,times[1:])],default=0.),'known_valid_frame_count':len(visible),'valid_frame_count':valid_count,'unknown_visibility_frame_count':len(unknown),'missing_visibility_check_frame_count':len(missing),'known_visible_support_frame_count':len(supported),'visible_support_frame_count':len(supported) if denominator_known else None,'evidence_score':score,'score_is_probability':False,'geometry_city':rep['geometry_city'],'geometry_method':'observed_curve_medoid','representative_candidate_id':rep['candidate_id'],'centroid_city':center.tolist(),'centroid_method':'coordinatewise median of all member curve vertices; not area centroid','bbox_city_minmax':[points.min(0).tolist(),points.max(0).tolist()],'difference_value':float(np.median(values)) if values else None,'difference_unit':first['difference_unit'],'status':'uncertain' if reasons else 'confirmed','reason_codes':reasons,'quality_flags':flags}
 evidence=[]
 for r in window:
  t=r['frame_id'];members=byframe[t]
  evidence.append({'schema_version':'1.0.0','run_id':'m4_fusion_v1','change_id':cid,'frame_id':t,'scene_id':first['scene_id'],'candidate_ids':[c['candidate_id'] for c in members],'cameras':sorted({c['camera'] for c in members}),'candidate_observation_count':len(members),'has_candidate_support':bool(members),'verified_visible':True if t in visible else None if t in unknown or t in missing else False,'supports_change_when_verified_visible':True if t in supported else None if t in unknown or t in missing else False})
 return result,evidence

def main():
 source=next((ROOT/'deliveries').glob('*_M3_v1'));m2=ROOT/'deliveries'/read(source/'provenance.json')['source_m2_package'];candidates=rows(source/'change_candidates.jsonl');frames=rows(m2/'tables/frames.jsonl');checks=rows(source/'map_checks.jsonl')
 out=ROOT/'deliveries'/source.name.replace('_M3_v1','_M4_v1');out.mkdir(exist_ok=True);groups=group_candidates(candidates);changes=[];evidence=[]
 for group in groups:
  c,e=fuse(group,frames,checks);changes.append(c);evidence.extend(e)
 for name,data in [('change_records',changes),('frame_evidence',evidence)]:
  with (out/f'{name}.jsonl').open('w',encoding='utf-8') as f:
   for r in data:f.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n')
 fields=['change_id','change_type','map_element_id','boundary_side','start_ns','end_ns','distinct_support_frame_count','support_duration_s','observed_continuity','valid_frame_count','evidence_score','difference_value','status']
 with (out/'change_records.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(changes)
 write(out/'rules.json',RULES)
 write(out/'provenance.json',{'m3_package':source.name,'m2_package':m2.name,'input_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [source/'change_candidates.jsonl',source/'map_checks.jsonl',m2/'tables/frames.jsonl']},'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'gt_read':False})
 summary={'input_candidate_observations':len(candidates),'candidate_groups':len(groups),'change_records':len(changes),'statuses':dict(Counter(c['status'] for c in changes)),'confirmed_changes':sum(c['status']=='confirmed' for c in changes),'available_visibility_scores':sum(c['evidence_score'] is not None for c in changes),'frame_evidence_rows':len(evidence),'reasons':dict(Counter(r for c in changes for r in c['reason_codes'])),'gt_read':False,'limitations':['M4 fuses existing M3 candidates only; it does not discover missed changes','Continuity and repetition are not truth or visibility support ratios','Upstream suspected curb false positives retained as uncertain','No reliable visibility denominator, so proposal S cannot be computed for current data']}
 write(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
 # Regression: same timestamp in another camera does not create an independent frame.
 if candidates:
  group=groups[0];base,_=fuse(group,frames,checks);duplicate=copy.deepcopy(group[0]);duplicate['candidate_id']+=':test';duplicate['camera']='synthetic_second_camera'
  multi,_=fuse(group+[duplicate],frames,checks);assert multi['distinct_support_frame_count']==base['distinct_support_frame_count'] and multi['observation_count']==base['observation_count']+1
  assert multi['evidence_score'] is None and multi['status']=='uncertain'
 allids=[c['candidate_id'] for c in candidates];assigned=[i for c in changes for i in c['source_candidate_ids']];assert sorted(allids)==sorted(assigned)
 assert all(c['known_visible_support_frame_count']<=c['known_valid_frame_count'] for c in changes)
 assert all(c['evidence_score'] is None and c['status']=='uncertain' for c in changes)
 assert all(r['verified_visible'] is None for r in evidence)
 # Verify attribution barriers: clips/types/map sides must not be merged.
 if candidates:
  a=copy.deepcopy(candidates[0]);b=copy.deepcopy(a);b['candidate_id']+=':clip';b['clip_id']+=':other';assert len(group_candidates([a,b]))==2
  b=copy.deepcopy(a);b['candidate_id']+=':side';b['boundary_side']='left' if a['boundary_side']=='right' else 'right';assert len(group_candidates([a,b]))==2
 write(out/'validation.json',{'status':'PASS','input_members_accounted_for':len(assigned),'errors':[],'regression_checks':['cross_camera_frame_deduplication','unknown_visibility_does_not_produce_score','clip_isolation','boundary_side_isolation'],'note':'Implementation consistency, not real change accuracy.'})
 shutil.copy2(ROOT/'M4处理说明.md',out/'README.md');qa=out/'qa';qa.mkdir(exist_ok=True)
 # Keep one source review image per fused group, with full source attribution.
 index=read(source/'qa_index.json');lookup={r['candidate_id']:r['file'] for r in index};qarefs=[]
 for c in changes:
  old=lookup.get(c['representative_candidate_id']) or next((lookup[i] for i in c['source_candidate_ids'] if i in lookup),None)
  if old:
   dest=qa/(c['change_id'].replace(':','_')+'.jpg');shutil.copy2(source/old,dest);qarefs.append({'change_id':c['change_id'],'image':dest.relative_to(out).as_posix(),'source_m3_image':old,'note':'Source observation review image, not fused reconstruction.'})
 write(out/'qa_index.json',qarefs)
 files=sorted(p for p in out.rglob('*') if p.is_file() and p.name!='file_manifest.json');write(out/'file_manifest.json',{'files':[{'path':p.relative_to(out).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]})
 with zipfile.ZipFile(out.with_suffix('.zip'),'w',zipfile.ZIP_DEFLATED) as z:
  for p in files+[out/'file_manifest.json']:z.write(p,out.name+'/'+p.relative_to(out).as_posix())
 with zipfile.ZipFile(out.with_suffix('.zip')) as z:assert z.testzip() is None
 print('ZIP',out.with_suffix('.zip'))
if __name__=='__main__':main()
