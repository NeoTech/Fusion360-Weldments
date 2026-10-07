"""Unit tests for the pure bend-sequence planner (``lib/bend_sequence.py``).

Run with plain ``python`` (no Fusion)::

    python -m unittest discover -s tests

These validate the chain walk, the developed-length marks, and the clock-angle
convention -- everything that does NOT require the ``adsk`` API.  Segments are
``{'start': (x,y,z), 'end': (x,y,z)}`` dicts in cm and bends are
``{'vertex': (x,y,z), 'clr': mm}`` records -- the shapes the glue command builds
from registry members and ``bend`` joints.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import bend_sequence as bs  # noqa: E402
from lib import registry as reg  # noqa: E402


def _leg(a, b, mid=None):
    return {'start': a, 'end': b, 'mid': mid}


def _bend(vertex, clr=100.0, die_id=None):
    return {'vertex': vertex, 'clr': clr, 'die_id': die_id}


# setback for a 90-deg bend on a 100 mm die, in mm: R*tan(45) = 100
SB90 = 100.0
# arc length of that bend, mm: R*theta = 100 * pi/2
ARC90 = 100.0 * math.pi / 2.0


class TestClock(unittest.TestCase):
    """The single convention function: clockwise seen from behind the machine."""

    def test_same_side_u_clocks_zero(self):
        # two coplanar bends curving the same way (a hairpin): no rotation
        segs = [_leg((0, 0, 0), (10, 0, 0)),
                _leg((10, 0, 0), (10, 10, 0)),
                _leg((10, 10, 0), (0, 10, 0))]
        bends = [_bend((10, 0, 0)), _bend((10, 10, 0))]
        rows = bs.bend_table(segs, bends)['rows']
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(rows[0]['clock_deg'], 0.0, places=6)
        self.assertAlmostEqual(rows[1]['clock_deg'], 0.0, places=6)

    def test_opposite_side_z_clocks_180(self):
        # a staircase: the second bend reverses the curve -> rotate half a turn
        segs = [_leg((0, 0, 0), (10, 0, 0)),
                _leg((10, 0, 0), (10, 10, 0)),
                _leg((10, 10, 0), (20, 10, 0))]
        bends = [_bend((10, 0, 0)), _bend((10, 10, 0))]
        rows = bs.bend_table(segs, bends)['rows']
        self.assertAlmostEqual(rows[1]['clock_deg'], 180.0, places=6)

    def test_rolled_90_is_positive(self):
        # +X -> +Y -> +Z: bend 2 rolls +90 about the shared +Y leg
        segs = [_leg((0, 0, 0), (10, 0, 0)),
                _leg((10, 0, 0), (10, 10, 0)),
                _leg((10, 10, 0), (10, 10, 10))]
        bends = [_bend((10, 0, 0)), _bend((10, 10, 0))]
        rows = bs.bend_table(segs, bends)['rows']
        self.assertAlmostEqual(rows[1]['clock_deg'], 90.0, places=6)

    def test_rolled_minus_90_is_negative(self):
        segs = [_leg((0, 0, 0), (10, 0, 0)),
                _leg((10, 0, 0), (10, 10, 0)),
                _leg((10, 10, 0), (10, 10, -10))]
        bends = [_bend((10, 0, 0)), _bend((10, 10, 0))]
        rows = bs.bend_table(segs, bends)['rows']
        self.assertAlmostEqual(rows[1]['clock_deg'], -90.0, places=6)

    def test_reversed_datum_keeps_clock_handedness(self):
        # Reading the same tube from the far end flips both the feed axis and
        # the bend order, which cancel in atan2 -> the clock is unchanged.
        segs = [_leg((0, 0, 0), (10, 0, 0)),
                _leg((10, 0, 0), (10, 10, 0)),
                _leg((10, 10, 0), (10, 10, 10))]
        bends = [_bend((10, 0, 0)), _bend((10, 10, 0))]
        fwd = bs.bend_table(segs, bends)['rows']
        rev = bs.bend_table(segs, bends, first=2,
                            start_vertex=(10, 10, 10))['rows']
        self.assertAlmostEqual(fwd[1]['clock_deg'], rev[1]['clock_deg'],
                               places=6)


class TestMarks(unittest.TestCase):
    """Developed positions: start/end marks and feed distance."""

    def test_first_bend_starts_at_zero(self):
        # datum leg == setback, so the first tangent lands exactly on the mark 0
        segs = [_leg((0, 0, 0), (10, 0, 0)), _leg((10, 0, 0), (10, 10, 0))]
        bends = [_bend((10, 0, 0))]
        rows = bs.bend_table(segs, bends)['rows']
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]['start_mark_mm'], 0.0, places=6)
        self.assertAlmostEqual(rows[0]['end_mark_mm'], ARC90, places=6)
        self.assertAlmostEqual(rows[0]['feed_mm'], ARC90, places=6)

    def test_second_bend_marks(self):
        segs = [_leg((0, 0, 0), (10, 0, 0)),
                _leg((10, 0, 0), (10, 10, 0)),
                _leg((10, 10, 0), (0, 10, 0))]
        bends = [_bend((10, 0, 0)), _bend((10, 10, 0))]
        rows = bs.bend_table(segs, bends)['rows']
        # bend 2 sharp vertex sits 200 mm along the straight tube; it loses one
        # setback and the length bend 1 already removed (2*sb - arc).
        expect_start = 200.0 - SB90 - (2 * SB90 - ARC90)
        self.assertAlmostEqual(rows[1]['start_mark_mm'], expect_start, places=6)
        self.assertAlmostEqual(rows[1]['end_mark_mm'],
                               expect_start + ARC90, places=6)

    def test_angle_is_geometric_turn(self):
        segs = [_leg((0, 0, 0), (10, 0, 0)), _leg((10, 0, 0), (10, 10, 0))]
        bends = [_bend((10, 0, 0))]
        rows = bs.bend_table(segs, bends)['rows']
        self.assertAlmostEqual(rows[0]['angle_deg'], 90.0, places=6)

    def test_developed_length_subtracts_savings(self):
        segs = [_leg((0, 0, 0), (10, 0, 0)),
                _leg((10, 0, 0), (10, 10, 0)),
                _leg((10, 10, 0), (0, 10, 0))]
        bends = [_bend((10, 0, 0)), _bend((10, 10, 0))]
        r = bs.bend_table(segs, bends)
        # 300 mm straight, two bends each saving (2*sb - arc)
        expect = 300.0 - 2 * (2 * SB90 - ARC90)
        self.assertAlmostEqual(r['developed_mm'], expect, places=6)

    def test_straight_tube_no_rows(self):
        segs = [_leg((0, 0, 0), (10, 0, 0)), _leg((10, 0, 0), (20, 0, 0))]
        r = bs.bend_table(segs, [])
        self.assertEqual(r['rows'], [])
        self.assertAlmostEqual(r['developed_mm'], 200.0, places=6)

    def test_mixed_die_radii(self):
        # a 45-deg bend (clr 100) then another 45-deg bend (clr 200) on one tube
        c, s = math.cos(math.radians(45)), math.sin(math.radians(45))
        v1 = (10, 0, 0)
        v2 = (10 + 10 * c, 10 * s, 0)
        segs = [_leg((0, 0, 0), v1),
                _leg(v1, v2),
                _leg(v2, (v2[0], v2[1] + 10, 0))]
        bends = [_bend(v1, clr=100), _bend(v2, clr=200)]
        rows = bs.bend_table(segs, bends)['rows']
        self.assertAlmostEqual(rows[0]['angle_deg'], 45.0, places=6)
        self.assertAlmostEqual(rows[1]['angle_deg'], 45.0, places=6)
        # each bend's arc uses its own die radius
        self.assertAlmostEqual(rows[0]['end_mark_mm'] - rows[0]['start_mark_mm'],
                               100.0 * math.radians(45), places=6)
        self.assertAlmostEqual(rows[1]['end_mark_mm'] - rows[1]['start_mark_mm'],
                               200.0 * math.radians(45), places=6)


class TestWalk(unittest.TestCase):
    def test_walk_follows_shared_vertices(self):
        segs = [_leg((0, 0, 0), (10, 0, 0)),
                _leg((10, 10, 0), (10, 0, 0)),      # reversed input order
                _leg((10, 10, 0), (0, 10, 0))]
        order, complete = bs.walk(segs, 0)
        self.assertEqual(order, [0, 1, 2])
        self.assertTrue(complete)

    def test_butt_vertex_continues_without_row(self):
        # no bend record at the middle vertex: a butt/miter, walk passes through
        segs = [_leg((0, 0, 0), (10, 0, 0), 1),
                _leg((10, 0, 0), (10, 10, 0), 2),
                _leg((10, 10, 0), (10, 20, 0), 3),
                _leg((10, 20, 0), (0, 20, 0), 4)]
        bends = [_bend((10, 0, 0)), _bend((10, 20, 0))]
        rows = bs.bend_table(segs, bends)['rows']
        self.assertEqual(len(rows), 2)
        self.assertEqual([r['after_mid'] for r in rows], [1, 3])

    def test_branch_vertex_is_incomplete(self):
        segs = [_leg((0, 0, 0), (10, 0, 0)),
                _leg((10, 0, 0), (10, 10, 0)),
                _leg((10, 0, 0), (20, 0, 0))]        # fork at (10,0,0)
        bends = [_bend((10, 0, 0))]
        r = bs.bend_table(segs, bends)
        self.assertFalse(r['complete'])

    def test_partial_chain_is_incomplete(self):
        # a detached leg the walk never reaches -> not the whole tube
        segs = [_leg((0, 0, 0), (10, 0, 0)),
                _leg((10, 0, 0), (10, 10, 0)),
                _leg((50, 50, 50), (60, 50, 50))]
        bends = [_bend((10, 0, 0))]
        r = bs.bend_table(segs, bends)
        self.assertFalse(r['complete'])


class TestOrient(unittest.TestCase):
    def test_reverses_when_vertex_is_the_far_end(self):
        segs = [_leg((0, 0, 0), (10, 0, 0), 1)]
        out = bs.orient(segs, 0, (10, 0, 0))
        self.assertEqual(out[0]['start'], (10, 0, 0))
        self.assertEqual(out[0]['end'], (0, 0, 0))

    def test_keeps_when_vertex_is_the_near_end(self):
        segs = [_leg((0, 0, 0), (10, 0, 0), 1)]
        out = bs.orient(segs, 0, (0, 0, 0))
        self.assertEqual(out[0]['start'], (0, 0, 0))

    def test_raises_on_unrelated_vertex(self):
        segs = [_leg((0, 0, 0), (10, 0, 0), 1)]
        with self.assertRaises(ValueError):
            bs.orient(segs, 0, (5, 5, 5))


class TestDieIds(unittest.TestCase):
    def test_die_id_carried_per_bend(self):
        segs = [_leg((0, 0, 0), (10, 0, 0)),
                _leg((10, 0, 0), (10, 10, 0))]
        bends = [_bend((10, 0, 0), die_id='CHS-CLR-100')]
        rows = bs.bend_table(segs, bends)['rows']
        self.assertEqual(rows[0]['die_id'], 'CHS-CLR-100')


class TestPlanFromRegistry(unittest.TestCase):
    """The pure registry -> table mapping (the command's data half)."""

    def _member(self, mid, a, b, family='CHS'):
        return reg.Member(mid, a, b, designation='21.3', family=family)

    def _bend_joint(self, jid, mids, vertex, clr):
        refs = [{'mid': m, 'role': None} for m in mids]
        return reg.Joint(jid, 'bend', refs, vertex=vertex,
                         params={'clr_mm': clr})

    def test_members_and_joints_produce_the_table(self):
        members = [self._member(1, (0, 0, 0), (10, 0, 0)),
                   self._member(2, (10, 0, 0), (10, 10, 0)),
                   self._member(3, (10, 10, 0), (0, 10, 0))]
        joints = [self._bend_joint(1, [1, 2], (10, 0, 0), 100.0),
                  self._bend_joint(2, [2, 3], (10, 10, 0), 100.0)]
        r = bs.plan(members, joints)
        self.assertEqual(len(r['rows']), 2)
        self.assertAlmostEqual(r['rows'][1]['clock_deg'], 0.0, places=6)
        self.assertTrue(r['complete'])

    def test_other_tubes_joints_are_ignored(self):
        members = [self._member(1, (0, 0, 0), (10, 0, 0)),
                   self._member(2, (10, 0, 0), (10, 10, 0))]
        joints = [self._bend_joint(1, [1, 2], (10, 0, 0), 100.0),
                  self._bend_joint(2, [7, 8], (50, 50, 0), 100.0)]
        r = bs.plan(members, joints)
        self.assertEqual(len(r['rows']), 1)

    def test_die_id_callback_is_used(self):
        members = [self._member(1, (0, 0, 0), (10, 0, 0)),
                   self._member(2, (10, 0, 0), (10, 10, 0))]
        joints = [self._bend_joint(1, [1, 2], (10, 0, 0), 100.0)]
        r = bs.plan(members, joints, die_id=lambda j: 'CHS-CLR-100')
        self.assertEqual(r['rows'][0]['die_id'], 'CHS-CLR-100')

    def test_too_few_members_is_an_error(self):
        r = bs.plan([self._member(1, (0, 0, 0), (10, 0, 0))], [])
        self.assertIn('error', r)

    def test_non_bend_joints_never_emit_rows(self):
        members = [self._member(1, (0, 0, 0), (10, 0, 0)),
                   self._member(2, (10, 0, 0), (20, 0, 0))]
        joints = [reg.Joint(1, 'miter', [{'mid': 1, 'role': 'end'},
                                         {'mid': 2, 'role': 'end'}],
                            vertex=(10, 0, 0))]
        r = bs.plan(members, joints)
        self.assertEqual(r['rows'], [])


class TestTubeMembers(unittest.TestCase):
    """Discovering a whole tube from one leg, by following bend joints."""

    def _member(self, mid, a, b):
        return reg.Member(mid, a, b, designation='21.3', family='CHS')

    def _bend(self, jid, mids, vertex, clr=100.0):
        return reg.Joint(jid, 'bend', [{'mid': m, 'role': None} for m in mids],
                         vertex=vertex, params={'clr_mm': clr})

    def _l_tube(self):
        return ([self._member(1, (0, 0, 0), (10, 0, 0)),
                 self._member(2, (10, 0, 0), (10, 10, 0))],
                [self._bend(1, [1, 2], (10, 0, 0))])

    def test_one_leg_finds_the_whole_tube(self):
        members, joints = self._l_tube()
        self.assertEqual([m.mid for m in bs.tube_members(members, joints, 1)],
                         [1, 2])
        self.assertEqual([m.mid for m in bs.tube_members(members, joints, 2)],
                         [1, 2])

    def test_walks_through_a_middle_leg(self):
        members = [self._member(1, (0, 0, 0), (10, 0, 0)),
                   self._member(2, (10, 0, 0), (10, 10, 0)),
                   self._member(3, (10, 10, 0), (0, 10, 0))]
        joints = [self._bend(1, [1, 2], (10, 0, 0)),
                  self._bend(2, [2, 3], (10, 10, 0))]
        self.assertEqual([m.mid for m in bs.tube_members(members, joints, 2)],
                         [1, 2, 3])

    def test_frame_corner_is_not_followed(self):
        # A butt/miter joins two DIFFERENT tubes; only bend joints extend a tube.
        members = [self._member(1, (0, 0, 0), (10, 0, 0)),
                   self._member(2, (10, 0, 0), (10, 10, 0)),
                   self._member(3, (10, 0, 0), (20, 0, 0))]
        joints = [self._bend(1, [1, 2], (10, 0, 0)),
                  reg.Joint(2, 'butt', [{'mid': 1, 'role': 'end'},
                                        {'mid': 3, 'role': 'start'}],
                            vertex=(10, 0, 0))]
        self.assertEqual([m.mid for m in bs.tube_members(members, joints, 1)],
                         [1, 2])

    def test_unbent_member_yields_only_itself(self):
        members = [self._member(1, (0, 0, 0), (10, 0, 0)),
                   self._member(2, (10, 0, 0), (20, 0, 0))]
        self.assertEqual([m.mid for m in bs.tube_members(members, [], 1)], [1])


class TestFreeEnds(unittest.TestCase):
    def test_two_ends_of_an_l(self):
        segs = [_leg((0, 0, 0), (10, 0, 0), 1), _leg((10, 0, 0), (10, 10, 0), 2)]
        self.assertEqual(sorted(bs.free_ends(segs), key=lambda e: e[0]),
                         [(0, (0, 0, 0)), (1, (10, 10, 0))])

    def test_closed_loop_has_none(self):
        segs = [_leg((0, 0, 0), (10, 0, 0), 1), _leg((10, 0, 0), (10, 10, 0), 2),
                _leg((10, 10, 0), (0, 0, 0), 3)]
        self.assertEqual(bs.free_ends(segs), [])


class TestPlanFromPick(unittest.TestCase):
    """The single-pick entry point the command calls."""

    def _member(self, mid, a, b):
        return reg.Member(mid, a, b, designation='21.3', family='CHS')

    def _bend(self, jid, mids, vertex, clr=100.0):
        return reg.Joint(jid, 'bend', [{'mid': m, 'role': None} for m in mids],
                         vertex=vertex, params={'clr_mm': clr})

    def setUp(self):
        self.members = [self._member(1, (0, 0, 0), (10, 0, 0)),
                        self._member(2, (10, 0, 0), (10, 10, 0))]
        self.joints = [self._bend(1, [1, 2], (10, 0, 0))]

    def test_click_near_start_reads_from_there(self):
        r = bs.plan_from_pick(self.members, self.joints, 1, click=(0, 0, 0))
        self.assertEqual(r['rows'][0]['after_mid'], 1)
        self.assertEqual(r['rows'][0]['before_mid'], 2)

    def test_click_near_far_end_reads_backwards(self):
        r = bs.plan_from_pick(self.members, self.joints, 1, click=(10, 10, 0))
        self.assertEqual(r['rows'][0]['after_mid'], 2)
        self.assertEqual(r['rows'][0]['before_mid'], 1)

    def test_reverse_flips_the_datum(self):
        fwd = bs.plan_from_pick(self.members, self.joints, 1, click=(0, 0, 0))
        rev = bs.plan_from_pick(self.members, self.joints, 1, click=(0, 0, 0),
                                reverse=True)
        self.assertEqual(fwd['rows'][0]['after_mid'], 1)
        self.assertEqual(rev['rows'][0]['after_mid'], 2)
        self.assertAlmostEqual(fwd['developed_mm'], rev['developed_mm'])

    def test_picking_the_middle_leg_still_works(self):
        members = [self._member(1, (0, 0, 0), (10, 0, 0)),
                   self._member(2, (10, 0, 0), (10, 10, 0)),
                   self._member(3, (10, 10, 0), (0, 10, 0))]
        joints = [self._bend(1, [1, 2], (10, 0, 0)),
                  self._bend(2, [2, 3], (10, 10, 0))]
        r = bs.plan_from_pick(members, joints, 2, click=(0, 10, 0))
        self.assertEqual(len(r['rows']), 2)
        self.assertEqual(r['rows'][0]['after_mid'], 3)
        self.assertTrue(r['complete'])

    def test_no_bend_joints_is_an_error(self):
        r = bs.plan_from_pick([self._member(1, (0, 0, 0), (10, 0, 0))], [], 1)
        self.assertIn('error', r)

    def test_marks_match_the_explicit_datum(self):
        picked = bs.plan_from_pick(self.members, self.joints, 1, click=(0, 0, 0))
        direct = bs.plan(self.members, self.joints, first=0,
                         start_vertex=(0, 0, 0))
        self.assertEqual(picked['rows'], direct['rows'])


if __name__ == '__main__':
    unittest.main()
