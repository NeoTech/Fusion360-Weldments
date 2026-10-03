"""Unit tests for the pure-Python profile catalogue + geometry (``lib/profiles.py``).

These run with plain ``python`` (no Fusion, no pytest):

    python -m unittest discover -s tests

They validate everything that does NOT require the ``adsk`` API: JSON loading,
dropdown label helpers, designation lookup, basis vectors, and the cross-section
polygon/circle builders for every family in ``data/profiles.json``.
"""

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import profiles as prof  # noqa: E402


class TestLoader(unittest.TestCase):
    def setUp(self):
        self.families = prof.annotate_families(prof.load_profiles())

    def test_all_eight_families_present(self):
        abbrs = {f["abbreviation"] for f in self.families}
        self.assertEqual(abbrs, {"IPE", "HEA", "HEB", "UPE", "UPN", "SHS", "RHS", "CHS"})

    def test_family_labels_count(self):
        labels = prof.family_labels(self.families)
        self.assertEqual(len(labels), len(self.families))
        self.assertTrue(all(" - " in s for s in labels))

    def test_designations_non_empty(self):
        for fam in self.families:
            self.assertGreater(len(prof.designations(fam)), 0, fam["abbreviation"])

    def test_find_designation_roundtrip(self):
        fam = self.families[0]
        first = prof.designations(fam)[0]
        found = prof.find_designation(fam, first["designation"])
        self.assertIs(found, first)

    def test_find_designation_missing(self):
        self.assertIsNone(prof.find_designation(self.families[0], "NOPE 999"))

    def test_abbreviation_annotated(self):
        for fam in self.families:
            for d in prof.designations(fam):
                self.assertEqual(d["_abbreviation"], fam["abbreviation"])


class TestVectors(unittest.TestCase):
    def test_basis_perpendicular(self):
        for direction in [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1), (0.66, -0.66, -0.33)]:
            u, v = prof.compute_basis(direction)
            dn = prof._norm(direction)
            self.assertAlmostEqual(prof._dot(u, dn), 0.0, places=9)
            self.assertAlmostEqual(prof._dot(v, dn), 0.0, places=9)
            self.assertAlmostEqual(prof._dot(u, v), 0.0, places=9)
            self.assertAlmostEqual(math.sqrt(prof._dot(u, u)), 1.0, places=9)
            self.assertAlmostEqual(math.sqrt(prof._dot(v, v)), 1.0, places=9)

    def test_basis_degenerate_vertical(self):
        # A purely vertical line must not blow up (reference switches to X).
        u, v = prof.compute_basis((0, 0, 1))
        self.assertAlmostEqual(prof._dot(u, (0, 0, 1)), 0.0, places=9)

    def test_map_local_to_model_scale(self):
        # 10 mm along u with u = unit X should advance 1 cm (internal units).
        pt = prof.map_local_to_model((0, 0, 0), (1, 0, 0), (0, 1, 0), 10, 0)
        self.assertAlmostEqual(pt[0], prof.MM_TO_CM * 10, places=9)

    def test_rotate_basis_zero_is_identity(self):
        u, v = prof.compute_basis((1, 1, 0))
        ru, rv = prof.rotate_basis(u, v, 0.0)
        self.assertEqual(ru, u)
        self.assertEqual(rv, v)

    def test_rotate_basis_stays_orthonormal_and_perp_to_axis(self):
        for direction in [(1, 0, 0), (0, 1, 0), (1, 1, 1), (0, 0, 1)]:
            u, v = prof.compute_basis(direction)
            dn = prof._norm(direction)
            for ang in (0.3, math.pi / 2, math.pi, -1.1):
                ru, rv = prof.rotate_basis(u, v, ang)
                self.assertAlmostEqual(math.sqrt(prof._dot(ru, ru)), 1.0, places=9)
                self.assertAlmostEqual(math.sqrt(prof._dot(rv, rv)), 1.0, places=9)
                self.assertAlmostEqual(prof._dot(ru, rv), 0.0, places=9)
                # Rotation is about the line axis: both stay perpendicular to it.
                self.assertAlmostEqual(prof._dot(ru, dn), 0.0, places=9)
                self.assertAlmostEqual(prof._dot(rv, dn), 0.0, places=9)

    def test_rotate_basis_90_swaps_u_into_v(self):
        # For a +X line, basis is u=?, v=?; rotating +90 deg must map u onto v.
        u, v = prof.compute_basis((1, 0, 0))
        ru, _rv = prof.rotate_basis(u, v, math.pi / 2)
        # ru should now point along the original v (rotation within the plane).
        for i in range(3):
            self.assertAlmostEqual(ru[i], v[i], places=9)

    def test_selection_reference_is_plane_normal(self):
        # Two lines in the XY plane -> shared reference is +/- Z (the 2D case).
        ref = prof.selection_reference([(1, 0, 0), (0, 1, 0)])
        self.assertAlmostEqual(abs(ref[2]), 1.0, places=9)
        self.assertAlmostEqual(ref[0], 0.0, places=9)
        self.assertAlmostEqual(ref[1], 0.0, places=9)

    def test_selection_reference_parallel_is_none(self):
        # All lines parallel -> no shared plane -> None (per-line fallback).
        self.assertIsNone(prof.selection_reference([(1, 0, 0), (2, 0, 0)]))
        self.assertIsNone(prof.selection_reference([(1, 0, 0)]))

    def test_shared_ref_aligns_perpendicular_lines(self):
        # A vertical + a diagonal line in the XZ plane: with the shared reference
        # (plane normal = Y), both profiles' "height" axis_v must be rolled the
        # same way, so joined/mitred ends line up.
        ref = prof.selection_reference([(0, 0, 1), (1, 0, 1)])
        _u1, v1 = prof.compute_basis((0, 0, 1), ref)
        _u2, v2 = prof.compute_basis((1, 0, 1), ref)
        # Both axis_v are perpendicular to their own line direction (valid basis).
        self.assertAlmostEqual(prof._dot(v1, (0, 0, 1)), 0.0, places=9)
        self.assertAlmostEqual(prof._dot(v2, prof._norm((1, 0, 1))), 0.0, places=9)
        # And they point the same way (parallel), so the profiles are rolled alike.
        cross = prof._cross(v1, v2)
        self.assertAlmostEqual(prof._dot(cross, cross), 0.0, places=9)

    def test_compute_basis_ref_overrides_fallback(self):
        # Passing an explicit ref makes axis_v track it, not the world fallback.
        u, v = prof.compute_basis((1, 0, 0), ref=(0, 1, 0))
        for i, want in enumerate((0.0, 1.0, 0.0)):
            self.assertAlmostEqual(v[i], want, places=9)


