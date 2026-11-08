"""Dataset setup only: isolate official stale map input from annotation labels."""
import json,hashlib,importlib.util
from pathlib import Path
ROOT=Path(__file__).resolve().parent
scene=next((ROOT/'Argoverse_tvy代表数据').glob('*/argotweak_annotation.json')).parent
source=scene/'argotweak_annotation.json';annotation=json.loads(source.read_text())
file=ROOT/'gt_sources/ArgoTweak_baselines/tools/utils/annotate.py'
spec=importlib.util.spec_from_file_location('official',file);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
pcgt,pcst,lsgt,lsst=module.load_annotations(annotation)
elements=[]
for lane in lsst:
    elements.append({k:v for k,v in lane.items() if k!='change_score'})
crossings=[{'id':p['id'],'points':[v.tolist() for v in p['points']]} for p in pcst]
out=ROOT/'m2_inputs';out.mkdir(exist_ok=True)
(out/'prior_map.json').write_text(json.dumps({'scene_id':scene.name,'lane_segments':elements,'pedestrian_crossings':crossings,'coordinates':'City XY; no heights in ArgoTweak input','role':'stale_prior_from_official_loader'},ensure_ascii=False),encoding='utf-8')
(out/'provenance.json').write_text(json.dumps({'annotation_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'official_loader_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'lane_segments':len(elements),'crossings':len(crossings),'note':'Dataset setup uses annotation to extract the supplied stale input only; inference reads prior_map.json without changes/modified/GT. Official raw codes are not detection predictions.'},indent=2),encoding='utf-8')
print(len(elements),len(crossings))
