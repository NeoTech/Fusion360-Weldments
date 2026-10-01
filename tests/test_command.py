"""API-facing stub tests for the weldment command glue (``commands/weldment/entry.py``).

These install a fake ``adsk`` module (see :mod:`adsk_stub`) so the command can be
imported and driven *without* Fusion.  They assert the important, easy-to-get-wrong
decisions in the construction code:

* path chaining is OFF (``noChainedCurves``)
* the construction plane sits at the line START (proportional distance 0)
* the extrude is a NEW BODY
* the extrude runs to an entity (the line end point), one-sided, positive direction

Run with:  python -m unittest discover -s tests
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adsk_stub  # noqa: E402  (installs nothing until .install())

adsk_stub.install()


def _make_package(pkg_name, dir_path):
    """Register a bare package (with __path__) WITHOUT executing its __init__.py.

    This lets ``Weldments.commands.weldment.entry`` import via relative paths
    (``from ...lib ...``) without dragging in the sibling sample commands or the
    full fusionAddInUtils package (which needs a richer adsk stub).
    """
    import types
    mod = types.ModuleType(pkg_name)
    mod.__path__ = [dir_path]
    mod.__package__ = pkg_name
    sys.modules[pkg_name] = mod
    return mod


# Build the "Weldments" package tree the way Fusion loads the add-in, so the
# command's relative imports (from ...lib, from ... import config) resolve.
_make_package("Weldments", ROOT)
_make_package("Weldments.lib", os.path.join(ROOT, "lib"))
_make_package("Weldments.commands", os.path.join(ROOT, "commands"))
_make_package("Weldments.commands.weldment", os.path.join(ROOT, "commands", "weldment"))

import importlib  # noqa: E402
entry = importlib.import_module("Weldments.commands.weldment.entry")
prof = importlib.import_module("Weldments.lib.profiles")


def _names():
    return [c[0] for c in adsk_stub.CALLS]


class TestBuildWeldment(unittest.TestCase):
    def setUp(self):
        adsk_stub.reset()
        self.root = adsk_stub.FakeRoot()
        # IPE 80 cross-section, geometry resolved through the real catalogue.
        families = prof.annotate_families(prof.load_profiles())
        self.geom = prof.section_geometry(prof.designations(families[0])[0])

    def test_builds_and_returns_true(self):
        line = adsk_stub.FakeLine(start=(0, 0, 0), end=(10, 0, 0))
        ok = entry._build_weldment(self.root, line, self.geom, "IPE 80")
        self.assertTrue(ok)

    def test_returns_feature_sketch_plane_tuple(self):
        # _build_weldment now returns (feature, sketch, plane) for preview
        # teardown, or None on failure.
        line = adsk_stub.FakeLine()
        objs = entry._build_weldment(self.root, line, self.geom, "IPE 80")
        self.assertIsInstance(objs, tuple)
        self.assertEqual(len(objs), 3)

    def test_preview_build_does_not_crash(self):
        # preview=True ghosts the body; must not raise even though the stub
        # feature has no real bodies collection.
        line = adsk_stub.FakeLine()
        objs = entry._build_weldment(self.root, line, self.geom, "IPE 80",
                                     preview=True)
        self.assertIsInstance(objs, tuple)

    def test_clear_preview_deletes_all_three(self):
        line = adsk_stub.FakeLine()
        objs = entry._build_weldment(self.root, line, self.geom, "IPE 80")
        entry._preview_objs = [objs]
        adsk_stub.reset()
        entry._clear_preview()
        # feature, sketch and plane are each deleted (order matters).
        self.assertEqual(len([c for c in adsk_stub.CALLS if c[0].endswith("deleteMe")]), 3)
        self.assertEqual(entry._preview_objs, [])

    def test_chaining_is_off(self):
        line = adsk_stub.FakeLine()
        entry._build_weldment(self.root, line, self.geom, "IPE 80")
        path_calls = [c for c in adsk_stub.CALLS if c[0] == "Path.create"]
        # Only one path now: the start plane (the end is a distance extent).
        self.assertEqual(len(path_calls), 1)
        self.assertEqual(path_calls[0][1][0],
                         adsk_stub.sys.modules["adsk.fusion"].ChainedCurveOptions.noChainedCurves)

    def test_single_plane_at_line_start(self):
        line = adsk_stub.FakeLine()
        entry._build_weldment(self.root, line, self.geom, "IPE 80")
        sp = [c for c in adsk_stub.CALLS if c[0] == "ConstructionPlaneInput.setByPath"]
        self.assertEqual(len(sp), 1)
        fusion = adsk_stub.sys.modules["adsk.fusion"]
        # The plane is placed by an absolute (physical) distance along the path
        # so the signed Offset Start can move it before/after the line start.
        self.assertEqual(sp[0][1][0], fusion.PathDistanceTypes.PhysicalPathDistanceType)
        # With no offset the plane sits at the start of the path (distance 0.0).
        real = [c[1][0] for c in adsk_stub.CALLS if c[0] == "ValueInput.createByReal"]
        self.assertIn(0.0, real)

    def test_offset_start_moves_plane(self):
        # A positive Offset Start places the plane that far along the line.
        line = adsk_stub.FakeLine(start=(0, 0, 0), end=(10, 0, 0))
        entry._build_weldment(self.root, line, self.geom, "IPE 80",
                              offset_start=2.0)
        sp = [c for c in adsk_stub.CALLS if c[0] == "ConstructionPlaneInput.setByPath"]
        self.assertAlmostEqual(sp[0][1][1].value, 2.0)

    def test_offsets_adjust_extrude_length(self):
        # length 5, offset_start 1, offset_end -2 -> extrude 5 + (-2) - 1 = 2.
        line = adsk_stub.FakeLine(start=(0, 0, 0), end=(5, 0, 0))
        entry._build_weldment(self.root, line, self.geom, "IPE 80",
                              offset_start=1.0, offset_end=-2.0)
        de = [c for c in adsk_stub.CALLS if c[0] == "DistanceExtentDefinition.create"]
        self.assertAlmostEqual(de[0][1][0].value, 2.0)

    def test_extrude_distance_equals_line_length(self):
        line = adsk_stub.FakeLine(start=(0, 0, 0), end=(3, 4, 0))  # length 5
        entry._build_weldment(self.root, line, self.geom, "IPE 80")
        de = [c for c in adsk_stub.CALLS if c[0] == "DistanceExtentDefinition.create"]
        self.assertEqual(len(de), 1)
        # It receives a ValueInput whose real value is the line length (5 cm).
        vi = de[0][1][0]
        self.assertAlmostEqual(vi.value, 5.0)

    def test_extrude_is_new_body_positive_direction(self):
        line = adsk_stub.FakeLine()
        entry._build_weldment(self.root, line, self.geom, "IPE 80")
        fusion = adsk_stub.sys.modules["adsk.fusion"]
        ci = [c for c in adsk_stub.CALLS if c[0] == "ExtrudeFeatures.createInput"]
        self.assertEqual(ci[0][1][0], fusion.FeatureOperations.NewBodyFeatureOperation)
        se = [c for c in adsk_stub.CALLS if c[0] == "ExtrudeFeatureInput.setOneSideExtent"]
        self.assertEqual(se[0][1][0], fusion.ExtentDirections.PositiveExtentDirection)
        self.assertIn("ExtrudeFeatures.add", _names())

    def test_vertical_line_does_not_crash(self):
        line = adsk_stub.FakeLine(start=(0, 0, 0), end=(0, 0, 10))
        self.assertTrue(entry._build_weldment(self.root, line, self.geom, "IPE 80"))


class TestDropdownHelpers(unittest.TestCase):
    def test_dropdown_index_reads_isSelected(self):
        # _dropdown_index must scan listItems (no selectedItem attribute exists).
        class Item:
            def __init__(self, sel):
                self.isSelected = sel

        class Items:
            def __init__(self, sels):
                self._s = [Item(s) for s in sels]

            @property
            def count(self):
                return len(self._s)

            def item(self, i):
                return self._s[i]

        class DD:
            listItems = Items([False, True, False])

        self.assertEqual(entry._dropdown_index(DD()), 1)


if __name__ == "__main__":
    unittest.main()
