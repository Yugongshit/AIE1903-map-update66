"""Before/after comparison shares registration and never invents displacement."""
from copy import deepcopy
import unittest
import xml.etree.ElementTree as ET

from map_update.simulation_comparison import prepare_comparison, project_point, render_comparison_svg
from map_update.simulation_visuals import prepare_update_overlay


def line(points=None, **extra):
    return {'type': 'ROAD_LINE_BROKEN_SINGLE_WHITE',
            'polyline': points or [[100., 200.], [110., 200.], [120., 200.]], **extra}


def comparison(before, after, *, operation='replace', synthetic=False, panel_size=(480, 330)):
    log = [{'feature_id': 'line-1', 'operation': operation, 'before': before, 'after': after}]
    overlay = prepare_update_overlay(log, source_to_runtime_offset=(-100, -200), synthetic=synthetic)
    return prepare_comparison(overlay, patch_log=log, panel_size=panel_size)


class SimulationComparisonTests(unittest.TestCase):
    def test_bus_access_update_is_visible_even_with_identical_geometry(self):
        model = comparison(line(source_lane_type='VEHICLE', vehicle_access=['vehicle', 'bus']),
                           line(source_lane_type='BUS', vehicle_access=['bus']))
        self.assertEqual(model['translation']['kind'], 'unchanged')
        self.assertEqual([item['field'] for item in model['attribute_changes']],
                         ['source_lane_type', 'vehicle_access'])
        self.assertIn('Source Lane Type: vehicle -> bus', model['summary_lines'])

    def test_uniform_translation_is_measured_from_all_vertices(self):
        model = comparison(line(), line([[103., 204.], [113., 204.], [123., 204.]]))
        translation = model['translation']
        self.assertEqual(translation['kind'], 'uniform_translation')
        self.assertEqual([translation['dx_m'], translation['dy_m'], translation['distance_m']], [3., 4., 5.])
        self.assertEqual(translation['arrow'], {'start': [10., 0.], 'end': [13., 4.]})
        viewport = model['viewport']
        self.assertTrue(viewport['same_transform_for_both_sides'])
        a, b = project_point(model, [10., 0.]), project_point(model, [13., 4.])
        self.assertAlmostEqual(b[0]-a[0], 3.*viewport['pixels_per_metre'])
        self.assertAlmostEqual(b[1]-a[1], -4.*viewport['pixels_per_metre'])

    def test_shared_viewport_preserves_offsets_instead_of_independent_fit(self):
        model = comparison(line(), line([[100., 201.], [110., 201.], [120., 201.]]))
        scale = model['viewport']['pixels_per_metre']
        for old, new in zip(model['before']['paths'][0]['points'], model['after']['paths'][0]['points']):
            self.assertEqual(project_point(model, old)[0], project_point(model, new)[0])
            self.assertAlmostEqual(project_point(model, old)[1]-project_point(model, new)[1], scale)

    def test_nonuniform_changes_do_not_get_average_displacement_or_arrow(self):
        model = comparison(line(), line([[100., 200.], [111., 202.], [122., 200.]]))
        self.assertEqual(model['translation']['kind'], 'deformation')
        for key in ('dx_m', 'dy_m', 'distance_m', 'arrow'):
            self.assertIsNone(model['translation'][key])
        self.assertIn('Non-uniform', model['summary_lines'][0])

    def test_added_and_deleted_features_have_explicit_absence(self):
        added = comparison(None, line(), operation='add')
        self.assertFalse(added['before']['present'])
        self.assertEqual(added['before']['paths'], [])
        self.assertEqual(added['before']['missing_label'], 'NOT PRESENT BEFORE')
        deleted = comparison(line(), None, operation='delete')
        self.assertFalse(deleted['after']['present'])
        self.assertEqual(deleted['after']['paths'], [])
        self.assertEqual(deleted['before']['paths'][0]['role'], 'removed')
        for value in (added, deleted):
            self.assertEqual(value['translation']['kind'], 'unpaired')
            self.assertIsNone(value['translation']['arrow'])

    def test_attribute_only_type_and_topology_change_has_no_fake_motion(self):
        before = line(entry_lanes=['A'], exit_lanes=['B'])
        after = line(entry_lanes=['A'], exit_lanes=['C'])
        after['type'] = 'ROAD_LINE_SOLID_SINGLE_WHITE'
        model = comparison(before, after)
        self.assertEqual(model['translation']['kind'], 'unchanged')
        self.assertEqual(model['translation']['distance_m'], 0.)
        self.assertIsNone(model['translation']['arrow'])
        changes = {row['field']: row for row in model['attribute_changes']}
        self.assertEqual(changes['exit_lanes'], {'field': 'exit_lanes', 'before': ['B'], 'after': ['C']})
        self.assertIn('type', changes)
        self.assertTrue(model['topology_available'])

    def test_topology_counts_normalize_single_ids_and_do_not_guess_unknown_shapes(self):
        model = comparison(line(left_neighbor='lane-alpha', right_neighbor=None),
                           line(left_neighbor='lane-beta', right_neighbor=['lane-alpha', 'lane-gamma']))
        self.assertIn('Left Neighbor: 1 -> 1 (+1 / -1)', model['summary_lines'])
        self.assertIn('Right Neighbor: 0 -> 2 (+2 / -0)', model['summary_lines'])
        unknown = comparison(line(left_neighbor={'unrecognized': 'lane-alpha'}),
                             line(left_neighbor=['lane-beta']))
        self.assertIn('Left Neighbor: changed; reference encoding unavailable for counts', unknown['summary_lines'])

    def test_unpaired_vertex_counts_do_not_claim_a_deformation_metric(self):
        model = comparison(line(), line([[100., 200.], [120., 200.]]))
        self.assertEqual(model['translation']['kind'], 'unpaired')
        self.assertIsNone(model['translation']['distance_m'])

    def test_polygon_and_centerline_must_translate_together(self):
        before = line(polygon=[[100., 198.], [120., 198.], [120., 202.], [100., 202.]])
        after = deepcopy(before)
        after['polyline'] = [[p[0], p[1]+1] for p in before['polyline']]
        model = comparison(before, after)
        self.assertEqual(model['translation']['kind'], 'deformation')
        self.assertIsNone(model['translation']['arrow'])

    def test_no_patch_metadata_reports_unknown_topology(self):
        overlay = prepare_update_overlay([{'feature_id': 'x', 'operation': 'replace', 'before': line(), 'after': line()}])
        model = prepare_comparison(overlay)
        self.assertFalse(model['topology_available'])
        self.assertIn('Topology attributes were not supplied', model['summary_lines'][-1])

    def test_patch_metadata_cannot_describe_another_geometry(self):
        log = [{'feature_id': 'x', 'operation': 'replace', 'before': line(), 'after': line()}]
        overlay = prepare_update_overlay(log)
        modified = deepcopy(log)
        modified[0]['after']['polyline'][0][0] += 1
        with self.assertRaisesRegex(ValueError, 'geometry differs'):
            prepare_comparison(overlay, patch_log=modified)

    def test_context_does_not_expand_local_view_or_mutate_inputs(self):
        log = [{'feature_id': 'x', 'operation': 'replace', 'before': line(), 'after': line()}]
        overlay = prepare_update_overlay(log)
        saved = deepcopy(overlay)
        plain = prepare_comparison(overlay)
        context = prepare_comparison(overlay, context_paths=[{'points': [[-1e6, -1e6], [1e6, 1e6]]}])
        self.assertEqual(plain['viewport'], context['viewport'])
        self.assertEqual(context['context_paths'][0]['role'], 'unchanged_context')
        self.assertEqual(saved, overlay)

    def test_empty_patch_explicit_zero_changes(self):
        model = prepare_comparison(prepare_update_overlay([]))
        self.assertTrue(model['empty'])
        self.assertEqual(model['changed_features'], 0)
        self.assertEqual(model['summary_lines'][0], 'NO MAP CHANGES APPLIED')
        self.assertFalse(model['before']['present'])
        self.assertFalse(model['after']['present'])
        self.assertIn('NO MAP CHANGES APPLIED', render_comparison_svg(model))
        self.assertIn('NO STRUCTURAL EDITS', render_comparison_svg(model))
        with self.assertRaisesRegex(ValueError, 'cannot omit'):
            prepare_comparison(prepare_update_overlay([]), patch_log=[{'feature_id': 'omitted'}])

    def test_selection_and_dimension_validation(self):
        overlay = prepare_update_overlay([{'feature_id': 'x', 'operation': 'add', 'before': None, 'after': line()}])
        for index in (-1, 1, True):
            with self.subTest(index=index), self.assertRaises(ValueError):
                prepare_comparison(overlay, index)
        with self.assertRaises(ValueError):
            prepare_comparison(overlay, panel_size=(0, 300))

    def test_svg_is_standalone_readable_and_demo_is_explicit(self):
        model = comparison(line(), line([[101., 200.], [111., 200.], [121., 200.]]), synthetic=True)
        saved = deepcopy(model)
        svg = render_comparison_svg(model)
        root = ET.fromstring(svg)
        self.assertEqual(root.attrib['width'], '1440')
        for text in ('BEFORE', 'AFTER', 'REGISTERED CHANGE', 'CONTROLLED DEMO', '1.000 m', '+X', '+Y'):
            self.assertIn(text, svg)
        self.assertIn('marker-end="url(#arrowhead)"', svg)
        self.assertEqual(saved, model)
        ordinary = render_comparison_svg(comparison(None, line(), operation='add'))
        self.assertNotIn('CONTROLLED DEMO', ordinary)
        self.assertNotIn('marker-end=', ordinary)

    def test_svg_escapes_source_labels(self):
        model = comparison(None, line(), operation='add')
        model['feature_id'] = '<script>alert(1)</script>'
        svg = render_comparison_svg(model)
        ET.fromstring(svg)
        self.assertNotIn('<script>', svg)
        self.assertIn('&lt;script&gt;', svg)


if __name__ == '__main__':
    unittest.main()
