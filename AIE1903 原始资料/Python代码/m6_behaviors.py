"""M6: offline measured-trajectory features and conservative rule-based episodes."""
import json,hashlib,csv,shutil,zipfile
from collections import defaultdict,Counter
from pathlib import Path
import numpy as np
from m3_changes import ROOT,read,rows,write
RULES={'version':'m6_behavior_v1','regression_half_window_s':.5,'minimum_regression_samples':5,'minimum_regression_span_s':.4,'maximum_position_fit_rms_m':.8,'maximum_measurement_gap_s':.35,'stationary_speed_mps':.5,'moving_speed_mps':1.,'minimum_stationary_duration_s':2.,'minimum_moving_duration_s':1.,'minimum_context_moving_duration_s':1.,'minimum_direction_displacement_m':.75,'straight_max_net_deg':15.,'straight_max_accumulated_deg':35.,'turn_min_net_deg':30.,'uturn_net_deg':[150.,210.],'uturn_max_displacement_path_ratio':.65,'heading_uses_speed_min_mps':1.,'reference_lane_or_detour_inference':False,'gt_read':False}

def moving_shape(points):
 xy=np.array(points)[:,:2];indices=[0]
 for i in range(1,len(xy)):
  if np.linalg.norm(xy[i]-xy[indices[-1]])>=.75:indices.append(i)
 coarse=xy[indices]
 if len(coarse)<3:return 'uncertain',{'reason':'INSUFFICIENT_DIRECTION_DISPLACEMENT'}
 delta=np.diff(coarse,axis=0);heading=np.unwrap(np.arctan2(delta[:,1],delta[:,0]));net=float(np.degrees(heading[-1]-heading[0]));accum=float(np.degrees(np.abs(np.diff(heading)).sum()));path=float(np.linalg.norm(np.diff(xy,axis=0),axis=1).sum());displacement=float(np.linalg.norm(xy[-1]-xy[0]));ratio=displacement/path if path>1e-9 else None
 metrics={'net_heading_change_deg':net,'accumulated_heading_change_deg':accum,'path_length_m':path,'net_displacement_m':displacement,'displacement_path_ratio':ratio,'direction_waypoints':len(coarse)}
 if 150<=abs(net)<=210 and ratio is not None and ratio<=.65 and accum<=270:return 'u_turn_candidate',metrics
 if abs(net)<=15 and accum<=35:return 'straight',metrics
 if abs(net)>=30 and accum<=180:return 'turn',metrics
 return 'uncertain',metrics

def feature_rows(samples):
 ordered=sorted(samples,key=lambda s:int(s['timestamp_ns']));observed=[s for s in ordered if s['observed']];t0=int(ordered[0]['timestamp_ns']);ts=np.array([(int(s['timestamp_ns'])-t0)/1e9 for s in observed]);positions=np.array([s['measurement_position_city'] for s in observed]);index={s['timestamp_ns']:i for i,s in enumerate(observed)};features=[]
 for s in ordered:
  reasons=[];velocity=None;point=None;rms=None;speed=None;heading=None
  if not s['observed']:reasons.append('PREDICTED_SAMPLE_EXCLUDED')
  elif not s['velocity_usable_for_behavior_candidate']:reasons.append('M5_VELOCITY_NOT_READY')
  if s['observed']:
   i=index[s['timestamp_ns']];sel=np.flatnonzero(np.abs(ts-ts[i])<=.5+1e-9)
   if len(sel)<5 or ts[sel[-1]]-ts[sel[0]]<.4-1e-9:reasons.append('INSUFFICIENT_OBSERVED_REGRESSION_WINDOW')
   elif np.diff(ts[sel]).max()>.35:reasons.append('REGRESSION_WINDOW_HAS_LONG_GAP')
   else:
    dt=ts[sel]-ts[i];A=np.column_stack([np.ones(len(sel)),dt]);fit=np.linalg.lstsq(A,positions[sel,:2],rcond=None)[0];residual=np.linalg.norm(A@fit-positions[sel,:2],axis=1);threshold=max(.3,3*float(np.median(residual)));keep=residual<=threshold
    if keep.sum()>=5 and np.ptp(dt[keep])>=.4-1e-9:
     fit=np.linalg.lstsq(A[keep],positions[sel[keep],:2],rcond=None)[0];rms=float(np.sqrt(np.mean(np.sum((A[keep]@fit-positions[sel[keep],:2])**2,axis=1))))
    else:rms=float(np.sqrt(np.mean(residual**2)))
    velocity=fit[1];speed=float(np.linalg.norm(velocity));point=[*fit[0].tolist(),float(s['measurement_position_city'][2])]
    if rms>.8:reasons.append('POSITION_FIT_TOO_NOISY')
    if speed>35:reasons.append('SPEED_OUTSIDE_ENGINEERING_RANGE')
    if np.linalg.norm(velocity-np.array(s['velocity_city_xy_mps']))>3:reasons.append('REGRESSION_VS_KALMAN_VELOCITY_DISAGREE')
    if speed>=1:heading=float(np.arctan2(velocity[1],velocity[0]))
  valid=not reasons
  state='stationary' if valid and speed<.5 else 'moving' if valid and speed>=1 else 'transition' if valid else 'unusable'
  features.append({'schema_version':'1.0.0','run_id':'m6_behavior_v1','source_run_id':s['run_id'],'scene_id':s['scene_id'],'clip_id':s['clip_id'],'track_id':s['track_id'],'timestamp_ns':s['timestamp_ns'],'measurement_id':s['measurement_id'],'observed':s['observed'],'behavior_usable':valid,'motion_state':state,'position_city_fitted':point,'velocity_city_xy_mps':velocity.tolist() if velocity is not None else None,'speed_mps':speed,'heading_rad':heading,'position_fit_rms_m':rms,'reference_lane_id':None,'lateral_offset_m':None,'reason_codes':reasons,'quality_flags':['visible_surface_center_not_amodal_center','offline_local_regression_not_causal','identity_not_independently_verified']})
 return features

