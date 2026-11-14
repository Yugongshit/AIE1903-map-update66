"""Pedestrian population semantics, geometry, repeatability and intent privacy."""
import math
from pathlib import Path
import random
import unittest
from shapely.geometry import LineString, Point, box
from types import SimpleNamespace
from map_update.simulation_population import Walker
from map_update.simulation_platform_data import load_scene
from map_update.simulation_network import route_path
from map_update.simulation_walking import WalkingNetwork
from map_update.simulation_traffic import cyclic_traffic, traffic_options, vehicle_perception
from map_update.simulation_interactive import validated_missions
from map_update.simulation_room import InteractiveRoom


class WalkingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload=load_scene(Path(__file__).resolve().parents[1], 'nuscenes-scene0980')
        cls.config=cyclic_traffic(cls.payload,dict(route_mode='network',vehicle_count=0,pedestrians=True,stop_enabled=False))
        cls.network=WalkingNetwork(cls.payload['maps']['updated'],[route_path(r,cls.payload['maps']['updated']) for r in cls.config['routes']])

    def test_pedestrian_only_has_ego_but_no_background_missions(self):
        self.assertEqual(self.config['missions'],[])
        self.assertEqual(validated_missions(self.config),{})
        room=InteractiveRoom();room.create('nuscenes-scene0980','updated',missions=[],vehicle_ids=('ego',))
        self.assertEqual(room.vehicle_ids,('ego',))
        with self.assertRaises(ValueError):traffic_options(dict(vehicle_count=0))
        single=cyclic_traffic(self.payload,dict(vehicle_count=0,pedestrians=True,construction=True))
        self.assertEqual(single['missions'],[])

    def test_spawns_and_trips_are_seeded_reachable_and_do_not_shuttle(self):
        n=self.network
        def run(seed):
            rng=random.Random(seed);starts=n.spawn_nodes(4,rng,[]);result=[]
            for start in starts:
                recent=[start];trips=[]
                for _ in range(5):
                    route=n.trip(start,rng,recent);points=[n.points[i] for i in route]
                    self.assertTrue(n.allowed.covers(LineString(points)))
                    self.assertGreaterEqual(math.dist(points[0],points[-1]),20)
                    self.assertEqual(n.component[start],n.component[route[-1]])
                    self.assertFalse(n.crossing_at(points[-1]))
                    self.assertTrue(all(math.dist(points[-1],n.points[old])>20 for old in recent[-3:]))
                    trips.append(route);recent=(recent+[route[-1]])[-4:];start=route[-1]
                result.append(trips)
            for i,start in enumerate(starts):
                for other in starts[i+1:]:self.assertGreaterEqual(math.dist(n.points[start],n.points[other]),12)
            return starts,result
        self.assertEqual(run(0),run(0));self.assertNotEqual(run(0),run(7))

    def test_spawn_rejects_occupied_space_and_retains_disconnected_topology(self):
        n=self.network;nodes=n.spawn_nodes(4,random.Random(2),[n.surface])
        self.assertEqual(nodes,[])
        for node,edges in enumerate(n.edges):
            for other,_ in edges:self.assertEqual(n.component[node],n.component[other])
        self.assertGreater(len(n.components),1)

    def test_head_on_walkers_pass_without_leaving_sidewalk(self):
        class Body:
            def __init__(self, pos): self.pos=pos; self.velocity=[0,0]; self.heading=0.
            def set_heading_theta(self, theta): self.heading=theta
            def set_velocity(self, direction, speed=0):
                length=math.hypot(*direction)
                self.velocity=[v*speed/length if length else 0 for v in direction]
        network=SimpleNamespace(points=[[-10.,0.],[10.,0.]],crosswalks={},motion_area=box(-20,-2,20,2),crossing_at=lambda pos:None)
        walkers=[]
        for i,x in enumerate((-1.,1.)):
            w=Walker.__new__(Walker); w.identifier=f'walker-{i}';w.network=network
            w.obj=Body([x,0.]);w.position=lambda w=w:list(w.obj.pos);w.previous=w.position()
            w.blocked_since=None;w.speed=1.;w.wait_until=0.;w.route=[i,1-i];w.target=1;w.detour=[];w.distance=0.;w.crossing=None
            w.crossing_distance=0.;w.approved_crossing=None;w.phase='walking';w.crossings=0;w.visited_crosswalks=set()
            walkers.append(w)
        for step in range(70):
            actors=[dict(vehicle_id=w.identifier,position_city_m=w.position(),heading_rad=w.obj.heading,
                length_m=.6,width_m=.6,type='PEDESTRIAN',speed_m_s=math.hypot(*w.obj.velocity)) for w in walkers]
            for w in walkers:w.advance(step*.1,actors)
            for w in walkers:
                w.obj.pos=[p+v*.1 for p,v in zip(w.obj.pos,w.obj.velocity)]
                self.assertTrue(network.motion_area.covers(Point(w.position())))
            self.assertGreater(math.dist(walkers[0].position(),walkers[1].position()),.85)
        self.assertGreater(walkers[0].position()[0],2.)
        self.assertLess(walkers[1].position()[0],-2.)

    def test_narrow_encounter_can_replan_without_crossing_the_other_person(self):
        n=self.network;position=[570.718302,1335.732496]
        blocked=Point([569.986902,1334.986578]).buffer(.95)
        goal=min(n.destinations[n.component[min(range(len(n.points)),key=lambda i:math.dist(position,n.points[i]))]],key=lambda i:math.dist([560,1265],n.points[i]))
        route=n.reroute(position,goal,random.Random(0),blocked)
        self.assertIsNotNone(route)
        line=LineString([position,*[n.points[i] for i in route]])
        self.assertTrue(n.motion_area.covers(line))
        self.assertFalse(line.intersects(blocked))
        self.assertGreater(line.length,25)

    def test_pedestrian_intentions_are_not_observations(self):
        import json
        car=dict(vehicle_id='ego',position_city_m=[0.,0.],heading_rad=0.,speed_m_s=0.,length_m=4.,width_m=2.)
        pedestrian=dict(vehicle_id='walker-1',position_city_m=[8.,0.],heading_rad=0.,speed_m_s=1.,length_m=.6,width_m=.6,
            type='PEDESTRIAN',synthetic=True,source_scope='synthetic_pedestrian',route=[1,2],goal=[100,0],wait_until=50,completed_trips=4)
        frame=dict(vehicles={'ego':car},static_actors=[pedestrian])
        observed=vehicle_perception(frame,'ego')
        encoded=json.dumps(observed)
        for secret in ('wait_until','completed_trips','"goal"','"route"'):self.assertNotIn(secret,encoded)
        self.assertTrue(any(a['vehicle_id']=='walker-1' for a in observed['current_objects']))

if __name__=='__main__':unittest.main()
