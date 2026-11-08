"""M2 color/edge baseline. Reads images, calibration, height raster and prior only."""
import json,csv
from pathlib import Path
from collections import Counter
import cv2
import numpy as np
from scipy.ndimage import map_coordinates
from PIL import Image,ImageDraw
from m1_prepare import ROOT,read,write

class Ground:
 def __init__(self,scene):
  self.height=np.load(next((scene/'map').glob('*ground_height_surface*.npy'))).astype(np.float32)
  sim=read(next((scene/'map').glob('*img_Sim2_city.json')));self.r=np.array(sim['R']).reshape(2,2);self.t=np.array(sim['t']);self.s=sim['s']
 def sample(self,xy):
  uv=(xy@self.r.T+self.t)*self.s
  return map_coordinates(self.height,[uv[:,1],uv[:,0]],order=1,mode='constant',cval=np.nan,prefilter=False)
 def project(self,pixels,pose,k):
  ray=np.column_stack([(pixels[:,0]-k['cx'])/k['fx'],(pixels[:,1]-k['cy'])/k['fy'],np.ones(len(pixels))])@pose[:3,:3].T
  ray/=np.linalg.norm(ray,axis=1)[:,None];origin=pose[:3,3];dist=np.arange(.5,45.01,.5)
  points=origin+dist[:,None,None]*ray[None,:,:]
  res=points[:,:,2]-self.sample(points[:,:,:2].reshape(-1,2)).reshape(len(dist),-1)
  cross=np.isfinite(res[:-1])&np.isfinite(res[1:])&(res[:-1]>=0)&(res[1:]<=0)
  valid=cross.any(0);idx=np.argmax(cross,axis=0);cols=np.arange(len(pixels))
  frac=np.clip(res[idx,cols]/np.maximum(res[idx,cols]-res[idx+1,cols],1e-9),0,1)
  distance=dist[idx]+frac*.5;xyz=origin+distance[:,None]*ray;xyz[~valid]=np.nan
  return xyz

def nearest_distance(points,line):
 a=line[:-1];v=line[1:]-a;den=np.maximum((v*v).sum(1),1e-9)
 u=np.clip(((points[:,None,:]-a)*v).sum(2)/den,0,1)
 return np.linalg.norm(points[:,None,:]-(a+u[:,:,None]*v),axis=2).min(1)

