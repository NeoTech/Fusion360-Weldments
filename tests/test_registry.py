"""Unit tests for the pure-Python member/joint registry (``lib/registry.py``).

Run with plain ``python`` (no Fusion):

    python -m unittest discover -s tests

These validate record modelling, geometry lookup (re-run pickup by centreline
proximity, joint-by-member), and JSON round-trip -- the data backbone the Phase-4
toolbox tools and the registry-as-BOM panel build on.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import registry as reg  # noqa: E402


GEOM = {'kind': 'circles', 'radii': [10.0, 9.0]}


class TestGeometryHelpers(unittest.TestCase):
    def test_midpoint(self):
        self.assertEqual(reg.midpoint((0, 0, 0), (10, 0, 0)), (5, 0, 0))

    def test_closest_on_segment_clamps(self):
        cp, t = reg.closest_point_on_segment((20, 5, 0), (0, 0, 0), (10, 0, 0))
        self.assertEqual(cp, (10, 0, 0))     # past the end -> clamp to b
        self.assertEqual(t, 1.0)
        cp, t = reg.closest_point_on_segment((-3, 0, 0), (0, 0, 0), (10, 0, 0))
        self.assertEqual(cp, (0, 0, 0))      # before the start -> clamp to a
        self.assertEqual(t, 0.0)

    def test_closest_on_segment_perpendicular(self):
        cp, _t = reg.closest_point_on_segment((5, 7, 0), (0, 0, 0), (10, 0, 0))
        self.assertAlmostEqual(cp[0], 5.0)
        self.assertAlmostEqual(cp[1], 0.0)

    def test_degenerate_segment(self):
        cp, t = reg.closest_point_on_segment((3, 3, 0), (1, 1, 1), (1, 1, 1))
        self.assertEqual(cp, (1, 1, 1))
        self.assertEqual(t, 0.0)


class TestMemberRecords(unittest.TestCase):
    def test_ids_increment(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0), geom=GEOM)
        b = r.add_member((0, 0, 0), (0, 10, 0), geom=GEOM)
        self.assertEqual(a.mid, 1)
        self.assertEqual(b.mid, 2)

    def test_direction(self):
        m = reg.Member(1, (0, 0, 0), (0, 0, 10))
        self.assertEqual(m.direction, (0.0, 0.0, 1.0))

    def test_member_lookup(self):
        r = reg.Registry()
        m = r.add_member((0, 0, 0), (10, 0, 0))
        self.assertIs(r.member(m.mid), m)
        self.assertIsNone(r.member(999))

    def test_member_by_feature(self):
        r = reg.Registry()
        m = r.add_member((0, 0, 0), (10, 0, 0), feature=12, body_index=0)
        self.assertIs(r.member_by_feature(12, 0), m)
        self.assertIsNone(r.member_by_feature(12, 5))
        self.assertIs(r.member_by_feature(12), m)   # body_index optional


class TestUpsertPickup(unittest.TestCase):
    """Re-run pickup: rebuilding the same tube edits, not duplicates."""

    def test_same_line_updates_in_place(self):
        r = reg.Registry()
        m = r.add_member((0, 0, 0), (10, 0, 0), geom=GEOM, designation='20x1')
        again = r.upsert_member((0, 0, 0), (10, 0, 0), feature=99)
        self.assertIs(again, m)                 # same object, edited
        self.assertEqual(len(r.members), 1)
        self.assertEqual(m.feature, 99)         # field updated
        self.assertEqual(m.designation, '20x1')  # untouched field kept

    def test_reversed_line_matches(self):
        # A member drawn start<->end swapped is the same tube (anti-parallel).
        r = reg.Registry()
        m = r.add_member((0, 0, 0), (10, 0, 0))
        again = r.upsert_member((10, 0, 0), (0, 0, 0))
        self.assertIs(again, m)
        self.assertEqual(len(r.members), 1)

    def test_different_line_adds(self):
        r = reg.Registry()
        r.add_member((0, 0, 0), (10, 0, 0))
        n = r.upsert_member((0, 0, 0), (0, 10, 0))
        self.assertEqual(len(r.members), 2)
        self.assertEqual(n.mid, 2)

    def test_parallel_but_far_adds(self):
        r = reg.Registry()
        r.add_member((0, 0, 0), (10, 0, 0))
        n = r.upsert_member((0, 50, 0), (10, 50, 0))   # same dir, offset
        self.assertEqual(len(r.members), 2)
        self.assertEqual(n.mid, 2)


class TestMemberNearPoint(unittest.TestCase):
    def test_finds_closest(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        r.add_member((0, 0, 0), (0, 10, 0))
        self.assertIs(r.member_near_point((5, 0.01, 0)), a)

    def test_none_when_far(self):
        r = reg.Registry()
        r.add_member((0, 0, 0), (10, 0, 0))
        self.assertIsNone(r.member_near_point((5, 5, 0), tol=0.05))


class TestJointRecords(unittest.TestCase):
    def test_refs_and_subject(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        b = r.add_member((5, 0, 20), (5, 0, 0))
        j = r.add_joint('cope', [{'mid': b.mid, 'role': 'end'},
                                {'mid': a.mid, 'role': None}],
                        vertex=(5, 0, 0), params={'depth_mm': 0.0})
        self.assertEqual(j.subject(), b.mid)
        self.assertEqual(j.member_ids(), [b.mid, a.mid])
        self.assertTrue(j.touches(a.mid))

    def test_joints_for_member(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        b = r.add_member((0, 0, 0), (0, 10, 0))
        j1 = r.add_joint('miter', [{'mid': a.mid, 'role': 'end'},
                                   {'mid': b.mid, 'role': 'start'}])
        j2 = r.add_joint('butt', [{'mid': b.mid, 'role': 'end'}])
        self.assertEqual({j.jid for j in r.joints_for_member(a.mid)}, {j1.jid})
        self.assertEqual({j.jid for j in r.joints_for_member(b.mid)},
                         {j1.jid, j2.jid})


class TestRemoveMember(unittest.TestCase):
    def test_prunes_joint_refs(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        b = r.add_member((0, 0, 0), (0, 10, 0))
        r.add_joint('miter', [{'mid': a.mid, 'role': 'end'},
                              {'mid': b.mid, 'role': 'start'}])
        r.remove_member(a.mid)
        self.assertEqual(len(r.members), 1)
        # The joint survives with only b's ref (a's ref pruned).
        self.assertEqual(len(r.joints), 1)
        self.assertEqual(r.joints[0].member_ids(), [b.mid])

    def test_drops_empty_joint(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        r.add_joint('butt', [{'mid': a.mid, 'role': 'end'}])
        r.remove_member(a.mid)
        self.assertEqual(r.joints, [])   # joint had only that ref -> dropped


class TestEditInPlace(unittest.TestCase):
    """The BOM panel's write path (Phase 4b): edit a record without re-detect."""

    def test_set_member_field(self):
        r = reg.Registry()
        m = r.add_member((0, 0, 0), (10, 0, 0), designation='20x1')
        self.assertTrue(r.set_member(m.mid, designation='25x2', name='Tube-A'))
        self.assertEqual(r.member(m.mid).designation, '25x2')
        self.assertEqual(r.member(m.mid).name, 'Tube-A')

    def test_set_member_ignores_geometry(self):
        # The edit path must not let a panel edit corrupt the centreline.
        r = reg.Registry()
        m = r.add_member((0, 0, 0), (10, 0, 0))
        r.set_member(m.mid, start=(99, 99, 99), feature=7)
        self.assertEqual(r.member(m.mid).start, (0, 0, 0))
        self.assertIsNone(r.member(m.mid).feature)

    def test_set_member_unknown_mid(self):
        r = reg.Registry()
        self.assertFalse(r.set_member(999, name='x'))

    def test_set_joint_kind(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        j = r.add_joint('cope', [{'mid': a.mid, 'role': 'end'}])
        self.assertTrue(r.set_joint_kind(j.jid, 'butt'))
        self.assertEqual(r.joint(j.jid).kind, 'butt')

    def test_set_joint_param(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        j = r.add_joint('cope', [{'mid': a.mid, 'role': 'end'}],
                        params={'depth_mm': 0.0})
        self.assertTrue(r.set_joint_param(j.jid, 'depth_mm', 5.0))
        self.assertEqual(r.joint(j.jid).params['depth_mm'], 5.0)

    def test_edits_survive_round_trip(self):
        r = reg.Registry()
        m = r.add_member((0, 0, 0), (10, 0, 0), designation='20x1')
        r.set_member(m.mid, designation='25x2')
        r2 = reg.Registry.from_json(r.to_json())
        self.assertEqual(r2.member(m.mid).designation, '25x2')


class TestPlacementFields(unittest.TestCase):
    """A1 (registry truth): members carry everything a rebuild-from-record needs."""

    def test_defaults(self):
        m = reg.Member(1, (0, 0, 0), (10, 0, 0))
        self.assertEqual(m.angle_rad, 0.0)
        self.assertIsNone(m.ref)
        self.assertIsNone(m.anchor)
        self.assertEqual(m.offset_start, 0.0)
        self.assertEqual(m.offset_end, 0.0)

    def test_round_trip(self):
        r = reg.Registry()
        r.add_member((0, 0, 0), (10, 0, 0), geom=GEOM,
                     basis=[[0, 1, 0], [0, 0, 1]], designation='20x1',
                     family='CHS', angle_rad=0.5, ref=(0, 0, 1),
                     anchor=(5.0, -2.0), offset_start=-1.0, offset_end=2.0)
        r2 = reg.Registry.from_json(r.to_json())
        m = r2.member(1)
        self.assertAlmostEqual(m.angle_rad, 0.5)
        self.assertEqual(m.ref, [0, 0, 1])
        self.assertEqual(m.anchor, [5.0, -2.0])
        self.assertEqual(m.offset_start, -1.0)
        self.assertEqual(m.offset_end, 2.0)
        self.assertEqual(m.basis, [[0, 1, 0], [0, 0, 1]])
        self.assertEqual(m.family, 'CHS')

    def test_legacy_dict_loads_with_defaults(self):
        # Schema-version-1 records (no placement keys) must still parse.
        legacy = ('{"version": 1, "members": [{"mid": 1, "start": [0,0,0],'
                  ' "end": [10,0,0], "geom": null, "basis": null,'
                  ' "designation": "20x1", "family": "", "feature": 3,'
                  ' "body_index": 0, "name": ""}], "joints": []}')
        r = reg.Registry.from_json(legacy)
        m = r.member(1)
        self.assertEqual(m.angle_rad, 0.0)
        self.assertIsNone(m.ref)
        self.assertEqual(m.offset_end, 0.0)

    def test_upsert_updates_placement(self):
        r = reg.Registry()
        m = r.add_member((0, 0, 0), (10, 0, 0), angle_rad=0.0)
        again = r.upsert_member((0, 0, 0), (10, 0, 0), angle_rad=1.25)
        self.assertIs(again, m)
        self.assertAlmostEqual(m.angle_rad, 1.25)


class TestSerialisation(unittest.TestCase):
    def test_round_trip(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0), geom=GEOM, designation='20x1',
                         family='CHS', feature=12, body_index=0, name='Tube-1')
        b = r.add_member((5, 0, 20), (5, 0, 0), geom=GEOM)
        r.add_joint('cope', [{'mid': b.mid, 'role': 'end'},
                             {'mid': a.mid, 'role': None}],
                    vertex=(5, 0, 0), params={'clr_mm': 90.0},
                    selections={'face': 'Face:23'})
        r2 = reg.Registry.from_json(r.to_json())
        self.assertEqual(len(r2.members), 2)
        self.assertEqual(len(r2.joints), 1)
        m = r2.member(a.mid)
        self.assertEqual(m.geom, GEOM)
        self.assertEqual(m.designation, '20x1')
        self.assertEqual(m.feature, 12)
        j = r2.joints[0]
        self.assertEqual(j.kind, 'cope')
        self.assertEqual(j.vertex, (5, 0, 0))
        self.assertEqual(j.params['clr_mm'], 90.0)
        self.assertEqual(j.selections['face'], 'Face:23')

    def test_empty_json(self):
        self.assertEqual(len(reg.Registry.from_json('').members), 0)
        self.assertEqual(len(reg.Registry.from_dict(None).joints), 0)

    def test_version_guard(self):
        d = {'version': reg.SCHEMA_VERSION + 1, 'members': [], 'joints': []}
        with self.assertRaises(ValueError):
            reg.Registry.from_dict(d)

    def test_json_is_stable(self):
        r = reg.Registry()
        r.add_member((0, 0, 0), (10, 0, 0), geom=GEOM)
        self.assertEqual(r.to_json(), reg.Registry.from_json(r.to_json()).to_json())


class TestSummary(unittest.TestCase):
    """B1: the registry surfaced as the frame's history for the BOM panel."""

    def test_member_rows_carry_cut_length_and_placement(self):
        r = reg.Registry()
        r.add_member((0, 0, 0), (10, 0, 0), geom=GEOM, designation='20x1',
                     family='CHS', name='Tube-1', angle_rad=0.5,
                     offset_start=1.0, offset_end=-2.0)
        row = r.summary()['members'][0]
        self.assertEqual(row['drawn_mm'], 100.0)          # 10 cm
        self.assertEqual(row['length_mm'], 70.0)          # 10 + (-2) - 1 = 7 cm
        self.assertEqual(row['offset_start_mm'], 10.0)
        self.assertEqual(row['offset_end_mm'], -20.0)
        self.assertAlmostEqual(row['angle_deg'], round(math.degrees(0.5), 2))
        self.assertEqual(row['family'], 'CHS')

    def test_joint_rows_carry_label_and_params(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0), name='Tube-1')
        b = r.add_member((5, 0, 20), (5, 0, 0), name='Tube-2')
        r.add_joint('cope_t', [{'mid': b.mid, 'role': None},
                              {'mid': a.mid, 'role': None}],
                    vertex=(5, 0, 0), params={'depth_mm': 5.0})
        j = r.summary()['joints'][0]
        self.assertEqual(j['kind'], 'cope_t')
        self.assertEqual(j['ref_names'], ['Tube-2', 'Tube-1'])
        self.assertIn('Cope (T)', j['label'])
        self.assertIn('Tube-2 onto Tube-1', j['label'])
        self.assertIn('depth 5 mm', j['label'])

    def test_label_falls_back_for_unknown_kind(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        r.add_joint('weird', [{'mid': a.mid, 'role': 'end'}])
        j = r.summary()['joints'][0]
        self.assertTrue(j['label'].startswith('Weird'))

    def test_body_names_win_the_display_name(self):
        # The BOM panel reflects the browser-tree body names (the user's spine
        # complaint): when the command layer resolves a live name for a member,
        # display_name and the joint labels use it; the stored record does not.
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0), designation='20x1', name='Old')
        b = r.add_member((5, 0, 20), (5, 0, 0))
        r.add_joint('cope_t', [{'mid': b.mid, 'role': None},
                              {'mid': a.mid, 'role': None}],
                    vertex=(5, 0, 0), params={'depth_mm': 5.0})
        s = r.summary(body_names={a.mid: 'Tube-A', b.mid: 'Tube-B'})
        rows = {m['mid']: m for m in s['members']}
        self.assertEqual(rows[a.mid]['display_name'], 'Tube-A')
        self.assertEqual(rows[a.mid]['name'], 'Old')   # record untouched
        self.assertEqual(rows[b.mid]['display_name'], 'Tube-B')
        self.assertIn('Tube-B onto Tube-A', s['joints'][0]['label'])

    def test_display_name_falls_back_without_body_names(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0), designation='20x1')
        b = r.add_member((5, 0, 20), (5, 0, 0))
        row_a, row_b = r.summary()['members']
        self.assertEqual(row_a['display_name'], '20x1')   # designation next
        self.assertEqual(row_b['display_name'], f"#{b.mid}")  # id last


