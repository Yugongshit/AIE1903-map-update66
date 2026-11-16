import json
from pathlib import Path
import tempfile
import unittest
from scripts.build_recorded_viewer import scene_payload, attach_reports


class RecordedViewerTests(unittest.TestCase):
    def test_invalid_states_are_not_rendered_and_source_z_is_preserved(self):
        scene={'id':'s','length':2,'metadata':{'sdc_id':'a','ts':[0,.1]},
               'map_features':{},'dynamic_map_states':{},
               'tracks':{'a':{'type':'VEHICLE','state':{
                   'valid':[True,False],'position':[[1,2,3],[float('nan'),0,0]],
                   'velocity':[[1,0],[0,0]],'heading':[.5,.6]}}}}
        row=scene_payload(scene)
        self.assertEqual(row['tracks'][0]['frames'][0]['position'],[1,2,3])
        self.assertIsNone(row['tracks'][0]['frames'][1])
        self.assertIsNone(row['tracks'][0]['frames'][0]['length'])
        json.dumps(row,allow_nan=False)

    def test_report_alignment_and_map_binding(self):
        scene={'id':'s','physical_map_sha256':'map','branches':[],
               'tracks':[{'id':k,'frames':[{'position':p}]} for k,p in [('a',[100,200]),('b',[102,200])]]}
        branch={'controlled':['a','b'],'takeover_step':0,'final_step':1,'initial_objects':{
            'a':{'position_m':[0,0]},'b':{'position_m':[2,0]}},'trace':[{'step':1,'objects':{}}]}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'report.json'
            report={'scene_id':'s','physical_map_sha256':'map','branches':[branch]}
            path.write_text(json.dumps(report));attach_reports([scene],[Path(tmp)])
            self.assertEqual(scene['branches'][0]['offset'],[100,200])
            report['physical_map_sha256']='other'
            path.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'map differs'):attach_reports([scene],[Path(tmp)])
            report['physical_map_sha256']='map';branch['initial_objects']['b']['position_m']=[80,0]
            path.write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'initial states'):attach_reports([scene],[Path(tmp)])

    def test_old_report_without_trace_does_not_invent_counterfactual_motion(self):
        scene={'id':'s','physical_map_sha256':'map','branches':[]}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'report.json').write_text(json.dumps({'scene_id':'s','physical_map_sha256':'map',
                'branches':[{'controlled':['a'],'deviation_from_log_m':{'a':1}}]}))
            attach_reports([scene],[Path(tmp)])
            self.assertEqual(scene['branches'],[])
