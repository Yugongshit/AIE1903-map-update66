"""M1 only: AV2/TBV synchronization and coordinate preparation. No change labels read."""
import sys, json, bisect, hashlib
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation, Slerp
from PIL import Image, ImageDraw
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'.runtime'))
import pyarrow.feather as feather

def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def write(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
def transform(row):
    t=np.eye(4);t[:3,:3]=Rotation.from_quat([row[k] for k in ['qx','qy','qz','qw']]).as_matrix();t[:3,3]=[row[k] for k in ['tx_m','ty_m','tz_m']];return t
def apply(x,t):return x@t[:3,:3].T+t[:3,3]
def nearest(ts,t):
    i=bisect.bisect_left(ts,t);return min((ts[j] for j in [i-1,i] if 0<=j<len(ts)),key=lambda v:abs(v-t))
def project(city,pose,ext,intr,size):
    xyz=apply(city,np.linalg.inv(pose@ext));z=xyz[:,2]
    with np.errstate(divide='ignore',invalid='ignore'):
        uv=xyz[:,:2]/z[:,None]*[intr['fx_px'],intr['fy_px']]+[intr['cx_px'],intr['cy_px']]
    uv*=np.array(size)/[intr['width_px'],intr['height_px']]
    mask=np.isfinite(uv).all(1)&(z>0.5)&(uv[:,0]>=0)&(uv[:,0]<size[0])&(uv[:,1]>=0)&(uv[:,1]<size[1])
    return uv,mask,z
def poly_distance(points,xy):
    if len(points)==1:return float(np.linalg.norm(points[0,:2]-xy))
    a=points[:-1,:2];v=points[1:,:2]-a
    u=np.clip(np.sum((xy-a)*v,axis=1)/np.maximum(np.sum(v*v,axis=1),1e-12),0,1)
    return float(np.linalg.norm(a+u[:,None]*v-xy,axis=1).min())

def main():
    scene=next((ROOT/'Argoverse_tvy代表数据').glob('*/scene_info.json')).parent
    out=ROOT/'m1_outputs'/scene.name;out.mkdir(parents=True,exist_ok=True)
    (out/'points_city').mkdir(exist_ok=True);(out/'qa').mkdir(exist_ok=True)
    poses=feather.read_table(scene/'city_SE3_egovehicle.feather').to_pylist()
    poses.sort(key=lambda r:r['timestamp_ns']);pt=[r['timestamp_ns'] for r in poses];origin=pt[0]
    assert len(set(pt))==len(pt)
    def pose(t):
        i=bisect.bisect_left(pt,t)
        if i<len(pt) and pt[i]==t:return transform(poses[i]),'exact',0
        if i==0 or i==len(pt):raise ValueError('pose outside coverage')
        gap=pt[i]-pt[i-1]
        if gap>100_000_000:raise ValueError('pose interpolation gap exceeds 100 ms')
        a,b=transform(poses[i-1]),transform(poses[i]);u=(t-pt[i-1])/gap
        mat=np.eye(4);mat[:3,3]=(1-u)*a[:3,3]+u*b[:3,3]
        mat[:3,:3]=Slerp([0.,1.],Rotation.from_matrix([a[:3,:3],b[:3,:3]]))([u]).as_matrix()[0]
        return mat,'interpolated',gap/1e6
    intr={r['sensor_name']:r for r in feather.read_table(scene/'calibration/intrinsics.feather').to_pylist()}
    ext={r['sensor_name']:transform(r) for r in feather.read_table(scene/'calibration/egovehicle_SE3_sensor.feather').to_pylist()}
    cams={p.name:sorted(int(f.stem) for f in p.glob('*.jpg')) for p in (scene/'sensors/cameras').iterdir() if p.is_dir()}
    sizes={c:Image.open(scene/f'sensors/cameras/{c}/{ts[0]}.jpg').size for c,ts in cams.items()}
    calibration={c:{'intrinsics_original':intr[c],'image_size':sizes[c],'ego_from_camera':ext[c].tolist(),'intrinsics_scale_xy':(np.array(sizes[c])/[intr[c]['width_px'],intr[c]['height_px']]).tolist()} for c in cams}
    write(out/'calibration.json',calibration)
    mapfile=next((scene/'map').glob('log_map_archive*.json'));base=read(mapfile)
    elements=[]
    for layer,items in base.items():
        for oid,obj in items.items():
            keys=['left_lane_boundary','right_lane_boundary'] if layer=='lane_segments' else ['edge1','edge2'] if layer=='pedestrian_crossings' else ['area_boundary']
            lines=[np.array([[p['x'],p['y'],p['z']] for p in obj[k]]) for k in keys]
            if layer=='drivable_areas':lines=[np.vstack([lines[0],lines[0][0]])]
            elements.append((layer,str(oid),obj,lines))
    clipdata=read(scene/'clips.json')['clips'];excluded={int(t) for c in clipdata for t in c.get('excluded_reference_lidar_timestamps_ns',[])}
    files=sorted((scene/'sensors/lidar').glob('*.feather'));records=[];errors=[];sync={c:[] for c in cams};point_count=0;qa=[];roundtrip=0.
    sample_indices=set(np.linspace(0,len(files)-1,3,dtype=int).tolist())
    for idx,file in enumerate(files):
        t=int(file.stem)
        if t in excluded:continue
        clip=next((c for c in clipdata if int(c['start_ns'])<=t<=int(c['end_ns'])),None)
        if not clip:continue
        try:
            cityego,method,gap=pose(t);images={};offsets={};camera_poses={}
            for c,ts in cams.items():
                ct=nearest(ts,t);delta=(ct-t)/1e6
                if abs(delta)>50:raise ValueError(f'{c} outside 50 ms')
                cp,cm,cg=pose(ct);camera_poses[c]=cp;sync[c].append(abs(delta))
                images[c]={'path':str((scene/f'sensors/cameras/{c}/{ct}.jpg').relative_to(ROOT)),'timestamp_ns':str(ct),'pose_city':cp.tolist(),'pose_method':cm,'pose_bracket_ms':cg}
                offsets[c]=delta
            tab=feather.read_table(file);xyz=np.column_stack([tab[k].to_numpy().astype(np.float64) for k in ['x','y','z']])
            if not np.isfinite(xyz).all():raise ValueError('nonfinite point coordinates')
            city=apply(xyz,cityego);roundtrip=max(roundtrip,float(np.abs(apply(city,np.linalg.inv(cityego))-xyz).max()))
            dest=out/'points_city'/f'{t}.npz';np.savez_compressed(dest,xyz_city=city.astype(np.float32),intensity=tab['intensity'].to_numpy(),laser_number=tab['laser_number'].to_numpy())
            local=[]
            for layer,oid,obj,lines in elements:
                # Polygon containment also admits large drivable regions surrounding ego.
                near=any(poly_distance(line,cityego[:2,3])<=30 for line in lines)
                if layer=='drivable_areas':
                    poly=lines[0][:,:2];x,y=cityego[:2,3];inside=False
                    for a,b in zip(poly[:-1],poly[1:]):
                        if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:inside=not inside
                    near=near or inside
                if near:local.append({'layer':layer,'id':oid})
            records.append({'timestamp_ns':str(t),'clip_id':clip['clip_id'],'images':images,'lidar_xyz_city':str(dest.relative_to(ROOT)),'lidar_source':str(file.relative_to(ROOT)),'ego_pose_city':cityego.tolist(),'pose_method':method,'pose_bracket_ms':gap,'calibration_ref':str((out/'calibration.json').relative_to(ROOT)),'local_map_elements':local,'local_map_radius_m':30,'map_source_role':'original_tbv_role_pending_semantic_confirmation','time_offsets_ms':offsets,'point_count':len(xyz)})
            point_count+=len(xyz)
            if idx in sample_indices:
                for c in cams:
                    image=Image.open(ROOT/images[c]['path']).convert('RGB');size=image.size;draw=ImageDraw.Draw(image)
                    uv,mask,z=project(city,camera_poses[c],ext[c],intr[c],size)
                    for p,d in zip(uv[mask][::3],z[mask][::3]):
                        color=(int(255*min(d/60,1)),220,int(255*(1-min(d/60,1))))
                        draw.ellipse((p[0]-1,p[1]-1,p[0]+1,p[1]+1),fill=color)
                    for layer,oid,obj,lines in elements:
                        if not any(poly_distance(line,cityego[:2,3])<50 for line in lines):continue
                        for line in lines:
                            for a,b in zip(line[:-1],line[1:]):
                                n=max(2,int(np.linalg.norm(b-a)/0.3)+1);dense=a+(b-a)*np.linspace(0,1,n)[:,None]
                                pu,valid,_=project(dense,camera_poses[c],ext[c],intr[c],size)
                                for j in range(len(pu)-1):
                                    if valid[j] and valid[j+1]:draw.line([tuple(pu[j]),tuple(pu[j+1])],fill=(255,80,80),width=2)
                    image.thumbnail((800,600));name=f'{idx:04d}_{c}.jpg';image.save(out/'qa'/name,quality=90);qa.append(name)
        except (ValueError,KeyError) as exc:errors.append({'timestamp_ns':str(t),'error':str(exc)})
        if idx%100==0:print(f'{idx+1}/{len(files)} processed',flush=True)
    with (out/'frames.jsonl').open('w',encoding='utf-8') as f:
        for row in records:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    write(out/'map_source.json',{'path':str(mapfile.relative_to(ROOT)),'sha256':hashlib.sha256(mapfile.read_bytes()).hexdigest(),'role':'unresolved: source metadata says current_base_map; do not assume stale prior','ground_height_files':[str(p.relative_to(ROOT)) for p in (scene/'map').glob('*ground*')],'note':'Local selection references whole source objects; no change labels loaded.'})
    summary={'frames':len(records),'input_lidar_files':len(files),'points':point_count,'errors':errors,'roundtrip_max_error_m':roundtrip,'pose_methods':{k:sum(r['pose_method']==k for r in records) for k in ['exact','interpolated']},'camera_max_abs_offset_ms':{c:max(v) for c,v in sync.items()},'qa_images':len(qa),'coordinate_rule':'Released sweeps already ego-frame: city_xyz = city_from_ego @ ego_xyz; no lidar extrinsic reapplied','limitations':['No independent calibration error GT','Static-scene ego motion compensation only; moving objects not compensated','Ground ray function in m1_frames.py uses source raster; independent ground accuracy not measured','Map is source geometry, not an audited stale prior','21 sampled views do not validate every frame']}
    write(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
