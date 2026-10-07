"""Unit tests for the pure gusset geometry (``lib/gussets.py``).

Run with plain ``python`` (no Fusion):

    python -m unittest discover -s tests

These validate the corner-vertex/plate-frame math, the three plate shapes, and
the profile inner-contour polygons -- everything that does NOT require the
``adsk`` API.  Centrelines are ``(start, end, geom, basis)`` tuples in cm, the
same shape ``weldment._member_centerline`` returns.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import gussets as gs  # noqa: E402


def _cl(start, end, geom=None):
    return (start, end, geom, None)


def _rect(half_mm):
    h = float(half_mm)
    return {'kind': 'polygons',
            'loops': [[(-h, -h), (h, -h), (h, h), (-h, h)]],
            'fillets': [[]]}


class TestCornerVertex(unittest.TestCase):
    def test_meeting_ends_give_the_vertex(self):
        a = _cl((0, 0, 0), (10, 0, 0))
        b = _cl((0, 0, 0), (0, 0, 10))
        self.assertEqual(gs.corner_vertex(a, b), (0.0, 0.0, 0.0))

    def test_contact_point_of_overlapping_ends(self):
        # Ends 2 mm apart but the segments cross: the vertex is the contact
        # point on the segments, not the midpoint of the two non-touching ends.
        a = _cl((0, 0, 0), (10, 0, 0))
        b = _cl((0.2, 0, 0), (0.2, 0, 10))
        v = gs.corner_vertex(a, b)
        self.assertAlmostEqual(v[0], 0.2, places=9)

    def test_far_apart_is_not_a_corner(self):
        a = _cl((0, 0, 0), (10, 0, 0))
        b = _cl((50, 0, 0), (50, 0, 10))
        self.assertIsNone(gs.corner_vertex(a, b))

    def test_face_to_face_t_corner_meets(self):
        # A vertical member resting ON TOP of a horizontal one: their centrelines
        # never touch (the vertical stops at the horizontal's top face, 5 cm up)
        # but the solids do, so this IS a corner -- the bug the old endpoint
        # rule missed.
        a = _cl((0, 0, 0), (100, 0, 0), _rect(50))       # horizontal, 100 mm deep
        b = _cl((50, 0, 5), (50, 0, 105), _rect(50))     # vertical, rests on top
        v = gs.corner_vertex(a, b)
        self.assertIsNotNone(v)
        self.assertAlmostEqual(v[0], 50.0, places=6)
        self.assertAlmostEqual(v[2], 2.5, places=6)      # mid-gap of the two faces

    def test_genuinely_separate_solids_do_not_meet(self):
        # Same T layout but the vertical member floats well clear: no contact.
        a = _cl((0, 0, 0), (100, 0, 0), _rect(50))
        b = _cl((50, 0, 200), (50, 0, 100), _rect(50))
        self.assertIsNone(gs.corner_vertex(a, b))


class TestCornerPlacement(unittest.TestCase):
    # A centreline vertex is buried in the material; the plate's corner belongs
    # on the reentrant corner where the two facing surfaces meet, offset by each
    # profile's registered half-width.
    def _clb(self, start, end, half_mm, basis):
        return (start, end, _rect(half_mm), basis)

    def test_l_corner_offsets_onto_both_faces(self):
        # A runs +X, B runs +Z, ends meeting at the origin; 100 mm sections
        # (half-width 5 cm).  The plate corner nests at (+5, 0, +5), not (0,0,0).
        a = self._clb((0, 0, 0), (100, 0, 0), 50, ((0, 1, 0), (0, 0, 1)))
        b = self._clb((0, 0, 0), (0, 0, 100), 50, ((1, 0, 0), (0, 1, 0)))
        v = gs.corner_vertex(a, b)
        p = gs.corner_placement(a, b, v)
        self.assertAlmostEqual(p[0], 5.0, places=6)
        self.assertAlmostEqual(p[1], 0.0, places=6)
        self.assertAlmostEqual(p[2], 5.0, places=6)

    def test_unknown_section_leaves_the_vertex(self):
        # No geometry to offset by: placement falls back to the vertex itself.
        a = _cl((0, 0, 0), (100, 0, 0))
        b = _cl((0, 0, 0), (0, 0, 100))
        v = gs.corner_vertex(a, b)
        p = gs.corner_placement(a, b, v)
        self.assertEqual(p, v)


class TestPlateFrame(unittest.TestCase):
    def test_perpendicular_corner_frame(self):
        a = _cl((0, 0, 0), (10, 0, 0))
        b = _cl((0, 0, 0), (0, 0, 10))
        n, e1, e2 = gs.plate_frame((0, 0, 0), a, b)
        # e1 runs along member A (+X); normal perpendicular to both runs (+/-Y).
        self.assertAlmostEqual(abs(e1[0]), 1.0, places=9)
        self.assertAlmostEqual(abs(n[1]), 1.0, places=9)
        self.assertAlmostEqual(n[0], 0.0, places=9)
        self.assertAlmostEqual(n[2], 0.0, places=9)

    def test_frame_is_right_handed_orthonormal(self):
        a = _cl((0, 0, 0), (10, 0, 0))
        b = _cl((0, 0, 0), (7, 0, 7))
        n, e1, e2 = gs.plate_frame((0, 0, 0), a, b)
        for v in (n, e1, e2):
            self.assertAlmostEqual(math.sqrt(sum(c * c for c in v)), 1.0, places=9)
        self.assertAlmostEqual(gs._dot(n, e1), 0.0, places=9)
        self.assertAlmostEqual(gs._dot(n, e2), 0.0, places=9)
        self.assertAlmostEqual(gs._dot(e1, e2), 0.0, places=9)

    def test_collinear_has_no_frame(self):
        a = _cl((0, 0, 0), (10, 0, 0))
        b = _cl((-10, 0, 0), (0, 0, 0))
        self.assertIsNone(gs.plate_frame((0, 0, 0), a, b))


class TestGussetPolygon(unittest.TestCase):
    def test_triangle(self):
        pts = gs.gusset_polygon('triangle', 100, 80)
        self.assertEqual(pts, [(0.0, 0.0), (100.0, 0.0), (0.0, 80.0)])

    def test_rectangle(self):
        pts = gs.gusset_polygon('rectangle', 100, 80)
        self.assertEqual(pts, [(0.0, 0.0), (100.0, 0.0),
                               (100.0, 80.0), (0.0, 80.0)])

    def test_triangle_extended_adds_a_band(self):
        tri = gs.gusset_polygon('triangle', 100, 80)
        ext = gs.gusset_polygon('triangle_extended', 100, 80, 20)
        self.assertEqual(len(tri), 3)
        self.assertEqual(len(ext), 5)
        # The band runs PARALLEL to the members: the (w,0)->(w,e) edge is along
        # member B (constant u), and (e,h)->(0,h) is along member A (constant v).
        self.assertEqual(ext, [(0.0, 0.0), (100.0, 0.0), (100.0, 20.0),
                               (20.0, 80.0), (0.0, 80.0)])

    def test_outside_mirrors_the_plate(self):
        inside = gs.gusset_polygon('triangle', 100, 80, alignment='inside')
        outside = gs.gusset_polygon('triangle', 100, 80, alignment='outside')
        self.assertEqual([(-u, -v) for u, v in inside], outside)

    def test_unknown_shape_raises(self):
        with self.assertRaises(ValueError):
            gs.gusset_polygon('hexagon', 10, 10)

    def test_unknown_alignment_raises(self):
        with self.assertRaises(ValueError):
            gs.gusset_polygon('triangle', 10, 10, alignment='sideways')


class TestProfileInnerPolygon(unittest.TestCase):
    def test_ibeam_stiffener_is_centred_on_the_web(self):
        des = {'h_mm': 100, 'b_mm': 96, 'tw_mm': 5, 'tf_mm': 8}
        loop = gs.profile_inner_polygon('HEA', des, 40)
        v_half = 100 / 2 - 8               # clear between flange faces
        self.assertEqual(loop, [(-20.0, -v_half), (20.0, -v_half),
                                (20.0, v_half), (-20.0, v_half)])

    def test_ibeam_depth_clamps_to_the_section(self):
        des = {'h_mm': 100, 'b_mm': 20, 'tw_mm': 5, 'tf_mm': 8}
        loop = gs.profile_inner_polygon('HEA', des, 1000)   # absurd depth
        us = [u for u, _v in loop]
        self.assertLessEqual(max(us), des['b_mm'] / 2)      # never past a tip

    def test_channel_starts_at_the_web(self):
        des = {'h_mm': 80, 'b_mm': 50, 'tw_mm': 4.5, 'tf_mm': 7}
        loop = gs.profile_inner_polygon('UPE', des, 30)
        u0 = -des['b_mm'] / 2 + des['tw_mm']               # web inner face
        self.assertAlmostEqual(loop[0][0], u0, places=9)
        self.assertAlmostEqual(loop[1][0], u0 + 30, places=9)

    def test_tube_family_returns_none(self):
        self.assertIsNone(gs.profile_inner_polygon('CHS', {'od_mm': 20}, 10))

    def test_missing_fields_returns_none(self):
        self.assertIsNone(gs.profile_inner_polygon('HEA', {'h_mm': 100}, 10))


if __name__ == '__main__':
    unittest.main()
