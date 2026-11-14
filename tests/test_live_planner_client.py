import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('live_client',Path(__file__).resolve().parents[1]/'scripts/interactive_planner_client.py')
client_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(client_module)

class EndLoop(Exception):pass

class Client:
    def __init__(self):self.calls=[]
    def request(self,path,**kwargs):
        self.calls.append((path,kwargs))
        return {'state':{'room':{'session_id':'s','epoch':1}},'planning-map':{'map_version':'road','map_features':{}},
                'claim':{'lease_token':'test-lease'},'heartbeat':{},'planning-input':{'session_id':'s','epoch':1,
                    'step':30,'road_version':'road','planning_version':'road:revision',
                    'state_layer':{'state_version':'revision','events':[{'kind':'stationary_vehicle_occupancy'}]},
                    'own_vehicle':{},'current_objects':[]},'input':{},'release':{}}[path]

class LivePlannerClientTests(unittest.TestCase):
    def test_hook_receives_online_state_and_action_declares_consumed_version(self):
        client=Client()
        with patch.object(client_module,'plan',return_value=[0.,-1.]) as hook, patch.object(client_module.time,'sleep',side_effect=EndLoop):
            with self.assertRaises(EndLoop):client_module.run(client,'s',['ego'],'join',live_map=True)
        context=hook.call_args.args[2]
        self.assertEqual(context['state_layer']['events'][0]['kind'],'stationary_vehicle_occupancy')
        action=next(kwargs['body'] for path,kwargs in client.calls if path=='input')
        self.assertEqual(action['planning_state_versions'],{'ego':'revision'})
        self.assertEqual(action['observation_step'],30)
        self.assertEqual(client.calls[-1][0],'release')
        self.assertNotIn('observation',[p for p,_ in client.calls])

    def test_no_implicit_fusion_of_multiple_observers(self):
        client=Client()
        with self.assertRaises(ValueError):client_module.run(client,'s',['ego','P1'],'join',live_map=True)
        self.assertEqual(client.calls,[])
