"""Read-only source audit; writes only to audit_initial_outputs."""
import json, hashlib, csv, bisect
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent
PREP = ROOT/'HDMap_Change_Prep_副本/HDMap_Change_Prep_副本'
OUT = ROOT/'audit_initial_outputs'
OUT.mkdir(exist_ok=True)
def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def rows(p): return [json.loads(s) for s in p.read_text(encoding='utf-8-sig').splitlines() if s.strip()]
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
result = {'scope':'Local copied preparation records and one representative scene; no full-disk revalidation', 'checks':[]}
def check(name, passed, detail): result['checks'].append(dict(name=name,passed=bool(passed),detail=detail))
meta=PREP/'step01/metadata'
sources=read(meta/'sources.json')
for entry in sources['sources']+sources['generated']:
    p=meta/entry['file']
    check('source_hash:'+p.name,p.exists() and sha(p)==entry['sha256'],entry['sha256'])
stage1=PREP/'step01/output_real_v1'
report=read(stage1/'report.json')
for name,count in report['output_rows'].items():
    actual=len(rows(stage1/name))
    check('manifest_count:'+name,actual==count,{'expected':count,'actual':actual})
stage2=PREP/'step02_clipped_v1'
groups={g:rows(stage2/(g+'_clips.jsonl')) for g in ['real_test','constructed_train','constructed_val']}
allclips=[r for records in groups.values() for r in records]
check('unique_clip_ids',len({r['clip_id'] for r in allclips})==len(allclips),len(allclips))
sets={g:{r['log_id'] for r in records} for g,records in groups.items()}
check('disjoint_log_groups',all(not sets[a]&sets[b] for a in sets for b in sets if a<b),{g:{'logs':len(sets[g]),'clips':len(groups[g])} for g in groups})
check('string_timestamps',all(isinstance(r[k],str) for r in allclips for k in ['start_ns','end_ns']),len(allclips))
scene=next(p.parent for p in (ROOT/'Argoverse_tvy代表数据').rglob('scene_info.json') if '__MACOSX' not in str(p))
info=read(scene/'scene_info.json'); annotation=read(scene/'argotweak_annotation.json')
check('representative_annotation_hash',sha(scene/'argotweak_annotation.json')==info['argotweak_annotation_sha256'],sha(scene/'argotweak_annotation.json'))
localclips=read(scene/'clips.json')['clips']
globalclips={r['clip_id']:r for r in allclips}
check('representative_clip_membership',all(r['clip_id'] in globalclips for r in localclips),[r['clip_id'] for r in localclips])
check('representative_clip_bounds',all(all(r[k]==globalclips.get(r['clip_id'],{}).get(k) for k in ['start_ns','end_ns','usable_lidar_reference_count']) for r in localclips), 'Compared with final exported manifests')
lidar=sorted(int(p.stem) for p in (scene/'sensors/lidar').glob('*.feather'))
check('representative_lidar_count',len(lidar)==sum(r['usable_lidar_reference_count']+len(r.get('excluded_reference_lidar_timestamps_ns',[])) for r in localclips),len(lidar))
sync={}
for cam in sorted((scene/'sensors/cameras').iterdir()):
    if not cam.is_dir():continue
    times=sorted(int(p.stem) for p in cam.glob('*.jpg'))
    offsets=[]
    for t in lidar:
        i=bisect.bisect_left(times,t)
        offsets.append(min(abs(times[j]-t) for j in [i-1,i] if 0<=j<len(times))/1e6)
    sync[cam.name]={'images':len(times),'max_offset_ms':max(offsets),'over_50ms':sum(v>50 for v in offsets)}
result['representative_camera_sync']=sync
base=read(next((scene/'map').glob('log_map_archive*.json')))
result['map_layers']={k:len(v) for k,v in base.items()}
objects=[]
for layer,maplayer in [('laneSegments','lane_segments'),('pedCrossings','pedestrian_crossings'),('drivableAreas','drivable_areas')]:
    for oid,obj in annotation[layer].items():
        raw=base.get(maplayer,{}).get(oid.split('-')[-1])
        entry={'layer':layer,'id':oid,'changes':json.dumps(obj['changes']),'modified':obj.get('modified'),'old_present':obj.get('old') is not None,'new_present':obj.get('new') is not None,'base_id_present':raw is not None}
        if layer=='laneSegments' and raw:
            for side in ['old','new']:
                val=obj.get(side)
                if val:
                    common=[k for k in raw if k in val and k!='id']
                    entry[side+'_common_fields_equal']=all(raw[k]==val[k] for k in common)
                    entry[side+'_different_fields']=','.join(k for k in common if raw[k]!=val[k])
                    geom_equal=all([(p['x'],p['y']) for p in raw[k]]==[(p['x'],p['y']) for p in val[k]] for k in ['left_lane_boundary','right_lane_boundary'])
                    attrs_equal=all(raw[k]==val[k] for k in ['lane_type','is_intersection','left_lane_mark_type','right_lane_mark_type'])
                    entry[side+'_xy_equal']=geom_equal
                    entry[side+'_attributes_equal']=attrs_equal
                    entry[side+'_xy_attributes_equal']=geom_equal and attrs_equal
        objects.append(entry)
result['annotation_stats']={layer:dict(Counter(r['changes'] for r in objects if r['layer']==layer)) for layer in annotation if isinstance(annotation[layer],dict)}
result['lane_comparison']={side:dict(Counter(str(r.get(side+'_common_fields_equal')) for r in objects if r['layer']=='laneSegments')) for side in ['old','new']}
result['lane_normalized_comparison']={side:{key:dict(Counter(str(r.get(side+'_'+key)) for r in objects if r['layer']=='laneSegments')) for key in ['xy_equal','attributes_equal','xy_attributes_equal']} for side in ['old','new']}
with (OUT/'object_audit.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=sorted(set().union(*(r.keys() for r in objects))))
    writer.writeheader();writer.writerows(objects)
result['limitations']=['No image-content or calibrated projection validation yet','Reference parser covers lane segments and crossings only, not all change-code definitions','Common-field comparison is not complete semantic map equivalence','Full dataset sensor files and original source annotations are not present locally for renewed verification']
(OUT/'audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