class TestSectionGeometry(unittest.TestCase):
    def setUp(self):
        self.families = prof.annotate_families(prof.load_profiles())
        self.by_abbr = {f["abbreviation"]: f for f in self.families}

    def _first(self, abbr):
        return prof.designations(self.by_abbr[abbr])[0]

    def test_ibeam_fillet_metadata(self):
        for abbr in ("IPE", "HEA", "HEB"):
            geom = prof.section_geometry(self._first(abbr))
            self.assertEqual(geom["kind"], "polygons")
            self.assertEqual(len(geom["loops"]), 1)
            # Loops are plain point polygons now (12 corners for an I/H).
            self.assertEqual(len(geom["loops"][0]), 12, abbr)
            # Four internal web-to-flange corners are flagged for filleting.
            self.assertEqual(len(geom["fillets"][0]), 4, abbr)
            for idx, r in geom["fillets"][0]:
                self.assertGreater(r, 0.0)
                self.assertIn(idx, (3, 4, 9, 10), abbr)

    def test_channel_fillet_metadata(self):
        for abbr in ("UPE", "UPN"):
            geom = prof.section_geometry(self._first(abbr))
            self.assertEqual(len(geom["loops"][0]), 8, abbr)
            self.assertEqual(len(geom["fillets"][0]), 2, abbr)  # web-to-flange

    def test_rect_hollow_two_loops(self):
        for abbr in ("SHS", "RHS"):
            geom = prof.section_geometry(self._first(abbr))
            self.assertEqual(geom["kind"], "polygons")
            self.assertEqual(len(geom["loops"]), 2, abbr)
            for loop in geom["loops"]:
                self.assertEqual(len(loop), 4, abbr)  # plain rectangles
            # Outer loop filleted at all 4 corners; inner radius = r - t.
            self.assertEqual(len(geom["fillets"][0]), 4, abbr)

    def test_chs_two_circles(self):
        geom = prof.section_geometry(self._first("CHS"))
        self.assertEqual(geom["kind"], "circles")
        self.assertEqual(len(geom["radii"]), 2)
        self.assertGreater(geom["radii"][0], geom["radii"][1])

    def test_every_designation_builds(self):
        # The strongest guard: every row in profiles.json must yield geometry.
        for fam in self.families:
            for d in prof.designations(fam):
                geom = prof.section_geometry(d)
                self.assertIn(geom["kind"], ("polygons", "circles"))

    def test_ibeam_dimensions(self):
        d = self._first("IPE")
        geom = prof.section_geometry(d)
        pts = geom["loops"][0]
        us = [p[0] for p in pts]
        vs = [p[1] for p in pts]
        self.assertAlmostEqual(max(us) - min(us), d["b_mm"], places=6)
        self.assertAlmostEqual(max(vs) - min(vs), d["h_mm"], places=6)

    def test_fillet_indices_in_range(self):
        # Every fillet corner index must address a real vertex of its loop, and
        # the radius must be positive and fit inside the section.
        for fam in self.families:
            for d in prof.designations(fam):
                geom = prof.section_geometry(d)
                if geom["kind"] != "polygons":
                    continue
                for loop, fillets in zip(geom["loops"], geom["fillets"]):
                    for idx, r in fillets:
                        self.assertGreaterEqual(idx, 0)
                        self.assertLess(idx, len(loop), d["designation"])
                        self.assertGreater(r, 0.0, d["designation"])

    def test_hollow_inner_radius_le_outer(self):
        # For SHS/RHS the inner corner radius (r - t) must never exceed outer.
        for abbr in ("SHS", "RHS"):
            for d in prof.designations(self.by_abbr[abbr]):
                geom = prof.section_geometry(d)
                ro = geom["fillets"][0][0][1] if geom["fillets"][0] else 0.0
                ri = geom["fillets"][1][0][1] if geom["fillets"][1] else 0.0
                self.assertLessEqual(ri, ro, d["designation"])

    def test_infer_abbreviation(self):
        self.assertEqual(prof._infer_abbreviation({"designation": "IPE 100"}), "IPE")
        self.assertEqual(prof._infer_abbreviation({"designation": "42.4x2.6", "od_mm": 42.4}), "CHS")