class TestPlanRebuild(unittest.TestCase):
    """B3: the records mapped back onto the builder's joint inputs."""

    def test_miter_lands_on_the_referenced_end(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        b = r.add_member((10, 0, 0), (10, 10, 0))
        r.add_joint('miter', [{'mid': a.mid, 'role': 'end'},
                              {'mid': b.mid, 'role': 'start'}],
                    vertex=(10, 0, 0))
        plan = r.plan_rebuild()
        self.assertEqual(plan[a.mid]['joint_ids'], ('none', 'miter'))
        self.assertEqual(plan[b.mid]['joint_ids'], ('miter', 'none'))

    def test_cope_variants_collapse_to_cope_with_depth(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        b = r.add_member((5, 0, 20), (5, 0, 0))
        r.add_joint('cope_t', [{'mid': b.mid, 'role': 'end'},
                              {'mid': a.mid, 'role': None}],
                    vertex=(5, 0, 0), params={'depth_mm': 5.0})
        plan = r.plan_rebuild()
        self.assertEqual(plan[b.mid]['joint_ids'], ('none', 'cope'))
        self.assertEqual(plan[b.mid]['cope_depth_mm'], 5.0)

    def test_saddle_and_through_become_butt_plus_flag(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        b = r.add_member((10, 0, 0), (10, 10, 0))
        r.add_joint('saddle', [{'mid': a.mid, 'role': 'end'},
                              {'mid': b.mid, 'role': 'start'}])
        r.add_joint('through', [{'mid': b.mid, 'role': 'start'},
                               {'mid': a.mid, 'role': 'end'}])
        plan = r.plan_rebuild()
        self.assertEqual(plan[a.mid]['joint_ids'], ('none', 'butt'))
        self.assertTrue(plan[a.mid]['saddle'])
        self.assertEqual(plan[b.mid]['joint_ids'], ('butt', 'none'))
        self.assertTrue(plan[b.mid]['through'])

    def test_bend_carries_clr_and_beats_miter_on_one_end(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        b = r.add_member((10, 0, 0), (10, 10, 0))
        r.add_joint('miter', [{'mid': a.mid, 'role': 'end'}])
        r.add_joint('bend', [{'mid': a.mid, 'role': 'end'},
                             {'mid': b.mid, 'role': 'start'}],
                    params={'clr_mm': 90.0})
        plan = r.plan_rebuild()
        self.assertEqual(plan[a.mid]['joint_ids'], ('none', 'bend'))
        self.assertEqual(plan[a.mid]['clr_mm'], 90.0)

    def test_member_without_joints_is_plain(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        plan = r.plan_rebuild()
        self.assertEqual(plan[a.mid]['joint_ids'], ('none', 'none'))
        self.assertFalse(plan[a.mid]['through'])
        self.assertFalse(plan[a.mid]['saddle'])

    def test_joint_referencing_a_deleted_member_is_dropped(self):
        r = reg.Registry()
        a = r.add_member((0, 0, 0), (10, 0, 0))
        b = r.add_member((10, 0, 0), (10, 10, 0))
        r.add_joint('miter', [{'mid': a.mid, 'role': 'end'},
                              {'mid': b.mid, 'role': 'start'}])
        r.remove_member(b.mid)
        plan = r.plan_rebuild()
        self.assertEqual(plan[a.mid]['joint_ids'], ('none', 'miter'))
        self.assertNotIn(b.mid, plan)


if __name__ == '__main__':
    unittest.main()
