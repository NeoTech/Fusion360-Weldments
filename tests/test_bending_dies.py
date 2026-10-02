"""Unit tests for the pure-Python bending-die loader (``lib/bending_dies.py``).

Run with plain ``python`` (no Fusion):

    python -m unittest discover -s tests

These validate catalogue loading, die lookup, family filtering, and the
family -> CLR-list matching the command layer uses to populate the Bend Die
dropdown.  The catalogue is reduced to the one value that shapes a swept bend
-- the centerline radius -- keyed by profile family.
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
        {"die_id": "CHS-CLR-120", "profile_family": "CHS", "clr_mm": 120.0},
        {"die_id": "CHS-CLR-228", "profile_family": "CHS", "clr_mm": 228.0},
        {"die_id": "SHS-CLR-150", "profile_family": "SHS", "clr_mm": 150.0},
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
        self.assertTrue(any("CHS-CLR-120 (R120)" in s for s in labels))

    def test_find_die(self):
        self.assertEqual(bd.find_die(CATALOGUE, "SHS-CLR-150")["clr_mm"], 150.0)
        self.assertIsNone(bd.find_die(CATALOGUE, "nope"))

    def test_die_clr_accessor(self):
        self.assertEqual(bd.die_clr({"clr_mm": 114.3}), 114.3)
        self.assertEqual(bd.die_clr({}), 0.0)
        self.assertEqual(bd.die_clr(None), 0.0)

    def test_dies_for_family(self):
        self.assertEqual(len(bd.dies_for_family(CATALOGUE, "CHS")), 2)
        self.assertEqual(len(bd.dies_for_family(CATALOGUE, "SHS")), 1)
        self.assertEqual(bd.dies_for_family(CATALOGUE, "IPE"), [])

    def test_rhs_aliases_to_shs_dies(self):
        # RHS shares SHS's flat-face bend tooling via _DIE_FAMILY_ALIASES, so it
        # resolves to the same dies even though the catalogue lists none under
        # "RHS".
        self.assertEqual(bd.dies_for_family(CATALOGUE, "RHS"),
                         bd.dies_for_family(CATALOGUE, "SHS"))
        die = bd.die_for_designation(
            CATALOGUE, {"h_mm": 40, "b_mm": 20, "t_mm": 3.0}, "RHS")
        self.assertEqual(die["die_id"], "SHS-CLR-150")


class TestMatching(unittest.TestCase):
    def test_family_only_match_round(self):
        # Any CHS designation resolves to the family's tightest die; the size
        # no longer gates anything.
        die = bd.die_for_designation(CATALOGUE, {"od_mm": 42.4, "t_mm": 2.6}, "CHS")
        self.assertEqual(die["die_id"], "CHS-CLR-120")

    def test_family_only_match_square(self):
        die = bd.die_for_designation(
            CATALOGUE, {"h_mm": 40, "b_mm": 40, "t_mm": 3.0}, "SHS")
        self.assertEqual(die["die_id"], "SHS-CLR-150")

    def test_size_no_longer_excludes(self):
        # A tiny tube the old OD ranges would have rejected now still gets the
        # family's default die -- the shop chooses the radius.
        die = bd.die_for_designation(CATALOGUE, {"od_mm": 6.0, "t_mm": 0.5}, "CHS")
        self.assertEqual(die["die_id"], "CHS-CLR-120")

    def test_wall_no_longer_excludes(self):
        die = bd.die_for_designation(
            CATALOGUE, {"h_mm": 40, "b_mm": 40, "t_mm": 9.0}, "SHS")
        self.assertEqual(die["die_id"], "SHS-CLR-150")

    def test_open_family_has_no_die(self):
        # A family with no dies at all (an open section) cannot be swept-bent.
        self.assertIsNone(bd.die_for_designation(
            CATALOGUE, {"h_mm": 200, "b_mm": 100, "tw_mm": 5.6}, "IPE"))

    def test_smallest_clr_is_default(self):
        die = bd.die_for_designation(CATALOGUE, {"od_mm": 60.3, "t_mm": 2.9}, "CHS")
        self.assertEqual(die["clr_mm"], 120.0)

    def test_dies_for_designation_lists_all_family_clrs(self):
        # Every CLR the family offers, sorted ascending, is available to pick.
        dies = bd.dies_for_designation(CATALOGUE, {"od_mm": 60.3, "t_mm": 2.9}, "CHS")
        self.assertEqual([d["clr_mm"] for d in dies], [120.0, 228.0])

    def test_dies_for_designation_single_family_die(self):
        dies = bd.dies_for_designation(CATALOGUE, {"h_mm": 40, "b_mm": 40}, "SHS")
        self.assertEqual([d["die_id"] for d in dies], ["SHS-CLR-150"])

    def test_dies_for_designation_open_family_empty(self):
        self.assertEqual(
            bd.dies_for_designation(CATALOGUE, {"h_mm": 200, "b_mm": 100},
                                    "IPE"), [])

    def test_best_die_is_tightest_of_all_matches(self):
        # die_for_designation must agree with the first of dies_for_designation.
        des = {"od_mm": 60.3, "t_mm": 2.9}
        best = bd.die_for_designation(CATALOGUE, des, "CHS")
        matches = bd.dies_for_designation(CATALOGUE, des, "CHS")
        self.assertIs(best, matches[0])

    def test_clr_cm_conversion(self):
        self.assertAlmostEqual(bd.clr_cm({"clr_mm": 150.0}), 15.0)
        self.assertEqual(bd.clr_cm(None), 0.0)


class TestAgainstProfiles(unittest.TestCase):
    """Bendable families resolve to a die for every designation they list.

    Matching is by family alone, so every CHS/SHS designation now resolves --
    there is no size/wall coverage window to fall outside any more.
    """

    def setUp(self):
        self.cat = bd.load_bending_dies()
        self.families = {f["abbreviation"]: f for f in prof.load_profiles()}

    def test_chs_designations_resolve(self):
        fam = self.families["CHS"]
        checked = 0
        for d in prof.designations(fam):
            self.assertIsNotNone(
                bd.die_for_designation(self.cat, d, "CHS"), d["designation"])
            checked += 1
        self.assertGreater(checked, 0, "CHS lists no designations")

    def test_shs_designations_resolve(self):
        fam = self.families["SHS"]
        checked = 0
        for d in prof.designations(fam):
            self.assertIsNotNone(
                bd.die_for_designation(self.cat, d, "SHS"), d["designation"])
            checked += 1
        self.assertGreater(checked, 0, "SHS lists no designations")

    def test_rhs_designations_share_shs_dies(self):
        # RHS bends on the same tooling as SHS: every RHS designation resolves,
        # and the offered CLRs are exactly SHS's.
        fam = self.families["RHS"]
        checked = 0
        for d in prof.designations(fam):
            self.assertIsNotNone(
                bd.die_for_designation(self.cat, d, "RHS"), d["designation"])
            checked += 1
        self.assertGreater(checked, 0, "RHS lists no designations")
        self.assertEqual(
            [x["clr_mm"] for x in bd.dies_for_family(self.cat, "RHS")],
            [x["clr_mm"] for x in bd.dies_for_family(self.cat, "SHS")])

    def test_open_section_family_has_no_die(self):
        # An open profile family (IPE) is not in the die catalogue at all.
        fam = self.families["IPE"]
        self.assertIsNone(
            bd.die_for_designation(self.cat, prof.designations(fam)[0], "IPE"))


if __name__ == "__main__":
    unittest.main()
