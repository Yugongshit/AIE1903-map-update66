import json,hashlib,shutil,zipfile
from pathlib import Path
import numpy as np
from m7_associate import ROOT,read,rows,write,decide,interval_relation,longest_near_run,project_to_line

def regressions():
 change={'status':'confirmed','quality_flags':[]};event={'status':'candidate','is_behavior_event':True}
 near=[{'timestamp_ns':str(1_000_000_000+int(i*.3*1e9)),'line_distance_m':2.} for i in range(5)];far=[{**r,'line_distance_m':8.} for r in near]
 assert decide(change,event,near,None)[0]=='associated'
 assert decide(change,event,far,None)[0]=='not_associated'
 assert decide(change,event,[],None)[0]=='uncertain'
 assert decide({**change,'status':'uncertain'},event,near,None)[0]=='uncertain'
 assert decide(change,event,near,{'review_status':'suspected_false_motion'})[0]=='uncertain'
 assert interval_relation('1','2','4','5')[0]=='event_before_change_observations'
 assert interval_relation('6','7','4','5')[0]=='event_after_change_observations'
 distance=project_to_line(np.array([[5.,2.]]),np.array([[0.,0.],[10.,0.]]))[0][0];assert abs(distance-2)<1e-9
 # More dense samples in a short run must not hide a longer qualifying near run.
 dense=[{'timestamp_ns':str(int(i*.01*1e9)),'line_distance_m':2.} for i in range(20)];assert longest_near_run(dense+near)[1]>=1.

def main():
 regressions();out=next((ROOT/'deliveries').glob('*_M7_v1'));prov=read(out/'provenance.json');m4=ROOT/'deliveries'/prov['source_m4_package'];m6=ROOT/'deliveries'/prov['source_m6_package'];m3=ROOT/'deliveries'/prov['source_m3_package'];m5=ROOT/'deliveries'/prov['source_m5_package']
 changes={c['change_id']:c for c in rows(m4/'change_records.jsonl')};events={e['event_id']:e for e in rows(m6/'behavior_events.jsonl')};measurements={m['measurement_id']:m for m in rows(m5/'measurements.jsonl')};candidates={c['candidate_id']:c for c in rows(m3/'change_candidates.jsonl')}
 pairs=rows(out/'associations.jsonl');screens=rows(out/'spatial_screening.jsonl');evidence=rows(out/'association_frame_evidence.jsonl');eligibility=rows(out/'behavior_eligibility.jsonl');coverage=rows(out/'change_coverage.jsonl');context=rows(out/'vehicle_context.jsonl');pairids={p['pair_id'] for p in pairs}
 assert len(pairids)==len(pairs) and len(eligibility)==len(events)
 for p in pairs:
  assert p['change_id'] in changes and p['event_id'] in events and p['track_id']==events[p['event_id']]['track_id'];assert p['association']=='uncertain' and p['distance_to_change_m'] is None and p['simultaneous_sample_count']==0
  related=[s for s in screens if s['pair_id']==p['pair_id']];assert len(related)==events[p['event_id']]['observed_sample_count'];assert abs(min(s['distance_to_representative_line_m'] for s in related)-p['spatial_screening_min_distance_m'])<1e-9
  assert p['interval_overlap_s']==0 and p['time_gap_s']>0
 for s in screens:
  assert s['pair_id'] in pairids and s['diagnostic_only'];m=measurements[s['measurement_id']];assert m['position_city']==s['position_city'] and m['timestamp_ns']==s['timestamp_ns'] and m['track_id']==s['track_id'];c=changes[s['change_id']];distance=float(project_to_line(np.array(s['position_city'][:2])[None],np.array(c['geometry_city'])[:,:2])[0][0]);assert abs(distance-s['distance_to_representative_line_m'])<1e-9
 for r in context:
  m=measurements[r['measurement_id']];c=candidates[r['change_source_candidate_id']];assert m['timestamp_ns']==r['timestamp_ns']==c['frame_id'];assert m['position_city']==r['position_city']
  distance=float(project_to_line(np.array(m['position_city'][:2])[None],np.array(c['geometry_city'])[:,:2])[0][0]);assert abs(distance-r['line_distance_m'])<1e-9
 assert not evidence
 assert all(c['association_coverage_status']=='no_simultaneously_evaluable_behavior' and c['context_vehicle_measurement_count']>0 for c in coverage)
 for path,expected in prov['input_sha256'].items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==expected
 report={'status':'PASS','pairs':len(pairs),'spatial_screening_samples_checked':len(screens),'context_vehicle_measurements_checked':len(context),'simultaneous_evidence_rows':len(evidence),'regression_tests':['simultaneous_near_association','simultaneous_far_nonassociation','no_overlap_stays_uncertain','uncertain_change_propagation','suspected_false_motion_block','point_to_line_distance','irregular_sampling_near_run'],'errors':[],'note':'Rule and provenance checks, not independent association accuracy.'};write(out/'validation.json',report);print(json.dumps(report,indent=2))
 shutil.copy2(ROOT/'M7处理说明.md',out/'README.md');shutil.copy2(m4/'change_records.jsonl',out/'source_changes.jsonl')
 used={p['event_id'] for p in pairs}
 with (out/'source_behavior_candidates.jsonl').open('w',encoding='utf-8') as f:
  for eid in sorted(used):f.write(json.dumps(events[eid],ensure_ascii=False)+'\n')
 files=sorted(p for p in out.rglob('*') if p.is_file() and p.name!='file_manifest.json');write(out/'file_manifest.json',{'files':[{'path':p.relative_to(out).as_posix(),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]})
 with zipfile.ZipFile(out.with_suffix('.zip'),'w',zipfile.ZIP_DEFLATED) as z:
  for p in files+[out/'file_manifest.json']:z.write(p,out.name+'/'+p.relative_to(out).as_posix())
 with zipfile.ZipFile(out.with_suffix('.zip')) as z:assert z.testzip() is None
 print('ZIP',out.with_suffix('.zip'),out.with_suffix('.zip').stat().st_size)
if __name__=='__main__':main()
