"""Independent output checks and sampled ground projection round trip."""
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from m1_frames import load_frames,ROOT

out=next((ROOT/'m1_outputs').iterdir())
frames=list(load_frames(out/'frames.jsonl'));errors=[]
for frame in frames:
    r=frame.record;p=frame.lidar_xyz_city;t=np.array(r['ego_pose_city'])
    if len(p)!=r['point_count'] or not np.isfinite(p).all():errors.append(r['timestamp_ns'])
    if not np.allclose(t[:3,:3].T@t[:3,:3],np.eye(3),atol=1e-9):errors.append('invalid rotation')
ground=[]
for idx in np.linspace(0,len(frames)-1,3,dtype=int):
    f=frames[idx];c='ring_front_center';cal=f.calibration[c];w,h=cal['image_size']
    pixels=np.array([[w*.35,h*.7],[w*.5,h*.8],[w*.65,h*.7],[w*.5,h*.95]])
    xyz=f.pixel_to_ground_city(c,pixels)
    pose=np.array(f.record['images'][c]['pose_city'])@np.array(cal['ego_from_camera'])
    valid=np.isfinite(xyz).all(1);pc=(xyz[valid]-pose[:3,3])@pose[:3,:3]
    k=cal['intrinsics_original'];uv=(pc[:,:2]/pc[:,2,None]*[k['fx_px'],k['fy_px']]+[k['cx_px'],k['cy_px']])*cal['intrinsics_scale_xy']
    ground.append({'timestamp_ns':f.record['timestamp_ns'],'valid_rays':int(valid.sum()),'reprojection_max_px':float(np.abs(uv-pixels[valid]).max()) if valid.any() else None})
qa=sorted((out/'qa').glob('*.jpg'));sheet=Image.new('RGB',(7*240,3*210),'white');draw=ImageDraw.Draw(sheet)
for i,p in enumerate(qa):
    im=Image.open(p);im.thumbnail((235,180));x=(i%7)*240;y=(i//7)*210;sheet.paste(im,(x,y));draw.text((x,y+182),p.stem,fill='black')
sheet.save(out/'qa_contact_sheet.jpg')
report={'frames_checked':len(frames),'errors':errors,'ground_ray_roundtrip':ground,'note':'Reprojection is an algebraic check, not independent geometric accuracy. Contact sheet requires visual review.'}
(out/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
