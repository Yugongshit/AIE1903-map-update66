"""Check stimulus selection and retain failed native branch evidence."""
import unittest
from scripts.check_recorded_takeover import probe_actions, check_result


class CheckerTests(unittest.TestCase):
    def test_stationary_vehicle_gets_motion_probe_not_ineffective_brake(self):
        obs = {'objects': {'parked': {'velocity_m_s': [0., 0.]},
                           'moving': {'velocity_m_s': [3., 4.]}}}
        self.assertEqual(probe_actions(obs, ['parked', 'moving']),
                         {'parked': [0., .3], 'moving': [0., -1.]})

    def test_low_speed_cutoff_uses_vector_norm(self):
        obs = {'objects': {'slow': {'velocity_m_s': [1., 0.]},
                           'reverse': {'velocity_m_s': [-2., 0.]}}}
        self.assertEqual(probe_actions(obs, ['slow', 'reverse']),
                         {'slow': [0., .3], 'reverse': [0., -1.]})

    def test_failed_motion_assertion_stays_failed_with_branch_evidence(self):
        report = {'checks': [], 'branches': [{'deviation_from_log_m': {'sdc': 0.}}]}
        with self.assertRaises(AssertionError):
            check_result(report, 'sdc_external_action_changes_trajectory', False)
        self.assertEqual(report['failed_checks'], ['sdc_external_action_changes_trajectory'])
        self.assertEqual(report['branches'][0]['deviation_from_log_m']['sdc'], 0.)
        self.assertEqual(report['checks'], [])
