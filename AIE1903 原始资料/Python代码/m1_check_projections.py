"""Validate saved full-scene pixel projections; build review sheet."""
import csv,json
import numpy as np
from PIL import Image,ImageDraw
from m1_prepare import ROOT,read,write
out=next((ROOT/'m1_outputs').iterdir());folder=out/'projections'
rows=list(csv.DictReader((folder/'index.csv').open(encoding='utf-8-sig')))
errors=[];pixels=0;segments=0;max_pixel_error=0.;cached_timestamp=None;cached_points=None
for row in rows:
    with np.load(ROOT/row['data']) as d:
        uv=d['lidar_uv'];seg=d['map_segments_uv'];w,h=d['image_size'];idx=d['lidar_point_index']
        if len(uv)!=int(row['projected_lidar_points']) or len(seg)!=int(row['map_segments']):errors.append(row['data'])
        for arr in [uv,seg.reshape(-1,2)]:
            # float32 storage may round a point just inside a border onto that border.
            if not np.isfinite(arr).all() or (arr<0).any() or (arr[:,0]>w+.001).any() or (arr[:,1]>h+.001).any():errors.append(row['data']+':bounds')
        if len(uv):
            if cached_timestamp!=row['lidar_timestamp_ns']:
                with np.load(out/'points_city'/f"{row['lidar_timestamp_ns']}.npz") as points:cached_points=points['xyz_city']
                cached_timestamp=row['lidar_timestamp_ns']
            p=cached_points[idx[::97]].astype(float)
            t=d['camera_from_city'];pc=p@t[:3,:3].T+t[:3,3];q=pc@d['K_actual'].T;q=q[:,:2]/q[:,2,None]
            max_pixel_error=max(max_pixel_error,float(np.max(np.abs(q-uv[::97]))))
        pixels+=len(uv);segments+=len(seg)
cameras=sorted({r['camera'] for r in rows});ts=sorted({r['lidar_timestamp_ns'] for r in rows});lookup={(r['lidar_timestamp_ns'],r['camera']):r for r in rows}
sheet=Image.new('RGB',(7*260,3*240),'white');draw=ImageDraw.Draw(sheet)
for j,t in enumerate([ts[0],ts[len(ts)//2],ts[-1]]):
    for i,c in enumerate(cameras):
        im=Image.open(ROOT/lookup[t,c]['overlay']);im.thumbnail((255,210));sheet.paste(im,(i*260,j*240));draw.text((i*260,j*240+214),c,fill='black')
sheet.save(folder/'review_sheet.jpg')
report={'views_checked':len(rows),'unique_frames':len(ts),'expected_views':582*7,'errors':errors,'total_projected_lidar_points':pixels,'total_map_segments':segments,'sampled_pixel_roundtrip_max_px':max_pixel_error,'note':'Counts include repeat projections of the same point in different cameras. Pixel roundtrip verifies computation, not physical calibration accuracy.'}
write(folder/'validation.json',report);print(json.dumps(report,indent=2))
assert len(rows)==582*7 and not errors and max_pixel_error<.001
