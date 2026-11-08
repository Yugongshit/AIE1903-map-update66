"""M6 regression checks, full provenance checks, visual review and packaging."""
import hashlib,json,shutil,zipfile
from collections import defaultdict
import numpy as np
from PIL import Image,ImageDraw
from m6_behaviors import ROOT,read,rows,write,moving_shape,segments,classify_episode

def regressions():
 straight=np.column_stack([np.linspace(0,10,101),np.zeros(101),np.zeros(101)]);assert moving_shape(straight)[0]=='straight'
 angle=np.linspace(0,np.pi/2,101);turn=np.column_stack([10*np.cos(angle),10*np.sin(angle),np.zeros(101)]);assert moving_shape(turn)[0]=='turn'
 angle=np.linspace(0,np.pi,101);uturn=np.column_stack([10*np.cos(angle),10*np.sin(angle),np.zeros(101)]);assert moving_shape(uturn)[0]=='u_turn_candidate'
 heading=np.radians(np.linspace(170,190,40));xy=np.cumsum(np.column_stack([np.cos(heading),np.sin(heading)]),axis=0);assert abs(moving_shape(np.column_stack([xy,np.zeros(40)]))[1]['net_heading_change_deg'])<30
 def group(state,start,stop):return [{'motion_state':state,'timestamp_ns':str(int(1e18)+int(t*1e9)),'position_city_fitted':[t,0,0]} for t in np.linspace(start,stop,21)]
 stationary=group('stationary',0,3);assert classify_episode(stationary,[stationary],0)[0]=='stationary_presence'
 moving=group('moving',0,2);stationary=group('stationary',2.1,5);assert classify_episode(stationary,[moving,stationary],1)[0]=='stop'
 distant=group('moving',0,2);later=group('stationary',3,6);assert classify_episode(later,[distant,later],1)[0]=='stationary_presence'

def main():
 regressions();out=next((ROOT/'deliveries').glob('*_M6_v1'));source=ROOT/'deliveries'/read(out/'provenance.json')['source_m5_package'];features=rows(out/'vehicle_features.jsonl');events=rows(out/'behavior_events.jsonl');assessments=rows(out/'track_assessments.jsonl');samples=rows(source/'track_samples.jsonl');tracks=rows(source/'tracks.jsonl');measurement={m['measurement_id']:m for m in rows(source/'measurements.jsonl')};bykey={(s['track_id'],s['timestamp_ns']):s for s in samples};ff={(f['track_id'],f['timestamp_ns']):f for f in features};grouped=defaultdict(list)
 assert len(features)==len(samples) and set(ff)==set(bykey);assert {a['track_id'] for a in assessments}=={t['track_id'] for t in tracks};assert len({e['event_id'] for e in events})==len(events)
 for f in features:
  s=bykey[f['track_id'],f['timestamp_ns']];assert f['observed']==s['observed'] and f['measurement_id']==s['measurement_id']
  if f['behavior_usable']:assert f['observed'] and s['velocity_usable_for_behavior_candidate'] and f['measurement_id'] in measurement and np.isfinite(f['position_city_fitted']).all()
  if not f['observed']:assert not f['behavior_usable']
  grouped[f['track_id']].append(f)
 for e in events:
  assert int(e['start_ns'])<=int(e['end_ns']) and abs((int(e['end_ns'])-int(e['start_ns']))/1e9-e['duration_s'])<1e-9;assert e['predicted_samples_used']==0;assert len(e['source_measurement_ids'])==len(e['source_sample_timestamps_ns'])==e['observed_sample_count']==len(e['event_geometry_city'])
  for t,mid,position in zip(e['source_sample_timestamps_ns'],e['source_measurement_ids'],e['event_geometry_city']):
   f=ff[e['track_id'],t];assert f['behavior_usable'] and f['measurement_id']==mid and position==f['position_city_fitted'] and bykey[e['track_id'],t]['observed']
  assert e['behavior_type']!='detour_candidate'
  if e['behavior_type'] in ['stationary_presence','uncertain']:assert not e['is_behavior_event']
  if e['behavior_type']=='stop':assert e['duration_s']>=2 and e['mean_speed_mps']<.5 and 'LOW_SPEED_WITH_MOVING_CONTEXT' in e['reason_codes']
  if e['behavior_type']=='straight':assert e['duration_s']>=1 and abs(e['metrics']['net_heading_change_deg'])<=15 and e['metrics']['accumulated_heading_change_deg']<=35
 # Reconstruct all segment boundaries and rule outputs from features.
 egroup=defaultdict(list)
 for e in events:egroup[e['track_id']].append(e)
 for tid,features_for_track in grouped.items():
  gg=segments(sorted(features_for_track,key=lambda f:int(f['timestamp_ns'])));actual=egroup[tid];assert len(gg)==len(actual)
  for i,g in enumerate(gg):assert classify_episode(g,gg,i)[0]==actual[i]['behavior_type']
 qa=out/'qa';qa.mkdir(exist_ok=True);detections={d['detection_id']:d for d in rows(source/'detections.jsonl')};views={v['view_id']:v for v in rows(source/'views.jsonl')};qaindex=[]
 for e in events:
  if e['status']!='candidate':continue
  mids=e['source_measurement_ids'];chosen=[mids[0],mids[len(mids)//2],mids[-1]];sheet=Image.new('RGB',(3*400,520),'white');draw=ImageDraw.Draw(sheet)
  for j,mid in enumerate(chosen):
   m=measurement[mid];d=max([detections[i] for i in m['detection_ids']],key=lambda d:d['score']);view=views[d['view_id']]
   with Image.open(ROOT/view['source_image_ref']) as image:im=image.convert('RGB')
   dd=ImageDraw.Draw(im);dd.rectangle(tuple(d['bbox_xyxy_px']),outline=(0,255,100),width=5);im.thumbnail((395,460));sheet.paste(im,(j*400,40));draw.text((j*400+4,22),['start','middle','end'][j]+' '+m['timestamp_ns'],fill='black')
  draw.text((4,4),e['event_id']+' '+e['behavior_type']+' candidate',fill='black');p=qa/(e['event_id'].replace(':','_')+'.jpg');sheet.save(p,quality=90);qaindex.append({'event_id':e['event_id'],'file':p.relative_to(out).as_posix(),'note':'Three source detections; not proof of identity continuity or behavior truth.'})
 write(out/'qa_index.json',qaindex)
 report={'status':'PASS','source_samples':len(samples),'feature_rows':len(features),'episodes':len(events),'tracks_assessed':len(assessments),'predictions_used_in_events':0,'regression_tests':['straight_shape','quarter_turn_shape','uturn_shape','heading_wraparound','stationary_presence_not_stop','stop_requires_moving_context','no_context_across_long_gap'],'errors':[],'note':'Rule and lineage checks only; no independent behavior accuracy.'};write(out/'validation.json',report);print(json.dumps(report,indent=2));shutil.copy2(ROOT/'M6处理说明.md',out/'README.md')
 files=sorted(p for p in out.rglob('*') if p.is_file() and p.name!='file_manifest.json');write(out/'file_manifest.json',{'files':[{'path':p.relative_to(out).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in files]})
 with zipfile.ZipFile(out.with_suffix('.zip'),'w',zipfile.ZIP_DEFLATED) as z:
  for p in files+[out/'file_manifest.json']:z.write(p,out.name+'/'+p.relative_to(out).as_posix())
 with zipfile.ZipFile(out.with_suffix('.zip')) as z:assert z.testzip() is None
 print('ZIP',out.with_suffix('.zip'),out.with_suffix('.zip').stat().st_size)
if __name__=='__main__':main()