class TestGridPosition(unittest.TestCase):
    """The Position alignment grid: section_extents, grid_anchor, displace_origin."""

    def setUp(self):
        self.families = prof.annotate_families(prof.load_profiles())
        self.by_abbr = {f["abbreviation"]: f for f in self.families}

    def _geom(self, abbr):
        return prof.section_geometry(prof.designations(self.by_abbr[abbr])[0])

    def test_grid_positions_has_nine_with_center_first(self):
        keys = [k for k, _ in prof.GRID_POSITIONS]
        self.assertEqual(len(keys), 9)
        self.assertEqual(keys[0], "center")
        self.assertEqual(set(keys), {"center", "top", "bottom", "left", "right",
                                     "top-left", "top-right", "bottom-left",
                                     "bottom-right"})

    def test_extents_symmetric_for_rect(self):
        umin, umax, vmin, vmax = prof.section_extents(self._geom("SHS"))
        self.assertAlmostEqual(umin, -umax, places=6)
        self.assertAlmostEqual(vmin, -vmax, places=6)

    def test_center_anchor_is_origin(self):
        for abbr in ("SHS", "RHS", "CHS", "IPE"):
            self.assertEqual(prof.grid_anchor(self._geom(abbr), "center"),
                             (0.0, 0.0), abbr)

    def test_top_anchor_is_upper_edge(self):
        _u, _um, _v, vmax = prof.section_extents(self._geom("SHS"))
        au, av = prof.grid_anchor(self._geom("SHS"), "top")
        self.assertAlmostEqual(au, 0.0, places=6)
        self.assertAlmostEqual(av, vmax, places=6)

    def test_corner_anchor_is_box_corner(self):
        umin, umax, vmin, vmax = prof.section_extents(self._geom("RHS"))
        self.assertEqual(prof.grid_anchor(self._geom("RHS"), "top-right"),
                         (umax, vmax))
        self.assertEqual(prof.grid_anchor(self._geom("RHS"), "bottom-left"),
                         (umin, vmin))

    def test_displace_center_is_identity(self):
        origin = (1.0, 2.0, 3.0)
        self.assertEqual(prof.displace_origin(origin, (1, 0, 0), (0, 1, 0), None),
                         origin)
        self.assertEqual(prof.displace_origin(origin, (1, 0, 0), (0, 1, 0), (0, 0)),
                         origin)

    def test_displace_moves_centroid_off_the_line(self):
        # A "top" anchor (v = +20 mm) on a section with basis u=+X, v=+Y must
        # slide the centroid DOWN by 20mm (2cm) so the top edge lands on origin.
        origin = (0.0, 0.0, 0.0)
        got = prof.displace_origin(origin, (1, 0, 0), (0, 1, 0), (0.0, 20.0))
        self.assertAlmostEqual(got[1], -2.0, places=9)   # 20mm -> 2cm, negative
        self.assertAlmostEqual(got[0], 0.0, places=9)


if __name__ == "__main__":
    unittest.main()
