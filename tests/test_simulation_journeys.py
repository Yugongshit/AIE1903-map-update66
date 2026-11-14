from copy import deepcopy
import json,math,random
from pathlib import Path
import unittest
from shapely.geometry import LineString
from map_update.simulation_platform_data import load_scene
from map_update.simulation_journeys import JourneyNetwork,journey_traffic
from map_update.simulation_traffic import cyclic_traffic,traffic_options,CycleFollower,vehicle_perception
from map_update.simulation_interactive import validated_missions
from map_update.simulation_interactive_scenarios import vehicle_footprint
from map_update.simulation_following import current_leader
from map_update.simulation_session import validated_camera
from map_update.simulation_room import InteractiveRoom


class JourneyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload=load_scene(Path(__file__).resolve().parents[1],'nuscenes-scene0980')
        cls.options=dict(route_mode='journeys',network_extent='district',vehicle_count=96,following_model='idm',lane_changes=True,stop_enabled=False)
        cls.config=cyclic_traffic(cls.payload,cls.options)
        cls.network=JourneyNetwork(cls.payload['maps']['updated'],blocked_lanes=cls.config['journey_blocked_lanes'])

    def test_open_graph_covers_outer_roads_and_spawns_are_reproducible(self):
        c=self.config;self.assertEqual(c,cyclic_traffic(self.payload,self.options));area=c['traffic_area']
        self.assertGreater(area['unique_lane_length_m'],7000);self.assertGreater(area['bounds_city_m'][2]-area['bounds_city_m'][0],550)
        self.assertGreaterEqual(area['entry_count'],10);self.assertGreaterEqual(area['exit_count'],15)
        outside=sum(not(395.85<x<611.91 and 1073.32<y<1463.36) for x,y in (m['position_scenario_m'] for m in c['missions']))
        self.assertGreater(outside,48)
        self.assertFalse(c.get('cyclic_path'));self.assertFalse(c.get('routes'))
        self.assertLess(len(json.dumps(c).encode()),65536)
        self.assertNotEqual(c['missions'],cyclic_traffic(self.payload,dict(self.options,seed=7))['missions'])

    def test_parallel_safe_spawns_have_no_overlap_and_allow_multiple_lanes(self):
        bodies=[vehicle_footprint(m['position_scenario_m'],m['heading_rad']) for m in self.config['missions']]
        for i,body in enumerate(bodies):
            for other in bodies[i+1:]:self.assertGreaterEqual(body.distance(other),.6)
        for mission in self.config['missions']:
            lane=mission['journey_lane'];geom=self.network.geometries[lane]
            if lane in self.network.exits:self.assertGreaterEqual(geom.length-geom.project(mission['position_scenario_m'])[1],20.)

    def test_destinations_change_and_all_route_edges_are_source_connections(self):
        n=self.network;start='313a6405-789e-4a67-b83b-de64fc7d56e7';goals=[];routes=[];rng=random.Random(5);recent=[]
        for _ in range(30):
            lanes,end=n.trip(start,6.,rng,recent);self.assertEqual(lanes[0],start)
            for a,b in zip(lanes,lanes[1:]):self.assertIn(b,n.graph[a])
            path=n.path(lanes,end);self.assertGreater(math.dist(path[0],path[-1]),1.)
            self.assertLess(max(math.dist(a,b) for a,b in zip(path,path[1:])),5.01)
            goals.append(lanes[-1]);routes.append(lanes);recent=(recent+[lanes[-1]])[-5:]
        self.assertGreater(len(set(goals)),5);self.assertGreater(len({tuple(r) for r in routes}),5)
        a=n.trip(start,6.,random.Random(99));b=n.trip(start,6.,random.Random(99));self.assertEqual(a,b)

    def test_interior_dead_end_is_not_a_boundary_exit(self):
        n=self.network
        self.assertNotIn('c3c60f7b-d9f5-4d75-a271-e708575ab7d5',n.graph)
        self.assertFalse(n.blocked & set(n.graph))
        self.assertTrue(all(not n.graph[k] for k in n.exits))
        self.assertTrue(all(k in n.graph for k in n.entries))

    def test_open_follower_brakes_at_end_without_wrapping_to_start(self):
        follower=CycleFollower([[0,0],[100,0]],2.,closed=False)
        state=dict(position_runtime_m=[99.9,0],heading_rad=0.,speed_m_s=1.,wheelbase_m=2.5,max_steering_rad=.6)
        action=follower(state);d=follower.diagnostics()
        self.assertLess(action[1],0);self.assertEqual(d['target_speed_m_s'],0)
        self.assertTrue(all(p[0]>=99 for p in d['points']));self.assertEqual(d['goal_scenario_m'],[100,0])
        def car(key,x):return dict(vehicle_id=key,type='VEHICLE',position_city_m=[x,0],heading_rad=0.,speed_m_s=1.,length_m=4.5,width_m=1.8)
        own=car('P1',98);self.assertIsNone(current_leader(own,[own,car('P2',2)],[[0,0],[100,0]]))

    def test_open_lane_change_cannot_wrap_from_integer_endpoint_to_origin(self):
        from shapely.geometry import Polygon
        from map_update.simulation_lane_changes import passing_path
        from map_update.simulation_path_geometry import PathGeometry
        base=[[0.,0.],[100.,0.]]
        neighbour=dict(source_geometry=PathGeometry(base),target_geometry=PathGeometry([[0.,3.5],[100.,3.5]]),
                       surface=Polygon([[-5,-3],[105,-3],[105,7],[-5,7]]),dashed=LineString([[0,1.75],[100,1.75]]))
        path,*_=passing_path(neighbour,10.,base,closed=False)
        self.assertEqual(path[-1],[100.,0.])
        self.assertLess(max(math.dist(a,b) for a,b in zip(path,path[1:])),2.)
        self.assertGreater(path[-1][0],path[0][0])

    def test_new_actor_in_same_control_slot_has_a_distinct_measurement_identity(self):
        own=dict(vehicle_id='ego',position_city_m=[0,0],heading_rad=0.,speed_m_s=0.,length_m=4.,width_m=2.)
        state=dict(vehicles={'ego':own,'P1':dict(own,vehicle_id='P1',position_city_m=[10,0],actor_instance_id='traffic-P1-g1',journey_lane='private',goal_scenario_m=[99,99])})
        first=vehicle_perception(state,'ego');state['vehicles']['P1']['actor_instance_id']='traffic-P1-g2'
        second=vehicle_perception(state,'ego')
        self.assertEqual(first['current_objects'][0]['vehicle_id'],'traffic-P1-g1')
        self.assertEqual(second['current_objects'][0]['vehicle_id'],'traffic-P1-g2')
        self.assertNotIn('private',json.dumps(second));self.assertNotIn('goal_scenario_m',json.dumps(second))
        self.assertEqual(vehicle_perception(state,'P1')['own_vehicle']['vehicle_id'],'P1')

    def test_128_slot_configuration_still_fits_bounded_worker_protocol(self):
        config=cyclic_traffic(self.payload,dict(self.options,vehicle_count=128))
        self.assertEqual(len(validated_missions(config)),128);self.assertLess(len(json.dumps(config).encode()),65536)
        room=InteractiveRoom();room.create('scene','updated',config['missions'],vehicle_ids=('ego',*[f'P{i}' for i in range(1,129)]))
        self.assertEqual(len(room.vehicle_ids),129)
        self.assertEqual(validated_camera({'target':'ego','yaw':0,'pitch':30,'distance':25,'pan':[0,0]},{'target':'P128'})['target'],'P128')
        with self.assertRaises(ValueError):traffic_options(dict(self.options,vehicle_count=129))

if __name__=='__main__':unittest.main()
