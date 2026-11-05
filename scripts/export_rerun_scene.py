"""Export delivered scene geometry to Rerun 0.22.1, without inventing images."""
import argparse
import csv
from hashlib import sha256
import json
from pathlib import Path
import re
import uuid

import numpy as np
import rerun as rr
import rerun.blueprint as bp
import rerun.dataframe as rdf


def clipped_segments(ring, bounds):
    """Liang–Barsky display clipping; source map geometry is never rewritten."""
    xmin,ymin,xmax,ymax=bounds
    result=[]
    for a,b in zip(ring,ring[1:]):
        dx,dy=b[0]-a[0],b[1]-a[1];lo,hi=0.,1.;ok=True
        for p,q in [(-dx,a[0]-xmin),(dx,xmax-a[0]),(-dy,a[1]-ymin),(dy,ymax-a[1])]:
            if p==0:
                if q<0:ok=False;break
            elif p<0:lo=max(lo,q/p)
            else:hi=min(hi,q/p)
        if ok and lo<=hi:result.append([[a[0]+lo*dx,a[1]+lo*dy],[a[0]+hi*dx,a[1]+hi*dy]])
    return result


def verify_recording(path, original, views, language):
    loaded=rdf.load_recording(path)
    table=loaded.view(index='frame',contents='/world/objects/**').select().read_all()
    columns={name:table[name].to_pylist() for name in table.column_names if name=='frame' or name.startswith('/world/objects:')}
    assert table.num_rows==len(views), table.num_rows
    verified=0
    for i,fi in enumerate(columns['frame']):
        annotations=views[fi]['annotations']
        for key in ['category','instance_token','annotation_token']:
            assert columns['/world/objects:'+key][i]==[a[key] for a in annotations],(fi,key)
        for j,a in enumerate(annotations):
            row=original[fi,a['instance_token']];q=json.loads(row['rotation_wxyz'])
            for component,expected in [('PoseTranslation3D',a['center_xyz_m']),('HalfSize3D',[v/2 for v in a['dimensions_lwh_m']]),('PoseRotationQuat',[q[1],q[2],q[3],q[0]])]:
                assert np.allclose(columns['/world/objects:'+component][i][j],expected,rtol=1e-6,atol=1e-4),(fi,component)
            expected=(a['name_zh'] if language=='zh' else a['category'])+' #'+a['display_id']
            assert columns['/world/objects:Text'][i][j]==expected
            verified+=1
    assert verified==len(original)
    return verified


