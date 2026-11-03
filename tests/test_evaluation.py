from copy import deepcopy
from pathlib import Path
import unittest

from map_update.delivery import read_csv
from map_update.evaluation import controlled_metrics, evaluate_controlled, POSITIVE, NEGATIVE


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT/'AIE1903 原始资料/M3_scene0980_proposal_supplement 2/results'
METHOD = 'quality_gated_repeated_evidence'


def row(identifier, truth, prediction):
    return {'case_id': identifier, 'data_type': 'CONTROLLED_PERTURBATION',
            'scripted_change': str(truth), METHOD: prediction}


class EvaluationTests(unittest.TestCase):
    def test_abstention_remains_in_coverage_and_all_positive_recall(self):
        r = controlled_metrics([row('1', True, POSITIVE), row('2', True, 'INSUFFICIENT_EVIDENCE'),
                                row('3', False, NEGATIVE), row('4', False, 'NEEDS_FURTHER_VERIFICATION')], METHOD)
        self.assertEqual(r['f1_resolved'], 1)
        self.assertEqual(r['coverage'], .5)
        self.assertEqual(r['recall_all_positives'], .5)
        self.assertEqual((r['tp'], r['tn'], r['fp'], r['fn']), (1, 1, 0, 0))
        self.assertEqual((r['abstained_positives'], r['abstained_negatives']), (1, 1))

    def test_no_decisions_are_not_perfect_scores_or_zero_accuracy(self):
        r = controlled_metrics([row('1', True, 'INSUFFICIENT_EVIDENCE')], METHOD)
        self.assertIsNone(r['precision_resolved'])
        self.assertIsNone(r['f1_resolved'])
        self.assertIsNone(r['fpr_resolved'])
        self.assertEqual(r['coverage'], 0)
        self.assertEqual(r['recall_all_positives'], 0)

    def test_empty_stratum_has_unknown_denominators(self):
        r = controlled_metrics([], METHOD)
        self.assertIsNone(r['coverage'])
        self.assertIsNone(r['recall_all_positives'])

    def test_unlabeled_real_rows_cannot_be_treated_as_controlled_truth(self):
        r = row('1', False, NEGATIVE)
        for field, value in [('data_type', 'REAL_SCENE'), ('scripted_change', ''), (METHOD, 'unknown')]:
            invalid = {**r, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                controlled_metrics([invalid], METHOD)

    def test_duplicate_cases_cannot_inflate_metrics(self):
        r = row('1', True, POSITIVE)
        with self.assertRaises(ValueError):
            controlled_metrics([r, deepcopy(r)], METHOD)

    def test_all_supplied_comparisons_reproduce_and_show_rejection_tradeoff(self):
        result = evaluate_controlled(read_csv(RESULTS/'controlled_case_decisions.csv'),
                                     read_csv(RESULTS/'controlled_comparison.csv'))
        self.assertEqual(len(result['strata']), 6)
        self.assertTrue(result['comparison_verified'])
        q = result['strata']['all'][2]
        self.assertEqual((q['cases'], q['resolved'], q['tp'], q['fp'], q['tn'], q['fn']), (126, 36, 24, 0, 9, 3))
        self.assertEqual(q['insufficient']+q['needs_review'], 90)
        self.assertAlmostEqual(q['recall_all_positives'], 24/108)
        self.assertAlmostEqual(q['f1_resolved'], 48/51)

    def test_wrong_supplied_metric_is_detected(self):
        rows = read_csv(RESULTS/'controlled_case_decisions.csv')
        comparisons = read_csv(RESULTS/'controlled_comparison.csv')
        comparisons[2]['coverage'] = '1.0'
        with self.assertRaisesRegex(ValueError, 'does not reproduce'):
            evaluate_controlled(rows, comparisons)


if __name__ == '__main__':
    unittest.main()
