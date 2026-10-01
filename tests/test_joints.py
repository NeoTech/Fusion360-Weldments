"""Unit tests for the pure-Python corner-joint geometry (``lib/joints.py``).

Run with plain ``python`` (no Fusion):

    python -m unittest discover -s tests

These validate corner detection, section depth, and the per-line butt/miter
offsets -- everything that does NOT require the ``adsk`` API.  Lines are the
test stub's ``FakeLine`` (which exposes ``worldGeometry``/``length`` exactly like
a real sketch line).
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import joints as jt  # noqa: E402
from adsk_stub import FakeLine  # noqa: E402


def _rect(depth_mm, width_mm=None):
    """A polygon geom whose bounding box is depth_mm x width_mm (mm)."""
    w = width_mm if width_mm is not None else depth_mm
    h, b = depth_mm, w
    return {'kind': 'polygons',
            'loops': [[(-b / 2, -h / 2), (b / 2, -h / 2),
                       (b / 2, h / 2), (-b / 2, h / 2)]],
            'fillets': [[]]}


def _circle(od_mm):
    return {'kind': 'circles', 'radii': [od_mm / 2.0, od_mm / 2.0 - 2.0]}


class TestLineAccessors(unittest.TestCase):
    def test_endpoints_and_direction(self):
        ln = FakeLine((0, 0, 0), (10, 0, 0))
        s, e = jt.line_endpoints(ln)
        self.assertEqual(s, (0, 0, 0))
        self.assertEqual(e, (10, 0, 0))
        self.assertEqual(jt.line_direction(ln), (1.0, 0.0, 0.0))

    def test_direction_is_unit(self):
        ln = FakeLine((0, 0, 0), (3, 4, 0))
        d = jt.line_direction(ln)
        self.assertAlmostEqual(math.sqrt(sum(c * c for c in d)), 1.0)


class TestCornerDetection(unittest.TestCase):
    def test_l_frame_one_corner(self):
        # A horizontal line ending at origin, a vertical line starting at origin.
        lines = [FakeLine((-10, 0, 0), (0, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0))]
        corners = jt.detect_corners(lines)
        self.assertEqual(len(corners), 1)
        self.assertEqual(corners[0]['point'], (0, 0, 0))
        self.assertEqual({mi for mi, _ in corners[0]['members']}, {0, 1})

    def test_disconnected_lines_no_corner(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 5, 0), (10, 5, 0))]
        self.assertEqual(jt.detect_corners(lines), [])

    def test_three_way_corner(self):
        # Three lines all meeting at the origin.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0)),
                 FakeLine((0, 0, 0), (0, 0, 10))]
        corners = jt.detect_corners(lines)
        self.assertEqual(len(corners), 1)
        self.assertEqual(len(corners[0]['members']), 3)

    def test_roles_start_vs_end(self):
        lines = [FakeLine((-10, 0, 0), (0, 0, 0)),   # ends at corner
                 FakeLine((0, 0, 0), (0, 10, 0))]     # starts at corner
        corner = jt.detect_corners(lines)[0]
        roles = dict(corner['members'])
        self.assertEqual(roles[0], 'end')
        self.assertEqual(roles[1], 'start')


class TestMemberDepth(unittest.TestCase):
    def test_rect_depth_is_max_extent(self):
        # 100 x 46 rect -> half-extent 50 -> depth 100 mm.
        self.assertAlmostEqual(jt.member_depth(_rect(100, 46)), 100.0)

    def test_square_depth(self):
        self.assertAlmostEqual(jt.member_depth(_rect(50, 50)), 50.0)

    def test_circle_depth_is_od(self):
        self.assertAlmostEqual(jt.member_depth(_circle(60)), 60.0)

    def test_none_geom_zero(self):
        self.assertEqual(jt.member_depth(None), 0.0)


class TestButtJoint(unittest.TestCase):
    def test_incoming_no_axial_trim(self):
        # Butt/cope/miter are realised by real boolean cuts (corner_cuts), not
        # an axial trim, so corner_offsets contributes nothing for them.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_rect(100), _rect(80)]
        offs = jt.corner_offsets(lines, geoms, ['none', 'butt'])
        self.assertEqual(offs, [(0.0, 0.0), (0.0, 0.0)])

    def test_none_joint_is_noop(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        offs = jt.corner_offsets(lines, [_rect(100), _rect(80)], ['none', 'none'])
        self.assertEqual(offs, [(0.0, 0.0), (0.0, 0.0)])

    def test_cope_same_as_butt(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_rect(100), _circle(80)]
        butt = jt.corner_offsets(lines, geoms, ['butt', 'butt'])
        cope = jt.corner_offsets(lines, geoms, ['cope', 'cope'])
        self.assertEqual(butt, cope)


class TestMiterJoint(unittest.TestCase):
    def test_miter_extends_to_corner(self):
        # A miter runs corner-to-corner, so each member EXTENDS past the
        # centreline vertex by the setback (d/2)/tan(phi/2); the bisector plane
        # then trims the diagonal.  For a 100 mm section at a right angle that
        # is 50/tan(45) = 50 mm = 5 cm, applied at the corner (start) end.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0))]
        geoms = [_rect(100), _rect(100)]
        offs = jt.corner_offsets(lines, geoms, ['miter', 'miter'])
        self.assertAlmostEqual(offs[0][0], -5.0)
        self.assertAlmostEqual(offs[1][0], -5.0)
        self.assertEqual(offs[0][1], 0.0)
        self.assertEqual(offs[1][1], 0.0)

    def test_miter_zero_when_collinear(self):
        # A straight run (180 deg turn) needs no miter setback.
        lines = [FakeLine((-10, 0, 0), (0, 0, 0)),
                 FakeLine((0, 0, 0), (10, 0, 0))]
        offs = jt.corner_offsets(lines, [_rect(100), _rect(100)],
                                 ['miter', 'miter'])
        self.assertAlmostEqual(offs[0][1], 0.0)
        self.assertAlmostEqual(offs[1][0], 0.0)


class TestCornerCuts(unittest.TestCase):
    def test_miter_right_angle_planes(self):
        # Two mitred members at a right angle -> one bisector plane per member.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0))]
        cuts = jt.corner_cuts(lines, ['miter', 'miter'])
        self.assertEqual(len(cuts), 2)
        for c in cuts:
            self.assertEqual(c['kind'], 'plane')
            self.assertEqual(c['point'], (0, 0, 0))
            # Normal is the 45-degree bisector between the two outward dirs.
            n = c['normal']
            self.assertAlmostEqual(abs(n[0]), math.sqrt(0.5))
            self.assertAlmostEqual(abs(n[1]), math.sqrt(0.5))

    def test_butt_is_body_saddle(self):
        # A butt member is cut by the neighbour's body (saddle), not a plane.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        cuts = jt.corner_cuts(lines, ['none', 'butt'])
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]['member'], 1)
        self.assertEqual(cuts[0]['kind'], 'body')
        self.assertEqual(cuts[0]['tool'], 0)

    def test_none_and_bend_not_cut(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0))]
        self.assertEqual(jt.corner_cuts(lines, ['none', 'none']), [])
        self.assertEqual(jt.corner_cuts(lines, ['bend', 'bend']), [])

    def test_collinear_miter_no_plane(self):
        # Collinear members: bisector normal is zero -> no sensible cut.
        lines = [FakeLine((-10, 0, 0), (0, 0, 0)),
                 FakeLine((0, 0, 0), (10, 0, 0))]
        self.assertEqual(jt.corner_cuts(lines, ['miter', 'miter']), [])


class TestSweptBend(unittest.TestCase):
    def test_turn_angle_right_angle(self):
        # Members pointing +X and +Y away from the vertex -> 90 deg turn.
        self.assertAlmostEqual(jt.bend_turn_angle((1, 0, 0), (0, 1, 0)), math.pi / 2)

    def test_turn_angle_straight_is_zero(self):
        # Collinear run (opposite outward dirs) -> no turn.
        self.assertAlmostEqual(jt.bend_turn_angle((1, 0, 0), (-1, 0, 0)), 0.0)

    def test_setback_90_equals_radius(self):
        # SB = R*tan(45) = R.  R=100 mm -> 10 cm.
        self.assertAlmostEqual(jt.bend_setback(100.0, math.pi / 2), 10.0)

    def test_arc_length_90(self):
        # L = R*theta = 100 mm * pi/2 -> 15.708 cm.
        self.assertAlmostEqual(jt.bend_arc_length(100.0, math.pi / 2),
                               100.0 * math.pi / 2 * jt.MM_TO_CM)

    def test_bend_plan_90_corner(self):
        # Two legs meeting at the origin (+X and +Y), both 'bend', R=100 mm.
        lines = [FakeLine((0, 0, 0), (30, 0, 0)),
                 FakeLine((0, 0, 0), (0, 30, 0))]
        plans = jt.bend_plan(lines, ['bend', 'bend'], [100.0, 100.0])
        self.assertEqual(len(plans), 1)
        p = plans[0]
        self.assertAlmostEqual(p['theta'], math.pi / 2)
        # Center sits at (R, R) = (10, 10) cm inside the turn.
        self.assertAlmostEqual(p['center'][0], 10.0)
        self.assertAlmostEqual(p['center'][1], 10.0)
        # Tangent points are R back along each leg from the vertex: (10,0,0)/(0,10,0).
        t = {i: pt for i, _r, pt in p['tangent']}
        self.assertAlmostEqual(t[0][0], 10.0)
        self.assertAlmostEqual(t[0][1], 0.0)
        self.assertAlmostEqual(t[1][0], 0.0)
        self.assertAlmostEqual(t[1][1], 10.0)

    def test_bend_plan_requires_both_bend(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0))]
        # Only one leg asks for a bend -> no swept corner.
        self.assertEqual(jt.bend_plan(lines, ['bend', 'none'], [100.0, 100.0]), [])

    def test_bend_offsets_trim_legs(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0))]
        offs = jt.corner_offsets(lines, [_rect(40), _rect(40)],
                                 ['bend', 'bend'], clr_by_line=[100.0, 100.0])
        # Each leg starts at the corner -> offset_start pushed +SB (10 cm) inward.
        self.assertAlmostEqual(offs[0][0], 10.0)
        self.assertAlmostEqual(offs[1][0], 10.0)


class TestFamilyFiltering(unittest.TestCase):
    def test_joints_for_family(self):
        self.assertEqual(jt.joints_for_family({'joints': ['none', 'butt', 'miter']}),
                         ['none', 'butt', 'miter'])

    def test_missing_joints_defaults_none(self):
        self.assertEqual(jt.joints_for_family({}), ['none'])
        self.assertEqual(jt.joints_for_family(None), ['none'])

    def test_labels_and_roundtrip(self):
        ids = jt.joints_for_family({'joints': ['none', 'miter', 'bend']})
        labels = jt.joint_labels(ids)
        self.assertEqual([jt.joint_id_from_label(l) for l in labels], ids)


class TestDataProfileJoints(unittest.TestCase):
    """Every family in the shipped catalogue declares a valid joint set."""

    def setUp(self):
        from lib import profiles as prof
        self.families = prof.load_profiles()

    def test_all_families_declare_joints(self):
        for fam in self.families:
            js = fam.get('joints')
            self.assertTrue(js, fam['abbreviation'])
            self.assertIn('none', js, fam['abbreviation'])
            for j in js:
                self.assertIn(j, jt.ALL_IDS, fam['abbreviation'])

    def test_bend_only_on_hollow_and_round(self):
        for fam in self.families:
            if 'bend' in fam['joints']:
                self.assertIn(fam['abbreviation'], {'SHS', 'RHS', 'CHS'})


if __name__ == '__main__':
    unittest.main()
