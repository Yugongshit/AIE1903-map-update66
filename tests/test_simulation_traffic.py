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
                        {'stop_duration_s': 0}, {'unknown': 1}, {'area':'invented'},
                        {'following_model':'trained'}, {'driver_style':'teleport'}):
            with self.assertRaises(ValueError):
                traffic_options(options)

    def test_near_recording_region_preserves_ego_roads_and_spawn_clearance(self):
        from map_update.simulation_interactive_scenarios import vehicle_footprint
        before=deepcopy(self.payload)
        near=cyclic_traffic(self.payload,dict(area='near_recording',vehicle_count=8,following_model='idm',driver_style='mixed'))
        far=cyclic_traffic(self.payload,{})
        self.assertLess(near['traffic_area']['route_distance_to_recorded_ego_m'],15.)
        self.assertGreater(far['traffic_area']['route_distance_to_recorded_ego_m'],100.)
        self.assertEqual(self.payload,before)
        ego=self.payload['tracks'][self.payload['ego_track_id']]
        occupied=[vehicle_footprint(ego['position'][0],ego['heading'][0])]
        occupied.extend(vehicle_footprint(a['position'],a['heading'],a['length'],a['width'],margin=0.)
            for a in self.payload['static_overview_actors'])
        for mission in near['missions']:
            shape=vehicle_footprint(mission['position_scenario_m'],mission['heading_rad'])
            self.assertFalse(any(shape.intersects(o) for o in occupied))
            occupied.append(shape)

    def test_behaviour_ablation_does_not_change_spawns_or_desired_speeds(self):
        base=dict(area='near_recording',vehicle_count=8,speed_spread=.3,seed=0)
        a=cyclic_traffic(self.payload,dict(base,following_model='legacy',driver_style='normal'))
        b=cyclic_traffic(self.payload,dict(base,following_model='idm',driver_style='mixed'))
        for x,y in zip(a['missions'],b['missions']):
            for key in ('position_scenario_m','heading_rad','cruise_speed_m_s','source_lane_ids'):
                self.assertEqual(x[key],y[key])


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

    def test_idm_diagnostics_are_background_only_and_external_actions_stay_untouched(self):
        from map_update.simulation_following import driver_parameters
        features, config = scene()
        config['cyclic_path']=[[100.,200.],[180.,200.],[180.,280.],[100.,280.],[100.,200.]]
        config['traffic']=dict(following_model='idm')
        config['missions'][0].update(cycle_follower=True,driver_parameters=driver_parameters(0,'P1','normal'))
        env=Env();runtime=InteractiveRuntime(env,config,features,map_sha256='source')
        self.addCleanup(runtime.close)
        runtime.controls({});runtime.before_step()
        self.assertEqual(runtime.diagnostics['P1']['following']['model'],'idm')
        measured=vehicle_perception(runtime.report(),'P1')
        self.assertNotIn('following',str(measured))
        self.assertNotIn('driver_parameters',str(measured))
        runtime.controls({'P1':{'mode':'external','action':[.1,.2]}});runtime.before_step()
        self.assertEqual(runtime.actions['P1'],[.1,.2])
        self.assertIsNone(runtime.report()['vehicles']['P1']['route'])
        runtime.controls({'P1':{'mode':'ai'}});runtime.before_step()
        self.assertEqual(runtime.diagnostics['P1']['following']['parameters']['style'],'normal')

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
