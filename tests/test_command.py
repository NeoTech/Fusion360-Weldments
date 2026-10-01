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


class _Args:
    """Minimal stand-in for InputChangedEventArgs / SelectionEventArgs."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def _make_dialog():
    """Build a FakeCommand whose inputs mirror command_created's layout."""
    cmd = adsk_stub.FakeCommand()
    inputs = cmd.commandInputs
    inputs.addSelectionInput('path', 'Lines', '')
    inputs.addDropDownCommandInput('family', 'Profile', 0)
    inputs.addDropDownCommandInput('designation', 'Designation', 0)
    tbl = inputs.addTableCommandInput('params', 'Per Line', 4, '1:3:3:3')
    sync = inputs.addBoolValueInput('sync_all', 'Sync all', True, '', True)
    tbl.addToolbarCommandInput(sync)
    return cmd, inputs, tbl


class TestPerLineTable(unittest.TestCase):
    def setUp(self):
        adsk_stub.reset()
        entry._row_ids = []
        entry._uid_counter[0] = 0
        entry._FAMILIES = prof.annotate_families(prof.load_profiles())

    def _pick(self, inputs, lines):
        sel = inputs.itemById('path')
        for ln in lines:
            sel.addSelection(ln)
        return sel

    def test_sync_table_rows_one_row_per_line(self):
        _cmd, inputs, tbl = _make_dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))]
        entry._sync_table_rows(inputs, lines)
        self.assertEqual(len(entry._row_ids), 2)
        self.assertEqual(tbl.rowCount, 2)

    def test_sync_table_rows_shrinks(self):
        _cmd, inputs, tbl = _make_dialog()
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 3)
        self.assertEqual(len(entry._row_ids), 3)
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        self.assertEqual(len(entry._row_ids), 1)
        self.assertEqual(tbl.rowCount, 1)

    def test_sync_table_rows_preserves_existing(self):
        _cmd, inputs, tbl = _make_dialog()
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        first_id = entry._row_ids[0]['rot']
        inputs.itemById(first_id).value = 0.25
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine(), adsk_stub.FakeLine()])
        # Row 0 keeps its identity and value; a new row 1 is appended.
        self.assertEqual(entry._row_ids[0]['rot'], first_id)
        self.assertAlmostEqual(inputs.itemById(first_id).value, 0.25)
        self.assertEqual(len(entry._row_ids), 2)

    def test_cell_column_mapping(self):
        self.assertEqual(entry._cell_column('rot_5'), 'rot')
        self.assertEqual(entry._cell_column('os_5'), 'os')
        self.assertEqual(entry._cell_column('oe_5'), 'oe')
        self.assertIsNone(entry._cell_column('num_5'))
        self.assertIsNone(entry._cell_column('family'))

    def test_sync_all_hidden_until_two_rows(self):
        _cmd, inputs, tbl = _make_dialog()
        sync = inputs.itemById('sync_all')
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        self.assertFalse(sync.isVisible)  # 1 row: nothing to sync
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 2)
        self.assertTrue(sync.isVisible)   # 2 rows: show the toggle
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        self.assertFalse(sync.isVisible)  # back to 1 row: hide again

    def test_row_params_reads_per_row_values(self):
        _cmd, inputs, tbl = _make_dialog()
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 2)
        inputs.itemById(entry._row_ids[0]['rot']).value = 0.5
        inputs.itemById(entry._row_ids[1]['os']).value = 2.0
        self.assertEqual(entry._row_params(inputs, 0)[0], 0.5)
        self.assertEqual(entry._row_params(inputs, 1)[1], 2.0)
        # A row past the table defaults to zeros.
        self.assertEqual(entry._row_params(inputs, 9), (0.0, 0.0, 0.0))

    def test_sync_all_propagates_rotation(self):
        _cmd, inputs, tbl = _make_dialog()
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 3)
        rot0 = inputs.itemById(entry._row_ids[0]['rot'])
        rot0.value = 0.75
        entry.command_input_changed(_Args(input=rot0, inputs=inputs))
        for r in (1, 2):
            self.assertAlmostEqual(
                inputs.itemById(entry._row_ids[r]['rot']).value, 0.75)

    def test_sync_all_propagates_offset(self):
        _cmd, inputs, tbl = _make_dialog()
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 2)
        os1 = inputs.itemById(entry._row_ids[1]['os'])
        os1.value = 3.0
        entry.command_input_changed(_Args(input=os1, inputs=inputs))
        self.assertAlmostEqual(
            inputs.itemById(entry._row_ids[0]['os']).value, 3.0)

    def test_no_propagation_when_unsynced(self):
        _cmd, inputs, tbl = _make_dialog()
        inputs.itemById('sync_all').value = False
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 2)
        rot0 = inputs.itemById(entry._row_ids[0]['rot'])
        rot0.value = 0.9
        entry.command_input_changed(_Args(input=rot0, inputs=inputs))
        self.assertAlmostEqual(
            inputs.itemById(entry._row_ids[1]['rot']).value, 0.0)

    def test_rotation_cell_has_no_manipulator(self):
        # Rotation is a plain spinner: it must never draw a canvas handle.
        _cmd, inputs, tbl = _make_dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))]
        entry._sync_table_rows(inputs, lines)
        entry._update_manipulators(inputs, lines)
        for r in (0, 1):
            self.assertEqual(
                inputs.itemById(entry._row_ids[r]['rot']).manipulatorCount, 0)

    def test_synced_arrows_only_row_zero(self):
        _cmd, inputs, tbl = _make_dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))]
        entry._sync_table_rows(inputs, lines)
        entry._update_manipulators(inputs, lines)
        # Only row 0's offset arrows anchor to the canvas when Sync all is on.
        self.assertEqual(inputs.itemById(entry._row_ids[0]['os']).manipulatorCount, 1)
        self.assertEqual(inputs.itemById(entry._row_ids[1]['os']).manipulatorCount, 0)
        # Row 1's cells stay VISIBLE (a table row hides when all its cells are
        # invisible), but its arrows are disabled so only row 0 shows handles.
        self.assertTrue(inputs.itemById(entry._row_ids[1]['os']).isVisible)
        self.assertFalse(inputs.itemById(entry._row_ids[1]['os']).isEnabled)

    def test_unsynced_arrows_anchor_each_row(self):
        _cmd, inputs, tbl = _make_dialog()
        inputs.itemById('sync_all').value = False
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))]
        entry._sync_table_rows(inputs, lines)
        entry._update_manipulators(inputs, lines)
        for r in (0, 1):
            self.assertEqual(
                inputs.itemById(entry._row_ids[r]['os']).manipulatorCount, 1)
            self.assertTrue(
                inputs.itemById(entry._row_ids[r]['os']).isVisible)
        # Row 1's offset-start arrow anchors to line 1's start point (0,0,0).
        mo = inputs.itemById(entry._row_ids[1]['os']).manipulatorOrigin
        self.assertEqual((round(mo.x, 3), round(mo.y, 3), round(mo.z, 3)),
                         (0.0, 0.0, 0.0))

    def test_select_builds_rows_and_anchors(self):
        cmd, inputs, tbl = _make_dialog()
        sel = self._pick(inputs, [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0))])
        entry.command_select(_Args(activeInput=sel))
        self.assertEqual(len(entry._row_ids), 1)
        self.assertEqual(inputs.itemById(entry._row_ids[0]['os']).manipulatorCount, 1)


if __name__ == "__main__":
    unittest.main()
