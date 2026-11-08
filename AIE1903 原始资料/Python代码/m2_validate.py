import json
import numpy as np
from PIL import Image,ImageDraw
from m1_prepare import ROOT,write
out=next((ROOT/'m2_outputs').iterdir())
def load(name):return [json.loads(s) for s in (out/name).read_text(encoding='utf-8').splitlines()]
obs=load('observed_lines.jsonl');matches=load('matches.jsonl');views=load('visibility.jsonl');prior=json.loads((ROOT/'m2_inputs/prior_map.json').read_text(encoding='utf-8'));ids={v['id'] for v in prior['lane_segments']}
errors=[]
if len({o['observation_id'] for o in obs})!=len(obs):errors.append('duplicate observation id')
if {o['observation_id'] for o in obs}!={m['observation_id'] for m in matches}:errors.append('observation-match mismatch')
for o in obs:
 p=np.array(o['geometry_city'])
 if not np.isfinite(p).all() or len(p)<2:errors.append(o['observation_id'])
for m in matches:
 if m['matched'] and (m['element_id'] not in ids or m['distance_m']>3 or m['direction_difference_deg']>25):errors.append(m['observation_id'])
if len(views)!=582*3 or any(v['status']!='unknown' for v in views):errors.append('view status invalid')
sheet=Image.new('RGB',(3*320,3*270),'white');draw=ImageDraw.Draw(sheet)
for i,p in enumerate(sorted((out/'qa').glob('*.jpg'))):
 im=Image.open(p);im.thumbnail((315,240));x=(i%3)*320;y=(i//3)*270;sheet.paste(im,(x,y));draw.text((x,y+242),p.stem,fill='black')
sheet.save(out/'review_sheet.jpg')
report={'observations_checked':len(obs),'matches_checked':len(matches),'views_checked':len(views),'errors':errors,'status':'PASS' if not errors else 'FAIL','note':'Structural checks only. No precision/recall or true visibility assessment.'};write(out/'validation.json',report);print(json.dumps(report,indent=2));assert not errors