def export(source, raw, output):
    if int(np.__version__.split('.')[0])>=2:raise ValueError('Rerun 0.22.1 AnyValues requires NumPy 1.x; use requirements-rerun.txt')
    if rr.__version__ != '0.22.1':raise ValueError('Use rerun-sdk==0.22.1 to match the supplied recording')
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    source=Path(source);raw=Path(raw)
    text=source.read_text();data=json.loads(re.search(r'id="studio-data">(.*?)</script>',text,re.S)[1])
    real,camera=data['real'],data['camera']
    xy=[f['ego']['xy'] for f in real['frames']]
    bounds=[min(p[0] for p in xy)-50,min(p[1] for p in xy)-50,max(p[0] for p in xy)+50,max(p[1] for p in xy)+50]
    start=xy[0]
    with (raw/'processed/objects.csv').open(encoding='utf-8-sig') as f:
        original={(int(x['frame_index']),x['instance_token']):x for x in csv.DictReader(f)}
    manifest={'schema':'aie1903-rerun-scene-1.0','sdk_version':rr.__version__,
              'dataset_id':'nuscenes','scene_id':'scene-0980','camera_images':0,
              'role':'Re-export of delivered geometry and annotations; not the original full camera recording or new model output',
              'display_map_clip_xyxy':bounds,'frame_count':len(real['frames']),'annotation_rows':len(original),'recordings':{},
              'object_source_sha256':sha256((raw/'processed/objects.csv').read_bytes()).hexdigest(),
              'source_map_sha256':real['map']['source_sha256'],'frames':[
                  {'frame_index':f['frame_index'],'elapsed_s':f['elapsed_s'],'timestamp_ns':f['timestamp_ns']} for f in real['frames']]}
    for language in ('en','zh'):
        zh=language=='zh';recording_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'aie1903/nuscenes/scene0980/source-geometry/0.22.1/'+language))
        path=output/f'scene0980-delivered-{language}.rrd'
        rr.init('aie1903_scene0980_delivered_geometry_'+language,recording_id=recording_id,spawn=False,strict=True);rr.save(path)
        rr.send_blueprint(bp.Blueprint(bp.Tabs(
            bp.Horizontal(bp.Spatial2DView(origin='/bev',name='Map and observations',visual_bounds=bp.VisualBounds2D(x_range=[start[0]-55,start[0]+55],y_range=[start[1]-75,start[1]+35])),bp.Spatial3DView(origin='/world',name='3D scene'),column_shares=[.52,.48],name='Scene'),
            bp.Horizontal(bp.Spatial2DView(origin='/front_projection',name='CAM_FRONT geometry (not an image)'),bp.TextDocumentView(origin='/source',name='Source and limits'),column_shares=[.7,.3],name='Projection'),
            bp.TextDocumentView(origin='/evidence',name='Frame evidence'),
            active_tab=0),bp.TimePanel(expanded=True),bp.SelectionPanel(expanded=False),bp.BlueprintPanel(expanded=False),auto_layout=False,auto_views=False))
        rr.log('/world',rr.ViewCoordinates.RIGHT_HAND_Z_UP,static=True)
        rr.log('/source',rr.TextDocument('# scene-0980\n\n'+(
            '来自成员交付的真实地图、物体标注和感知曲线。这个 RRD 是可读数据的重新封装，不是完整原始相机录制。\n\n当前没有相机照片。前视图为标定几何投影。物体类别与尺寸来自 nuScenes 标注；实际轨迹不是规划路线。'
            if zh else 'Real map geometry, object annotations and perception curves from the team delivery. This RRD repackages readable data; it is not the original full camera recording.\n\nCamera images are unavailable. The front view is calibrated geometry. Object classes and sizes are nuScenes annotations; the recorded ego path is not a planned route.'),media_type='text/markdown'),static=True)
        for e in real['map']['elements']:
            g=e['geometry_city'];rings=[g['coordinates']] if g['type']=='LineString' else g['coordinates']
            rings=[seg for ring in rings for seg in clipped_segments(ring,bounds)]
            if not rings:continue
            color=[175,199,211] if g['type']=='LineString' else [68,100,119]
            rr.log('/world/map/'+e['layer']+'/'+e['element_id'],rr.LineStrips3D([[[*p,0] for p in ring] for ring in rings],colors=color,radii=.035),static=True)
            rr.log('/bev/map/'+e['layer']+'/'+e['element_id'],rr.LineStrips2D(rings,colors=color,radii=rr.Radius.ui_points(.7)),static=True)
        rr.log('/world/recorded_ego_path',rr.LineStrips3D([[[*f['ego']['xy'],.1] for f in real['frames']]],colors=[71,202,178],radii=.08),static=True)
        rr.log('/bev/recorded_ego_path',rr.LineStrips2D([[f['ego']['xy'] for f in real['frames']]],colors=[71,202,178],radii=rr.Radius.ui_points(1.5)),static=True)
        seen=0
        for f,view in zip(real['frames'],camera['views']):
            fi=f['frame_index'];rr.set_time_sequence('frame',fi);rr.set_time_nanos('elapsed_s',round(f['elapsed_s']*1e9))
            # Rerun is latest-at: clearing dynamic subtrees is essential when
            # a source annotation / curve is absent from the next frame.
            for dynamic in ['/world/objects','/world/perception','/world/candidates','/bev/objects','/bev/perception','/bev/candidates','/front_projection']:
                rr.log(dynamic,rr.Clear(recursive=True))
            labels=[];colors=[];quaternions=[]
            for a in view['annotations']:
                row=original[fi,a['instance_token']];q=json.loads(row['rotation_wxyz']);seen+=1
                assert a['category']==row['category']
                assert a['dimensions_lwh_m']==[float(row[k]) for k in ('length_m','width_m','height_m')]
                labels.append((a['name_zh'] if zh else a['category'])+' #'+a['display_id'])
                colors.append([int(a['color'][i:i+2],16) for i in (1,3,5)])
                quaternions.append([q[1],q[2],q[3],q[0]])
            objects_in_frame=view['annotations']
            rr.log('/world/objects',rr.Boxes3D(centers=[a['center_xyz_m'] for a in objects_in_frame],sizes=[a['dimensions_lwh_m'] for a in objects_in_frame],quaternions=quaternions,colors=colors,labels=labels,show_labels=False))
            rr.log('/world/objects',rr.AnyValues(category=np.array([a['category'] for a in objects_in_frame],dtype=object),instance_token=np.array([a['instance_token'] for a in objects_in_frame],dtype=object),annotation_token=np.array([a['annotation_token'] for a in objects_in_frame],dtype=object),source='processed/objects.csv',frame=fi))
            rr.log('/bev/objects',rr.LineStrips2D([a['footprint_xy'] for a in objects_in_frame],colors=colors,radii=rr.Radius.ui_points(1.3),labels=labels,show_labels=False))
            rr.log('/world/ego',rr.Points3D([[*f['ego']['xy'],.3]],colors=[108,230,206],radii=.7,labels=['EGO']))
            rr.log('/bev/ego',rr.Points2D([f['ego']['xy']],colors=[108,230,206],radii=rr.Radius.ui_points(5),labels=['EGO']))
            for i,l in enumerate(f['observed_lines']):
                rr.log('/world/perception/'+str(i),rr.LineStrips3D([[[*p,.06] for p in l['geometry_city']['coordinates']]],colors=[237,186,112],radii=.07))
                rr.log('/bev/perception/'+str(i),rr.LineStrips2D([l['geometry_city']['coordinates']],colors=[237,186,112],radii=rr.Radius.ui_points(1.5)))
            for o in f['occupancy']:
                rings=[x['geometry_city']['coordinates'][0] for x in o['objects']]
                rr.log('/bev/candidates/'+o['candidate_id'],rr.LineStrips2D(rings,colors=[199,166,251],radii=rr.Radius.ui_points(2),labels=[o['candidate_id'].split('-')[-1]],show_labels=False))
            for key,group,color in [('map','lines',[205,220,219]),('perception','observations',[237,186,112])]:
                for i,l in enumerate(view[group]):rr.log('/front_projection/'+key+'/'+str(i),rr.LineStrips2D(l['segments'],colors=color,radii=rr.Radius.ui_points(1)))
            objects={a['instance_token']:a for a in view['annotations']}
            strips=[];strip_colors=[];strip_labels=[]
            for b in view['boxes']:
                a=objects[b['instance_token']];color=[int(a['color'][i:i+2],16) for i in (1,3,5)]
                strips.extend(b['segments']);strip_colors.extend([color]*len(b['segments']));strip_labels.extend([a['name_zh'] if zh else a['category']]*len(b['segments']))
            if strips:rr.log('/front_projection/objects',rr.LineStrips2D(strips,colors=strip_colors,radii=rr.Radius.ui_points(1.2),labels=strip_labels,show_labels=False))
            rr.log('/evidence',rr.TextDocument(f'# scene-0980 · frame {fi:02d} · {f["elapsed_s"]:.3f} s\n\n'+
                f'**Objects:** {len(view["annotations"])} · **Candidates:** {len(f["candidate_ids"])}\n\n'+
                '\n'.join('- '+x for x in f['candidate_ids'])+'\n\nReal base-map writes: **0**. M3: **7 / 7 Insufficient Evidence**. Recorded trajectory is retrospective.\n\nSource timestamp (ns): '+f['timestamp_ns'],media_type='text/markdown'))
        rr.disconnect();assert seen==len(original)
        verified=verify_recording(path,original,camera['views'],language)
        loaded=rdf.load_recording(path);columns=[str(c) for c in loaded.schema().component_columns()]
        manifest['recordings'][language]={'file':path.name,'recording_id':loaded.recording_id(),'bytes':path.stat().st_size,'sha256':sha256(path.read_bytes()).hexdigest(),'annotation_rows_verified_from_saved_file':verified,'clear_components_present':any('ClearIsRecursive' in x for x in columns),'image_components':sum(':ImageBuffer' in x or ':Blob' in x for x in columns)}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(manifest['recordings'],ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=Path);p.add_argument('--raw',required=True,type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args();export(a.source,a.raw,a.out)
