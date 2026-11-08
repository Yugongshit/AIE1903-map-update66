"""Reproduction, attribution and evaluation-scope checks for supplied Argoverse."""

from pathlib import Path
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

from map_update import argoverse as av


RAW = Path(__file__).resolve().parents[1]/'AIE1903 原始资料'


class ArgoverseRerunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temp.name)/'rerun'
        cls.evaluation_reads = []
        checked = av._checked

        def trace(raw, package, names, role, sources):
            if role == 'evaluation_only':
                freeze = av._read(cls.output/'prediction_freeze.json')
                cls.evaluation_reads.append(all(av._hash(cls.output/name) == digest for name, digest in freeze['files_sha256'].items()))
            return checked(raw, package, names, role, sources)

        with patch.object(av, '_checked', side_effect=trace):
            av.run_argoverse(RAW, cls.output)
        cls.result = av._read(cls.output/'result.json')

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_actual_algorithms_reproduce_supplied_candidates_and_reasons(self):
        r = self.result
        self.assertEqual(r['summary'], dict(frames=582, views=1746, observations=10278,
            projected_observations=6770, m3_candidates=4, m4_groups=1, confirmed_changes=0,
            base_map_updates=0, pending_manual_reviews=0))
        self.assertTrue(all(equal for stage in r['equivalence'].values() for equal in stage.values()))
        candidate = r['candidates'][0]
        self.assertEqual(candidate['status'], 'uncertain')
        self.assertEqual(candidate['distinct_support_frame_count'], 4)
        self.assertAlmostEqual(candidate['support_duration_s'], .300589)
        self.assertIsNone(candidate['evidence_score'])
        self.assertIn('VISIBILITY_DENOMINATOR_UNKNOWN', candidate['reason_codes'])
        self.assertEqual(candidate['persistence_class'], 'not_implemented')

    def test_ground_truth_is_opened_only_after_predictions_are_frozen(self):
        self.assertEqual(self.evaluation_reads, [True])
        staged = self.output/'prediction_inputs'
        self.assertFalse(any('M8' in str(p) or 'gt_' in p.name for p in staged.rglob('*')))
        self.assertFalse(self.result['prediction_freeze']['gt_read'])
        self.assertEqual(av._read(self.output/'M3/provenance.json')['gt_read'], False)
        self.assertEqual(av._read(self.output/'M4/provenance.json')['gt_read'], False)
        self.assertTrue(all(av._hash(self.output/name) == digest for name, digest in self.result['prediction_freeze']['files_sha256'].items()))

    def test_retrieval_scope_does_not_claim_visible_accuracy_or_true_negatives(self):
        e = self.result['evaluation']
        self.assertEqual((e['inventory_objects'], e['objects'], e['excluded_unsupported_objects']), (288, 273, 15))
        self.assertEqual((e['changed_objects'], e['unretrieved_changed_objects']), (98, 98))
        self.assertIsNone(e['precision'])
        self.assertEqual((e['recall'], e['f1']), (0, 0))
        self.assertIsNone(e['visibility_conditioned_accuracy'])
        self.assertNotIn('true_negatives', e)
        self.assertEqual((e['candidate_hits'], e['candidate_count'], e['false_positives']), (0, 1, 0))
        self.assertEqual(e['candidate_diagnostics'][0]['evaluation_role'], 'candidate_only_not_final_positive')
        self.assertEqual(e['gt_type_counts']['geometry_changed'], 30)

    def test_global_curb_flag_is_not_presented_as_specific_detected_curb(self):
        d = self.result['stages']['M2']['diagnostics']
        self.assertEqual(d['ambiguous_top2_same_boundary_and_attribute'], 941)
        self.assertEqual(d['ambiguous_matches'], 1451)
        self.assertEqual(d['quality_flag_counts']['curb_confusion_possible'], 10278)

    def test_existing_output_is_never_overwritten(self):
        before = av._hash(self.output/'result.json')
        with self.assertRaises(FileExistsError):
            av.run_argoverse(RAW, self.output)
        self.assertEqual(av._hash(self.output/'result.json'), before)

    def test_manifest_rejects_tampered_consumed_input(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp)/'M2'
            package.mkdir()
            source = av._package(RAW, 'M2')
            for name in ('file_manifest.json', 'summary.json'):
                shutil.copy2(source/name, package/name)
            (package/'summary.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'manifest hash mismatch'):
                av._checked(Path(temp), package, ('summary.json',), 'frozen_prediction_input', [])

    def test_observation_identity_cannot_be_attached_to_a_different_view(self):
        with tempfile.TemporaryDirectory() as temp:
            source = av._package(RAW, 'M2')
            package = Path(temp)/source.name
            (package/'tables').mkdir(parents=True)
            for name in av.M2_FILES:
                (package/name).symlink_to(source/name)
            matches = av._rows(source/'tables/matches.jsonl')
            matches[0]['view_id'] = 'different-view'
            target = package/'tables/matches.jsonl'
            target.unlink()
            target.write_text(''.join(json.dumps(row)+'\n' for row in matches))
            with self.assertRaisesRegex(ValueError, 'attribution mismatch'):
                av._identity(package)


if __name__ == '__main__':
    unittest.main()
