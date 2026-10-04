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


def _tube(depth_mm, width_mm, wall_mm):
    """A hollow rectangular-section geom (two loops: outer + inner void)."""
    h, b = depth_mm, width_mm
    t = wall_mm
    return {'kind': 'polygons',
            'loops': [[(-b / 2, -h / 2), (b / 2, -h / 2),
                       (b / 2, h / 2), (-b / 2, h / 2)],
                      [(-(b / 2 - t), -(h / 2 - t)), (b / 2 - t, -(h / 2 - t)),
                       (b / 2 - t, h / 2 - t), (-(b / 2 - t), h / 2 - t)]],
            'fillets': [[], []]}


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
    def test_incoming_trims_to_near_face(self):
        # A butt is a pure axial trim: the incoming member (line 1, ending at
        # the corner) stops short by the through member's half-depth, and the
        # through member (line 0) extends past the corner by its own half-depth
        # for a flush corner.  No boolean.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_rect(100), _rect(80)]
        offs = jt.corner_offsets(lines, geoms, ['none', 'butt'])
        # Line 0 (through, corner at its START) extends past the vertex by the
        # incoming member's half-depth (80/2 = 40 mm = 4 cm).
        self.assertAlmostEqual(offs[0][0], -4.0)
        self.assertEqual(offs[0][1], 0.0)
        # Line 1 (incoming, butt, corner at its END) stops short by the through
        # member's half-depth (100/2 = 50 mm = 5 cm).
        self.assertEqual(offs[1][0], 0.0)
        self.assertAlmostEqual(offs[1][1], -5.0)

    def test_none_joint_is_noop(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        offs = jt.corner_offsets(lines, [_rect(100), _rect(80)], ['none', 'none'])
        self.assertEqual(offs, [(0.0, 0.0), (0.0, 0.0)])

    def test_both_butt_never_leaves_a_gap(self):
        # Two butt members at one corner: exactly one runs through and the
        # other backs off -- never both backing off (which left a gap).
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_rect(100), _rect(80)]
        offs = jt.corner_offsets(lines, geoms, ['butt', 'butt'])
        # Line 0 backs off at its start (+4), line 1 runs through its end (+5).
        self.assertAlmostEqual(offs[0][0], 4.0)
        self.assertAlmostEqual(offs[1][1], 5.0)
        # Neither member both-extends-and-trims: one grows, one shrinks.
        self.assertGreater(offs[0][0], 0.0)
        self.assertGreater(offs[1][1], 0.0)

    def test_through_flag_picks_the_through_member(self):
        # Marking line 0 'through' flips roles: line 0 extends, line 1 backs off.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_rect(100), _rect(80)]
        offs = jt.corner_offsets(lines, geoms, ['butt', 'butt'],
                                 through_by_line=[True, False])
        self.assertAlmostEqual(offs[0][0], -4.0)   # line 0 runs through
        self.assertAlmostEqual(offs[1][1], -5.0)   # line 1 backs off

    def test_cope_at_corner_saddles_into_tool(self):
        # A cope at a shared-vertex corner is a SADDLE, not a plain butt: the
        # backing-off member reaches PAST the vertex into the tool so the boolean
        # (see corner_cuts) has overlap to carve.  A butt stops at the near face
        # (-half); a cope reaches the far face (+half) of a solid tool.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_rect(100), _circle(80)]
        butt = jt.corner_offsets(lines, geoms, ['none', 'butt'])
        cope = jt.corner_offsets(lines, geoms, ['none', 'cope'])
        # The butt backs off to the near face; the cope overshoots into the tool.
        self.assertAlmostEqual(butt[1][1], -5.0)
        self.assertAlmostEqual(cope[1][1], 5.0)
        # The through member grows to the incoming member's face in both cases.
        self.assertAlmostEqual(cope[0][0], butt[0][0])

    def test_saddled_butt_runs_to_far_face(self):
        # A SADDLED butt against a SOLID tool runs the backing-off member to the
        # tool's FAR face (extend, +half) so the boolean notch has overlap to
        # carve; a plain butt stops at the NEAR face (-half).  Same magnitude,
        # opposite sign -- the sign is what makes the saddle actually remove
        # material instead of nothing.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_rect(100), _rect(80)]
        plain = jt.corner_offsets(lines, geoms, ['none', 'butt'])
        saddled = jt.corner_offsets(lines, geoms, ['none', 'butt'],
                                    saddle_by_line=[False, True])
        self.assertAlmostEqual(plain[1][1], -5.0)     # near face
        self.assertAlmostEqual(saddled[1][1], 5.0)    # far face (extended)

    def test_saddled_butt_hollow_tool_penetrates_wall(self):
        # Against a HOLLOW tool a far-face run pokes straight through and a
        # near-face run leaves the wall poking through the member, so a saddled
        # butt stops just PAST the near wall (-half + wall): the boolean carves a
        # saddle through the wall only.  Here half = 5 cm, wall = 8 mm = 0.8 cm.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_tube(100, 100, 8), _rect(80)]
        offs = jt.corner_offsets(lines, geoms, ['none', 'butt'],
                                 saddle_by_line=[False, True])
        self.assertAlmostEqual(offs[1][1], -5.0 + 0.8)   # just past the near wall

    def test_cope_depth_deepens_saddled_butt(self):
        # A cope/saddle DEPTH adds extra bite into the neighbour beyond the
        # default stopping face.  Same hollow tool as above (near wall at -4.2),
        # plus a 10 mm (1 cm) depth -> the tip reaches -3.2.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_tube(100, 100, 8), _rect(80)]
        offs = jt.corner_offsets(lines, geoms, ['none', 'butt'],
                                 saddle_by_line=[False, True],
                                 cope_depth_by_line=[0.0, 10.0])
        self.assertAlmostEqual(offs[1][1], -5.0 + 0.8 + 1.0)

    def test_cope_depth_ignored_without_saddle(self):
        # A plain (unsaddled) butt stops at the near face and the depth does not
        # apply -- there is no boolean to deepen.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_tube(100, 100, 8), _rect(80)]
        offs = jt.corner_offsets(lines, geoms, ['none', 'butt'],
                                 saddle_by_line=[False, False],
                                 cope_depth_by_line=[0.0, 10.0])
        self.assertAlmostEqual(offs[1][1], -5.0)

    def test_per_end_joints_are_independent(self):
        # A joint belongs to a line END, so passing (start, end) pairs lets the
        # two ends differ.  Here line 0 mitres at its START and butts at its END;
        # only the end that meets a neighbour takes effect at each corner.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((10, 0, 0), (10, 10, 0))]
        geoms = [_rect(100), _rect(100)]
        # Scalar (both ends 'miter') vs a per-end pair -- both must be accepted.
        scalar = jt.corner_offsets(lines, geoms, ['miter', 'miter'])
        paired = jt.corner_offsets(lines, geoms,
                                   [('miter', 'miter'), ('miter', 'miter')])
        self.assertEqual(scalar, paired)


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

    def test_miter_setback_is_directional(self):
        # An I-beam-shaped section (80 tall x 46 wide) at a right-angle corner.
        # With the placed bases, the setback measures the section's extent in the
        # MITER PLANE (the 46mm flange), NOT its max dimension (the 80mm web).
        # Without bases it falls back to the isotropic member_depth/2 = 4cm.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0))]
        geoms = [_rect(80, 46), _rect(80, 46)]
        # member0 runs +X with u=+Y (flange across the joint), v=+Z.
        b0 = ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        # member1 runs +Y with u=-X (flange across the joint), v=+Z.
        b1 = ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        fallback = jt.corner_offsets(lines, geoms, ['miter', 'miter'])
        directional = jt.corner_offsets(lines, geoms, ['miter', 'miter'],
                                        bases=[b0, b1])
        self.assertAlmostEqual(fallback[0][0], -4.0)      # member_depth/2 (80/2)
        self.assertAlmostEqual(directional[0][0], -2.3)   # flange half (46/2)
        self.assertAlmostEqual(directional[1][0], -2.3)

    def test_miter_setback_tracks_rotation(self):
        # Rolling member0 by 90 deg (web now across the joint) must change ONLY
        # member0's setback to the web half (40mm -> 4cm); member1 stays flange.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0))]
        geoms = [_rect(80, 46), _rect(80, 46)]
        b0_flange = ((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        b0_web = ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0))       # u/v swapped = 90 roll
        b1 = ((-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        offs = jt.corner_offsets(lines, geoms, ['miter', 'miter'],
                                 bases=[b0_web, b1])
        self.assertAlmostEqual(offs[0][0], -4.0)          # web across the joint
        self.assertAlmostEqual(offs[1][0], -2.3)          # flange across the joint


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

    def test_butt_produces_no_cut(self):
        # A butt is a pure axial trim (handled by corner_offsets), so it must
        # NOT generate a boolean cut.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        self.assertEqual(jt.corner_cuts(lines, ['none', 'butt']), [])

    def test_cope_at_corner_is_body_saddle(self):
        # A cope whose end coincides with a corner (shared vertex) saddles into
        # the neighbour's END face -- coping a tube over the open end of another
        # ("cope to the end of the pipe").  The backing-off (cope) member is cut
        # against the through member's body; the through member keeps its end.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        cuts = jt.corner_cuts(lines, ['none', 'cope'])
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]['member'], 1)
        self.assertEqual(cuts[0]['kind'], 'body')
        self.assertEqual(cuts[0]['tool'], 0)

    def test_cope_at_t_junction_is_body_saddle(self):
        # A cope member whose END lands on the interior of another member's run
        # (a T) is saddled to that member's body (a boolean cut), not a plane.
        lines = [FakeLine((0, 0, 0), (50, 0, 0)),        # tool: long run on X
                 FakeLine((25, 0, 20), (25, 0, 0))]      # cope: ends mid-run
        cuts = jt.corner_cuts(lines, ['none', 'cope'])
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]['member'], 1)
        self.assertEqual(cuts[0]['kind'], 'body')
        self.assertEqual(cuts[0]['tool'], 0)

    def test_butt_at_t_junction_trims_no_cut(self):
        # A butt at a T-junction is a pure axial trim to the tool's near face
        # (no boolean), so corner_cuts emits nothing but corner_offsets trims.
        lines = [FakeLine((0, 0, 0), (50, 0, 0)),
                 FakeLine((25, 0, 20), (25, 0, 0))]
        geoms = [_rect(100), _rect(100)]
        self.assertEqual(jt.corner_cuts(lines, ['none', 'butt']), [])
        offs = jt.corner_offsets(lines, geoms, ['none', 'butt'])
        # The incoming member (line 1, corner at its END) backs off by the
        # tool's half-depth along its axis (100/2 = 50 mm = 5 cm).
        self.assertAlmostEqual(offs[1][1], -5.0)

    def test_saddled_butt_at_t_junction_is_body_cut(self):
        # A butt at a T-junction with Saddle checked gets the conformal cut.
        lines = [FakeLine((0, 0, 0), (50, 0, 0)),
                 FakeLine((25, 0, 20), (25, 0, 0))]
        cuts = jt.corner_cuts(lines, ['none', 'butt'],
                              saddle_by_line=[False, True])
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]['kind'], 'body')
        self.assertEqual(cuts[0]['tool'], 0)

    def test_none_and_bend_not_cut(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0))]
        self.assertEqual(jt.corner_cuts(lines, ['none', 'none']), [])
        self.assertEqual(jt.corner_cuts(lines, ['bend', 'bend']), [])

    def test_saddled_butt_is_body_cut(self):
        # Two butt members, one marked 'through' and the other 'saddle': the
        # backing-off member gets a boolean notch against the through member's
        # body (the user's "hits the inside of a U/I shape" case).
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        cuts = jt.corner_cuts(lines, ['butt', 'butt'],
                              saddle_by_line=[False, True],
                              through_by_line=[True, False])
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]['member'], 1)   # the backing-off member
        self.assertEqual(cuts[0]['kind'], 'body')
        self.assertEqual(cuts[0]['tool'], 0)     # cut by the through member

    def test_butt_saddle_without_through_flag(self):
        # A saddle on a butt member that is itself the through member produces
        # no cut -- only the member that backs off is notched.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        cuts = jt.corner_cuts(lines, ['butt', 'butt'],
                              saddle_by_line=[True, False],
                              through_by_line=[True, False])
        self.assertEqual(cuts, [])

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

    def test_bend_plan_radius_from_either_leg(self):
        # A bend corner must plan whenever EITHER selected leg carries a die
        # radius.  Reading only the first leg's radius dropped the arc when the
        # first leg had no die but its partner did -- the legs got trimmed by
        # corner_offsets (which uses the pair max) but no arc was built, leaving
        # a gap (the reported "bend won't form" on a chain's first corner).
        lines = [FakeLine((0, 0, 0), (30, 0, 0)),
                 FakeLine((0, 0, 0), (0, 30, 0))]
        plans = jt.bend_plan(lines, ['bend', 'bend'], [0.0, 100.0])
        self.assertEqual(len(plans), 1)
        # The radius used is the pair max (100 mm), matching the trim.
        self.assertAlmostEqual(plans[0]['radius_cm'], 10.0)
        self.assertAlmostEqual(plans[0]['center'][0], 10.0)
        self.assertAlmostEqual(plans[0]['center'][1], 10.0)

    def test_bend_plan_inverse_flips_direction(self):
        # The arc centre is symmetric in the two legs, but the sweep is the sign
        # of the revolve angle, so a corner whose lines were picked in reverse
        # order sweeps the wrong way.  Inverse flips that sign (direction).
        lines = [FakeLine((0, 0, 0), (30, 0, 0)),
                 FakeLine((0, 0, 0), (0, 30, 0))]
        normal = jt.bend_plan(lines, ['bend', 'bend'], [100.0, 100.0])[0]
        inv = jt.bend_plan(lines, ['bend', 'bend'], [100.0, 100.0],
                           inverse_by_line=[True, False])[0]
        # Normal sweeps +1; inverse sweeps -1.  The axis vector is the geometric
        # normal u x v (+Z) in both -- negating a line's direction is a no-op.
        self.assertAlmostEqual(normal['direction'], 1.0)
        self.assertAlmostEqual(inv['direction'], -1.0)
        self.assertAlmostEqual(normal['axis'][2], 1.0)
        self.assertAlmostEqual(inv['axis'][2], 1.0)
        # The centre and turn angle are unchanged -- only the sweep direction.
        self.assertAlmostEqual(inv['center'][0], normal['center'][0])
        self.assertAlmostEqual(inv['theta'], normal['theta'])

    def test_bend_plan_inverse_on_either_leg(self):
        # Inverse is per-line; flagging either leg flips the shared corner.
        lines = [FakeLine((0, 0, 0), (30, 0, 0)),
                 FakeLine((0, 0, 0), (0, 30, 0))]
        a = jt.bend_plan(lines, ['bend', 'bend'], [100.0, 100.0],
                         inverse_by_line=[True, False])[0]['direction']
        b = jt.bend_plan(lines, ['bend', 'bend'], [100.0, 100.0],
                         inverse_by_line=[False, True])[0]['direction']
        self.assertAlmostEqual(a, -1.0)   # both flip the sweep
        self.assertAlmostEqual(b, -1.0)

    def test_bend_offsets_trim_legs(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0))]
        offs = jt.corner_offsets(lines, [_rect(40), _rect(40)],
                                 ['bend', 'bend'], clr_by_line=[100.0, 100.0])
        # Each leg starts at the corner -> offset_start pushed +SB (10 cm) inward.
        self.assertAlmostEqual(offs[0][0], 10.0)
        self.assertAlmostEqual(offs[1][0], 10.0)