def segments(features):
 groups=[]
 for f in features:
  if not f['behavior_usable']:continue
  if not groups or f['motion_state']!=groups[-1][-1]['motion_state'] or (int(f['timestamp_ns'])-int(groups[-1][-1]['timestamp_ns']))/1e9>.35:groups.append([f])
  else:groups[-1].append(f)
 return groups

def duration(group):return (int(group[-1]['timestamp_ns'])-int(group[0]['timestamp_ns']))/1e9

def classify_episode(group,allgroups,index):
 state=group[0]['motion_state'];d=duration(group);metrics={};reasons=[];behavior='uncertain';is_behavior=True
 if state=='stationary':
  if d<2:reasons.append('STATIONARY_DURATION_TOO_SHORT')
  else:
   # Context must belong to the same gap-free usable run, not another track or clip.
   context=False
   for direction in [-1,1]:
    j=index+direction;previous=group
    while 0<=j<len(allgroups):
     other=allgroups[j];gap=(int(previous[0]['timestamp_ns'])-int(other[-1]['timestamp_ns']))/1e9 if direction==-1 else (int(other[0]['timestamp_ns'])-int(previous[-1]['timestamp_ns']))/1e9
     if gap>.35:break
     if other[0]['motion_state']=='moving' and duration(other)>=1:context=True;break
     previous=other;j+=direction
   behavior='stop' if context else 'stationary_presence';is_behavior=context
   reasons.append('LOW_SPEED_WITH_MOVING_CONTEXT' if context else 'NO_MOVING_CONTEXT_PARKING_OR_STATIC_FALSE_POSITIVE_POSSIBLE')
 elif state=='moving':
  if d<1:reasons.append('MOVING_DURATION_TOO_SHORT')
  else:
   behavior,metrics=moving_shape([f['position_city_fitted'] for f in group])
   if behavior=='uncertain':reasons.append('DIRECTION_OR_TRAJECTORY_SHAPE_UNRESOLVED')
   elif behavior=='u_turn_candidate':
    # Require measured usable context on both sides to avoid truncated U-turn claims.
    before=index>0 and (int(group[0]['timestamp_ns'])-int(allgroups[index-1][-1]['timestamp_ns']))/1e9<=.35 and duration(allgroups[index-1])>=.5
    after=index+1<len(allgroups) and (int(allgroups[index+1][0]['timestamp_ns'])-int(group[-1]['timestamp_ns']))/1e9<=.35 and duration(allgroups[index+1])>=.5
    if not(before and after):behavior='uncertain';reasons.append('UTURN_SHAPE_WITHOUT_COMPLETE_ENTRY_EXIT_CONTEXT')
    else:reasons.append('OBSERVED_UTURN_SHAPE_CANDIDATE')
   else:reasons.append('OBSERVED_TRAJECTORY_SHAPE_RULE')
 else:reasons.append('TRANSITION_SPEED_BETWEEN_STATIONARY_AND_MOVING')
 if behavior=='uncertain':is_behavior=False
 return behavior,is_behavior,metrics,reasons

