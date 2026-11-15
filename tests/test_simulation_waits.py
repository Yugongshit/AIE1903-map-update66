import unittest

from map_update.simulation_waits import TrafficWaits


class TrafficWaitTests(unittest.TestCase):
    def test_vehicle_pedestrian_mutual_wait_is_a_cycle(self):
        monitor = TrafficWaits()
        monitor.update([dict(vehicle_id='car', speed_m_s=0.)], {}, {},
            {'car': {'vehicle_id': 'walker-8'}}, {}, 10., {'car': 'car1'})
        walkers = [dict(event_id='walker-8', stationary_for_s=8., waiting_for=['car'])]
        self.assertEqual(monitor.report(walkers)['waiting_cycles'], [['car', 'walker-8']])
        self.assertEqual(monitor.report(walkers)['moving'], 0)
        walkers[0]['stationary_for_s'] = 0.
        self.assertEqual(monitor.report(walkers)['waiting_cycles'], [])

    def test_cycles_duration_and_replacement_identity(self):
        monitor = TrafficWaits()
        actors = [dict(vehicle_id=k, speed_m_s=0.) for k in ('a', 'b')]
        blockers = {'a': {'waiting_for': ['b'], 'junction_id': 'j'},
                    'b': {'waiting_for': ['a'], 'junction_id': 'j'}}
        identities = {'a': 'a1', 'b': 'b1'}
        monitor.update(actors, {}, {}, {}, blockers, 5., identities)
        rows = monitor.update(actors, {}, {}, {}, blockers, 40., identities)
        self.assertEqual(rows['a']['stopped_for_s'], 35.)
        self.assertEqual(monitor.report()['waiting_cycles'], [['a', 'b']])
        identities['a'] = 'a2'
        rows = monitor.update(actors, {}, {}, {}, blockers, 41., identities)
        self.assertEqual(rows['a']['stopped_for_s'], 0.)
        actors[1]['speed_m_s'] = 1.
        monitor.update(actors, {}, {}, {}, blockers, 42., identities)
        self.assertEqual(monitor.report()['waiting_cycles'], [])

    def test_following_and_external_obstacle_are_visible(self):
        monitor = TrafficWaits()
        rows = monitor.update([dict(vehicle_id='a', speed_m_s=0.)], {},
            {'a': {'following': {'leader': {'vehicle_id': 'b'}}}},
            {'a': {'vehicle_id': 'walker-1'}}, {}, 10., {'a': 'a1'})
        self.assertEqual(rows['a']['reason'], 'following_queue')
        self.assertEqual(rows['a']['waiting_for'], ['b', 'walker-1'])
        self.assertEqual(monitor.report()['waiting_cycles'], [])

    def test_nonzero_controller_target_does_not_claim_successful_motion(self):
        monitor = TrafficWaits()
        actors = [dict(vehicle_id='a', speed_m_s=0.)]
        route = {'a': {'status': 'tracking', 'target_speed_m_s': 2., 'lateral_error_m': .1}}
        monitor.update(actors, {}, route, {}, {}, 0., {'a': 'a1'})
        row = monitor.update(actors, {}, route, {}, {}, 4., {'a': 'a1'})['a']
        self.assertEqual(row['reason'], 'tracking_without_progress')
        self.assertEqual(row['waiting_for'], [])
        self.assertEqual(row['target_speed_m_s'], 2.)

    def test_brake_accelerator_oscillation_retains_a_timestamped_cause(self):
        monitor = TrafficWaits()
        actors = [dict(vehicle_id='a', speed_m_s=.1)]
        ids = {'a': 'a1'}
        monitor.update(actors, {'a': 'nearby_actor_braking'}, {}, {'a': {'vehicle_id': 'b'}}, {}, 0., ids)
        row = monitor.update(actors, {}, {}, {}, {}, .1, ids)['a']
        self.assertEqual(row['reason'], 'nearby_actor_braking')
        self.assertEqual(row['waiting_for'], ['b'])
        self.assertEqual(row['recent_stop_age_s'], .1)
        row = monitor.update(actors, {}, {}, {}, {}, 1.1, ids)['a']
        self.assertIsNone(row['recent_stop_reason'])
        self.assertEqual(row['waiting_for'], [])
