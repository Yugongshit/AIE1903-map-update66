"""Independent GT export, audit diagnostics and annotation-gated evaluation."""
import json,csv,hashlib,zipfile,shutil
from collections import Counter
from pathlib import Path
import numpy as np
from m3_changes import ROOT,read,rows,write,sample,project_to_line

def geometric_difference(a,b):
 if len(a)<2 or len(b)<2:return float(np.linalg.norm(np.array(a)[0]-np.array(b)[0]))
 p,q=sample(np.array(a,float)),sample(np.array(b,float));return .5*(float(project_to_line(p,q)[0].mean())+float(project_to_line(q,p)[0].mean()))
def xy(points):
 values=[[p['x'],p['y']] for p in points]
 if len(values)>1 and values[-1]==values[0]:values.pop()
 return values
def csvwrite(path,fields,records):
 with path.open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(records)
def new_template(path,fields,records):
 if not path.exists():csvwrite(path,fields,records)
def metric(tp,fp,fn,tn,abstentions):
 p=tp/(tp+fp) if tp+fp else None;r=tp/(tp+fn) if tp+fn else None;f=2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None
 return {'TP':tp,'FP':fp,'FN':fn,'TN':tn,'abstentions':abstentions,'precision':p,'recall':r,'f1':f,'note':'Uncertain counts as abstention and as missed positive on positive GT; no-predicted-positive precision is null.'}

