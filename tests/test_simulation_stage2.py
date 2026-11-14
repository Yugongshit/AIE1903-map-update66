from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import math
import json
import unittest
from shapely.geometry import LineString, Polygon
from map_update.simulation_network import network_traffic, crossing_sites, JunctionCoordinator, route_path
from map_update.simulation_population import IncidentScheduler, TrafficPopulation
from map_update.simulation_traffic import cyclic_traffic, traffic_options, vehicle_perception
from map_update.simulation_interactive_scenarios import vehicle_footprint
from map_update.simulation_platform_data import load_scene
from map_update.simulation_live_map import ObservedStateMap


def actor(key, x, y=0., heading=0., speed=0.):
    return dict(vehicle_id=key,position_city_m=[x,y],heading_rad=heading,type='VEHICLE',
                speed_m_s=speed,length_m=4.5,width_m=2.)


class Stage2GenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload=load_scene(Path(__file__).resolve().parents[1],'nuscenes-scene0980')

    def test_connected_source_routes_are_reproducible_without_moving_ego(self):
        payload=deepcopy(self.payload)
        options=dict(route_mode='network',seed=7,vehicle_count=12)
        first=cyclic_traffic(payload,options)
        self.assertEqual(first,cyclic_traffic(payload,options))
        self.assertEqual(payload,self.payload)
        self.assertEqual(len(first['routes']),3)
        self.assertLess(len(json.dumps(first).encode()),60000)
        self.assertEqual(len({m['route_id'] for m in first['missions']}),3)
        for i,route in enumerate(first['routes']):
            path=route_path(route,payload['maps']['updated'])
            self.assertTrue(LineString(path).is_simple)
            self.assertEqual(path[0],path[-1])
            if i:self.assertTrue(set(route['lanes'])&set(first['routes'][0]['lanes']))
        boxes=[vehicle_footprint(m['position_scenario_m'],m['heading_rad']) for m in first['missions']]
        for i,box in enumerate(boxes):
            for other in boxes[i+1:]:self.assertGreaterEqual(box.distance(other),7.)
        for mission in first['missions']:
            for zone in first['junctions']:
                self.assertGreaterEqual(math.dist(mission['position_scenario_m'],zone['position_city_m']),zone['radius_m']+8.)

    def test_event_flags_do_not_change_routes_or_initial_vehicle_positions(self):
        a=cyclic_traffic(self.payload,dict(route_mode='network'))
        b=cyclic_traffic(self.payload,dict(route_mode='network',pedestrians=True,incidents=True,recurring_construction=True))
        self.assertEqual(a['missions'],b['missions']);self.assertEqual(a['junctions'],b['junctions'])

    def test_new_options_validate_flags_counts_and_mode(self):
        for options in ({'route_mode':'invented'},{'pedestrians':1},{'incidents':'yes'},
                        {'recurring_construction':1},{'pedestrian_count':0},{'pedestrian_count':5},{'pedestrian_count':True}):
            with self.assertRaises(ValueError):traffic_options(options)

    def test_walkers_use_source_crosswalk_polygons_and_report_absent_capacity(self):
        config=cyclic_traffic(self.payload,dict(route_mode='network'))
        paths=[route_path(r,self.payload['maps']['updated']) for r in config['routes']]
        sites=crossing_sites(self.payload['maps']['updated'],paths,[],3,0)
        self.assertTrue(sites)
        for site in sites:
            poly=Polygon(self.payload['maps']['updated'][site['source_crosswalk_id']]['polygon'])
            self.assertTrue(poly.buffer(1e-6).covers(LineString(site['endpoints'])))
        self.assertEqual(crossing_sites({},paths,[],3,0),[])


