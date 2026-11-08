from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import unittest

from map_update.decision_data import build_decision_data,nearest_on_segment
from map_update.delivery import read_csv
from map_update.evaluation import evaluate_controlled

ROOT=Path(__file__).resolve().parents[1]
RESULTS=ROOT/'AIE1903 原始资料/M3_scene0980_proposal_supplement 2/results'
CASES=RESULTS/'controlled_case_decisions.csv'
COMPARISON=RESULTS/'controlled_comparison.csv'


class DecisionPresentationDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload={'dataset_id':'nuscenes','real':{'dataset_id':'nuscenes','scene_id':'scene-0980','visit_id':'fixture-visit',
            'maintenance':{'base_map_updates':[]},'accuracy':{'precision':None,'recall':None,'f1':None},
            'map':{'dataset_id':'nuscenes','version':'fixture-v1','coordinate_reference':{'frame':'City','units':'m','city_id':'fixture'},'source_sha256':'fixture-map','elements':[{'element_id':'reference-1','geometry_city':{'type':'LineString','coordinates':[[0,0],[10,0]]}}]},
            'candidates':[{'candidate_id':'LD1','change_type':'line_discrepancy','frame_window':[0,0],'map_element_id':None}],
            'frames':[{'frame_index':0,'timestamp_ns':'1000000000','occupancy':[],'observed_lines':[{'candidate_ids':['LD1'],'geometry_city':{'type':'LineString','coordinates':[[1,4],[3,4],[5,4]]},'line_id':1,'source_file':'fixture.csv'}]}]},
            'camera':{'views':[{'annotations':[]}]},
            'provenance':[{'file':p.name,'sha256':sha256(p.read_bytes()).hexdigest()} for p in [CASES,COMPARISON]],
            'controlled_evaluation':evaluate_controlled(read_csv(CASES),read_csv(COMPARISON))}

    def test_distance_is_a_source_point_preview_not_an_edit_target(self):
        original=deepcopy(self.payload)
        out=build_decision_data(self.payload,CASES,COMPARISON)
        sample=out['distance_samples']['LD1']['0']
        self.assertEqual(sample['observation_xy'],[3,4])
        self.assertEqual(sample['reference_xy'],[3,0])
        self.assertEqual(sample['distance_m'],4)
        self.assertIn('not a verified edit target',sample['meaning'])
        self.assertEqual(self.payload,original)
        self.assertEqual(len(out['controlled_cases']),126)
        decision=out['automatic_decisions']['candidates']['LD1']
        self.assertEqual(decision['action'],'difference_recorded')
        self.assertFalse(decision['manual_review_required'])
        self.assertEqual(out['automatic_decisions']['difference_layer']['differences'][0]['observations'][0]['geometry_city']['coordinates'],[[1,4],[3,4],[5,4]])

    def test_changed_source_cannot_reuse_old_evaluation(self):
        data=deepcopy(self.payload);data['provenance'][0]['sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            build_decision_data(data,CASES,COMPARISON)

    def test_per_case_metrics_must_reproduce_displayed_metrics(self):
        data=deepcopy(self.payload);data['controlled_evaluation']['strata']['all'][2]['coverage']=1.
        with self.assertRaisesRegex(ValueError,'do not reproduce'):
            build_decision_data(data,CASES,COMPARISON)

    def test_argoverse_is_not_silently_mixed_into_nuscenes(self):
        data=deepcopy(self.payload);data['dataset_id']='argoverse'
        with self.assertRaisesRegex(ValueError,'reviewed nuScenes'):
            build_decision_data(data,CASES,COMPARISON)

    def test_new_real_writes_require_an_updated_presentation_contract(self):
        data=deepcopy(self.payload);data['real']['maintenance']['base_map_updates']=[{'id':'new'}]
        with self.assertRaisesRegex(ValueError,'newly resolved'):
            build_decision_data(data,CASES,COMPARISON)

    def test_preview_projection_clamps_to_segment_endpoints(self):
        self.assertEqual(nearest_on_segment([-4,3],[0,0],[10,0]),[0,0])
        self.assertEqual(nearest_on_segment([15,3],[0,0],[10,0]),[10,0])
        self.assertEqual(nearest_on_segment([4,3],[1,2],[1,2]),[1,2])

    def test_other_delivery_cannot_be_attached_to_a_different_case(self):
        with self.assertRaisesRegex(ValueError,'source candidate identities'):
            build_decision_data(self.payload,CASES,COMPARISON,ROOT/'external/perception_delivery')