class TestExistingMemberContext(unittest.TestCase):
    """A new member joining an ALREADY-EXISTING weldment (context members).

    Phase J: when adding to an existing frame, the design's placed members are
    passed as ``context`` so a selected line's butt/cope/miter/bend end can join
    them directly -- no shadow line / duplicate part.  Context members are never
    edited (their offsets stay (0, 0)); a corner/T-junction against one reports
    the context member with a negative index (~k).
    """

    def _ctx(self, start, end, depth_mm=100.0):
        return {'line': FakeLine(start, end),
                'geom': _rect(depth_mm), 'basis': None}

    def test_butt_against_existing_corner(self):
        # A selected line's END meets an existing member's END (a corner).  The
        # existing member runs through (joint 'none'), so the new line backs off
        # by the existing member's half-depth (100/2 = 5 cm).
        lines = [FakeLine((0, -10, 0), (0, 0, 0))]
        offs = jt.corner_offsets(lines, [_rect(100)], ['butt'],
                                 context=[self._ctx((0, 0, 0), (10, 0, 0))])
        self.assertAlmostEqual(offs[0][1], -5.0)

    def test_cope_against_existing_t_junction(self):
        # A selected line's END lands on the interior of an EXISTING member's
        # run -> a cope body cut whose tool index is negative (~0).
        lines = [FakeLine((25, 0, 20), (25, 0, 0))]
        cuts = jt.corner_cuts(lines, ['cope'],
                              context=[self._ctx((0, 0, 0), (50, 0, 0))])
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]['kind'], 'body')
        self.assertEqual(cuts[0]['tool'], ~0)   # -1: context member 0

    def test_cope_against_existing_trims_to_wall(self):
        # A hollow existing tube (wall 2 mm) saddled by a cope: the tip stops
        # just past the near wall.  Half-depth 5 cm, wall 0.2 cm -> reach -4.8.
        lines = [FakeLine((25, 0, 20), (25, 0, 0))]
        ctx = [{'line': FakeLine((0, 0, 0), (50, 0, 0)),
                'geom': _circle(100.0), 'basis': None}]
        offs = jt.corner_offsets(lines, [_rect(100)], ['cope'],
                                 context=ctx)
        self.assertAlmostEqual(offs[0][1], -4.8)

    def test_miter_against_existing_corner(self):
        # A selected miter end meeting an existing member's END gets the miter
        # extension (the same setback a selected-vs-selected miter corner gives).
        lines = [FakeLine((0, 0, 0), (10, 0, 0))]
        offs = jt.corner_offsets(lines, [_rect(100)], ['miter'],
                                 context=[self._ctx((0, 0, 0), (0, 10, 0))])
        self.assertAlmostEqual(offs[0][0], -5.0)

    def test_miter_cut_against_existing(self):
        # A miter against an existing member emits a plane cut with a negative
        # tool index; the existing member itself is never cut.
        lines = [FakeLine((0, 0, 0), (10, 0, 0))]
        cuts = jt.corner_cuts(lines, ['miter'],
                              context=[self._ctx((0, 0, 0), (0, 10, 0))])
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]['kind'], 'plane')
        self.assertEqual(cuts[0]['member'], 0)
        self.assertEqual(cuts[0]['tool'], ~0)

    def test_context_only_corner_is_ignored(self):
        # A corner formed ONLY by two context members must not be returned --
        # the caller never edits existing parts.
        corners = jt.detect_corners([],
                                    context=[FakeLine((0, 0, 0), (10, 0, 0)),
                                             FakeLine((0, 0, 0), (0, 10, 0))])
        self.assertEqual(corners, [])

    def test_bend_into_existing_member(self):
        # A selected bend leg meeting an existing member's END is planned as a
        # bend; the arc blends into the existing member's direction.  The
        # selected leg must come first in tangent (the builder revolves from it).
        lines = [FakeLine((0, 0, 0), (10, 0, 0))]
        plans = jt.bend_plan(lines, ['bend'], [100.0],
                             context=[self._ctx((0, 0, 0), (0, 10, 0))])
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0]['tangent'][0][0], 0)   # selected leg first

    def test_no_context_matches_old_behaviour(self):
        # Passing no context reproduces the pure selected-lines behaviour.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        a = jt.corner_offsets(lines, [_rect(100), _rect(100)], ['none', 'butt'])
        b = jt.corner_offsets(lines, [_rect(100), _rect(100)], ['none', 'butt'],
                              context=[])
        self.assertEqual(a, b)


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


