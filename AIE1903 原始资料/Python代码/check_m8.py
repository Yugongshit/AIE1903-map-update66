import json,csv,hashlib,zipfile,shutil
import numpy as np
from m8_evaluate import ROOT,read,rows,write,metric,geometric_difference

def main():
 out=next((ROOT/'deliveries').glob('*_M8_v1'));inventory=rows(out/'gt_object_inventory.jsonl');changed=rows(out/'gt_changed_objects.jsonl');current=read(out/'current_gt_map.json');prior=read(out/'prior_map_reference.json');report=read(out/'evaluation_report.json');prov=read(out/'provenance.json')
 assert len(inventory)==288 and len(changed)==98 and sum(r['official_changed'] for r in inventory)==98 and sum(not r['official_changed'] for r in inventory)==190
 assert len({(r['layer'],r['element_id']) for r in inventory})==288
 assert len(current['lane_segments'])==240 and len(prior['lane_segments'])==219 and len(current['pedestrian_crossings'])==33 and len(prior['pedestrian_crossings'])==27
 assert hashlib.sha256((ROOT/prov['source_annotation']).read_bytes()).hexdigest()==prov['annotation_sha256']
 for r in changed:
  assert r['official_changed'] and r['raw_changes']!='[0]'
  if 'added' in r['state_delta_types']:assert r['prior_state'] is None and r['current_state'] is not None
 diag=read(out/'prediction_gt_diagnostics.json');assert len(diag)==1 and diag[0]['official_gt_changed'] is False and diag[0]['prediction_status']=='uncertain' and not diag[0]['geometry_metrics_eligible']
 # Current unfilled templates must not produce apparent accuracy.
 scope=list(csv.DictReader((out/'annotation_templates/map_evaluation_scope.csv').open(encoding='utf-8-sig')))
 if not any(r['include_in_evaluation'].strip() for r in scope):assert report['map_binary_evaluation']['metrics'] is None
 for key in ['vehicle_detection_evaluation','vehicle_tracking_evaluation','absolute_3d_localization_evaluation','behavior_evaluation','association_evaluation']:assert report[key]['metrics'] is None
 vehicle=list(csv.DictReader((out/'annotation_templates/vehicle_2d_gt.csv').open(encoding='utf-8-sig')));assert len(vehicle)==189 and len({r['view_id'] for r in vehicle})==189
 assert metric(0,0,5,2,1)['precision'] is None and metric(0,0,5,2,1)['recall']==0 and metric(0,0,5,2,1)['f1']==0
 assert metric(2,1,2,3,1)['precision']==2/3
 assert abs(geometric_difference([[0,0],[10,0]],[[0,2],[10,2]])-2)<1e-9
 # Prove untouched manual fields in newly generated templates, without modifying user work.
 blank_truth=sum(not r['truth_objects_json'].strip() for r in vehicle)
 validation={'status':'PASS','gt_objects':len(inventory),'gt_changed_objects':len(changed),'candidate_gt_checks':len(diag),'annotation_images':len(vehicle),'unfilled_vehicle_truth_rows':blank_truth,'regression_tests':['no_predictions_precision_is_null','abstention_positive_is_missed_positive','known_precision_example','parallel_geometry_distance'],'errors':[],'note':'GT/export and metric safeguards checked; formal independent evaluation remains pending.'};write(out/'validation.json',validation);print(json.dumps(validation,indent=2))
 shutil.copy2(ROOT/'M8处理说明.md',out/'README.md')
 files=sorted(p for p in out.rglob('*') if p.is_file() and p.name!='file_manifest.json');write(out/'file_manifest.json',{'files':[{'path':p.relative_to(out).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in files]})
 with zipfile.ZipFile(out.with_suffix('.zip'),'w',zipfile.ZIP_DEFLATED) as z:
  for p in files+[out/'file_manifest.json']:z.write(p,out.name+'/'+p.relative_to(out).as_posix())
 with zipfile.ZipFile(out.with_suffix('.zip')) as z:assert z.testzip() is None
 print('ZIP',out.with_suffix('.zip'),out.with_suffix('.zip').stat().st_size)
if __name__=='__main__':main()
