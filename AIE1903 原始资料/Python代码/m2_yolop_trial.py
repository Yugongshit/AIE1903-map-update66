"""Small YOLOP CPU trial using official ONNX weights; no map/GT input."""
import json,time,hashlib,subprocess,argparse
from collections import defaultdict
from pathlib import Path
import cv2
import numpy as np
import onnxruntime as ort
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parent

def nms(raw,threshold=.25,iou=.45):
 p=raw[0];p=p[p[:,4]>.25].copy();p[:,5]*=p[:,4];p=p[p[:,5]>=threshold]
 if len(p)==0:return np.empty((0,6),np.float32)
 boxes=np.column_stack([p[:,0]-p[:,2]/2,p[:,1]-p[:,3]/2,p[:,0]+p[:,2]/2,p[:,1]+p[:,3]/2,p[:,5],np.zeros(len(p))])
 order=np.argsort(-boxes[:,4]);keep=[]
 while len(order) and len(keep)<300:
  i=order[0];keep.append(i);other=order[1:];a=boxes[i];b=boxes[other]
  wh=np.maximum(0,np.minimum(a[2:4],b[:,2:4])-np.maximum(a[:2],b[:,:2]));inter=wh[:,0]*wh[:,1]
  union=(a[2]-a[0])*(a[3]-a[1])+(b[:,2]-b[:,0])*(b[:,3]-b[:,1])-inter
  order=other[inter/np.maximum(union,1e-9)<=iou]
 return boxes[keep]

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--size',type=int,choices=[640,1280],default=640);parser.add_argument('--vehicle-only',action='store_true');args=parser.parse_args();size=args.size
 src=ROOT/'model_sources/YOLOP';model=src/f'weights/yolop-{size}-{size}.onnx';out=ROOT/('m2_yolop_trial' if not args.vehicle_only and size==640 else f'm2_yolop_trial_{size}_vehicle_only');out.mkdir(exist_ok=True);(out/'masks').mkdir(exist_ok=True);(out/'comparisons').mkdir(exist_ok=True)
 m1=next((ROOT/'m1_outputs').iterdir());records=[json.loads(s) for s in (m1/'frames.jsonl').read_text(encoding='utf-8').splitlines()]
 indices=sorted(set(np.linspace(0,len(records)-1,10,dtype=int).tolist()+[290]));cameras=['ring_front_center','ring_front_left','ring_front_right']
 baseline=defaultdict(list)
 previous=next((ROOT/'m2_outputs').glob('*/observed_lines.jsonl'))
 for s in previous.read_text(encoding='utf-8').splitlines():
  row=json.loads(s);baseline[row['timestamp_ns'],row['camera']].append(row)
 options=ort.SessionOptions();options.intra_op_num_threads=4;options.inter_op_num_threads=1
 session=ort.InferenceSession(str(model),sess_options=options,providers=['CPUExecutionProvider'])
 # Warmup excludes loading and one-time initialization from reported inference speed.
 session.run(None,{'images':np.zeros((1,3,size,size),np.float32)})
 results=[]
 for index in indices:
  r=records[index]
  for camera in cameras:
   start=time.perf_counter();path=ROOT/r['images'][camera]['path']
   with Image.open(path) as image:rgb=np.array(image.convert('RGB'))
   h,w=rgb.shape[:2];ratio=min(size/h,size/w);nw,nh=round(w*ratio),round(h*ratio);left,top=(size-nw)//2,(size-nh)//2
   canvas=np.full((size,size,3),114,np.uint8);canvas[top:top+nh,left:left+nw]=cv2.resize(rgb,(nw,nh),interpolation=cv2.INTER_AREA)
   x=(canvas.astype(np.float32)/255-np.array([.485,.456,.406],np.float32))/np.array([.229,.224,.225],np.float32)
   x=np.ascontiguousarray(x.transpose(2,0,1)[None]);t=time.perf_counter();det,road,lane=session.run(None,{'images':x});inftime=time.perf_counter()-t
   assert np.isfinite(det).all() and np.isfinite(road).all() and np.isfinite(lane).all()
   boxes=nms(det);boxes[:,[0,2]]=(boxes[:,[0,2]]-left)/ratio;boxes[:,[1,3]]=(boxes[:,[1,3]]-top)/ratio;boxes[:,[0,2]]=np.clip(boxes[:,[0,2]],0,w);boxes[:,[1,3]]=np.clip(boxes[:,[1,3]],0,h)
   def restore(logits):
    # Retain binary original-size masks, using nearest-neighbor for categorical data.
    mask=logits[0].argmax(0).astype(np.uint8)[top:top+nh,left:left+nw]
    return cv2.resize(mask,(w,h),interpolation=cv2.INTER_NEAREST)
   roadmask=restore(road);lanemask=restore(lane);vehicle=np.zeros((h,w),np.uint8)
   for box in boxes:
    x1,y1,x2,y2=box[:4].astype(int);vehicle[y1:y2,x1:x2]=1
   # Dilated drivable region includes bordering lane lines. These are heuristic masks.
   kernel=max(5,int(round(min(w,h)*.015))|1);roadnear=cv2.dilate(roadmask,np.ones((kernel,kernel),np.uint8))
   filtered=lanemask*(vehicle==0) if args.vehicle_only else lanemask*(roadnear>0)*(vehicle==0)
   name=f'{index:04d}_{camera}'
   for suffix,mask in [('road',roadmask),('lane_raw',lanemask),('lane_filtered',filtered)]:Image.fromarray((mask*255).astype(np.uint8)).save(out/'masks'/f'{name}_{suffix}.png')
   def panel(mask,color):
    im=rgb.copy();use=mask>0;im[use]=(im[use]*.4+np.array(color)*.6).astype(np.uint8);return Image.fromarray(im)
   raw=panel(lanemask,(255,60,60));clean=panel(filtered,(255,60,60));area=panel(roadmask,(40,220,100));draw=ImageDraw.Draw(area)
   for b in boxes:draw.rectangle(tuple(b[:4]),outline=(255,200,0),width=4)
   old=Image.fromarray(rgb);d=ImageDraw.Draw(old)
   for o in baseline[r['timestamp_ns'],camera]:d.line([tuple(v) for v in o['pixel_curve_original']],fill=(255,120,0),width=4)
   sheet=Image.new('RGB',(4*400,440),'white');ds=ImageDraw.Draw(sheet)
   for j,(im,label) in enumerate([(old,'Color/edge baseline'),(raw,'YOLOP lane raw'),(area,'YOLOP road + vehicle boxes'),(clean,'YOLOP lane after filtering')]):
    im.thumbnail((395,400));sheet.paste(im,(400*j,25));ds.text((400*j+3,3),label,fill='black')
   sheet.save(out/'comparisons'/f'{name}.jpg',quality=90)
   row={'frame_index':index,'timestamp_ns':r['timestamp_ns'],'camera':camera,'image_path':str(path.relative_to(ROOT)),'input_resize':[nw,nh],'padding_left_top':[left,top],'vehicle_detections':boxes.tolist(),'baseline_candidates':len(baseline[r['timestamp_ns'],camera]),'raw_lane_pixels':int(lanemask.sum()),'filtered_lane_pixels':int(filtered.sum()),'raw_lane_inside_detected_boxes':int((lanemask*vehicle).sum()),'raw_lane_outside_road_neighborhood':int((lanemask*(roadnear==0)).sum()),'inference_seconds':inftime,'end_to_end_seconds':time.perf_counter()-start}
   results.append(row);print(f'{len(results)}/{len(indices)*3} {name}: {inftime:.3f}s, {len(boxes)} vehicles',flush=True)
 (out/'results.json').write_text(json.dumps(results,indent=2,ensure_ascii=False),encoding='utf-8')
 summary={'model':f'official YOLOP yolop-{size}-{size}.onnx','filter':'vehicle_boxes_only' if args.vehicle_only else 'road_neighborhood_and_vehicle_boxes','source_commit':subprocess.check_output(['git','-C',str(src),'rev-parse','HEAD'],text=True).strip(),'sha256':hashlib.sha256(model.read_bytes()).hexdigest(),'backend':'onnxruntime CPU, 4 intra-op threads','images':len(results),'timesteps':indices,'mean_inference_seconds':float(np.mean([r['inference_seconds'] for r in results])),'median_inference_seconds':float(np.median([r['inference_seconds'] for r in results])),'mean_end_to_end_seconds':float(np.mean([r['end_to_end_seconds'] for r in results])),'cameras':cameras,'gt_or_map_used':False,'thresholds':{'detection_conf':.25,'nms_iou':.45},'limitations':['No independent road/lane labels: no precision, recall or IoU measured','Detection boxes approximate occlusion; box filtering can remove real lane pixels','Road dilation/filtering heuristic may suppress road boundary markings','No solid/dashed or crosswalk classification','33 deterministic samples from one scene do not establish dataset-wide performance']}
 (out/'summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding='utf-8')
 # Small nine-view sheet; inspect full comparison files for detail.
 grid=Image.new('RGB',(1600,3*440),'white')
 for j,i in enumerate([0,290,581]):
  for k,c in enumerate(cameras):
   p=Image.open(out/'comparisons'/f'{i:04d}_{c}.jpg');p.thumbnail((530,430));grid.paste(p,(k*533,j*440))
 grid.save(out/'review_sheet.jpg',quality=95)
 print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
