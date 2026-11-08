"""M7: time-aligned association candidates; diagnostics are not association claims."""
import json,csv,hashlib,zipfile,shutil
from pathlib import Path
from collections import defaultdict,Counter
import numpy as np
from PIL import Image,ImageDraw
from m3_changes import ROOT,read,rows,write,project_to_line
RULES={'version':'m7_association_v1','change_region_buffer_m':3.,'near_line_distance_m':3.,'far_line_distance_m':5.,'minimum_simultaneous_samples':3,'minimum_simultaneous_duration_s':1.,'maximum_support_gap_s':.35,'requires_confirmed_change':True,'requires_candidate_behavior':True,'blocked_manual_review_statuses':['suspected_false_motion'],'time_alignment':'exact LiDAR reference timestamps, same scene and clip; no interpolation','static_geometry_screening_only':True,'gt_read':False,'causal_claim':False}

def interval_relation(start,end,change_start,change_end):
 a,b,c,d=map(int,[start,end,change_start,change_end]);overlap=max(0,min(b,d)-max(a,c))/1e9
 if b<c:return 'event_before_change_observations',(c-b)/1e9,0.
 if a>d:return 'event_after_change_observations',(a-d)/1e9,0.
 return 'intervals_overlap',0.,overlap

def longest_near_run(evidence):
 longest=[];current=[]
 for row in sorted(evidence,key=lambda r:int(r['timestamp_ns'])):
  if row['line_distance_m']>RULES['near_line_distance_m']:current=[];continue
  if current and (int(row['timestamp_ns'])-int(current[-1]['timestamp_ns']))/1e9>RULES['maximum_support_gap_s']:current=[]
  current.append(row)
  current_span=int(current[-1]['timestamp_ns'])-int(current[0]['timestamp_ns']);longest_span=int(longest[-1]['timestamp_ns'])-int(longest[0]['timestamp_ns']) if longest else -1
  if (current_span,len(current))>(longest_span,len(longest)):longest=current.copy()
 return len(longest),(int(longest[-1]['timestamp_ns'])-int(longest[0]['timestamp_ns']))/1e9 if longest else 0.

def decide(change,event,evidence,review):
 reasons=[]
 if change['status']!='confirmed':reasons.append('CHANGE_NOT_CONFIRMED')
 if 'curb_confusion_possible' in change['quality_flags']:reasons.append('CHANGE_CURB_CONFUSION_RISK')
 if event['status']!='candidate' or not event['is_behavior_event']:reasons.append('BEHAVIOR_NOT_EVALUABLE')
 if review and review.get('review_status') in RULES['blocked_manual_review_statuses']:reasons.append('BEHAVIOR_SUSPECTED_FALSE_MOTION')
 n,duration=longest_near_run(evidence)
 span=(int(evidence[-1]['timestamp_ns'])-int(evidence[0]['timestamp_ns']))/1e9 if evidence else 0.
 if not evidence:reasons.append('NO_SIMULTANEOUS_MEASURED_BEHAVIOR_SAMPLES')
 elif len(evidence)<3 or span<1:reasons.append('SIMULTANEOUS_WINDOW_TOO_SHORT')
 if reasons:return 'uncertain',reasons,n,duration
 if n>=3 and duration>=1:return 'associated',['SUFFICIENT_SIMULTANEOUS_NEAR_OBSERVATIONS'],n,duration
 if min(r['line_distance_m'] for r in evidence)>5:return 'not_associated',['FAR_DURING_SUFFICIENT_OBSERVED_WINDOW_ONLY'],n,duration
 return 'uncertain',['SPATIAL_OR_CONTINUITY_EVIDENCE_INSUFFICIENT'],n,duration

