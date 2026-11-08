"""Load M1 FrameData and intersect camera rays with the source ground raster."""
import json
from pathlib import Path
import numpy as np
from scipy.optimize import brentq
ROOT=Path(__file__).resolve().parent

class FrameData:
    def __init__(self,record):
        self.record=record
        self.calibration=json.loads((ROOT/record['calibration_ref']).read_text(encoding='utf-8'))
    @property
    def lidar_xyz_city(self):
        with np.load(ROOT/self.record['lidar_xyz_city']) as data:return data['xyz_city']
    def local_map(self):
        source=ROOT/self.record['lidar_source'];scene=source.parents[2]
        data=json.loads(next((scene/'map').glob('log_map_archive*.json')).read_text())
        return [{**v,'object':data[v['layer']][v['id']]} for v in self.record['local_map_elements']]
    def pixel_to_ground_city(self,camera,pixels,max_range_m=60):
        """First ray-height crossing; NaN when out of raster, sky or no crossing.

        Static ground only. Raster interpolation is bilinear; no occlusion inference.
        """
        scene=(ROOT/self.record['lidar_source']).parents[2]
        raster=np.load(next((scene/'map').glob('*ground_height_surface*.npy')),mmap_mode='r')
        sim=json.loads(next((scene/'map').glob('*img_Sim2_city.json')).read_text())
        rot=np.array(sim['R']).reshape(2,2);trans=np.array(sim['t']);scale=sim['s']
        def height(xy):
            x,y=scale*(rot@xy+trans);ix,iy=int(np.floor(x)),int(np.floor(y))
            if ix<0 or iy<0 or ix+1>=raster.shape[1] or iy+1>=raster.shape[0]:return np.nan
            patch=raster[iy:iy+2,ix:ix+2]
            if not np.isfinite(patch).all():return np.nan
            u,v=x-ix,y-iy
            return float(patch[0,0]*(1-u)*(1-v)+patch[0,1]*u*(1-v)+patch[1,0]*(1-u)*v+patch[1,1]*u*v)
        cal=self.calibration[camera];intr=cal['intrinsics_original']
        pose=np.array(self.record['images'][camera]['pose_city'])@np.array(cal['ego_from_camera'])
        pixels=np.atleast_2d(pixels)/np.array(cal['intrinsics_scale_xy'])
        rays=np.column_stack([(pixels[:,0]-intr['cx_px'])/intr['fx_px'],(pixels[:,1]-intr['cy_px'])/intr['fy_px'],np.ones(len(pixels))])
        rays=rays@pose[:3,:3].T;rays/=np.linalg.norm(rays,axis=1)[:,None];origin=pose[:3,3]
        output=np.full((len(rays),3),np.nan)
        for i,ray in enumerate(rays):
            def residual(d):
                p=origin+ray*d;return p[2]-height(p[:2])
            prev=0.;fp=residual(prev)
            for distance in np.arange(.25,max_range_m+.001,.25):
                f=residual(distance)
                if np.isfinite(fp) and np.isfinite(f) and fp>=0 and f<=0:
                    d=brentq(residual,prev,distance);output[i]=origin+ray*d;break
                prev,fp=distance,f
        return output

def load_frames(path):
    for line in Path(path).read_text(encoding='utf-8').splitlines():yield FrameData(json.loads(line))
