from copy import deepcopy
import math
import unittest

from map_update.simulation_following import (driver_parameters, current_leader,
    idm_acceleration, acceleration_action)

PATH = [[0.,0.],[100.,0.],[100.,100.],[0.,100.],[0.,0.]]


def actor(identifier, position, speed=2., heading=0.):
    return dict(vehicle_id=identifier, type='VEHICLE', position_city_m=position,
        heading_rad=heading, speed_m_s=speed, length_m=4.5)


class FollowingTests(unittest.TestCase):
    def test_driver_style_is_stable_and_explicitly_uncalibrated(self):
        a = [driver_parameters(7, f'P{i}', 'mixed') for i in range(12)]
        self.assertEqual(a, [driver_parameters(7, f'P{i}', 'mixed') for i in range(12)])
        self.assertGreater(len({p['style'] for p in a}), 1)
        self.assertTrue(all(p['calibrated'] is False for p in a))

    def test_free_flow_equilibrium_and_speed_limit(self):
        p = driver_parameters(0,'P1','normal')
        self.assertGreater(idm_acceleration(0., 2.5, None, p), 0)
        self.assertAlmostEqual(idm_acceleration(2.5, 2.5, None, p), 0)
        self.assertLess(idm_acceleration(3., 2.5, None, p), 0)

    def test_closing_gap_demands_more_braking_than_receding_leader(self):
        p = driver_parameters(0,'P1','normal')
        approaching = idm_acceleration(2., 2.5, dict(gap_m=5.,speed_m_s=0.), p)
        receding = idm_acceleration(2., 2.5, dict(gap_m=5.,speed_m_s=3.), p)
        self.assertLess(approaching, receding)
        self.assertLess(approaching, 0.)
        self.assertEqual(idm_acceleration(2., 2.5, dict(gap_m=-1.,speed_m_s=0.), p), -4.)

    def test_cautious_driver_holds_more_space(self):
        leader = dict(gap_m=6.,speed_m_s=2.)
        slow = idm_acceleration(2.,2.5,leader,driver_parameters(0,'P1','cautious'))
        tight = idm_acceleration(2.,2.5,leader,driver_parameters(0,'P1','assertive'))
        self.assertLess(slow,tight)

    def test_ring_seam_and_nearest_leader(self):
        own=actor('P1',[0.,3.],heading=-math.pi/2)
        actors=[own,actor('P2',[6.,0.]),actor('P3',[20.,0.])]
        leader=current_leader(own,actors,PATH)
        self.assertEqual(leader['vehicle_id'],'P2')
        self.assertAlmostEqual(leader['gap_m'],4.5)

    def test_adjacent_opposing_behind_distant_and_static_are_not_leaders(self):
        own=actor('P1',[20.,0.])
        actors=[own,actor('adjacent',[23.,3.]),actor('opposing',[25.,0.],heading=math.pi),
            actor('behind',[10.,0.]),actor('far',[71.,0.]),dict(actor('barrier',[28.,0.]),type='BARRIER')]
        self.assertIsNone(current_leader(own,actors,PATH))

    def test_future_actions_and_goals_do_not_affect_current_leader(self):
        own=actor('P1',[20.,0.]);other=actor('P2',[30.,0.])
        original=deepcopy(other)
        expected=current_leader(own,[own,other],PATH)
        other.update(action=[1.,1.],event={'stop_at':100},goal_scenario_m=[99.,0.])
        self.assertEqual(expected,current_leader(own,[own,other],PATH))
        self.assertEqual(original['position_city_m'],other['position_city_m'])

    def test_pedal_conversion_is_bounded_and_does_not_change_vehicle(self):
        class Vehicle:
            MASS=1000.
            config={'max_engine_force':1000.}
        vehicle=Vehicle()
        self.assertEqual(acceleration_action(1.,vehicle),.25)
        self.assertEqual(acceleration_action(-4.,vehicle),-1.)
        self.assertEqual(acceleration_action(100.,vehicle),1.)
        self.assertEqual(vehicle.config,{'max_engine_force':1000.})