def main():
 source=next((ROOT/'deliveries').glob('*_M5_v1'));samples=rows(source/'track_samples.jsonl');tracks=rows(source/'tracks.jsonl');bytrack=defaultdict(list)
 for s in samples:bytrack[s['track_id']].append(s)
 out=ROOT/'deliveries'/source.name.replace('_M5_v1','_M6_v1');out.mkdir(exist_ok=True);features=[];events=[];assessments=[]
 for track in tracks:
  data=bytrack[track['track_id']];ff=feature_rows(data);features.extend(ff);gg=segments(ff);eventids=[]
  for j,group in enumerate(gg):
   behavior,is_behavior,metrics,reasons=classify_episode(group,gg,j);eid=f"behavior:{track['track_id']}:{j:04d}";eventids.append(eid)
   positions=[f['position_city_fitted'] for f in group];first,last=group[0],group[-1]
   left=(int(first['timestamp_ns'])-int(track['start_ns']))/1e9;right=(int(track['last_observed_ns'])-int(last['timestamp_ns']))/1e9
   flags=['no_independent_behavior_or_identity_gt','visible_surface_center_motion','detour_not_evaluable_without_reference_route']
   if left<=1.1:flags.append('left_boundary_censored_or_warmup')
   if right<=.35:flags.append('right_boundary_censored')
   if behavior=='turn':flags.append('road_direction_not_independently_established')
   events.append({'schema_version':'1.0.0','run_id':'m6_behavior_v1','source_run_id':'m5_vehicle_v1','scene_id':first['scene_id'],'clip_id':first['clip_id'],'event_id':eid,'track_id':track['track_id'],'behavior_type':behavior,'is_behavior_event':is_behavior,'status':'uncertain' if behavior=='uncertain' else 'observed_presence' if behavior=='stationary_presence' else 'candidate','start_ns':first['timestamp_ns'],'end_ns':last['timestamp_ns'],'duration_s':duration(group),'motion_state':first['motion_state'],'source_measurement_ids':[f['measurement_id'] for f in group],'source_sample_timestamps_ns':[f['timestamp_ns'] for f in group],'observed_sample_count':len(group),'predicted_samples_used':0,'event_geometry_city':positions,'geometry_method':'robust_local_regression_at_observed_timestamps','mean_speed_mps':float(np.mean([f['speed_mps'] for f in group])),'metrics':metrics,'trajectory_completeness':'boundary_censored' if any('censored' in f for f in flags) else 'within_observed_track_window_not_verified_maneuver_complete','reason_codes':reasons,'quality_flags':flags})
  assessments.append({'schema_version':'1.0.0','run_id':'m6_behavior_v1','track_id':track['track_id'],'clip_id':track['clip_id'],'source_sample_count':len(data),'observed_sample_count':sum(s['observed'] for s in data),'behavior_usable_samples':sum(f['behavior_usable'] for f in ff),'event_ids':eventids,'status':'has_candidate_episodes' if any(e['track_id']==track['track_id'] and e['status']=='candidate' for e in events) else 'insufficient_or_presence_only','detour_assessment':'not_evaluable_no_reference_route','reference_lane_id':None})
 for name,data in [('vehicle_features',features),('behavior_events',events),('track_assessments',assessments)]:
  with (out/f'{name}.jsonl').open('w',encoding='utf-8') as f:
   for r in data:f.write(json.dumps(r,ensure_ascii=False,allow_nan=False)+'\n')
 with (out/'behavior_events.csv').open('w',encoding='utf-8-sig',newline='') as f:
  fields=['event_id','track_id','behavior_type','status','is_behavior_event','start_ns','end_ns','duration_s','observed_sample_count','mean_speed_mps','trajectory_completeness'];w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(events)
 write(out/'rules.json',RULES);write(out/'provenance.json',{'source_m5_package':source.name,'input_sha256':{n:hashlib.sha256((source/n).read_bytes()).hexdigest() for n in ['track_samples.jsonl','tracks.jsonl']},'source_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'gt_read':False})
 summary={'tracks':len(tracks),'source_samples':len(samples),'predicted_samples_excluded':sum(not s['observed'] for s in samples),'behavior_usable_samples':sum(f['behavior_usable'] for f in features),'feature_exclusion_reasons':dict(Counter(r for f in features for r in f['reason_codes'])),'episodes':len(events),'behaviors':dict(Counter(e['behavior_type'] for e in events)),'episode_statuses':dict(Counter(e['status'] for e in events)),'candidate_behavior_events':sum(e['status']=='candidate' for e in events),'tracks_without_usable_samples':sum(t['behavior_usable_samples']==0 for t in assessments),'gt_read':False,'limitations':['Rule-based observed trajectory candidates, not driver intent','No reliable reference lane, no lateral offset or detour inference','ID switches, parked-vehicle point-center drift and false detections can create spurious behavior','Motion-state runs are not fine-grained turn segmentation','No independent behavior labels or accuracy metrics']};write(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