def main():
 m1=next((ROOT/'m1_outputs').iterdir());records=[json.loads(s) for s in (m1/'frames.jsonl').read_text(encoding='utf-8').splitlines()]
 prior=read(ROOT/'m2_inputs/prior_map.json');cal=read(m1/'calibration.json');scene=(ROOT/records[0]['lidar_source']).parents[2];ground=Ground(scene)
 out=ROOT/'m2_outputs'/scene.name;out.mkdir(parents=True,exist_ok=True);(out/'qa').mkdir(exist_ok=True)
 # Keep prior attributes/relationships; duplicate shared boundaries are retained with object identities.
 boundaries=[]
 for lane in prior['lane_segments']:
  for side in ['left','right']:
   p=np.array([[v['x'],v['y']] for v in lane[side+'_lane_boundary']])
   if len(p)>1:boundaries.append({'element_id':lane['id'],'side':side,'xy':p,'marking_type':lane[side+'_lane_mark_type']})
 observed=[];matches=[];visibility=[];issues=[];cameras=['ring_front_center','ring_front_left','ring_front_right'];qa_idx=set(np.linspace(0,len(records)-1,3,dtype=int))
 for i,record in enumerate(records):
  ego=np.array(record['ego_pose_city'])[:2,3]
  local=[b for b in boundaries if np.min(np.linalg.norm(b['xy']-ego,axis=1))<60]
  for cam in cameras:
   ci=cal[cam];inf=record['images'][cam]
   with Image.open(ROOT/inf['path']) as img:im=img.convert('RGB');im.thumbnail((960,960));rgb=np.array(im)
   h,w=rgb.shape[:2];intr=ci['intrinsics_original'];kx=w/intr['width_px'];ky=h/intr['height_px'];k={'fx':intr['fx_px']*kx,'fy':intr['fy_px']*ky,'cx':intr['cx_px']*kx,'cy':intr['cy_px']*ky}
   pose=np.array(inf['pose_city'])@np.array(ci['ego_from_camera']);hsv=cv2.cvtColor(rgb,cv2.COLOR_RGB2HSV)
   white=cv2.inRange(hsv,np.array([0,0,155]),np.array([179,65,255]));yellow=cv2.inRange(hsv,np.array([12,65,90]),np.array([40,255,255]))
   roi=np.zeros((h,w),np.uint8);poly=np.array([[int(.05*w),int(.92*h)],[int(.3*w),int(.53*h)],[int(.7*w),int(.53*h)],[int(.95*w),int(.92*h)]],np.int32);cv2.fillPoly(roi,[poly],255)
   mask=(white|yellow)&roi;edges=cv2.Canny(mask,60,160);lines=cv2.HoughLinesP(edges,1,np.pi/180,threshold=25,minLineLength=25,maxLineGap=12)
   candidates=[]
   if lines is not None:
    for x1,y1,x2,y2 in lines[:,0,:]:
     if abs(y2-y1)<8:continue
     candidates.append((float(np.hypot(x2-x1,y2-y1)),np.array([[x1,y1],[x2,y2]],float)))
   candidates.sort(key=lambda x:-x[0]);kept=[];draw=ImageDraw.Draw(im)
   for _,pix in candidates[:100]:
    sample=pix[0]+(pix[1]-pix[0])*np.linspace(0,1,8)[:,None];xyz=ground.project(sample,pose,k)
    if not np.isfinite(xyz).all():continue
    length=float(np.linalg.norm(np.diff(xyz[:,:2],axis=0),axis=1).sum())
    if not 1.5<=length<=25:continue
    xy=xyz[:,:2];direction=xy[-1]-xy[0];direction/=np.linalg.norm(direction)
    if any(np.linalg.norm(xy.mean(0)-p.mean(0))<.6 and abs(np.dot(direction,d))>.96 for p,d in kept):continue
    kept.append((xy,direction));oid=f"obs_{i:04d}_{cam}_{len(kept):03d}"
    ix=np.clip(sample[:,0].astype(int),0,w-1);iy=np.clip(sample[:,1].astype(int),0,h-1);col='yellow' if np.count_nonzero(yellow[iy,ix])>2 else 'white' if np.count_nonzero(white[iy,ix])>2 else 'unknown'
    row={'observation_id':oid,'timestamp_ns':record['timestamp_ns'],'camera':cam,'geometry_city':xyz.tolist(),'pixel_curve_original':(sample/np.array([w/ci['image_size'][0],h/ci['image_size'][1]])).tolist(),'length_m':length,'color_candidate':col,'marking_type':'unknown','status':'unverified_color_edge_candidate'}
    observed.append(row);best=None
    for b in local:
     v=b['xy'][-1]-b['xy'][0];norm=np.linalg.norm(v)
     if norm<1e-6:continue
     angle=float(np.degrees(np.arccos(np.clip(abs(np.dot(direction,v/norm)),0,1))))
     if angle>25:continue
     ds=nearest_distance(xy,b['xy']);distance=float(np.mean(ds));overlap=float(np.mean(ds<=3))
     if distance<=3 and overlap>=.5 and (best is None or distance<best['distance_m']):best={'element_id':b['element_id'],'side':b['side'],'distance_m':distance,'direction_difference_deg':angle,'sample_fraction_within_3m':overlap}
    matches.append({'observation_id':oid,'timestamp_ns':record['timestamp_ns'],'matched':best is not None,**(best or {})})
    draw.line([tuple(pix[0]),tuple(pix[1])],fill=(0,255,100) if best else (255,130,0),width=3)
    if len(kept)>=30:break
   visibility.append({'timestamp_ns':record['timestamp_ns'],'camera':cam,'status':'unknown','reason':'No occlusion/road segmentation model; absent detection is not evidence of deletion','candidate_count':len(kept)})
   if i in qa_idx:im.thumbnail((800,600));im.save(out/'qa'/f'{i:04d}_{cam}.jpg',quality=90)
  if (i+1)%100==0:print(f'{i+1}/{len(records)} frames; {len(observed)} candidates',flush=True)
 for name,items in [('observed_lines.jsonl',observed),('matches.jsonl',matches),('visibility.jsonl',visibility)]:
  with (out/name).open('w',encoding='utf-8') as f:
   for item in items:f.write(json.dumps(item,ensure_ascii=False)+'\n')
 write(out/'prior_elements.json',prior)
 summary={'frames':len(records),'cameras':cameras,'observations':len(observed),'matched':sum(m['matched'] for m in matches),'unmatched':sum(not m['matched'] for m in matches),'colors':dict(Counter(o['color_candidate'] for o in observed)),'errors':issues,'method':'color-edge-Hough baseline; not pretrained','visibility':'unknown for all views','gt_used_for_inference':False,'limitations':['No crosswalk detector','No solid/dashed classifier','Vehicles, shadows and building edges may cause false positives','Not a verified road segmentation; results are candidates','No change classification or deletion inference','Only three front cameras processed']}
 write(out/'summary.json',summary);print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
