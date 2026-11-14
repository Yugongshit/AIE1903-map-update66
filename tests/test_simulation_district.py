from pathlib import Path
import json
import math
import random
import unittest
from shapely.geometry import LineString, Point, box
from types import SimpleNamespace
from map_update.simulation_population import Walker
from map_update.simulation_platform_data import load_scene
from map_update.simulation_traffic import cyclic_traffic, traffic_options
from map_update.simulation_network import route_path
from map_update.simulation_walking import WalkingNetwork
from map_update.simulation_district import BackgroundActivity
from map_update.simulation_path_geometry import PathGeometry
from map_update.simulation_planner import _project, _point_at
from map_update.simulation_interactive_scenarios import vehicle_footprint
from map_update.simulation_room import InteractiveRoom


class DistrictTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload=load_scene(Path(__file__).resolve().parents[1],'nuscenes-scene0980')
        cls.config=cyclic_traffic(cls.payload,dict(route_mode='network',network_extent='district',vehicle_count=32,
            pedestrians=True,pedestrian_count=32,construction=True,construction_count_min=12,construction_count_max=16))

    def test_expanded_routes_use_source_geometry_and_compact_wire_config(self):
        c=self.config;base=cyclic_traffic(self.payload,dict(route_mode='network'))
        self.assertGreater(len(self.payload['display_walkways']),50)
        self.assertGreater(c['traffic_area']['unique_lane_length_m'],base['traffic_area']['unique_lane_length_m']*1.5)
        self.assertGreater(len(c['routes']),len(base['routes']))
        self.assertLess(len(json.dumps(c).encode()),60000)
        self.assertEqual(len(c['missions']),32)
        self.assertEqual(c,cyclic_traffic(self.payload,dict(route_mode='network',network_extent='district',vehicle_count=32,
            pedestrians=True,pedestrian_count=32,construction=True,construction_count_min=12,construction_count_max=16)))
        for route in c['routes']:self.assertTrue(LineString(route_path(route,self.payload['maps']['updated'])).is_simple)
        room=InteractiveRoom();room.create('scene','updated',missions=c['missions'],vehicle_ids=('ego',*[f'P{i}' for i in range(1,33)]))
        self.assertEqual(len(room.vehicle_ids),33)

    def test_dense_spawns_and_distributed_obstacles_have_space(self):
        c=self.config;cars=[vehicle_footprint(m['position_scenario_m'],m['heading_rad']) for m in c['missions']]
        for i,body in enumerate(cars):
            for other in cars[i+1:]:self.assertGreaterEqual(body.distance(other),7.)
        obstacles=[vehicle_footprint(o['position_city_m'],o['heading_rad'],1.,2.5,margin=.5) for o in c['constructions']]
        for i,body in enumerate(obstacles):
            self.assertTrue(all(body.distance(car)>=4 for car in cars))
            self.assertTrue(all(body.distance(other)>=20 for other in obstacles[i+1:]))
        self.assertGreaterEqual(len(obstacles),8)
        ys=[o['position_city_m'][1] for o in c['constructions']];self.assertGreater(max(ys)-min(ys),150)

    def test_pedestrians_spread_across_source_walkways_and_preserve_seed(self):
        c=self.config;n=WalkingNetwork(self.payload['maps']['updated'],[route_path(r,self.payload['maps']['updated']) for r in c['routes']],expanded=True)
        starts=n.spawn_nodes(32,random.Random(0),[])
        self.assertEqual(len(starts),32);self.assertEqual(starts,n.spawn_nodes(32,random.Random(0),[]))
        self.assertGreaterEqual(len({(int(n.points[i][0]//80),int(n.points[i][1]//80)) for i in starts}),20)
        for start in starts:
            trip=n.trip(start,random.Random(start),distance_range=(70,250))
            self.assertTrue(n.allowed.covers(LineString([n.points[i] for i in trip])))
        with self.assertRaises(ValueError):traffic_options({'vehicle_count':65})
        with self.assertRaises(ValueError):traffic_options({'pedestrian_count':33})
        with self.assertRaises(ValueError):traffic_options({'construction_count_max':17})

    def test_activity_varies_speed_and_staggers_stops_without_external_control(self):
        cars={f'P{i}':dict(position_city_m=[i*100.,0.],speed_m_s=2.) for i in range(1,7)}
        modes={k:'ai' for k in cars};modes['P6']='external'
        def trace(seed):
            model=BackgroundActivity(seed,list(cars));rows=[]
            for t in range(240):
                model.advance(t,cars,modes,[],[])
                self.assertFalse(model.stopping('P6'))
                rows.append([(k,model.factor(k),model.stopping(k)) for k in cars])
            self.assertGreater(model.completed,3)
            return rows
        a=trace(0);self.assertEqual(a,trace(0));self.assertNotEqual(a,trace(7))
        self.assertGreater(len({row[0][1] for row in a}),3)
        self.assertTrue(any(0<sum(x[2] for x in row)<5 for row in a))

    def test_opposite_pedestrians_do_not_reserve_the_entire_crosswalk(self):
        w=Walker.__new__(Walker);w.identifier='walker-1';w.speed=1.
        w.network=SimpleNamespace(crosswalks={'crossing':box(-2,-10,2,10)})
        other=dict(vehicle_id='walker-2',type='PEDESTRIAN',position_city_m=[0,7],heading_rad=0.,length_m=.6,width_m=.6,speed_m_s=0.)
        self.assertTrue(w.safe_crossing('crossing',[other]))
        other.update(type='VEHICLE',length_m=4.5,width_m=2.)
        self.assertFalse(w.safe_crossing('crossing',[other]))

    def test_physical_blockage_can_choose_a_destination_away_from_the_collider(self):
        c=self.config;n=WalkingNetwork(self.payload['maps']['updated'],[route_path(r,self.payload['maps']['updated']) for r in c['routes']],expanded=True)
        position=[376.,1146.878];direction=[0.,-1.]
        start=min(range(len(n.points)),key=lambda i:math.dist(position,n.points[i]))
        goal=n.trip(start,random.Random(0))[-1]
        blocked=Point([376.,1145.678]).buffer(.8)
        route=n.reroute(position,goal,random.Random(0),blocked,avoid_direction=direction)
        self.assertIsNotNone(route)
        line=LineString([position,*[n.points[i] for i in route]])
        self.assertTrue(n.motion_area.covers(line));self.assertFalse(line.intersects(blocked))
        self.assertGreater(n.points[route[-1]][1],position[1]+10)

    def test_cached_path_geometry_matches_reference_queries(self):
        path=[[0,0],[4,0],[4,0],[4,7],[0,7],[0,0]];geometry=PathGeometry(path)
        rng=random.Random(3)
        for _ in range(100):
            pos=[rng.uniform(-2,8),rng.uniform(-2,9)];a=geometry.project(pos);b=_project(pos,path)
            self.assertAlmostEqual(a[0],b[0],places=10);self.assertAlmostEqual(a[1],b[1],places=10)
            self.assertAlmostEqual(a[3],b[3],places=10)
        for s in (-1,0,4,5,11,15,22,99):
            self.assertLess(math.dist(geometry.point_at(s),_point_at(path,s)),1e-10)

if __name__=='__main__':unittest.main()
