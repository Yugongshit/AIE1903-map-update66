from copy import deepcopy
import math
from pathlib import Path
import unittest

from map_update.camera import camera_point, inverse_pose, pixel, project_segment, project_ground, rotation, build_camera_views
from map_update.reliability import integrate_m3
from map_update.studio import build_write_experiment, issue_register
from map_update.semantics import describe_category
from map_update.delivery import read_csv

ROOT=Path(__file__).resolve().parents[1]
RAW=ROOT/'AIE1903 原始资料'
IDENTITY={'translation':[0,0,0],'rotation':[1,0,0,0]}
CALIBRATION={**IDENTITY,'camera_intrinsic':[[50,0,50],[0,50,50],[0,0,1]]}


class ProjectionMathTests(unittest.TestCase):
    def test_semantic_names_do_not_guess_more_than_source_category(self):
        self.assertEqual(describe_category('movable_object.barrier')['name_zh'],'路障 / 隔离栏')
        self.assertEqual(describe_category('movable_object.trafficcone')['name_zh'],'交通锥')
        self.assertEqual(describe_category('static_object.bicycle_rack')['name_zh'],'自行车架')
        self.assertEqual(describe_category('movable_object.pushable_pullable')['name_zh'],'可推拉物体')
        unknown=describe_category('a-new-unmapped-class')
        self.assertEqual(unknown['family'],'other')
        self.assertEqual(unknown['category'],'a-new-unmapped-class')
        self.assertEqual(unknown['name_zh'],'未细分类别')

    def test_inverse_pose_respects_rotation_translation_and_order(self):
        pose={'translation':[10,20,3],'rotation':[math.sqrt(.5),0,0,math.sqrt(.5)]}
        point=inverse_pose([10,22,3],pose)
        for a,b in zip(point,[2,0,0]):self.assertAlmostEqual(a,b)
        camera={**IDENTITY,'translation':[1,0,0]}
        for a,b in zip(camera_point([10,22,3],pose,camera),[1,0,0]):self.assertAlmostEqual(a,b)

    def test_pinhole_center_and_behind_camera(self):
        self.assertEqual(pixel([0,0,10],CALIBRATION['camera_intrinsic']),[50,50])
        with self.assertRaises(ValueError):pixel([0,0,-1],CALIBRATION['camera_intrinsic'])
        with self.assertRaises(ValueError):rotation([0,0,0,0])

    def test_segment_clipping_preserves_crossing_and_discards_behind(self):
        p=project_segment([-20,0,10],[20,0,10],IDENTITY,CALIBRATION,100,100)
        self.assertEqual(p,[[0,50],[100,50]])
        self.assertIsNone(project_segment([0,0,-5],[1,0,-1],IDENTITY,CALIBRATION))
        crossing=project_segment([0,0,-1],[1,0,3],IDENTITY,CALIBRATION,100,100)
        self.assertIsNotNone(crossing)
        self.assertTrue(all(math.isfinite(x) and 0<=x<=100 for p in crossing for x in p))

    def test_empty_ground_polygon_behind_camera_is_not_drawn(self):
        # A conventional optical camera at z=2, looking along +z, sees ground behind it.
        camera={**CALIBRATION,'translation':[0,0,2]}
        self.assertEqual(project_ground([[0,0],[1,0],[1,1],[0,0]],IDENTITY,camera,closed=True),[])


class ActualSceneStudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene=integrate_m3(RAW,ROOT/'external/perception_delivery',RAW/'M3_scene0980_proposal_supplement 2/results/real_scene_handoff.json')
        cls.camera=build_camera_views(RAW,cls.scene)
        cls.experiment=build_write_experiment(cls.scene)

    def test_camera_metadata_is_complete_and_images_are_not_invented(self):
        self.assertEqual(len(self.camera['views']),41)
        self.assertEqual(len(self.camera['checks']),41)
        self.assertEqual(self.camera['available_images'],0)
        self.assertEqual(self.camera['route_type'],'recorded_ego_path_not_planned')
        for view,frame in zip(self.camera['views'],self.scene['replay']):
            self.assertEqual(view['frame_index'],frame['frame_index'])
            self.assertLessEqual(abs(int(view['timestamp_ns'])-int(frame['timestamp_ns'])),50_000_000)
            self.assertIn('/CAM_FRONT/',view['filename'])
        self.assertAlmostEqual(max(abs(v['delta_from_frame_ms']) for v in self.camera['views']),37.609)

    def test_all_projected_segments_are_finite_and_inside_image(self):
        total=0
        for view in self.camera['views']:
            segments=[s for group in ['lines','observations','boxes'] for obj in view[group] for s in obj['segments']]+view['recorded_route']
            for segment in segments:
                for x,y in segment:
                    self.assertTrue(math.isfinite(x) and math.isfinite(y))
                    self.assertTrue(0<=x<=1600 and 0<=y<=900)
                total+=1
        self.assertGreater(total,1000)

    def test_names_sizes_and_heading_match_each_original_annotation(self):
        original={(int(r['frame_index']),r['instance_token']):r for r in read_csv(RAW/'processed/objects.csv')}
        seen=set();display_ids={}
        for view in self.camera['views']:
            for a in view['annotations']:
                key=(view['frame_index'],a['instance_token']);row=original[key];seen.add(key)
                self.assertEqual(a['category'],row['category'])
                self.assertEqual(a['dimensions_lwh_m'],[float(row[k]) for k in ('length_m','width_m','height_m')])
                self.assertEqual(a['yaw_deg'],float(row['yaw_global_deg']))
                self.assertEqual(a['annotation_token'],row['annotation_token'])
                self.assertEqual(a['timestamp_ns'],self.scene['replay'][view['frame_index']]['timestamp_ns'])
                self.assertNotEqual(a['name_zh'],'未细分类别')
                self.assertEqual(a['display_id'],display_ids.setdefault(a['instance_token'],a['display_id']))
                self.assertEqual(a['footprint_xy'][0],a['footprint_xy'][-1])
        self.assertEqual(seen,set(original))
        self.assertEqual(len(set(display_ids.values())),len(display_ids))

    def test_object_to_occupancy_link_uses_exact_instance_and_frame(self):
        for view,f in zip(self.camera['views'],self.scene['replay']):
            for a in view['annotations']:
                expected={o['candidate_id'] for o in f['occupancy'] if any(x['instance_token']==a['instance_token'] for x in o['objects'])}
                self.assertEqual(set(a['occupancy_candidate_ids']),expected)

    def test_projected_box_faces_use_bounded_finite_coordinates(self):
        for view in self.camera['views']:
            tokens={a['instance_token'] for a in view['annotations']}
            for box in view['boxes']:
                self.assertIn(box['instance_token'],tokens)
                for face in box['faces']:
                    self.assertGreaterEqual(len(face),3)
                    self.assertTrue(all(math.isfinite(x) and math.isfinite(y) and 0<=x<=1600 and 0<=y<=900 for x,y in face))

    def test_experiment_uses_real_geometry_but_separate_invented_evidence(self):
        lab=self.experiment
        target=next(e for e in self.scene['reference_map']['elements'] if e['element_id']==lab['target_id'])
        self.assertEqual(target['geometry_city'],lab['baseline']['elements'][0]['geometry_city'])
        self.assertFalse(lab['real_map_modified'])
        self.assertIsNone(lab['real_candidate_id'])
        self.assertEqual(lab['independent_real_visits_added'],0)
        self.assertEqual(lab['baseline']['map_role'],'controlled_synthetic_prior')
        self.assertNotEqual(lab['baseline']['map_id'],self.scene['reference_map']['map_id'])

    def test_every_offset_runs_existing_engine_and_rolls_back_exactly(self):
        original=self.experiment['baseline']['elements']
        for variant in self.experiment['variants']:
            for stage in variant['stages'][:3]:
                self.assertEqual(stage['map']['elements'],original)
                self.assertEqual(len(stage['versions']),1)
            applied=variant['stages'][3]
            self.assertNotEqual(applied['map']['elements'],original)
            self.assertEqual(applied['versions'][-1]['patch']['operation'],'geometry_change')
            self.assertEqual(len(applied['versions'][-1]['patch']['evidence_record_ids']),3)
            restored=variant['stages'][4]
            self.assertEqual(restored['map']['elements'],original)
            self.assertEqual(len(restored['versions']),3)
            self.assertEqual(restored['versions'][-1]['patch']['operation'],'rollback')
            self.assertAlmostEqual(variant['offset_m'],variant['measured_geometry_delta_m'],places=6)

    def test_experiment_does_not_mutate_real_scene_or_m3(self):
        before=deepcopy(self.scene)
        build_write_experiment(self.scene)
        self.assertEqual(before,self.scene)
        self.assertEqual(self.scene['maintenance']['base_map_updates'],[])

    def test_issue_register_has_actionable_owners_and_acceptance(self):
        issues=issue_register(self.scene,self.camera)
        self.assertEqual(len(issues),11)
        self.assertEqual(sum(i['priority']=='P0' for i in issues),6)
        self.assertTrue(all(i['finding'] and i['impact'] and i['owner'] and i['next'] and i['acceptance'] and i['source'] for i in issues))


if __name__=='__main__':unittest.main()
