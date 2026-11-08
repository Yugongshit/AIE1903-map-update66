"""Export annotation labels losslessly, distinguishing verified names from unknown codes."""
import json,csv,hashlib,subprocess
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'gt_sources/ArgoTweak_baselines'
OUT=ROOT/'gt_exports';OUT.mkdir(exist_ok=True)
definitions={
 0:('no_change','无变化','对象未标注变化'),
 1:('insertion','新增','官方基线称insertion；其相对地图方向须另行验证'),
 2:('deletion','删除','官方基线称deletion；其相对地图方向须另行验证'),
 3:('geometry','几何变化','地图要素边界几何变化'),
 5:('markings','标线变化','标线变化；论文包含类型和颜色，但基线配置不使用颜色'),
}
commit=subprocess.check_output(['git','-C',str(SOURCE),'rev-parse','HEAD'],text=True).strip()
code_source=SOURCE/'tools/convert_data.py'
assert 'reduced_change_scores = [0,1,2,3,5]' in code_source.read_text()
rows=[];full=[];counts=Counter();unknown=Counter()
for annotation_path in (ROOT/'Argoverse_tvy代表数据').glob('*/argotweak_annotation.json'):
 data=json.loads(annotation_path.read_text());scene=annotation_path.parent.name
 for layer in ['laneSegments','pedCrossings','drivableAreas']:
  for oid,obj in data[layer].items():
   codes=obj['changes'];names=[definitions[c][0] if c in definitions else f'unresolved_code_{c}' for c in codes]
   nonzero=any(c!=0 for c in codes);unresolved=[c for c in codes if c not in definitions]
   for c in codes:
    counts[str(c)]+=1
    if c not in definitions:unknown[str(c)]+=1
   row={'scene_id':scene,'layer':layer,'element_id':oid,'changed':nonzero,'codes':json.dumps(codes),'category_names':';'.join(names),'unresolved_codes':json.dumps(unresolved),'modified':obj.get('modified'),'verified':obj.get('verified'),'old_present':obj.get('old') is not None,'new_present':obj.get('new') is not None,'direction_status':'raw_annotation_direction_not_resolved','visibility_status':'not_evaluated'}
   rows.append(row);full.append({**row,'raw_annotation':obj})
 provenance={'path':str(annotation_path.relative_to(ROOT)),'sha256':hashlib.sha256(annotation_path.read_bytes()).hexdigest()}
def save(name,value): (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def csvsave(name,items):
 with (OUT/name).open('w',encoding='utf-8-sig',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(items[0]));w.writeheader();w.writerows(items)
csvsave('gt_objects.csv',rows);csvsave('gt_changed_objects.csv',[r for r in rows if r['changed']])
save('gt_objects_with_geometry.json',{'source':provenance,'objects':full})
save('category_definitions.json',{'baseline_commit':commit,'code_source':'tools/convert_data.py reduced_change_scores; projects/lanesegnet/datasets/pipelines/mapmodification.py','verified_code_names':{str(k):{'name':v[0],'name_zh':v[1],'definition':v[2]} for k,v in definitions.items()},'unresolved_observed_codes':sorted(map(int,unknown)),'paper_atomic_categories':['geometry','markings','type','connectivity','insertion','deletion'],'paper_source':'https://arxiv.org/html/2509.08764v1#S4.SS1','warning':'The baseline reduces labels to 0,1,2,3,5. Other codes are preserved, not relabelled as unchanged. No claim that this dictionary covers the full release.'})
summary={'objects':len(rows),'changed_objects':sum(r['changed'] for r in rows),'changed_by_layer':dict(Counter(r['layer'] for r in rows if r['changed'])),'code_object_occurrences':dict(counts),'unresolved_code_occurrences':dict(unknown),'changed_objects_with_unresolved_codes':sum(r['changed'] and r['unresolved_codes']!='[]' for r in rows),'labels_are_multilabel':True,'direction_status':'not_ready_for_directional_added_removed_evaluation','baseline_commit':commit}
save('summary.json',summary)
assert len(full)==len(rows) and sum(r['changed'] for r in rows)==98
assert all(json.loads(r['codes'])==f['raw_annotation']['changes'] for r,f in zip(rows,full))
print(json.dumps(summary,ensure_ascii=False,indent=2))
