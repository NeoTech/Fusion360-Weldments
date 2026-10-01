"""Unit tests for the pure-Python bending-die loader (``lib/bending_dies.py``).

Run with plain ``python`` (no Fusion):

    python -m unittest discover -s tests

These validate catalogue loading, die lookup, family filtering, and the
designation -> die matching used to place a swept-bend arc.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import bending_dies as bd  # noqa: E402
from lib import profiles as prof  # noqa: E402


CATALOGUE = {
    "units": "mm",
    "dies": [
        {"die_id": "CHS-R120", "profile_family": "CHS", "groove_profile_type": "round",
         "nominal_CLR_mm": 120.0, "compatible_OD_mm": [21.3, 60.3],
         "wall_thickness_range_mm": [1.5, 4.0]},
        {"die_id": "CHS-R228", "profile_family": "CHS", "groove_profile_type": "round",
         "nominal_CLR_mm": 228.0, "compatible_OD_mm": [60.3, 114.3],
         "wall_thickness_range_mm": [2.0, 5.0]},
        {"die_id": "SHS-R150", "profile_family": "SHS", "groove_profile_type": "square",
         "nominal_CLR_mm": 150.0, "compatible_width_mm": [20.0, 50.0],
         "compatible_height_mm": [20.0, 50.0], "wall_thickness_range_mm": [2.0, 5.0]},
    ],
}


class TestLoader(unittest.TestCase):
    def test_shipped_catalogue_loads(self):
        cat = bd.load_bending_dies()
        self.assertGreater(len(bd.dies(cat)), 0)
        self.assertEqual(cat.get("units"), "mm")

    def test_missing_file_is_empty(self):
        cat = bd.load_bending_dies(path=os.devnull + "_nope_does_not_exist.json")
        self.assertEqual(bd.dies(cat), [])

    def test_die_labels(self):
        labels = bd.die_labels(CATALOGUE)
        self.assertEqual(len(labels), 3)
        self.assertTrue(any("CHS-R120" in s for s in labels))

    def test_find_die(self):
        self.assertEqual(bd.find_die(CATALOGUE, "SHS-R150")["nominal_CLR_mm"], 150.0)
        self.assertIsNone(bd.find_die(CATALOGUE, "nope"))

    def test_dies_for_family(self):
        self.assertEqual(len(bd.dies_for_family(CATALOGUE, "CHS")), 2)
        self.assertEqual(len(bd.dies_for_family(CATALOGUE, "SHS")), 1)
        self.assertEqual(bd.dies_for_family(CATALOGUE, "IPE"), [])


class TestMatching(unittest.TestCase):
    def test_round_matches_by_od(self):
        die = bd.die_for_designation(CATALOGUE, {"od_mm": 42.4, "t_mm": 2.6}, "CHS")
        self.assertEqual(die["die_id"], "CHS-R120")

    def test_square_matches_by_size(self):
        die = bd.die_for_designation(
            CATALOGUE, {"h_mm": 40, "b_mm": 40, "t_mm": 3.0}, "SHS")
        self.assertEqual(die["die_id"], "SHS-R150")

    def test_open_section_cannot_bend(self):
        # An IPE has no od/h/b that a groove matches -> no die.
        self.assertIsNone(bd.die_for_designation(
            CATALOGUE, {"h_mm": 200, "b_mm": 100, "tw_mm": 5.6}, "IPE"))

    def test_wall_out_of_range_no_die(self):
        die = bd.die_for_designation(
            CATALOGUE, {"h_mm": 40, "b_mm": 40, "t_mm": 9.0}, "SHS")
        self.assertIsNone(die)

    def test_smallest_clr_wins(self):
        # A 60.3 OD sits on the boundary of both CHS dies; the smaller CLR wins.
        die = bd.die_for_designation(CATALOGUE, {"od_mm": 60.3, "t_mm": 2.9}, "CHS")
        self.assertEqual(die["nominal_CLR_mm"], 120.0)

    def test_clr_cm_conversion(self):
        self.assertAlmostEqual(bd.clr_cm({"nominal_CLR_mm": 150.0}), 15.0)
        self.assertEqual(bd.clr_cm(None), 0.0)


class TestAgainstProfiles(unittest.TestCase):
    """Every bendable family in the shipped catalogue resolves to a die."""

    def setUp(self):
        self.cat = bd.load_bending_dies()
        self.families = {f["abbreviation"]: f for f in prof.load_profiles()}

    def test_chs_designations_resolve(self):
        fam = self.families["CHS"]
        for d in prof.designations(fam)[:5]:
            self.assertIsNotNone(
                bd.die_for_designation(self.cat, d, "CHS"), d["designation"])

    def test_shs_designations_resolve(self):
        fam = self.families["SHS"]
        for d in prof.designations(fam)[:5]:
            self.assertIsNotNone(
                bd.die_for_designation(self.cat, d, "SHS"), d["designation"])


if __name__ == "__main__":
    unittest.main()
