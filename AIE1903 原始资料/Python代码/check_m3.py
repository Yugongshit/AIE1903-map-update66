"""M3 numerical regressions, record checks and candidate review images."""
import json,hashlib,zipfile,shutil
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
from m3_changes import ROOT,compare,read,rows,write,RULES
from m2_extract import Ground

def main():
 # Long prior / short observation must compare only the observed extent.
 line=np.array([[0.,0.],[100.,0.]])
 for offset in [0.,2.]:
  obs=np.array([[20.,offset,0.],[30.,offset,0.]])
  value=compare(obs,line);assert abs(value['difference_m']-offset)<1e-9 and abs(value['overlap_m']-10)<1e-9
  reverse=compare(obs[::-1],line);assert abs(reverse['difference_m']-offset)<1e-9
 source=next((ROOT/'deliveries').glob('*_M2_v1'));out=ROOT/'deliveries'/source.name.replace('_M2_v1','_M3_v1')
 observations={o['observation_id']:o for o in rows(source/'tables/observations.jsonl')};decisions=rows(out/'decisions.jsonl');candidates=rows(out/'change_candidates.jsonl');mapchecks=rows(out/'map_checks.jsonl');rules=read(out/'rules.json')
 assert len(decisions)==len(observations) and {d['observation_id'] for d in decisions}==set(observations)
 assert len({c['candidate_id'] for c in candidates})==len(candidates)
 assert {d['candidate_id'] for d in decisions if d['candidate_id']}=={c['candidate_id'] for c in candidates}
 prior=read(source/'prior_map.json');boundaries={(l['id'],side):np.array([[p['x'],p['y']] for p in l[side+'_lane_boundary']]) for l in prior['lane_segments'] for side in ['left','right']}
 for c in candidates:
  o=observations[c['observation_id']];assert c['geometry_city']==o['geometry_city'] and c['frame_id']==o['frame_id'] and c['source_run_id']==o['run_id'];assert c['change_type']!='removed' and c['status']=='pending_multiframe_confirmation' and c['evidence_score'] is None
  assert o['length_m']>=3 and o['model_score_mean']>=.6 and c['geometry_usable'] is True and c['observation_valid'] is None
  if c['change_type']=='geometry_changed':
   metric=compare(np.array(c['geometry_city']),boundaries[c['map_element_id'],c['boundary_side']]);assert abs(metric['difference_m']-c['difference_value'])<1e-9 and c['difference_value']>1.5
  elif c['change_type']=='marking_changed':assert c['metrics']['observed_color']!=c['metrics']['prior_color'] and c['metrics']['difference_m']<=.75
  elif c['change_type']=='added':assert c['metrics']['minimum_distance_to_any_prior_boundary_m']>3 and not c['metrics']['remote_parallel_counterparts']
  else:raise AssertionError('unknown change type')
 assert all(r['deletion_assessment']=='not_evaluable' and r['visibility']=='unknown' for r in mapchecks)
 views={v['view_id']:v for v in rows(source/'tables/views.jsonl')};cal=read(source/'calibration.json');m1=next((ROOT/'m1_outputs').iterdir());first=rows(m1/'frames.jsonl')[0];ground=Ground((ROOT/first['lidar_source']).parents[2]);qa=out/'qa';qa.mkdir(exist_ok=True)
 selected=sorted([c for c in candidates if c['change_type']=='geometry_changed'],key=lambda c:-c['difference_value'])[:3]+[c for c in candidates if c['change_type']=='marking_changed'][:2]+[c for c in candidates if c['change_type']=='added'][:1]
 if not selected:selected=candidates[:6]
 qaids=[]
 for i,c in enumerate(selected):
  v=views[c['view_id']];o=observations[c['observation_id']]
  with Image.open(ROOT/v['source_image_ref']) as image:im=image.convert('RGB')
  draw=ImageDraw.Draw(im);pixels=np.array(o['pixel_curve_xy'])[o['geometry_pixel_indices']];draw.line([tuple(p) for p in pixels],fill=(0,255,255),width=5)
  if 'map_geometry_xy' in c['metrics']:
   xy=np.array(c['metrics']['map_geometry_xy']);z=ground.sample(xy);xyz=np.column_stack([xy,z]);ci=cal[v['camera']];pose=np.array(v['city_from_ego_at_image'])@np.array(ci['ego_from_camera']);pc=(xyz-pose[:3,3])@pose[:3,:3];k=ci['intrinsics_original']
   with np.errstate(divide='ignore',invalid='ignore'):uv=pc[:,:2]/pc[:,2,None]*[k['fx_px'],k['fy_px']]+[k['cx_px'],k['cy_px']]
   uv*=np.array(im.size)/[k['width_px'],k['height_px']]
   for j in range(len(uv)-1):
    if np.isfinite(uv[j:j+2]).all() and (pc[j:j+2,2]>.5).all():draw.line([tuple(uv[j]),tuple(uv[j+1])],fill=(255,80,80),width=4)
  im.thumbnail((1000,750));d=ImageDraw.Draw(im);d.rectangle((0,0,im.width,33),fill='black');d.text((4,4),f"{c['change_type']} pending M4 | cyan: observed; red: prior",fill='white');d.text((4,18),f"{c['frame_id']} {c['camera']}",fill='white');im.save(qa/f'{i:02d}.jpg',quality=92);qaids.append({'file':f'qa/{i:02d}.jpg','candidate_id':c['candidate_id']})
 write(out/'qa_index.json',qaids)
 report={'status':'PASS','input_observations':len(observations),'decisions':len(decisions),'candidates':len(candidates),'map_checks':len(mapchecks),'regression_tests':['short_observation_vs_long_prior','parallel_displacement','reverse_curve_direction'],'errors':[],'note':'Checks rule implementation and provenance, not real change accuracy.'};write(out/'validation.json',report);print(json.dumps(report,indent=2))
 shutil.copy2(ROOT/'M3处理说明.md',out/'README.md')
 files=sorted(p for p in out.rglob('*') if p.is_file() and p.name!='file_manifest.json')
 write(out/'file_manifest.json',{'files':[{'path':p.relative_to(out).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in files]})
 with zipfile.ZipFile(out.with_suffix('.zip'),'w',zipfile.ZIP_DEFLATED) as z:
  for p in files+[out/'file_manifest.json']:z.write(p,out.name+'/'+p.relative_to(out).as_posix())
 with zipfile.ZipFile(out.with_suffix('.zip')) as z:assert z.testzip() is None
 print('ZIP',out.with_suffix('.zip'),out.with_suffix('.zip').stat().st_size)
if __name__=='__main__':main()