class TestPositionAnchor(unittest.TestCase):
    """The Position grid anchor threaded into the joint trims (lib/joints)."""

    _UX = (1.0, 0.0, 0.0)
    _UY = (0.0, 1.0, 0.0)

    def test_half_extent_centred_is_symmetric(self):
        # anchor None == anchor (0,0): the historical half-width (50mm -> 5cm).
        g = _rect(100, 100)
        a = jt._half_extent_cm(g, (self._UX, self._UY), self._UX)
        b = jt._half_extent_cm(g, (self._UX, self._UY), self._UX, (0.0, 0.0))
        self.assertAlmostEqual(a, 5.0)
        self.assertAlmostEqual(a, b, places=9)

    def test_half_extent_left_anchor_spans_full_width(self):
        # A section tangent to the line by its LEFT edge (anchor u=-50) spans its
        # whole 100mm width to the right -> half-extent along X doubles to 10cm.
        g = _rect(100, 100)
        e = jt._half_extent_cm(g, (self._UX, self._UY), self._UX, (-50.0, 0.0))
        self.assertAlmostEqual(e, 10.0)

    def test_half_extent_circle_anchor_adds_shift(self):
        # A circle (r=50) tangent to the line by its left edge (anchor u=-50):
        # extent along X = radius + |shift| = 100mm -> 10cm.
        g = _circle(100)
        e = jt._half_extent_cm(g, (self._UX, self._UY), self._UX, (-50.0, 0.0))
        self.assertAlmostEqual(e, 10.0)

    def test_corner_offsets_anchor_none_backward_compatible(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_rect(100), _rect(80)]
        a = jt.corner_offsets(lines, geoms, ['none', 'butt'])
        b = jt.corner_offsets(lines, geoms, ['none', 'butt'],
                              anchor_by_line=None)
        c = jt.corner_offsets(lines, geoms, ['none', 'butt'],
                              anchor_by_line=[None, None])
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_corner_offsets_anchor_grows_butt_trim(self):
        # The through member (line 0, running along X) is placed tangent to the
        # reference line by its BOTTOM edge (anchor v=-50).  The incoming member
        # (line 1) runs along Y and butts into it, so its trim measures line 0's
        # extent ALONG Y -- from the anchor that is the full 100mm height, not
        # the symmetric 50mm half.  The butt trim therefore doubles 5cm -> 10cm.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0))]
        geoms = [_rect(100), _rect(100)]
        bases = [(self._UX, self._UY), (self._UX, self._UY)]
        plain = jt.corner_offsets(lines, geoms, ['none', 'butt'], bases=bases)
        anchored = jt.corner_offsets(lines, geoms, ['none', 'butt'], bases=bases,
                                     anchor_by_line=[(0.0, -50.0), None])
        self.assertAlmostEqual(plain[1][1], -5.0)
        self.assertAlmostEqual(anchored[1][1], -10.0)


