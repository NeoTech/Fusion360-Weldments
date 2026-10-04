"""Unit tests for the refactor's joint_spec / bend_path (``lib/joints.py``).

These cover the scenarios the pre-refactor suite NEVER tested -- joints against
existing (context) members, channel (UPN-like) sections, cope at a corner vs a
T vs an angled T, and the bounding-box clamp that makes material outside a
joint box untouchable.  Run with plain ``python`` (no Fusion)::

    python -m unittest discover -s tests
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib import joints as jt  # noqa: E402
from adsk_stub import FakeLine  # noqa: E402


def _tube(depth_mm, width_mm, wall_mm):
    """Hollow rectangular section (outer + inner loops)."""
    h, b, t = depth_mm, width_mm, wall_mm
    return {'kind': 'polygons',
            'loops': [[(-b / 2, -h / 2), (b / 2, -h / 2),
                       (b / 2, h / 2), (-b / 2, h / 2)],
                      [(-(b / 2 - t), -(h / 2 - t)), (b / 2 - t, -(h / 2 - t)),
                       (b / 2 - t, h / 2 - t), (-(b / 2 - t), h / 2 - t)]],
            'fillets': [[], []]}


def _circle_tube(od_mm, wall_mm):
    return {'kind': 'circles', 'radii': [od_mm / 2.0, od_mm / 2.0 - wall_mm]}


def _channel(depth_mm, width_mm, wall_mm):
    """Open C-section (single loop, like a simplified UPN)."""
    h, b, t = depth_mm, width_mm, wall_mm
    return {'kind': 'polygons',
            'loops': [[(-b / 2, -h / 2), (b / 2, -h / 2), (b / 2, -h / 2 + t),
                       (-b / 2 + t, -h / 2 + t), (-b / 2 + t, h / 2 - t),
                       (b / 2, h / 2 - t), (b / 2, h / 2), (-b / 2, h / 2)]],
            'fillets': [[]]}


def _ctx(line, geom):
    return {'line': line, 'geom': geom, 'basis': None, 'body': object()}


def _pt_dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _box_reaches(box, point):
    """True when ``point`` lies inside the (possibly rotated) joint box."""
    c = box['center']
    d, e1, e2 = box['axes']
    v = (point[0] - c[0], point[1] - c[1], point[2] - c[2])
    proj = (abs(jt._dot(v, d)), abs(jt._dot(v, e1)), abs(jt._dot(v, e2)))
    return all(p <= h + 1e-9 for p, h in zip(proj, box['half']))


class TestJointSpecKinds(unittest.TestCase):
    """One test per joint kind: the occurrence type and its cutter."""

    def setUp(self):
        # L-corner: line 0 along +X ending at (10,0,0); line 1 from there +Y.
        self.lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                      FakeLine((10, 0, 0), (10, 10, 0))]
        self.geom = _tube(20.0, 20.0, 2.0)     # SHS 20x20x2
        self.geoms = [self.geom, self.geom]

    def _spec(self, joints, **kw):
        return jt.joint_spec(self.lines, self.geoms, joints, **kw)

    def test_none_produces_no_occurrences(self):
        spec = self._spec(['none', 'none'])
        self.assertEqual(spec['occs'], [])
        self.assertEqual(spec['offsets'], [(0.0, 0.0), (0.0, 0.0)])

    def test_miter_corner_plane_cutter(self):
        spec = self._spec(['miter', 'miter'])
        miters = [o for o in spec['occs'] if o['kind'] == 'miter']
        self.assertEqual(len(miters), 2)
        for o in miters:
            self.assertEqual(o['vertex'], (10.0, 0.0, 0.0))
            cut = o['cutter']
            self.assertEqual(cut['type'], 'plane')
            self.assertEqual(cut['point'], (10.0, 0.0, 0.0))
            # Bisector normal is perpendicular to both legs' plane diagonal.
            n = cut['normal']
            self.assertAlmostEqual(sum(c * c for c in n), 1.0)
            # Setback > 0 and the box covers the wedge past the vertex.
            self.assertGreater(o['setback'], 0.0)
            self.assertTrue(_box_reaches(cut['region'], (10.0, 0.0, 0.0)))

    def test_butt_corner_no_cutter(self):
        # line 1 STARTS at the corner (its butt end backs off); line 0 ends
        # there and runs through (growing on its end offset).
        spec = self._spec(['none', 'butt'])
        butts = [o for o in spec['occs'] if o['kind'] == 'butt']
        self.assertEqual(len(butts), 1)
        self.assertIsNone(butts[0]['cutter'])
        offs = spec['offsets']
        self.assertNotEqual(offs[1][0], 0.0)     # line 1's start trimmed
        self.assertGreater(offs[0][1], 0.0)      # through member grows

    def test_butt_saddle_corner_body_cutter(self):
        # The butt end at the corner is line 1's START -> saddle that end.
        spec = self._spec(['none', 'butt'],
                          saddle_by_line=[False, (True, False)])
        sad = [o for o in spec['occs'] if o['kind'] == 'butt_saddle']
        self.assertEqual(len(sad), 1)
        cut = sad[0]['cutter']
        self.assertEqual(cut['type'], 'body')
        self.assertEqual(cut['tool'], 0)   # against the through member

    def test_cope_corner_is_cope_end(self):
        spec = self._spec(['none', 'cope'])
        cope = [o for o in spec['occs'] if o['kind'] == 'cope_end']
        self.assertEqual(len(cope), 1)
        self.assertEqual(cope[0]['cutter']['type'], 'body')

    def test_bend_corner_carries_path(self):
        spec = self._spec(['bend', 'bend'], clr_by_line=[100.0, 100.0])
        bends = [o for o in spec['occs'] if o['kind'] == 'bend']
        self.assertEqual(len(bends), 2)     # one per selected leg
        for o in bends:
            self.assertIsNone(o['cutter'])
            path = o['path']
            self.assertIsNotNone(path)
            self.assertAlmostEqual(path['theta'], math.pi / 2.0)
            self.assertAlmostEqual(path['radius_cm'], 10.0)
            # Path: tangent points sb = R*tan(45) = R back from the vertex.
            t1, t2 = path['t1'], path['t2']
            V = o['vertex']
            self.assertAlmostEqual(_pt_dist(t1, V), 10.0)   # sb = R*tan(45)
            self.assertAlmostEqual(_pt_dist(t2, V), 10.0)
            # The arc endpoints are the tangent points (tangent continuity):
            # |T1 - C| = |T2 - C| = R.
            self.assertAlmostEqual(_pt_dist(t1, path['center']), 10.0)
            self.assertAlmostEqual(_pt_dist(t2, path['center']), 10.0)

    def test_bend_without_radius_has_no_path(self):
        spec = self._spec(['bend', 'bend'], clr_by_line=[0.0, 0.0])
        bends = [o for o in spec['occs'] if o['kind'] == 'bend']
        self.assertTrue(all(o['path'] is None for o in bends))


class TestJointSpecTJunctions(unittest.TestCase):
    """Cope at a T (perpendicular), at an angled T, and plain butt at a T."""

    def setUp(self):
        self.geom = _tube(20.0, 20.0, 2.0)
        # Tool runs along +X; member's END lands on its interior.
        self.tool = FakeLine((0, 0, 0), (50, 0, 0))
        self.lines = [FakeLine((25, 0, 20), (25, 0, 0)), self.tool]

    def test_cope_perpendicular_t(self):
        spec = jt.joint_spec(self.lines, [self.geom, self.geom],
                             ['cope', 'none'])
        t = [o for o in spec['occs'] if o['kind'] == 'cope_t']
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]['cutter']['type'], 'body')
        self.assertEqual(t[0]['cutter']['tool'], 1)
        self.assertEqual(t[0]['vertex'], (25.0, 0.0, 0.0))

    def test_cope_angled_t(self):
        # Member comes in at 45 deg onto the tool's interior.
        lines = [FakeLine((25 + 20 * math.cos(math.pi / 4), 0.0,
                           20 * math.sin(math.pi / 4)), (25, 0, 0)),
                 self.tool]
        spec = jt.joint_spec(lines, [self.geom, self.geom], ['cope', 'none'])
        kinds = {o['kind'] for o in spec['occs']}
        self.assertIn('cope_angle', kinds)
        self.assertNotIn('cope_t', kinds)

    def test_plain_butt_t_no_cutter(self):
        spec = jt.joint_spec(self.lines, [self.geom, self.geom],
                             ['butt', 'none'])
        butts = [o for o in spec['occs'] if o['kind'] == 'butt']
        self.assertEqual(len(butts), 1)
        self.assertIsNone(butts[0]['cutter'])
        # Tip stops at the tool's near face: offset = -half extent.
        self.assertAlmostEqual(butts[0]['vertex'], (25.0, 0.0, 0.0))


class TestJointSpecAgainstContext(unittest.TestCase):
    """The scenarios the old suite never covered: joints vs EXISTING members."""

    def setUp(self):
        self.geom = _tube(20.0, 20.0, 2.0)
        self.chs = _circle_tube(20.0, 1.0)
        # Existing member: runs along +X from origin, 50 cm long.
        self.context = [_ctx(FakeLine((0, 0, 0), (50, 0, 0)), self.geom)]

    def test_miter_against_context(self):
        # New line's END meets the existing member's START at the origin at a
        # right angle (a collinear meeting would be a straight run, no joint).
        lines = [FakeLine((0, 10, 0), (0, 0, 0))]
        spec = jt.joint_spec(lines, [self.geom], ['miter'], context=self.context)
        miters = [o for o in spec['occs'] if o['kind'] == 'miter']
        self.assertEqual(len(miters), 1)
        o = miters[0]
        self.assertEqual(o['partner'][0], ~0)      # negative = context member
        self.assertEqual(o['cutter']['type'], 'plane')
        # Only the selected member is cut; the context member has no occurrence.
        self.assertEqual(o['member'], 0)
        self.assertTrue(all(oc['member'] >= 0 for oc in spec['occs']))

    def test_cope_into_context_end(self):
        # Cope over the OPEN END of an existing tube (corner, not T).
        lines = [FakeLine((0, 0, 20), (0, 0, 0))]   # ends at context start
        spec = jt.joint_spec(lines, [self.geom], ['cope'], context=self.context)
        cope = [o for o in spec['occs'] if o['kind'] == 'cope_end']
        self.assertEqual(len(cope), 1)
        self.assertEqual(cope[0]['cutter']['tool'], ~0)
        self.assertEqual(cope[0]['cutter']['type'], 'body')

    def test_butt_against_context_runs_through(self):
        # New member's END butts the existing member's END: the existing member
        # runs through (never edited); the new one backs off.
        lines = [FakeLine((0, 10, 0), (0, 0, 0))]
        spec = jt.joint_spec(lines, [self.geom], ['butt'], context=self.context)
        butts = [o for o in spec['occs'] if o['kind'] == 'butt']
        self.assertEqual(len(butts), 1)
        self.assertIsNone(butts[0]['cutter'])
        self.assertLess(spec['offsets'][0][1], 0.0)  # trimmed back

    def test_cope_t_against_context(self):
        # New member's END lands mid-run of an existing member.
        lines = [FakeLine((25, 0, 20), (25, 0, 0))]
        spec = jt.joint_spec(lines, [self.geom], ['cope'], context=self.context)
        t = [o for o in spec['occs'] if o['kind'] == 'cope_t']
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]['cutter']['tool'], ~0)

    def test_bend_against_context(self):
        # New leg bends into the existing member's direction at a shared vertex.
        lines = [FakeLine((0, 10, 0), (0, 0, 0))]
        spec = jt.joint_spec(lines, [self.geom], ['bend'],
                             clr_by_line=[100.0], context=self.context)
        bends = [o for o in spec['occs'] if o['kind'] == 'bend']
        self.assertEqual(len(bends), 1)
        path = bends[0]['path']
        self.assertIsNotNone(path)
        self.assertAlmostEqual(path['theta'], math.pi / 2.0)
        # Tangent points sit sb = R along each leg from the vertex.
        self.assertAlmostEqual(_pt_dist(path['t1'], (0, 0, 0)), 10.0)


class TestJointSpecChannel(unittest.TestCase):
    """UPN-like open sections: extents must come from the real outline."""

    def test_channel_miter_uses_section_extent(self):
        ch = _channel(80.0, 45.0, 6.0)
        lines = [FakeLine((0, 0, 0), (20, 0, 0)),
                 FakeLine((20, 0, 0), (20, 20, 0))]
        spec = jt.joint_spec(lines, [ch, ch], ['miter', 'miter'])
        miters = [o for o in spec['occs'] if o['kind'] == 'miter']
        self.assertEqual(len(miters), 2)
        for o in miters:
            # Setback reflects the channel's ~80 mm depth, not a tube guess:
            # h/tan(45) with h ~ 4 cm half-depth -> ~4 cm (bounded sanity).
            self.assertGreater(o['setback'], 1.0)
            self.assertLess(o['setback'], 8.0)
            # The joint box must cover the whole channel half-diagonal.
            diag = math.hypot(4.0, 2.25)     # cm: depth/2, width/2
            box = o['cutter']['region']
            self.assertGreaterEqual(box['half'][1], diag)

    def test_channel_cope_t_cutter_bounded(self):
        ch = _channel(80.0, 45.0, 6.0)
        tube = _tube(20.0, 20.0, 2.0)
        lines = [FakeLine((25, 0, 20), (25, 0, 0)),
                 FakeLine((0, 0, 0), (50, 0, 0))]
        spec = jt.joint_spec(lines, [ch, tube], ['cope', 'none'])
        t = [o for o in spec['occs'] if o['kind'] == 'cope_t']
        self.assertEqual(len(t), 1)
        box = t[0]['cutter']['region']
        # Box perpendicular extent covers the TOOL's 2 cm half-width (the
        # incoming channel's 4 cm half-depth is along the cut direction).
        self.assertGreaterEqual(min(box['half'][1], box['half'][2]), 2.0)


class TestJointBoxClamp(unittest.TestCase):
    """The bounding-box safety: a joint box never reaches past a leg midpoint."""

    def test_short_leg_clamps_the_box(self):
        # A 2 cm stub meeting a 100 cm run at a miter corner.
        lines = [FakeLine((0, 0, 0), (2, 0, 0)),
                 FakeLine((2, 0, 0), (2, 100, 0))]
        geom = _tube(20.0, 20.0, 2.0)
        spec = jt.joint_spec(lines, [geom, geom], ['miter', 'miter'])
        for o in spec['occs']:
            box = o['cutter']['region']
            # Axial half-extent <= half of the SHORT leg (1 cm) * safety.
            self.assertLessEqual(box['half'][0],
                                 0.5 * 2.0 * jt._BOX_SAFETY + 1e-9)
            # The far end of the long leg is untouchable.
            self.assertFalse(_box_reaches(box, (2.0, 99.0, 0.0)))

    def test_box_covers_the_waste(self):
        # Normal corner: the vertex itself is inside every joint box.
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((10, 0, 0), (10, 10, 0))]
        geom = _tube(20.0, 20.0, 2.0)
        spec = jt.joint_spec(lines, [geom, geom], ['miter', 'miter'])
        self.assertTrue(spec['occs'])
        for o in spec['occs']:
            self.assertTrue(_box_reaches(o['cutter']['region'], o['vertex']))


class TestBendPath(unittest.TestCase):
    """bend_path: tangent-arc centerline geometry, direction inherent."""

    def test_right_angle_geometry(self):
        u, v = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
        p = jt.bend_path((0, 0, 0), u, v, 10.0)
        self.assertAlmostEqual(p['theta'], math.pi / 2.0)
        self.assertAlmostEqual(p['radius_cm'], 10.0)
        # Center on the 45 diagonal at R/cos(45) = R*sqrt(2).
        self.assertAlmostEqual(p['center'][0], 10.0)
        self.assertAlmostEqual(p['center'][1], 10.0)
        # Tangent points R back along each leg.
        self.assertAlmostEqual(_pt_dist(p['t1'], (10.0, 0.0, 0.0)), 0.0)
        self.assertAlmostEqual(_pt_dist(p['t2'], (0.0, 10.0, 0.0)), 0.0)
        # The arc is tangent to both legs: its endpoints are the tangent points
        # and the radius vector at each is perpendicular to the leg.
        r1 = jt._norm(jt._sub(p['t1'], p['center']))
        self.assertAlmostEqual(abs(jt._dot(r1, u)), 0.0)
        r2 = jt._norm(jt._sub(p['t2'], p['center']))
        self.assertAlmostEqual(abs(jt._dot(r2, v)), 0.0)
        # entry/exit carry the tangent points with their leg directions.
        self.assertEqual(p['entry'], (p['t1'], u))
        self.assertEqual(p['exit'], (p['t2'], v))
        # Axis = u x v = +Z.
        self.assertAlmostEqual(p['axis'][2], 1.0)

    def test_obtuse_bend(self):
        # Legs 135 deg apart (45 deg turn).
        u = (1.0, 0.0, 0.0)
        v = (math.cos(math.radians(135)), math.sin(math.radians(135)), 0.0)
        p = jt.bend_path((0, 0, 0), u, v, 5.0)
        self.assertAlmostEqual(p['theta'], math.radians(45))
        sb = 5.0 * math.tan(math.radians(22.5))
        self.assertAlmostEqual(_pt_dist(p['t1'], (sb, 0.0, 0.0)), 0.0)

    def test_degenerate_returns_none(self):
        u = (1.0, 0.0, 0.0)
        self.assertIsNone(jt.bend_path((0, 0, 0), u, (-1.0, 0.0, 0.0), 10.0))
        self.assertIsNone(jt.bend_path((0, 0, 0), u, u, 10.0))
        self.assertIsNone(jt.bend_path((0, 0, 0), u, (0.0, 1.0, 0.0), 0.0))

    def test_path_entry_exit_chain(self):
        u, v = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
        p = jt.bend_path((0, 0, 0), u, v, 10.0)
        # A chain sweep runs: leg0 far end -> t1, arc t1 -> t2, t2 -> leg1 far
        # end.  entry/exit give the arc endpoints with their tangent dirs.
        self.assertEqual(p['entry'][0], p['t1'])
        self.assertEqual(p['entry'][1], u)
        self.assertEqual(p['exit'][0], p['t2'])
        self.assertEqual(p['exit'][1], v)
        # The arc chord subtends theta at the center.
        chord = _pt_dist(p['t1'], p['t2'])
        self.assertAlmostEqual(chord,
                               2 * 10.0 * math.sin(p['theta'] / 2.0))

    def test_reversing_legs_reverses_path(self):
        u, v = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)
        p1 = jt.bend_path((0, 0, 0), u, v, 10.0)
        p2 = jt.bend_path((0, 0, 0), v, u, 10.0)
        self.assertAlmostEqual(_pt_dist(p1['t1'], p2['t2']), 0.0)
        self.assertAlmostEqual(_pt_dist(p1['t2'], p2['t1']), 0.0)
        # Axis flips sign: the sweep direction is inherent in the ordering.
        for a, b in zip(p1['axis'], p2['axis']):
            self.assertAlmostEqual(a, -b)


class TestJointSpecOffsetsParity(unittest.TestCase):
    """joint_spec['offsets'] must equal corner_offsets for the same inputs."""

    def test_offsets_match_corner_offsets(self):
        lines = [FakeLine((0, 0, 0), (10, 0, 0)),
                 FakeLine((10, 0, 0), (10, 10, 0))]
        geom = _tube(20.0, 20.0, 2.0)
        for joints, kw in [
            (['none', 'none'], {}),
            (['none', 'butt'], {}),
            (['miter', 'miter'], {}),
            (['none', 'cope'], {}),
            (['bend', 'bend'], {'clr_by_line': [100.0, 100.0]}),
            (['none', 'butt'], {'saddle_by_line': [False, (False, True)]}),
        ]:
            spec = jt.joint_spec(lines, [geom, geom], joints, **kw)
            base = jt.corner_offsets(lines, [geom, geom], joints, **kw)
            self.assertEqual(spec['offsets'], base, f"joints={joints} kw={kw}")


if __name__ == '__main__':
    unittest.main()
