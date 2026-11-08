"""Compare matched-image experiments; no accuracy claimed without annotation."""
import json
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parent
names=['m2_yolop_trial','m2_yolop_trial_640_vehicle_only','m2_yolop_trial_1280_vehicle_only']
out=ROOT/'m2_yolop_comparison';out.mkdir(exist_ok=True)
experiments={};report={};masks_checked=0
for name in names:
 folder=ROOT/name;rows=json.loads((folder/'results.json').read_text(encoding='utf-8'));summary=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
 experiments[name]={(r['frame_index'],r['camera']):r for r in rows}
 for row in rows:
  with Image.open(ROOT/row['image_path']) as image:size=image.size
  for suffix in ['road','lane_raw','lane_filtered']:
   with Image.open(folder/'masks'/f"{row['frame_index']:04d}_{row['camera']}_{suffix}.png") as mask:
    assert mask.size==size and set(np.unique(mask))<={0,255};masks_checked+=1
 report[name]={'mean_inference_seconds':summary['mean_inference_seconds'],'mean_end_to_end_seconds':summary['mean_end_to_end_seconds'],'cameras':{c:{'images':len([r for r in rows if r['camera']==c]),'raw_empty':sum(r['raw_lane_pixels']==0 for r in rows if r['camera']==c),'filtered_empty':sum(r['filtered_lane_pixels']==0 for r in rows if r['camera']==c),'raw_pixels':sum(r['raw_lane_pixels'] for r in rows if r['camera']==c),'filtered_pixels':sum(r['filtered_lane_pixels'] for r in rows if r['camera']==c)} for c in summary['cameras']}}
assert all(set(experiments[n])==set(experiments[names[0]]) for n in names)
saved=[]
for key,row in experiments[names[0]].items():
 with Image.open(ROOT/row['image_path']) as image:rgb=np.array(image.convert('RGB'))
 sheet=Image.new('RGB',(3*500,510),'white');draw=ImageDraw.Draw(sheet)
 for j,name in enumerate(names):
  mask=np.array(Image.open(ROOT/name/'masks'/f'{key[0]:04d}_{key[1]}_lane_filtered.png'))>0
  pixels=rgb.copy();pixels[mask]=(pixels[mask]*.3+np.array([255,50,50])*.7).astype(np.uint8)
  im=Image.fromarray(pixels);im.thumbnail((495,470));sheet.paste(im,(j*500,25))
  draw.text((j*500+3,4),['640: road + vehicle filtering','640: vehicle filtering only','1280: vehicle filtering only'][j],fill='black')
 path=out/f'{key[0]:04d}_{key[1]}.jpg';sheet.save(path,quality=92);saved.append(path.name)
report['validation']={'matched_images':33,'binary_masks_checked':masks_checked,'status':'PASS','comparisons':len(saved),'note':'Pixel counts and empty outputs are diagnostics, not quality scores.'}
(out/'comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
