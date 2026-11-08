"""Project City map and existing City point clouds into every synchronized camera."""
import json, hashlib, csv
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from m1_prepare import ROOT, read, write, apply, transform, feather

def main():
    out=next((ROOT/'m1_outputs').iterdir())
    records=[json.loads(s) for s in (out/'frames.jsonl').read_text(encoding='utf-8').splitlines()]
    cal=read(out/'calibration.json');source=read(out/'map_source.json');mapfile=ROOT/source['path']
    assert hashlib.sha256(mapfile.read_bytes()).hexdigest()==source['sha256']
    base=read(mapfile);dense={};object_ids=[];object_layers=[]
    for layer,objects in base.items():
        for oid,obj in objects.items():
            key=(layer,str(oid));object_index=len(object_ids);object_ids.append(str(oid));object_layers.append(layer)
            keys=['left_lane_boundary','right_lane_boundary'] if layer=='lane_segments' else ['edge1','edge2'] if layer=='pedestrian_crossings' else ['area_boundary']
            parts=[]
            for k in keys:
                xyz=np.array([[p['x'],p['y'],p['z']] for p in obj[k]])
                if layer=='drivable_areas':xyz=np.vstack([xyz,xyz[0]])
                for a,b in zip(xyz[:-1],xyz[1:]):
                    n=max(1,int(np.ceil(np.linalg.norm(b-a)/.5)))
                    p=a+(b-a)*np.linspace(0,1,n+1)[:,None]
                    parts.extend(zip(p[:-1],p[1:]))
            dense[key]=(np.asarray(parts).reshape(-1,2,3),object_index)
    resultdir=out/'projections';resultdir.mkdir(exist_ok=True)
    for cam in cal:
        (resultdir/cam).mkdir(exist_ok=True)
    write(resultdir/'object_dictionary.json',{'ids':object_ids,'layers':object_layers,'map_source':source,'pixel_coordinates':'original image size; not thumbnail coordinates','occlusion':'not tested; in-view projection is not visibility evidence','map_scope':'objects selected by M1 within 30m of ego, full object geometry retained'})
    def process(record):
        timestamp=record['timestamp_ns'];cityego=np.array(record['ego_pose_city'])
        with np.load(ROOT/record['lidar_xyz_city']) as data:city=data['xyz_city'].astype(np.float64)
        # Independently reload source points to check actual persisted coordinate conversion.
        tab=feather.read_table(ROOT/record['lidar_source'],columns=['x','y','z'],use_threads=False)
        ego=np.column_stack([tab[k].to_numpy().astype(np.float64) for k in ['x','y','z']])
        expected=apply(ego,cityego);error=float(np.max(np.abs(city-expected)))
        assert len(city)==record['point_count'] and np.isfinite(city).all() and error<.001
        parts=[dense[(v['layer'],v['id'])] for v in record['local_map_elements']]
        segments=np.concatenate([p[0] for p in parts])
        identifiers=np.concatenate([np.full(len(p[0]),p[1],dtype=np.int32) for p in parts])
        rows=[]
        for cam,imageinfo in record['images'].items():
            info=cal[cam];intr=info['intrinsics_original'];pose=np.array(imageinfo['pose_city'])@np.array(info['ego_from_camera'])
            camera_from_city=np.linalg.inv(pose)
            with Image.open(ROOT/imageinfo['path']) as original:
                size=original.size;assert list(size)==info['image_size']
                overlay=original.convert('RGB');overlay.thumbnail((800,600))
            k=np.array([[intr['fx_px'],0,intr['cx_px']],[0,intr['fy_px'],intr['cy_px']],[0,0,1.]])
            k[:2]*=(np.array(size)/[intr['width_px'],intr['height_px']])[:,None]
            def project(points):
                pc=apply(points,camera_from_city);h=pc@k.T
                with np.errstate(invalid='ignore',divide='ignore'):uv=h[:,:2]/h[:,2,None]
                valid=np.isfinite(uv).all(1)&(pc[:,2]>.5)&(uv[:,0]>=0)&(uv[:,0]<size[0])&(uv[:,1]>=0)&(uv[:,1]<size[1])
                return uv,valid,pc[:,2]
            uv,valid,depth=project(city);indices=np.flatnonzero(valid)
            mapuv,mapvalid,mapdepth=project(segments.reshape(-1,3));mapuv=mapuv.reshape(-1,2,2);mapvalid=mapvalid.reshape(-1,2).all(1)
            # Exclude near-plane and image-border-crossing fragments conservatively.
            validsegments=mapuv[mapvalid];ids=identifiers[mapvalid]
            dest=resultdir/cam/timestamp
            np.savez_compressed(dest.with_suffix('.npz'),lidar_point_index=indices.astype(np.int32),lidar_uv=uv[valid].astype(np.float32),lidar_depth_m=depth[valid].astype(np.float32),map_segments_uv=validsegments.astype(np.float32),map_object_index=ids,camera_from_city=camera_from_city,K_actual=k,image_size=np.array(size))
            draw=ImageDraw.Draw(overlay);scale=np.array(overlay.size)/size
            for p in uv[valid][::8]*scale:draw.point(tuple(p),fill=(40,220,220))
            colors={'lane_segments':(255,90,70),'pedestrian_crossings':(255,220,30),'drivable_areas':(140,255,80)}
            for seg,oid in zip(validsegments*scale,ids):draw.line([tuple(seg[0]),tuple(seg[1])],fill=colors[object_layers[oid]],width=1)
            draw.rectangle((0,0,min(overlay.width,650),32),fill='black');draw.text((5,3),f'{timestamp} {cam}',fill='white');draw.text((5,17),'cyan: LiDAR | red: lane | yellow: crossing | green: drivable',fill='white')
            overlay.save(dest.with_suffix('.jpg'),quality=85)
            rows.append({'lidar_timestamp_ns':timestamp,'camera':cam,'image_timestamp_ns':imageinfo['timestamp_ns'],'offset_ms':record['time_offsets_ms'][cam],'projected_lidar_points':len(indices),'map_segments':len(ids),'coordinate_max_abs_error_m':error,'data':str(dest.with_suffix('.npz').relative_to(ROOT)),'overlay':str(dest.with_suffix('.jpg').relative_to(ROOT))})
        return rows
    results=[]
    with ThreadPoolExecutor(max_workers=1) as pool:
        for i,rows in enumerate(pool.map(process,records)):
            results.extend(rows)
            if (i+1)%50==0 or i+1==len(records):print(f'{i+1}/{len(records)} frames; {len(results)} camera projections',flush=True)
    with (resultdir/'index.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(results[0]));writer.writeheader();writer.writerows(results)
    summary={'frames':len(records),'camera_projections':len(results),'pointcloud_conversion_max_abs_error_m':max(r['coordinate_max_abs_error_m'] for r in results),'empty_lidar_views':sum(r['projected_lidar_points']==0 for r in results),'empty_map_views':sum(r['map_segments']==0 for r in results),'gt_read':False,'status':'COMPLETED','limitations':['In-frustum does not imply unoccluded visibility','Map fragments crossing image or near-plane boundaries omitted conservatively','No independently measured calibration or localization accuracy','Map role is not established by projection']}
    write(resultdir/'summary.json',summary);print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
