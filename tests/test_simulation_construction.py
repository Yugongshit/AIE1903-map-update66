from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from map_update.simulation_events import ConstructionEvent, sample_clearance_time
from map_update.simulation_traffic import cyclic_traffic, traffic_options, vehicle_perception
from map_update.simulation_live_map import ObservedStateMap, footprint
from map_update.simulation_platform_data import load_scene
from map_update.simulation_interactive_scenarios import vehicle_footprint


def actor(identifier, x, kind='VEHICLE', length=4.5):
    return dict(vehicle_id=identifier,type=kind,position_city_m=[x,0.],heading_rad=0.,
        speed_m_s=0.,length_m=length,width_m=2.,height_m=1.)


class ConstructionTests(unittest.TestCase):
    def test_generated_barrier_has_clear_initial_footprint_and_is_repeatable(self):
        payload=load_scene(Path(__file__).resolve().parents[1],'nuscenes-scene0980')
        before=deepcopy(payload)
        for count in (1,8,12):
            options=dict(area='near_recording',construction=True,stop_enabled=False,vehicle_count=count,seed=7)
            config=cyclic_traffic(payload,options)
            self.assertEqual(config,cyclic_traffic(payload,options))
            spec=config['construction']
            box=vehicle_footprint(spec['position_city_m'],spec['heading_rad'],spec['length_m'],spec['width_m'])
            self.assertTrue(spec['synthetic'])
            for m in config['missions']:
                self.assertFalse(box.intersects(vehicle_footprint(m['position_scenario_m'],m['heading_rad'])))
        self.assertEqual(payload,before)

    def test_invalid_flags_and_duration_rejected(self):
        for config in ({'construction':1},{'stop_enabled':'false'},{'construction_duration_s':0},
                       {'construction_duration_s':float('nan')}, {'construction_min_s':120},
                       {'construction_max_s':4}, {'construction_min_s':float('nan')},
                       {'construction_duration_s':60,'construction_min_s':45}):
            with self.assertRaises(ValueError):traffic_options(config)

    def test_clearance_is_seeded_bounded_and_not_one_fixed_deadline(self):
        values=[]
        for seed in range(200):
            options=traffic_options({'seed':seed})
            t=sample_clearance_time(options)
            self.assertEqual(t,sample_clearance_time(options))
            self.assertGreaterEqual(t,45.);self.assertLessEqual(t,120.)
            values.append(t)
        self.assertGreater(len(set(values)),100)
        self.assertGreater(max(values)-min(values),50.)

    def test_legacy_duration_migrates_to_random_range_and_normalization_is_idempotent(self):
        options=traffic_options({'construction_duration_s':60})
        self.assertEqual((options['construction_min_s'],options['construction_max_s']),(45.,120.))
        self.assertNotIn('construction_duration_s',options)
        self.assertEqual(options,traffic_options(options))

    def test_clearance_stream_is_independent_of_population_and_driver_choices(self):
        first=traffic_options({'seed':7})
        second=traffic_options({'seed':7,'vehicle_count':12,'driver_style':'mixed','speed_spread':.3})
        self.assertEqual(sample_clearance_time(first),sample_clearance_time(second))

    def test_changing_clearance_range_does_not_move_initial_world(self):
        payload=load_scene(Path(__file__).resolve().parents[1],'nuscenes-scene0980')
        first=cyclic_traffic(payload,{'seed':7,'construction':True})
        second=cyclic_traffic(payload,{'seed':7,'construction':True,'construction_min_s':120,'construction_max_s':240})
        for key in ('missions','construction','cyclic_path'):
            self.assertEqual(first[key],second[key])

    def test_clearance_uses_simulation_time_and_cleanup_is_idempotent(self):
        class Engine:
            def __init__(self):self.cleared=[]
            def spawn_object(self,cls,**kwargs):self.spawn=kwargs;return SimpleNamespace(id='native')
            def clear_objects(self,ids,force_destroy):self.cleared.extend(ids)
        engine=Engine()
        spec=dict(object_id='construction-C1',position_city_m=[10.,20.],heading_rad=0.,length_m=1.,width_m=2.5,height_m=1.)
        with patch('map_update.simulation_obstacle_runtime._measured_classes',return_value={'TRAFFIC_BARRIER':object}):
            event=ConstructionEvent(engine,{'old_origin_in_current_coordinate':[-5.,-10.]},spec,60.)
        self.assertEqual(engine.spawn['position'],[5.,10.])
        event.advance(59.9);self.assertEqual(event.report()['status'],'active')
        self.assertNotIn('duration',str(event.report()))
        event.advance(60.);event.advance(70.);event.close()
        self.assertEqual(engine.cleared,['native'])
        self.assertEqual(event.report()['removed_at_s'],60.)


class ObstacleEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.own=actor('ego',0.)
        self.barrier=actor('construction-C1',20.,'TRAFFIC_BARRIER',length=1.)
        self.map=ObservedStateMap({},'road')
        for step in range(25):self.observe(step,[self.barrier])

    def observe(self,step,objects,own=None):
        traffic={'vehicles':{'ego':own or self.own},'static_actors':objects,
                 'synthetic_events':[{'future_clearance_s':60.}]}
        measured=vehicle_perception(traffic,'ego',clearance_queries=self.map.clearance_queries())
        self.assertNotIn('future_clearance',str(measured))
        self.map.update(step,step*.1,measured)
        return measured

    def test_obstacle_confirmation_is_footprint_occupancy_not_lane_closure(self):
        e=self.map.snapshot()['events'][0]
        self.assertEqual(e['kind'],'stationary_obstacle_occupancy')
        self.assertFalse(e['lane_closed'])
        self.assertEqual(self.map.snapshot()['structural_writes'],0)

    def test_missing_object_without_free_scan_only_becomes_stale(self):
        for step in range(25,70):self.map.update(step,step*.1,{'current_objects':[]})
        self.assertEqual(self.map.snapshot()['events'][0]['status'],'stale')

    def test_repeated_current_free_space_clears_but_not_immediately(self):
        self.observe(25,[])
        self.assertEqual(self.map.snapshot()['events'][0]['status'],'active')
        for step in range(26,48):self.observe(step,[])
        e=self.map.snapshot()['events'][0]
        self.assertEqual(e['status'],'cleared')
        self.assertEqual(e['clear_reason'],'repeated_observed_free_previous_footprint')

    def test_out_of_range_cannot_certify_clearance(self):
        for step in range(25,70):
            p=self.observe(step,[],own=actor('ego',-40.))
            self.assertEqual(p['free_space_checks'],[])
        self.assertEqual(self.map.snapshot()['events'][0]['status'],'stale')

    def test_replacement_vehicle_blocks_clearance(self):
        for step in range(25,70):self.observe(step,[actor('other',20.)])
        e=next(e for e in self.map.snapshot()['events'] if e['actor_id']=='construction-C1')
        self.assertEqual(e['status'],'stale')

    def test_body_with_centre_outside_sensor_range_still_blocks_free_region(self):
        query=dict(actor_id='old',geometry_city_m=footprint(actor('old',48.,length=1.)))
        p=vehicle_perception({'vehicles':{'ego':self.own,'other':actor('other',52.,length=8.)},'static_actors':[]},
            'ego',clearance_queries=[query])
        self.assertEqual(p['current_objects'],[])
        self.assertFalse(p['free_space_checks'][0]['observed_free'])

    def test_gap_in_free_evidence_restarts_confirmation(self):
        for step in range(25,40):self.observe(step,[])
        self.observe(50,[])
        self.assertNotEqual(self.map.snapshot()['events'][0]['status'],'cleared')


if __name__=='__main__':unittest.main()
