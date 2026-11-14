from copy import deepcopy
import unittest

from map_update.simulation_live_map import ObservedStateMap

ROAD={'lane':{'type':'LANE_SURFACE_STREET','polyline':[[0.,0.],[100.,0.]]}}

def actor(x=20., speed=0.):
    return dict(vehicle_id='P1',type='VEHICLE',position_city_m=[x,0.],heading_rad=0.,
                speed_m_s=speed,length_m=4.5,width_m=1.8)

class LiveMapTests(unittest.TestCase):
    def setUp(self):
        self.map=ObservedStateMap(ROAD,'road')

    def feed(self,start,end,objects):
        for step in range(start,end):
            self.map.update(step,step*.1,{'current_objects':objects})

    def test_stop_requires_continuous_evidence_and_never_writes_road(self):
        original=deepcopy(ROAD)
        self.feed(0,20,[actor()])
        self.assertEqual(self.map.snapshot()['events'],[])
        self.feed(20,22,[actor()])
        event=self.map.snapshot()['events'][0]
        self.assertEqual(event['status'],'active')
        self.assertEqual(event['lane_id'],'lane')
        self.assertFalse(event['lane_closed'])
        self.assertEqual(ROAD,original)
        self.assertEqual(self.map.snapshot()['structural_writes'],0)

    def test_missing_becomes_stale_and_only_observed_departure_clears(self):
        self.feed(0,25,[actor()]);self.feed(25,60,[])
        self.assertEqual(self.map.snapshot()['events'][0]['status'],'stale')
        self.feed(60,62,[actor(20.,1.)])
        self.assertEqual(self.map.snapshot()['events'][0]['status'],'stale')
        self.feed(62,63,[actor(28.,1.)])
        self.assertEqual(self.map.snapshot()['events'][0]['status'],'cleared')

    def test_duplicate_reads_future_and_intermittent_evidence(self):
        self.feed(0,1,[actor()])
        for _ in range(50):self.map.update(0,0.,{'current_objects':[actor()]})
        self.assertEqual(self.map.snapshot()['events'],[])
        self.map.update(30,3.,{'current_objects':[actor()]})
        self.assertEqual(self.map.snapshot()['events'],[])
        with self.assertRaises(ValueError): self.map.update(20,2.,{'current_objects':[actor()]})

    def test_baseline_and_observed_share_road_but_not_state_updates(self):
        self.feed(0,25,[actor()])
        a,b=self.map.snapshot('baseline'),self.map.snapshot()
        self.assertEqual(a['road_version'],b['road_version'])
        self.assertEqual(a['events'],[]);self.assertTrue(b['events'])
        self.assertFalse(self.map.accepts_version(b['state_version'],0))
        self.assertTrue(self.map.accepts_version(b['state_version'],24))
        self.assertFalse(self.map.accepts_version('forged',24))
        b['events'].clear();self.assertTrue(self.map.snapshot()['events'])

    def test_other_observer_and_generator_intent_do_not_create_events(self):
        other=ObservedStateMap(ROAD,'road')
        self.feed(0,25,[dict(actor(),event_plan={'stop_at':0},route=['future'])])
        for step in range(25): other.update(step,step*.1,{'current_objects':[]})
        self.assertEqual(other.snapshot()['events'],[])
        text=str(self.map.snapshot())
        self.assertNotIn('event_plan',text);self.assertNotIn('future',text)

    def test_moving_traffic_and_ambiguous_lane_do_not_imply_closure(self):
        self.feed(0,25,[actor(speed=1.)]);self.assertEqual(self.map.snapshot()['events'],[])
        self.map=ObservedStateMap({**ROAD,'adjacent':ROAD['lane']},'road')
        self.feed(0,25,[actor()]);event=self.map.snapshot()['events'][0]
        self.assertIsNone(event['lane_id']);self.assertFalse(event['lane_closed'])
