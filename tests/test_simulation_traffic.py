"""Generation, information boundaries and physical-control event contracts."""
from copy import deepcopy
from pathlib import Path
import math
import json
import unittest

from map_update.simulation_traffic import cyclic_traffic, traffic_options, vehicle_perception, CycleFollower
from map_update.simulation_platform_data import load_scene
from map_update.simulation_bridge import feature_sha256
from test_simulation_interactive import Env, Vehicle, scene
from unittest.mock import patch
from map_update.simulation_interactive import InteractiveRuntime


class TrafficGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = load_scene(Path(__file__).resolve().parents[1], 'nuscenes-scene0980')

    def test_repeatable_clearance_and_source_cycle(self):
        before = feature_sha256(self.payload['maps'])
        a = cyclic_traffic(self.payload, {'seed': 7})
        self.assertLess(len(json.dumps({'command':'open','interactive':a})), 60000)
        self.assertEqual(a, cyclic_traffic(self.payload, {'seed': 7}))
        b = cyclic_traffic(self.payload, {'seed': 8})
        self.assertNotEqual(a['missions'][0]['position_scenario_m'], b['missions'][0]['position_scenario_m'])
        self.assertEqual(before, feature_sha256(self.payload['maps']))
        lanes = a['missions'][0]['source_lane_ids']
        for source, target in zip(lanes, lanes[1:]+lanes[:1]):
            self.assertIn(target, self.payload['maps']['updated'][source]['exit_lanes'])
        self.assertTrue(all(m['patrol_loop'] and m['synthetic_actor'] for m in a['missions']))

    def test_variable_population_and_seeded_speed_heterogeneity(self):
        from map_update.simulation_interactive import validated_missions
        for count in (1,8,12):
            config=cyclic_traffic(self.payload, {'vehicle_count':count,'speed_spread':.3})
            self.assertEqual(len(validated_missions(config)),count)
            self.assertLess(len(json.dumps({'command':'open','interactive':config})),60000)
            self.assertEqual(config,cyclic_traffic(self.payload, {'vehicle_count':count,'speed_spread':.3}))
            if count>1:self.assertGreater(len({m['cruise_speed_m_s'] for m in config['missions']}),1)

    def test_invalid_options_fail_closed(self):
        for options in ({'seed': True}, {'seed': -1}, {'speed_m_s': float('nan')},
                        {'stop_duration_s': 0}, {'unknown': 1}):
            with self.assertRaises(ValueError):
                traffic_options(options)


class PerceptionTests(unittest.TestCase):
    def test_distant_objects_and_private_intentions_are_not_observations(self):
        own = dict(vehicle_id='ego',position_city_m=[0.,0.],heading_rad=0.,route=['secret'], action=[1,1])
        near = dict(own,vehicle_id='P1',position_city_m=[20.,0.],goal_scenario_m=[90,0],
                    requested_action=[1,1],event={'start':100})
        far = dict(near,vehicle_id='P2',position_city_m=[51.,0.])
        state = dict(vehicles={'ego':own,'P1':near,'P2':far},static_actors=[])
        measured = vehicle_perception(state,'ego')
        self.assertEqual([v['vehicle_id'] for v in measured['current_objects']],['P1'])
        self.assertEqual(measured['current_objects'][0]['type'],'VEHICLE')
        for actor in [measured['own_vehicle'],*measured['current_objects']]:
            self.assertFalse(set(actor)&{'route','action','requested_action','goal_scenario_m','event'})
        self.assertEqual(state['vehicles']['P1'],near)

    def test_reentry_targets_start_at_actual_pose_and_jitter_does_not_count_travel(self):
        path=[[0,0],[100,0],[100,100],[0,100],[0,0]]
        follower=CycleFollower(path,2.5)
        state=dict(position_runtime_m=[30,0],heading_rad=0.,speed_m_s=0.,wheelbase_m=2.5,max_steering_rad=1.)
        follower(state)
        self.assertEqual(follower.diagnostics()['goal_scenario_m'],[70.,100.])
        for x in [31.,30.]*10:
            follower(dict(state,position_runtime_m=[x,0]))
        self.assertEqual(follower.diagnostics()['travelled_m'],0.)

    def test_reentry_does_not_steer_backwards_or_teleport(self):
        path=[[0,0],[100,0],[100,100],[0,100],[0,0]]
        follower=CycleFollower(path,2.5)
        state=dict(position_runtime_m=[30,0],heading_rad=math.pi,speed_m_s=0.,wheelbase_m=2.5,max_steering_rad=1.)
        before=deepcopy(state)
        self.assertEqual(follower(state),[0.,-1.])
        self.assertEqual(state,before)


class EventTests(unittest.TestCase):
    def setUp(self):
        native = patch("map_update.simulation_interactive._vehicle_class", return_value=Vehicle)
        native.start()
        self.addCleanup(native.stop)
    def test_stop_is_simulation_timed_and_never_overrides_player(self):
        features, config = scene()
        config['traffic'] = dict(seed=0,stop_start_s=5.,stop_duration_s=2.)
        env=Env(); runtime=InteractiveRuntime(env,config,features,map_sha256='source')
        self.addCleanup(runtime.close)
        env.episode_step=50
        runtime.controls({}); runtime.before_step()
        self.assertEqual(runtime.actions['P1'][1],-1.)
        self.assertEqual(runtime.reasons['P1'],'synthetic_scheduled_stop')
        env.episode_step=70
        runtime.before_step()
        self.assertGreater(runtime.actions['P1'][1],0.)
        env.episode_step=51
        runtime.controls({'P1':{'mode':'manual','action':[0,.2]}});runtime.before_step()
        self.assertEqual(runtime.actions['P1'],[0,.2])