def main():
 m2=next((ROOT/'deliveries').glob('*_M2_v1'));scene=m2.name.removesuffix('_M2_v1');out=ROOT/'deliveries'/(scene+'_M8_v1');out.mkdir(exist_ok=True);templates=out/'annotation_templates';templates.mkdir(exist_ok=True)
 source=ROOT/'Argoverse_tvy代表数据'/scene;annotationfile=source/'argotweak_annotation.json';annotation=read(annotationfile);info=read(source/'scene_info.json');prior=read(m2/'prior_map.json')
 assert info['group']=='constructed_train','This physical-current exporter has only been verified on the representative constructed train scene.'
 assert hashlib.sha256(annotationfile.read_bytes()).hexdigest()==info['argotweak_annotation_sha256']
 # This scene has no code 1 objects. Avoid exporting explanatory deletion placeholders as physical GT.
 assert all(v['changes']!=[1] for layer in ['laneSegments','pedCrossings'] for v in annotation[layer].values())
 current={'scene_id':scene,'coordinates':'City XY','role':'physical_current_map_from_ArgoTweak_old_in_this_constructed_scene','lane_segments':[v['old'] for v in annotation['laneSegments'].values() if v['old'] is not None],'pedestrian_crossings':[{'id':v['old']['id'],'points':xy(v['old']['edges'])} for v in annotation['pedCrossings'].values() if v['old'] is not None]}
 prior_lanes={l['id']:l for l in prior['lane_segments']};prior_pc={p['id']:p for p in prior['pedestrian_crossings']}
 inventory=[];gtchanges=[]
 for layer in ['laneSegments','pedCrossings','drivableAreas']:
  for oid,obj in annotation[layer].items():
   codes=obj['changes'];changed=any(c!=0 for c in codes);delta=[];differences={};before=None;after=obj['old'];supported=layer!='drivableAreas'
   if layer=='laneSegments':before=prior_lanes.get(oid)
   elif layer=='pedCrossings':before=prior_pc.get(oid)
   if supported:
    if before is None and after is not None:delta=['added']
    elif before is not None and after is None:delta=['removed']
    elif before is not None and after is not None:
     if layer=='laneSegments':
      for side in ['left','right']:
       value=geometric_difference(xy(before[side+'_lane_boundary']),xy(after[side+'_lane_boundary']));differences[side+'_geometry_difference_m']=value
      if max(differences.values())>.01:delta.append('geometry_changed')
      if any(before[k]!=after[k] for k in ['left_lane_mark_type','right_lane_mark_type']):delta.append('marking_changed')
      if before['lane_type']!=after['lane_type']:delta.append('lane_type_changed')
      if any(sorted(before[k])!=sorted(after[k]) for k in ['predecessors','successors']):delta.append('connectivity_changed')
      if before['is_intersection']!=after['is_intersection']:delta.append('intersection_attribute_changed')
     else:
      aa=before['points'];aa=aa[:-1] if aa[-1]==aa[0] else aa;bb=xy(after['edges']);a=np.array(aa);b=np.array(bb);a=np.vstack([a,a[0]]);b=np.vstack([b,b[0]]);d=geometric_difference(a,b);differences['polygon_boundary_difference_m']=d
      if d>.01:delta.append('geometry_changed')
   row={'scene_id':scene,'layer':layer,'element_id':oid,'official_changed':changed,'raw_changes':json.dumps(codes),'unresolved_numeric_codes':json.dumps([c for c in codes if c not in [0,1,2,3,5]]),'prior_present':before is not None if supported else None,'current_present':after is not None,'state_delta_types':delta,'state_comparison_supported':supported,'type_definition_source':'derived_from_prior_to_current_fields_not_full_official_numeric_code_mapping','differences':differences,'visibility_scope_verified':False}
   inventory.append(row)
   if changed:gtchanges.append({**row,'gt_change_id':f'gt:{scene}:{layer}:{oid}','prior_state':before,'current_state':after})
 write(out/'current_gt_map.json',current);write(out/'prior_map_reference.json',prior)
 for name,data in [('gt_object_inventory',inventory),('gt_changed_objects',gtchanges)]:
  with (out/f'{name}.jsonl').open('w',encoding='utf-8') as f:
   for row in data:f.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
 csvwrite(out/'gt_object_inventory.csv',['scene_id','layer','element_id','official_changed','raw_changes','unresolved_numeric_codes','prior_present','current_present','state_delta_types','state_comparison_supported'],[{**r,'state_delta_types':json.dumps(r['state_delta_types'])} for r in inventory])
 m4=ROOT/'deliveries'/(scene+'_M4_v1');m5=ROOT/'deliveries'/(scene+'_M5_v1');m6=ROOT/'deliveries'/(scene+'_M6_v1');m7=ROOT/'deliveries'/(scene+'_M7_v1');changes=rows(m4/'change_records.jsonl');gtbyid={r['element_id']:r for r in inventory};comparisons=[]
 for c in changes:
  gt=gtbyid.get(c['map_element_id']);comparisons.append({'change_id':c['change_id'],'map_element_id':c['map_element_id'],'prediction_status':c['status'],'prediction_type':c['change_type'],'official_gt_changed':gt['official_changed'] if gt else None,'gt_state_delta_types':gt['state_delta_types'] if gt else [],'evaluation_role':'confirmed_prediction' if c['status']=='confirmed' else 'abstention_not_predicted_positive','diagnostic':'candidate_on_gt_unchanged_object' if gt and not gt['official_changed'] else 'needs_type_and_scope_review','geometry_metrics_eligible':bool(gt and gt['official_changed'] and c['change_type'] in gt['state_delta_types'])})
 write(out/'prediction_gt_diagnostics.json',comparisons)
 csvwrite(out/'prediction_gt_diagnostics.csv',['change_id','map_element_id','prediction_status','prediction_type','official_gt_changed','gt_state_delta_types','evaluation_role','diagnostic','geometry_metrics_eligible'],comparisons)
 # Independent labels never prefilled from predictions. Map changed flag is original official GT.
 new_template(templates/'map_evaluation_scope.csv',['scene_id','layer','element_id','official_changed','include_in_evaluation','scope_reason','reviewer','evidence_reference'],[{k:r[k] for k in ['scene_id','layer','element_id','official_changed']} for r in inventory])
 frames=rows(m2/'tables/frames.jsonl');views={v['view_id']:v for v in rows(m2/'tables/views.jsonl')};start=int(frames[0]['timestamp_ns']);selected=[]
 # Three 10s windows at ~2Hz, independent of predicted event/candidate locations.
 for window_start in [0,24,48]:
  for relative in np.arange(window_start,window_start+10.001,.5):
   if start+int(relative*1e9)>int(frames[-1]['timestamp_ns']):continue
   f=min(frames,key=lambda f:abs(int(f['timestamp_ns'])-(start+int(relative*1e9))))
   for vid in f['view_ids']:
    v=views[vid];selected.append({'annotation_window_id':f'window_{window_start:02d}_{window_start+10:02d}','scene_id':scene,'frame_id':f['frame_id'],'camera':v['camera'],'view_id':vid,'image_timestamp_ns':v['image_timestamp_ns'],'source_image_ref':v['source_image_ref']})
 selected=list({(r['annotation_window_id'],r['view_id']):r for r in selected}.values())
 new_template(templates/'vehicle_2d_gt.csv',['annotation_window_id','scene_id','frame_id','camera','view_id','image_timestamp_ns','source_image_ref','truth_objects_json','annotator','evidence_reference'],selected)
 new_template(templates/'behavior_gt.csv',['scene_id','clip_id','gt_track_id','gt_behavior_event_id','start_ns','end_ns','behavior_type','status','annotator','evidence_reference'],[])
 new_template(templates/'association_gt.csv',['scene_id','gt_change_id','gt_track_id','gt_behavior_event_id','association','annotator','evidence_reference'],[])
 new_template(templates/'identity_matches.csv',['predicted_track_id','gt_track_id','annotation_window_id','reviewer','evidence_reference'],[{'predicted_track_id':r['track_id']} for r in rows(m5/'tracks.jsonl')])
 # Formal binary map evaluation requires a reviewed, non-leaky scope definition.
 scope=list(csv.DictReader((templates/'map_evaluation_scope.csv').open(encoding='utf-8-sig')));included=[r for r in scope if r['include_in_evaluation'].strip().lower() in ['true','1','yes'] and r['reviewer'].strip() and r['evidence_reference'].strip() and r['scope_reason'].strip()]
 positive={c['map_element_id'] for c in changes if c['status']=='confirmed'};abstain={c['map_element_id'] for c in changes if c['status']=='uncertain'};matrix=Counter()
 for r in included:
  g=gtbyid[r['element_id']]['official_changed'];p=r['element_id'] in positive;matrix['TP' if g and p else 'FP' if p else 'FN' if g else 'TN']+=1
 mapmetrics=metric(matrix['TP'],matrix['FP'],matrix['FN'],matrix['TN'],sum(r['element_id'] in abstain for r in included)) if included else None
 stagechecks={stage:read(ROOT/'deliveries'/(scene+f'_M{stage}_v1')/'validation.json')['status'] for stage in [2,3,4,5,6,7]}
 report={'schema_version':'1.0.0','run_id':'m8_evaluation_v1','scene_id':scene,'dataset_pool':info['group'],'independent_test_scene':False,'stage_consistency_checks':stagechecks,'map_gt_inventory':{'objects':len(inventory),'official_changed_objects':len(gtchanges),'unchanged_objects':sum(not r['official_changed'] for r in inventory),'changed_by_layer':dict(Counter(r['layer'] for r in gtchanges)),'derived_update_type_counts':dict(Counter(t for r in gtchanges for t in r['state_delta_types'])),'unresolved_raw_numeric_codes':sorted({code for r in inventory for code in json.loads(r['unresolved_numeric_codes'])}),'note':'State-delta types are independently computed field differences, not a claim to resolve all official numeric codes.'},'map_prediction_diagnostics':{'m4_groups':len(changes),'confirmed_predictions':len(positive),'uncertain_groups':len(abstain),'candidate_groups_on_official_unchanged_objects':sum(d['diagnostic']=='candidate_on_gt_unchanged_object' for d in comparisons),'note':'An uncertain candidate on unchanged GT is an erroneous candidate; it is not a confirmed final positive.'},'map_binary_evaluation':{'status':'EVALUATED_ON_REVIEWED_DEVELOPMENT_SCOPE' if included else 'AWAITING_REVIEWED_EVALUATION_SCOPE','reviewed_objects':len(included),'metrics':mapmetrics,'reason':'Three front cameras do not establish visibility coverage for all map objects; no automatic full-map recall.'},'map_type_evaluation':{'status':'NOT_FORMALLY_EVALUATED','metrics':None,'reason':'Complete numeric mapping, reviewed scope and final positive predictions unavailable.'},'vehicle_detection_evaluation':{'status':'AWAITING_INDEPENDENT_2D_LABELS','metrics':None,'proposed_annotation_images':len(selected)},'vehicle_tracking_evaluation':{'status':'AWAITING_INDEPENDENT_IDENTITIES','metrics':None},'absolute_3d_localization_evaluation':{'status':'NO_INDEPENDENT_3D_GT','metrics':None},'behavior_evaluation':{'status':'AWAITING_INDEPENDENT_BEHAVIOR_LABELS','metrics':None},'association_evaluation':{'status':'AWAITING_INDEPENDENT_ASSOCIATION_LABELS','metrics':None},'overall_status':'PARTIAL_GT_AUDIT_COMPLETE_FORMAL_EVALUATION_PENDING','limitations':['This is a development constructed_train scene, not an independent test','Prediction/GT scope and visibility must be fixed without viewing prediction correctness','Do not fill manual GT with pipeline predictions','No meaningful accuracy, IDF1, AP or F1 claimed without corresponding independent labels']}
 write(out/'evaluation_report.json',report)
 write(out/'provenance.json',{'annotation_sha256':hashlib.sha256(annotationfile.read_bytes()).hexdigest(),'source_annotation':str(annotationfile.relative_to(ROOT)),'source_pool':info['group'],'prior_map_sha256':hashlib.sha256((m2/'prior_map.json').read_bytes()).hexdigest(),'prediction_packages':[m4.name,m5.name,m6.name,m7.name],'script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'gt_access_stage':'M8 evaluation only','current_map_export_scope':'representative constructed_train with no code-1 explanatory deletion placeholders'})
 print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
