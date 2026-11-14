from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from shapely.geometry import LineString, Polygon
from map_update.simulation_platform_data import load_scene
from map_update.simulation_traffic import cyclic_traffic, traffic_options, vehicle_perception
from map_update.simulation_lane_changes import source_neighbours, passing_path, target_gap_safe, LaneChanges
from map_update.simulation_network import route_path
from map_update.simulation_interactive_scenarios import vehicle_footprint
from map_update.simulation_interactive import validated_missions
from map_update.simulation_room import InteractiveRoom
from map_update.simulation_perception import degrade_observation
from map_update.simulation_live_map import ObservedStateMap
from map_update.simulation_path_geometry import PathGeometry


def actor(key,x,y=0.,speed=0.):
    return dict(vehicle_id=key,type='VEHICLE',position_city_m=[x,y],heading_rad=0.,speed_m_s=speed,
                velocity_m_s=[speed,0.],length_m=4.5,width_m=1.8,height_m=1.5,synthetic=True)


class LaneTrafficTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload=load_scene(Path(__file__).resolve().parents[1],'nuscenes-scene0980')
        cls.options=dict(route_mode='network',network_extent='district',density_veh_km=24,
                         following_model='idm',lane_changes=True,seed=0,stop_enabled=False)
        cls.config=cyclic_traffic(cls.payload,cls.options)

    def test_density_counts_unique_lanes_and_spawns_multiple_cars_per_segment(self):
        c=self.config;area=c['traffic_area'];self.assertEqual(len(c['missions']),48)
        self.assertGreaterEqual(sum(n>1 for n in area['initial_lane_counts'].values()),8)
        self.assertEqual(c,cyclic_traffic(self.payload,self.options))
        self.assertAlmostEqual(area['actual_density_veh_km'],48000/area['unique_lane_length_m'])
        bodies=[vehicle_footprint(m['position_scenario_m'],m['heading_rad']) for m in c['missions']]
        for i,body in enumerate(bodies):
            for other in bodies[i+1:]:self.assertGreaterEqual(body.distance(other),3.)

    def test_max_density_fits_worker_and_room_limits(self):
        c=cyclic_traffic(self.payload,dict(self.options,density_veh_km=32,pedestrians=True,pedestrian_count=32,
                         construction=True,construction_count_min=12,construction_count_max=16,incidents=True))
        self.assertEqual(len(validated_missions(c)),64)
        wire=json.dumps(dict(command='open',database_path='x'*300,interactive=c)).encode()
        self.assertLess(len(wire),65536)
        room=InteractiveRoom();room.create('s','updated',missions=c['missions'],vehicle_ids=('ego',*[f'P{i}' for i in range(1,65)]))
        self.assertEqual(len(room.vehicle_ids),65)
        for invalid in ({'vehicle_count':65},{'density_veh_km':33},{'lane_changes':True},dict(self.options,vehicle_count=0,pedestrians=True)):
            with self.assertRaises(ValueError):traffic_options(invalid)

    def test_only_verified_long_dashed_sections_allow_continuous_on_road_pass(self):
        features=self.payload['maps']['updated'];original=deepcopy(features);neighbours=source_neighbours(features)
        n=next(n for n in neighbours if n['source'].startswith('313a6405'))
        route=next(r for r in self.config['routes'] if n['source'] in r['lanes']);base=route_path(route,features)
        path,end,merge,count=passing_path(n,9.,base)
        self.assertGreater(end,50);self.assertGreater(merge,30)
        self.assertLess(max(__import__('math').dist(a,b) for a,b in zip(path,path[1:])),1.5)
        for point in path[:count]:self.assertTrue(n['surface'].covers(__import__('shapely').geometry.Point(point)))
        self.assertIsNone(passing_path(n,n['source_geometry'].length-20,base))
        altered=deepcopy(features);altered[n['source']]['polygon']=[[0,0],[1,0],[1,1],[0,1]]
        self.assertFalse(any(x['source']==n['source'] for x in source_neighbours(altered)))
        self.assertEqual(original,features)

    def test_target_front_and_fast_rear_gap_reject_unsafe_merge(self):
        own=actor('P1',30,3.5,2.);geometry=PathGeometry([[0,0],[100,0]])
        self.assertTrue(target_gap_safe(own,[own,actor('P2',0,0,1.)],geometry))
        self.assertFalse(target_gap_safe(own,[own,actor('P2',23,0,5.)],geometry))
        self.assertFalse(target_gap_safe(own,[own,actor('P2',35,0,0.)],geometry))

    def test_external_takeover_releases_maneuver(self):
        manager=LaneChanges({}, {}, {},enabled=False);path=[[0,0],[10,0]]
        runtime=SimpleNamespace(path_overrides={'P1':path},path_geometries={id(path):object()},
                                junction_coordinator=SimpleNamespace(_path_geometry={id(path):object()}))
        manager.active['P1']={'path':path};manager.cancel(runtime,'P1')
        self.assertEqual(manager.cancelled,1);self.assertFalse(manager.active)
        self.assertFalse(runtime.path_overrides);self.assertFalse(runtime.path_geometries)
        self.assertFalse(runtime.junction_coordinator._path_geometry)



class SensorPreparationTests(unittest.TestCase):
    def observation(self,objects):
        state=dict(vehicles={'ego':actor('ego',0)},static_actors=objects)
        return vehicle_perception(state,'ego')

    def test_frame_determinism_order_independence_truth_immutability_and_privacy(self):
        truth=self.observation([dict(actor('a',20,10),goal='secret',event_schedule=[100])]);original=deepcopy(truth)
        args=dict(profile='occlusion_noise_v1',seed=7,step=100,vehicle_id='ego')
        a=degrade_observation(truth,**args);self.assertEqual(a,degrade_observation(truth,**args))
        self.assertEqual(truth,original);self.assertEqual(degrade_observation(truth),truth)
        self.assertNotIn('secret',json.dumps(a));self.assertNotIn('event_schedule',json.dumps(a))
        self.assertTrue(a['current_objects']);self.assertNotEqual(a['current_objects'][0]['position_city_m'],truth['current_objects'][0]['position_city_m'])
        other=degrade_observation(truth,**dict(args,step=101));self.assertNotEqual(a,other)

    def test_occlusion_and_dropout_do_not_create_false_clearance(self):
        obstacle=actor('obstacle',20);road={'l':dict(type='LANE_SURFACE_STREET',polyline=[[0,0],[100,0]])}
        layer=ObservedStateMap(road,'road')
        for step in range(25):layer.update(step,step*.1,self.observation([obstacle]))
        self.assertEqual(layer.snapshot()['events'][0]['status'],'active')
        for step in range(25,70):
            raw=self.observation([actor('front',10),obstacle]);raw['free_space_checks']=[dict(actor_id='obstacle',observed_free=True)]
            measured=degrade_observation(raw,profile='occlusion_noise_v1',step=step)
            self.assertNotIn('obstacle',[a['vehicle_id'] for a in measured['current_objects']])
            self.assertFalse(measured['free_space_checks']);layer.update(step,step*.1,measured)
        event=next(e for e in layer.snapshot()['events'] if e['actor_id']=='obstacle')
        self.assertEqual(event['status'],'stale');self.assertFalse(layer.snapshot('baseline')['events'])
        self.assertEqual(layer.snapshot()['structural_writes'],0)

if __name__=='__main__':unittest.main()
