"""Read-only presentation derivatives for the scene-0980 decision studio."""
from hashlib import sha256
from math import hypot

from .delivery import read_csv,read_json
from .evaluation import evaluate_controlled, METHODS
from .automatic import build_automatic_decisions


def nearest_on_segment(point, a, b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    length2=dx*dx+dy*dy
    factor=max(0.,min(1.,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/length2)) if length2 else 0.
    return [a[0]+factor*dx,a[1]+factor*dy]


def build_decision_data(payload, cases_path, comparison_path, delivery_root=None):
    real=payload['real']
    if payload['dataset_id']!='nuscenes' or real['scene_id']!='scene-0980':
        raise ValueError('This presentation uses the reviewed nuScenes scene-0980 only')
    # This release presents unresolved real evidence. A later real update needs
    # its own explicit presentation contract, not a silently reused zero badge.
    if real['maintenance']['base_map_updates'] or any(real['accuracy'][key] is not None for key in ['precision','recall','f1']):
        raise ValueError('Reconcile the UI with newly resolved real evidence before publishing')
    expected={item['file']:item['sha256'] for item in payload['provenance']}
    for path in [cases_path,comparison_path]:
        if sha256(path.read_bytes()).hexdigest()!=expected[path.name]:
            raise ValueError('Evaluation source hash mismatch: '+path.name)
    rows=read_csv(cases_path)
    evaluation=evaluate_controlled(rows,read_csv(comparison_path))
    if evaluation!=payload['controlled_evaluation']:
        raise ValueError('Per-case rows do not reproduce the existing controlled evaluation')
    delivery_sources=[]
    if delivery_root is not None:
        manifest={entry['path']:entry['sha256'] for entry in read_json(delivery_root/'manifest.json')['files']}
        names=['outputs/stage3/scored_candidates.csv','outputs/stage3/frame_evidence.csv','outputs/stage3/rules.json',
               'outputs/stage5/behavior_events.csv','outputs/stage6/associations.csv','outputs/stage6/rules.json']
        for name in names:
            digest=sha256((delivery_root/name).read_bytes()).hexdigest()
            if digest!=manifest[name]:raise ValueError('Delivered artifact hash mismatch: '+name)
            delivery_sources.append({'path':'external/perception_delivery/'+name,'sha256':digest})
        scored=read_csv(delivery_root/names[0]);lookup={c['candidate_id']:c for c in real['candidates']}
        if {r['candidate_id'] for r in scored}!=set(lookup):raise ValueError('Different source candidate identities')
        for row in scored:
            candidate=lookup[row['candidate_id']]
            for field,convert in [('evidence_score',float),('observed_frames',int),('support_frames',int),('median_difference',float)]:
                if convert(row[field])!=candidate[field]:raise ValueError('Source candidate field mismatch: '+field)
        actual_frames=read_csv(delivery_root/names[1])
        normalized=[{'candidate_id':r['candidate_id'],'frame':int(r['frame']),
                     'supports':r['supports_hypothesis']=='True','usable':r['observation_usable']=='True',
                     'value':float(r['difference_value']),'unit':r['difference_unit'],'source_file':r['source_file']} for r in actual_frames]
        if normalized!=real['frame_evidence']:raise ValueError('Source frame evidence mismatch')
        if read_csv(delivery_root/names[3])!=real['behaviors'] or read_csv(delivery_root/names[4])!=real['associations']:
            raise ValueError('Source behavior / association mismatch')
    references=[e for e in real['map']['elements'] if e['geometry_city']['type']=='LineString']
    categories={c['candidate_id']:set() for c in real['candidates']}
    samples={}
    for frame in real['frames']:
        for occupancy in frame['occupancy']:
            categories[occupancy['candidate_id']].update(o['category'] for o in occupancy['objects'])
        for line in frame['observed_lines']:
            if not line['candidate_ids']:
                continue
            coords=line['geometry_city']['coordinates']
            index=len(coords)//2;point=coords[index];nearest=None
            for element in references:
                ring=element['geometry_city']['coordinates']
                for a,b in zip(ring,ring[1:]):
                    q=nearest_on_segment(point,a,b);distance=hypot(point[0]-q[0],point[1]-q[1])
                    if nearest is None or distance<nearest[0]:nearest=(distance,q,element['element_id'])
            if nearest is None:
                continue
            for cid in line['candidate_ids']:
                samples.setdefault(cid,{}).setdefault(str(frame['frame_index']),{
                    'observation_xy':point,'reference_xy':nearest[1],'distance_m':nearest[0],
                    'reference_element_id':nearest[2],'observation_point_index':index,
                    'source_line_id':line['line_id'],'source_file':line['source_file'],
                    'meaning':'Single-point nearest-reference preview; not a verified edit target, source window median, or geometric accuracy.'})
    name_map={a['category']:a['name_zh'] for view in payload['camera']['views'] for a in view['annotations']}
    return {'schema':'decision-studio-1.0','dataset_id':'nuscenes','scene_id':real['scene_id'],
            'methods':METHODS,'controlled_cases':rows,'controlled_case_sha256':expected[cases_path.name],
            'controlled_metrics_reproduced':True,'source_map_sha256':real['map']['source_sha256'],
            'delivery_sources':delivery_sources,
            'automatic_decisions':build_automatic_decisions(real),
            'candidate_categories':{k:sorted(v) for k,v in categories.items()},
            'candidate_names_zh':{k:[name_map.get(c,c) for c in sorted(v)] for k,v in categories.items()},
            'distance_samples':samples,
            'semantics':{'source_labels':'original nuScenes annotations',
                         'scores':'whole-window preset-rule evidence scores; not probabilities',
                         'm3_join':'candidate ID and exact window; never track-wide propagation',
                         'missing_frame':'no source row, not observed clearance',
                         'controlled_write':'existing engine outputs on a copied map; no real candidate match'}}