def main():
 change_dir=next((ROOT/'deliveries').glob('*_M4_v1'));behavior_dir=next((ROOT/'deliveries').glob('*_M6_v1'));m3=ROOT/'deliveries'/read(change_dir/'provenance.json')['m3_package'];m5=ROOT/'deliveries'/read(behavior_dir/'provenance.json')['source_m5_package']
 changes=rows(change_dir/'change_records.jsonl');events=rows(behavior_dir/'behavior_events.jsonl');sourcec={c['candidate_id']:c for c in rows(m3/'change_candidates.jsonl')};measurements={m['measurement_id']:m for m in rows(m5/'measurements.jsonl')};track_samples=rows(m5/'track_samples.jsonl');reviews={r['event_id']:r for r in read(behavior_dir/'review_notes.json')['notes']} if (behavior_dir/'review_notes.json').exists() else {}
 out=ROOT/'deliveries'/change_dir.name.replace('_M4_v1','_M7_v1');out.mkdir(exist_ok=True);associations=[];aligned=[];screening=[];coverage=[];context=[];eligibility=[]
 qualified=[]
 for e in events:
  reasons=[]
  if not e['is_behavior_event'] or e['status']!='candidate':reasons.append('NOT_A_CLASSIFIED_BEHAVIOR_CANDIDATE')
  review=reviews.get(e['event_id'])
  if review and review['review_status']=='suspected_false_motion':reasons.append('MANUAL_REVIEW_SUSPECTED_FALSE_MOTION')
  eligibility.append({'event_id':e['event_id'],'track_id':e['track_id'],'behavior_type':e['behavior_type'],'eligible':not reasons,'reason_codes':reasons,'manual_review':review})
  if e['is_behavior_event'] and e['status']=='candidate':qualified.append(e)
 for change in changes:
  geometry_by_time=defaultdict(list)
  for cid in change['source_candidate_ids']:
   c=sourcec[cid];geometry_by_time[c['frame_id']].append(c)
  observedtracks=set();concurrent_events=[];eligible_concurrent_events=[]
  for s in track_samples:
   if not s['observed'] or s['scene_id']!=change['scene_id'] or s['clip_id']!=change['clip_id'] or s['timestamp_ns'] not in geometry_by_time:continue
   candidates=geometry_by_time[s['timestamp_ns']];point=np.array(s['measurement_position_city'][:2]);distances=[float(project_to_line(point[None],np.array(c['geometry_city'])[:,:2])[0][0]) for c in candidates];j=int(np.argmin(distances));observedtracks.add(s['track_id'])
   context.append({'change_id':change['change_id'],'timestamp_ns':s['timestamp_ns'],'track_id':s['track_id'],'measurement_id':s['measurement_id'],'position_city':s['measurement_position_city'],'line_distance_m':distances[j],'change_source_candidate_id':candidates[j]['candidate_id'],'note':'Vehicle measurement exists; does not establish a classified behavior or association.'})
  for event in qualified:
   if event['scene_id']!=change['scene_id'] or event['clip_id']!=change['clip_id']:continue
   pair_id=f"association:{change['change_id']}:{event['event_id']}";evidence=[];spatial=[]
   relation,gap,overlap=interval_relation(event['start_ns'],event['end_ns'],change['start_ns'],change['end_ns'])
   for mid,timestamp in zip(event['source_measurement_ids'],event['source_sample_timestamps_ns']):
    m=measurements[mid];assert m['track_id']==event['track_id'] and m['timestamp_ns']==timestamp
    point=np.array(m['position_city'][:2]);dist=float(project_to_line(point[None],np.array(change['geometry_city'])[:,:2])[0][0])
    screen={'pair_id':pair_id,'event_id':event['event_id'],'change_id':change['change_id'],'track_id':event['track_id'],'timestamp_ns':timestamp,'measurement_id':mid,'position_city':m['position_city'],'distance_to_representative_line_m':dist,'time_aligned_change_observation':timestamp in geometry_by_time,'diagnostic_only':True};screening.append(screen);spatial.append(screen)
    if timestamp not in geometry_by_time:continue
    values=geometry_by_time[timestamp];distances=[float(project_to_line(point[None],np.array(c['geometry_city'])[:,:2])[0][0]) for c in values];j=int(np.argmin(distances));distance=distances[j]
    row={'schema_version':'1.0.0','run_id':'m7_association_v1','pair_id':pair_id,'event_id':event['event_id'],'change_id':change['change_id'],'track_id':event['track_id'],'timestamp_ns':timestamp,'measurement_id':mid,'position_city':m['position_city'],'change_source_candidate_id':values[j]['candidate_id'],'line_distance_m':distance,'buffered_region_distance_m':max(0,distance-3),'is_near':distance<=3,'position_definition':'median visible LiDAR returns; not boundary clearance'};evidence.append(row);aligned.append(row)
   evidence.sort(key=lambda r:int(r['timestamp_ns']));association,reasons,nnear,nearduration=decide(change,event,evidence,reviews.get(event['event_id']))
   if evidence:
    relation='simultaneously_observed';concurrent_events.append(event['event_id'])
    if reviews.get(event['event_id'],{}).get('review_status') not in RULES['blocked_manual_review_statuses']:eligible_concurrent_events.append(event['event_id'])
   best=min(spatial,key=lambda r:r['distance_to_representative_line_m']) if spatial else None
   associations.append({'schema_version':'1.0.0','run_id':'m7_association_v1','pair_id':pair_id,'scene_id':change['scene_id'],'clip_id':change['clip_id'],'change_id':change['change_id'],'event_id':event['event_id'],'track_id':event['track_id'],'behavior_type':event['behavior_type'],'source_m4_run_id':change['run_id'],'source_m6_run_id':event['run_id'],'association':association,'reason_codes':reasons,'change_status':change['status'],'behavior_status':event['status'],'manual_behavior_review':reviews.get(event['event_id']),'behavior_start_ns':event['start_ns'],'behavior_end_ns':event['end_ns'],'change_observed_start_ns':change['start_ns'],'change_observed_end_ns':change['end_ns'],'time_relation':relation,'time_gap_s':gap,'interval_overlap_s':overlap,'simultaneous_sample_count':len(evidence),'distance_to_change_m':min(r['line_distance_m'] for r in evidence) if evidence else None,'consecutive_near_sample_count':nnear,'near_duration_s':nearduration,'spatial_screening_min_distance_m':best['distance_to_representative_line_m'] if best else None,'spatial_screening_closest_timestamp_ns':best['timestamp_ns'] if best else None,'spatial_screening_assumption':'Representative change geometry used outside its observation time for diagnostics only; existence/persistence not assumed','quality_flags':['association_not_causation','upstream_predictions_not_gt','vehicle_surface_center_distance_not_clearance']+change['quality_flags']+event['quality_flags']})
  coverage.append({'change_id':change['change_id'],'change_status':change['status'],'candidate_behavior_event_count':sum(e['scene_id']==change['scene_id'] and e['clip_id']==change['clip_id'] for e in qualified),'simultaneously_observed_event_ids':concurrent_events,'eligible_simultaneous_event_ids':eligible_concurrent_events,'simultaneously_measured_track_ids':sorted(observedtracks),'context_vehicle_measurement_count':sum(r['change_id']==change['change_id'] for r in context),'association_coverage_status':'has_simultaneous_behavior_samples' if eligible_concurrent_events else 'no_simultaneously_evaluable_behavior','note':'No eligible behavior is not equivalent to no vehicle or not_associated.'})
 for name,data in [('associations',associations),('association_frame_evidence',aligned),('spatial_screening',screening),('change_coverage',coverage),('vehicle_context',context),('behavior_eligibility',eligibility)]:
  with (out/f'{name}.jsonl').open('w',encoding='utf-8') as f:
   for r in data:f.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n')
 fields=['pair_id','change_id','event_id','track_id','behavior_type','association','time_relation','time_gap_s','simultaneous_sample_count','distance_to_change_m','spatial_screening_min_distance_m']
 with (out/'associations.csv').open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(associations)
 write(out/'rules.json',RULES)
 write(out/'provenance.json',{'source_m4_package':change_dir.name,'source_m6_package':behavior_dir.name,'source_m3_package':m3.name,'source_m5_package':m5.name,'input_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [change_dir/'change_records.jsonl',behavior_dir/'behavior_events.jsonl',behavior_dir/'review_notes.json',m3/'change_candidates.jsonl',m5/'measurements.jsonl',m5/'track_samples.jsonl'] if p.exists()},'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'gt_read':False})
 summary={'change_records':len(changes),'behavior_episodes':len(events),'candidate_behavior_events':len(qualified),'eligible_behavior_events_after_review':sum(e['eligible'] for e in eligibility),'pairs':len(associations),'associations':dict(Counter(r['association'] for r in associations)),'simultaneous_behavior_evidence_rows':len(aligned),'spatial_screening_rows':len(screening),'context_vehicle_measurements':len(context),'changes_without_simultaneous_evaluable_behavior':sum(c['association_coverage_status']=='no_simultaneously_evaluable_behavior' for c in coverage),'reason_counts':dict(Counter(r for a in associations for r in a['reason_codes'])),'gt_read':False,'causal_claim':False};write(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
 # Timeline illustrates the distinction between spatial screening and co-observation.
 first=min(int(s['timestamp_ns']) for s in track_samples);last=max(int(s['timestamp_ns']) for s in track_samples);span=(last-first)/1e9;image=Image.new('RGB',(1400,180+50*(len(changes)+len(qualified))),'white');draw=ImageDraw.Draw(image)
 def x(t):return 400+900*((int(t)-first)/1e9)/max(span,1e-9)
 draw.text((20,15),'M7 observation timeline: orange = uncertain change; blue = candidate behavior',fill='black');draw.text((20,35),'No temporal overlap does not prove no permanent change. Spatial proximity is not causation.',fill='black')
 y=100
 for c in changes:draw.text((20,y-5),'change '+c['map_element_id'],fill='black');draw.line((x(c['start_ns']),y,x(c['end_ns']),y),fill='orange',width=10);y+=50
 for e in qualified:draw.text((20,y-5),e['event_id'].replace('behavior:','')+' '+e['behavior_type'],fill='black');draw.line((x(e['start_ns']),y,x(e['end_ns']),y),fill=(50,110,200),width=8);y+=50
 for second in range(0,int(span)+1,5):xx=400+900*second/span;draw.line((xx,70,xx,y),fill=(220,220,220));draw.text((xx,y+8),str(second)+'s',fill='black')
 image.save(out/'timeline.png')
if __name__=='__main__':main()