class JunctionTests(unittest.TestCase):
    def setUp(self):
        self.zone=dict(junction_id='j',position_city_m=[0.,0.],radius_m=6.)
        self.paths={'a':[[-50,0],[50,0],[50,50],[-50,50],[-50,0]],
                    'b':[[0,-50],[0,50],[50,50],[50,-50],[0,-50]]}
        self.control=JunctionCoordinator([self.zone])

    def test_only_one_approach_is_granted_and_occupied_external_car_blocks(self):
        actors=[actor('a',-20),actor('b',0,-20,math.pi/2)]
        yields=self.control.update(actors,self.paths,0.)
        self.assertEqual(len(yields),1)
        holder=self.control.holders['j']['vehicle_id']
        self.assertNotIn(holder,yields)
        self.control=JunctionCoordinator([self.zone])
        yields=self.control.update(actors+[actor('ego',0)],self.paths,0.)
        self.assertEqual(set(yields),{'a','b'})
        self.assertFalse(self.control.holders)

    def test_lead_car_is_not_blocked_by_reservation_for_its_follower(self):
        paths={'a':self.paths['a'],'b':self.paths['a']}
        self.control.update([actor('a',-23),actor('b',-15)],paths,0.)
        self.assertEqual(self.control.holders['j']['vehicle_id'],'b')

    def test_zone_releases_after_holder_exits_and_waiting_car_proceeds(self):
        self.control.update([actor('a',-20),actor('b',0,-20,math.pi/2)],self.paths,0.)
        self.control.update([actor('a',0),actor('b',0,-20,math.pi/2)],self.paths,1.)
        self.control.update([actor('a',15),actor('b',0,-20,math.pi/2)],self.paths,2.)
        self.assertEqual(self.control.passes,1)
        self.assertEqual(self.control.holders['j']['vehicle_id'],'b')


class IncidentTests(unittest.TestCase):
    def test_collision_disables_then_repairs_without_publishing_future_time(self):
        incident=IncidentScheduler(7)
        incident.disable('P1',0.,'physical_collision')
        self.assertEqual(incident.collision_disables,1)
        self.assertNotIn('until',str(incident.report()))
        incident.advance(1.,{'P1':actor('P1',0)},{'P1':'external'},[],[])
        self.assertEqual(incident.active['P1']['status'],'disabled')
        incident.advance(46.,{'P1':actor('P1',0)},{'P1':'external'},[],[])
        self.assertFalse(incident.active);self.assertEqual(incident.completed,1)

    def test_random_breakdown_cannot_take_over_external_vehicle_or_block_junction(self):
        incident=IncidentScheduler(7)
        incident.advance(100.,{'ego':actor('ego',100),'P1':actor('P1',100)},{'ego':'external','P1':'external'},[],[])
        self.assertFalse(incident.active)
        incident.advance(200.,{'P1':actor('P1',0)},{'P1':'ai'},[dict(position_city_m=[0,0],radius_m=6)],[])
        self.assertFalse(incident.active)

    def test_pedestrian_does_not_become_a_permanent_map_closure(self):
        view=ObservedStateMap({},'road')
        ped=dict(actor('walker-W1',20),type='PEDESTRIAN',synthetic=True)
        for step in range(40):view.update(step,step*.1,{'current_objects':[ped]})
        self.assertEqual(view.snapshot()['events'],[])

    def test_recurring_obstacle_spawns_outside_current_observer_range(self):
        path=[[0,0],[200,0],[200,200],[0,200],[0,0]]
        rt=SimpleNamespace(traffic=traffic_options({}),ids=['ego','P1'],path_for=lambda k:path,
                           junctions=[],construction_events=[],metadata={},env=SimpleNamespace(engine=object()))
        population=TrafficPopulation(rt)
        cars=[actor('ego',0),actor('P1',30)]
        def event(engine,metadata,spec,duration):return SimpleNamespace(spec=spec,duration_s=duration,obj=object())
        with patch('map_update.simulation_population.ConstructionEvent',side_effect=event):
            self.assertTrue(population.spawn_construction(100.,cars))
        pos=rt.construction_events[0].spec['position_city_m']
        self.assertTrue(all(math.dist(pos,a['position_city_m'])>55 for a in cars))
        rt2=SimpleNamespace(**vars(rt));rt2.construction_events=[]
        population2=TrafficPopulation(rt2)
        with patch('map_update.simulation_population.ConstructionEvent',side_effect=event):
            population2.spawn_construction(100.,cars)
        self.assertEqual(rt2.construction_events[0].spec,rt.construction_events[0].spec)

if __name__=='__main__':unittest.main()