class TestBystanderLegAtCorner(unittest.TestCase):
    """A third member's END merely landing on a corner must not disable it.

    The old corner logic required EXACTLY two members at a vertex, so when a
    bystander tube's endpoint coincided with a clean two-member corner the whole
    corner was skipped -- bends stopped detecting and cope/miter trims vanished
    (the reported "mix bends and copes" / "adjacent tube" failures).  Joints are
    now resolved per END against a single partner, so the bystander is simply not
    the partner and the real joint still forms.
    """

    def test_bend_detected_with_bystander(self):
        # Lines 0 (+X) and 1 (+Y) bend at the origin; line 2 (+Z) is a bystander
        # whose start also lands there.  The bend must still be planned.
        lines = [FakeLine((0, 0, 0), (30, 0, 0)),
                 FakeLine((0, 0, 0), (0, 30, 0)),
                 FakeLine((0, 0, 0), (0, 0, 30))]
        plans = jt.bend_plan(lines, ['bend', 'bend', 'none'],
                             [100.0, 100.0, 0.0])
        self.assertEqual(len(plans), 1)
        self.assertAlmostEqual(plans[0]['theta'], math.pi / 2)
        # The arc blends the two bend legs (0 and 1), never the bystander.
        legs = {i for i, _r, _t in plans[0]['tangent']}
        self.assertEqual(legs, {0, 1})

    def test_bend_offsets_trim_with_bystander(self):
        lines = [FakeLine((0, 0, 0), (30, 0, 0)),
                 FakeLine((0, 0, 0), (0, 30, 0)),
                 FakeLine((0, 0, 0), (0, 0, 30))]
        geoms = [_rect(40)] * 3
        offs = jt.corner_offsets(lines, geoms, ['bend', 'bend', 'none'],
                                 clr_by_line=[100.0, 100.0, 0.0])
        # Both bend legs trim back to the tangent point (SB = R*tan45 = 10 cm);
        # the bystander leg is untouched.
        self.assertAlmostEqual(offs[0][0], 10.0)
        self.assertAlmostEqual(offs[1][0], 10.0)
        self.assertEqual(offs[2], (0.0, 0.0))

    def test_miter_cut_with_bystander(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, 0, 0), (0, 10, 0)),
                 FakeLine((0, 0, 0), (0, 0, 10))]
        cuts = jt.corner_cuts(lines, ['miter', 'miter', 'none'])
        # One bisector plane per mitred member; the bystander emits nothing.
        self.assertEqual(len(cuts), 2)
        self.assertEqual({c['member'] for c in cuts}, {0, 1})
        self.assertTrue(all(c['kind'] == 'plane' for c in cuts))

    def test_butt_trim_with_bystander(self):
        # Line 0 (+X, none) runs through; line 1 butts into it at the origin;
        # line 2 is a bystander landing on the same vertex.  The butt still trims.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((0, -10, 0), (0, 0, 0)),
                 FakeLine((0, 0, 0), (0, 0, 10))]
        geoms = [_rect(100), _rect(80), _rect(100)]
        offs = jt.corner_offsets(lines, geoms, ['none', 'butt', 'none'])
        self.assertAlmostEqual(offs[1][1], -5.0)   # backs off to line 0's face
        self.assertAlmostEqual(offs[0][0], -4.0)   # grows to line 1's face
        self.assertEqual(offs[2], (0.0, 0.0))      # bystander untouched


if __name__ == '__main__':
    unittest.main()
