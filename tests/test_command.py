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
jt = importlib.import_module("Weldments.lib.joints")
bd = importlib.import_module("Weldments.lib.bending_dies")


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


class TestPositionGrid(unittest.TestCase):
    """The global Position dropdown -> anchor -> build displacement wiring."""

    def setUp(self):
        adsk_stub.reset()
        self.root = adsk_stub.FakeRoot()
        families = prof.annotate_families(prof.load_profiles())
        self.by_abbr = {f["abbreviation"]: f for f in families}
        # SHS 10x10x1.0: half-height 5mm, so a "top" anchor displaces the
        # centroid 0.5cm off the line.
        self.shs = prof.section_geometry(prof.designations(self.by_abbr["SHS"])[0])

    def _position_inputs(self, key):
        cmd, inputs, _tbl = _make_dialog()
        dd = inputs.addDropDownCommandInput('position', 'Position', 0)
        for _k, label in prof.GRID_POSITIONS:
            dd.listItems.add(label, _k == key)
        return inputs

    def test_position_key_defaults_to_center(self):
        # No 'position' input at all -> historical centred placement.
        cmd, inputs, _tbl = _make_dialog()
        self.assertEqual(entry._position_key(inputs), "center")

    def test_position_key_reads_dropdown(self):
        for key, _label in prof.GRID_POSITIONS:
            inputs = self._position_inputs(key)
            self.assertEqual(entry._position_key(inputs), key)

    def test_section_anchor_none_for_center(self):
        inputs = self._position_inputs("center")
        self.assertIsNone(entry._section_anchor(inputs, self.shs))

    def test_section_anchor_is_top_edge(self):
        inputs = self._position_inputs("top")
        au, av = entry._section_anchor(inputs, self.shs)
        self.assertAlmostEqual(au, 0.0, places=6)
        self.assertAlmostEqual(av, 5.0, places=6)   # +half-height (mm)

    def test_anchor_slides_top_face_onto_the_line(self):
        # Line along +X -> axis_v = +Z.  A "top" anchor must move the section
        # DOWN so its top face is tangent to the line: every drawn point has
        # max z ~= 0 (vs ~+0.5cm when centred).
        line = adsk_stub.FakeLine(start=(0, 0, 0), end=(10, 0, 0))
        anchor = entry._section_anchor(self._position_inputs("top"), self.shs)
        adsk_stub.reset()
        entry._build_weldment(self.root, line, self.shs, "SHS", anchor=anchor)
        zs = [p.z for c in adsk_stub.CALLS
              if c[0] == "SketchLines.addByTwoPoints" for p in (c[1][0], c[1][1])]
        self.assertTrue(zs)
        self.assertAlmostEqual(max(zs), 0.0, places=6)

    def test_no_anchor_keeps_centred_placement(self):
        line = adsk_stub.FakeLine(start=(0, 0, 0), end=(10, 0, 0))
        adsk_stub.reset()
        entry._build_weldment(self.root, line, self.shs, "SHS")   # anchor=None
        zs = [p.z for c in adsk_stub.CALLS
              if c[0] == "SketchLines.addByTwoPoints" for p in (c[1][0], c[1][1])]
        self.assertAlmostEqual(max(zs), 0.5, places=6)   # half-height in cm


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
    # Rotation + start/end offsets are per-line table cells (columns 5-7), not
    # separate global inputs.  Columns 8-10 are Inverse / Bend Die / Cope Depth.
    tbl = inputs.addTableCommandInput(
        'params', 'Per Line', 11, '1:3:3:1:1:2:2:2:1:3:2')
    sync = inputs.addBoolValueInput('sync_all', 'Sync all', True, '', True)
    tbl.addToolbarCommandInput(sync)
    entry._make_header_row(inputs, tbl)   # row 0 = read-only column titles
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

    def _select_family(self, inputs, abbr):
        fam = inputs.itemById('family')
        for i in range(fam.listItems.count):
            fam.listItems.item(i).isSelected = fam.listItems.item(i).name.startswith(abbr)
        return fam

    def test_sync_table_rows_one_row_per_line(self):
        _cmd, inputs, tbl = _make_dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))]
        entry._sync_table_rows(inputs, lines)
        self.assertEqual(len(entry._row_ids), 2)
        self.assertEqual(tbl.rowCount, 3)   # header row + 2 data rows

    def test_header_row_has_column_titles(self):
        _cmd, inputs, tbl = _make_dialog()
        # Row 0 is the read-only header; its cells carry the column titles.
        self.assertEqual(inputs.itemById('hdr_0').value, '#')
        self.assertEqual(inputs.itemById('hdr_1').value, 'Joint Start')
        self.assertEqual(inputs.itemById('hdr_2').value, 'Joint End')
        self.assertEqual(inputs.itemById('hdr_3').value, 'Through')
        self.assertEqual(inputs.itemById('hdr_4').value, 'Saddle')
        self.assertEqual(inputs.itemById('hdr_5').value, 'Rotation')
        self.assertEqual(inputs.itemById('hdr_6').value, 'Offset Start')
        self.assertEqual(inputs.itemById('hdr_7').value, 'Offset End')
        self.assertEqual(inputs.itemById('hdr_8').value, 'Inverse')
        self.assertEqual(inputs.itemById('hdr_9').value, 'Bend Die')
        self.assertEqual(inputs.itemById('hdr_10').value, 'Cope Depth')

    def test_sync_table_rows_shrinks(self):
        _cmd, inputs, tbl = _make_dialog()
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 3)
        self.assertEqual(len(entry._row_ids), 3)
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        self.assertEqual(len(entry._row_ids), 1)
        self.assertEqual(tbl.rowCount, 2)   # header row + 1 data row

    def test_sync_table_rows_preserves_existing(self):
        _cmd, inputs, tbl = _make_dialog()
        self._select_family(inputs, 'SHS')
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        first_id = entry._row_ids[0]['joint_s']
        dd = inputs.itemById(first_id)
        for i in range(dd.listItems.count):
            dd.listItems.item(i).isSelected = dd.listItems.item(i).name == 'Butt'
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine(), adsk_stub.FakeLine()])
        # Row 0 keeps its identity and value; a new row 1 is appended.
        self.assertEqual(entry._row_ids[0]['joint_s'], first_id)
        self.assertEqual(entry._row_joint_at(inputs, 0, 'joint_s'), 'butt')
        self.assertEqual(len(entry._row_ids), 2)

    def test_cell_column_mapping(self):
        self.assertEqual(entry._cell_column('joint_s_5'), 'joint_s')
        self.assertEqual(entry._cell_column('joint_e_5'), 'joint_e')
        self.assertEqual(entry._cell_column('through_5'), 'through')
        self.assertEqual(entry._cell_column('saddle_5'), 'saddle')
        self.assertEqual(entry._cell_column('rot_5'), 'rot')
        self.assertEqual(entry._cell_column('os_5'), 'os')
        self.assertEqual(entry._cell_column('oe_5'), 'oe')
        self.assertEqual(entry._cell_column('inv_5'), 'inv')
        self.assertEqual(entry._cell_column('die_5'), 'die')
        self.assertEqual(entry._cell_column('cd_5'), 'cd')
        self.assertIsNone(entry._cell_column('num_5'))
        self.assertIsNone(entry._cell_column('hdr_1'))
        self.assertIsNone(entry._cell_column('family'))
        self.assertIsNone(entry._cell_column('rotation'))

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
        # Rotation/offset are per-LINE cells: each row reads its own values.
        self.assertEqual(entry._row_params(inputs, 0)[0], 0.5)
        self.assertEqual(entry._row_params(inputs, 1)[1], 2.0)
        # A row past the table defaults to zeros.
        self.assertEqual(entry._row_params(inputs, 9), (0.0, 0.0, 0.0))

    def test_sync_all_propagates_joint_start(self):
        _cmd, inputs, tbl = _make_dialog()
        self._select_family(inputs, 'SHS')
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 3)
        dd0 = inputs.itemById(entry._row_ids[0]['joint_s'])
        for i in range(dd0.listItems.count):
            dd0.listItems.item(i).isSelected = dd0.listItems.item(i).name == 'Miter'
        entry.command_input_changed(_Args(input=dd0, inputs=inputs))
        for r in (1, 2):
            self.assertEqual(entry._row_joint_at(inputs, r, 'joint_s'), 'miter')
            # The End dropdown is a different column and stays untouched.
            self.assertEqual(entry._row_joint_at(inputs, r, 'joint_e'), 'none')

    def test_sync_all_propagates_checkbox(self):
        _cmd, inputs, tbl = _make_dialog()
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 2)
        sa1 = inputs.itemById(entry._row_ids[1]['saddle'])
        sa1.value = True
        entry.command_input_changed(_Args(input=sa1, inputs=inputs))
        self.assertTrue(inputs.itemById(entry._row_ids[0]['saddle']).value)

    def test_sync_all_propagates_rotation_and_offset(self):
        # Rotation / Offset Start / Offset End are per-row cells too, so Sync
        # all must mirror them across rows like the other value columns.
        _cmd, inputs, tbl = _make_dialog()
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 3)
        rot1 = inputs.itemById(entry._row_ids[1]['rot'])
        rot1.value = 0.75
        entry.command_input_changed(_Args(input=rot1, inputs=inputs))
        os0 = inputs.itemById(entry._row_ids[0]['os'])
        os0.value = 3.0
        entry.command_input_changed(_Args(input=os0, inputs=inputs))
        for r in (0, 1, 2):
            self.assertAlmostEqual(entry._row_params(inputs, r)[0], 0.75)
            self.assertAlmostEqual(entry._row_params(inputs, r)[1], 3.0)

    def test_unsynced_rows_keep_their_own_rotation(self):
        _cmd, inputs, tbl = _make_dialog()
        inputs.itemById('sync_all').value = False
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 2)
        rot0 = inputs.itemById(entry._row_ids[0]['rot'])
        rot0.value = 0.5
        entry.command_input_changed(_Args(input=rot0, inputs=inputs))
        self.assertAlmostEqual(entry._row_params(inputs, 0)[0], 0.5)
        self.assertAlmostEqual(entry._row_params(inputs, 1)[0], 0.0)

    def test_unsynced_arrows_anchor_each_line(self):
        # Unsynced: row r's offset arrows anchor to line r, so every element
        # carries its own handles on the canvas.
        _cmd, inputs, tbl = _make_dialog()
        inputs.itemById('sync_all').value = False
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))]
        entry._sync_table_rows(inputs, lines)
        entry._update_manipulators(inputs, lines)
        for r in (0, 1):
            self.assertEqual(
                inputs.itemById(entry._row_ids[r]['os']).manipulatorCount, 1)
            self.assertEqual(
                inputs.itemById(entry._row_ids[r]['rot']).manipulatorCount, 0)

    def test_no_propagation_when_unsynced(self):
        _cmd, inputs, tbl = _make_dialog()
        inputs.itemById('sync_all').value = False
        self._select_family(inputs, 'SHS')
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 2)
        dd0 = inputs.itemById(entry._row_ids[0]['joint_s'])
        for i in range(dd0.listItems.count):
            dd0.listItems.item(i).isSelected = dd0.listItems.item(i).name == 'Miter'
        entry.command_input_changed(_Args(input=dd0, inputs=inputs))
        self.assertEqual(entry._row_joint_at(inputs, 1, 'joint_s'), 'none')

    def test_synced_arrows_only_row_zero(self):
        # Rotation/offset are per-row cells; when Sync all is on, only row 0's
        # arrows anchor to the canvas (editing any row propagates to all).
        _cmd, inputs, tbl = _make_dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))]
        entry._sync_table_rows(inputs, lines)
        entry._update_manipulators(inputs, lines)
        self.assertEqual(inputs.itemById(entry._row_ids[0]['rot']).manipulatorCount, 0)
        self.assertEqual(
            inputs.itemById(entry._row_ids[0]['os']).manipulatorCount, 1)
        self.assertEqual(
            inputs.itemById(entry._row_ids[1]['os']).manipulatorCount, 0)

    def test_select_builds_rows_and_anchors(self):
        cmd, inputs, tbl = _make_dialog()
        sel = self._pick(inputs, [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0))])
        entry.command_select(_Args(activeInput=sel))
        self.assertEqual(len(entry._row_ids), 1)
        self.assertEqual(
            inputs.itemById(entry._row_ids[0]['os']).manipulatorCount, 1)


class TestJointColumn(unittest.TestCase):
    """The per-line Joint dropdown + how it feeds the build offsets."""

    def setUp(self):
        adsk_stub.reset()
        entry._row_ids = []
        entry._uid_counter[0] = 0
        entry._FAMILIES = prof.annotate_families(prof.load_profiles())

    def _select_family(self, inputs, abbr):
        fam = inputs.itemById('family')
        for i in range(fam.listItems.count):
            fam.listItems.item(i).isSelected = fam.listItems.item(i).name.startswith(abbr)
        return fam

    def _set_joint(self, inputs, r, key, label):
        dd = inputs.itemById(entry._row_ids[r][key])
        for i in range(dd.listItems.count):
            dd.listItems.item(i).isSelected = dd.listItems.item(i).name == label

    def test_joint_dropdown_lists_family_joints(self):
        _cmd, inputs, tbl = _make_dialog()
        self._select_family(inputs, 'IPE')
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 2)
        for key in ('joint_s', 'joint_e'):
            dd = inputs.itemById(entry._row_ids[0][key])
            labels = [dd.listItems.item(i).name for i in range(dd.listItems.count)]
            self.assertEqual(labels, ['None', 'Butt', 'Miter'])  # IPE: no cope/bend

    def test_joint_defaults_to_none(self):
        _cmd, inputs, tbl = _make_dialog()
        self._select_family(inputs, 'SHS')
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        self.assertEqual(entry._row_joints(inputs, [None]), [('none', 'none')])

    def test_row_joint_reads_selection(self):
        _cmd, inputs, tbl = _make_dialog()
        self._select_family(inputs, 'SHS')
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        self._set_joint(inputs, 0, 'joint_e', 'Butt')
        # The two ends are independent: only the End dropdown changed.
        self.assertEqual(entry._row_joints(inputs, [None]), [('none', 'butt')])

    def test_per_end_joints_may_differ(self):
        # A joint belongs to a line END, so start and end can differ (miter one
        # way, butt the other) -- the core of the per-end model.
        _cmd, inputs, tbl = _make_dialog()
        self._select_family(inputs, 'SHS')
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        self._set_joint(inputs, 0, 'joint_s', 'Miter')
        self._set_joint(inputs, 0, 'joint_e', 'Butt')
        self.assertEqual(entry._row_joints(inputs, [None]), [('miter', 'butt')])

    def test_family_change_rebuilds_and_preserves_choice(self):
        _cmd, inputs, tbl = _make_dialog()
        self._select_family(inputs, 'SHS')
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        self._set_joint(inputs, 0, 'joint_s', 'Miter')
        # Switch to IPE (still supports Miter) -> choice preserved, cope/bend gone.
        self._select_family(inputs, 'IPE')
        entry.command_input_changed(_Args(input=inputs.itemById('family'), inputs=inputs))
        dd = inputs.itemById(entry._row_ids[0]['joint_s'])
        labels = [dd.listItems.item(i).name for i in range(dd.listItems.count)]
        self.assertEqual(labels, ['None', 'Butt', 'Miter'])
        self.assertEqual(entry._row_joint_at(inputs, 0, 'joint_s'), 'miter')

    def test_family_change_drops_unsupported_choice(self):
        _cmd, inputs, tbl = _make_dialog()
        self._select_family(inputs, 'SHS')
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        self._set_joint(inputs, 0, 'joint_s', 'Bend')
        # Switch to IPE (no Bend) -> falls back to None.
        self._select_family(inputs, 'IPE')
        entry.command_input_changed(_Args(input=inputs.itemById('family'), inputs=inputs))
        self.assertEqual(entry._row_joint_at(inputs, 0, 'joint_s'), 'none')

    def test_sync_all_propagates_joint_end(self):
        _cmd, inputs, tbl = _make_dialog()
        self._select_family(inputs, 'SHS')
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 3)
        dd0 = inputs.itemById(entry._row_ids[0]['joint_e'])
        for i in range(dd0.listItems.count):
            dd0.listItems.item(i).isSelected = dd0.listItems.item(i).name == 'Miter'
        entry.command_input_changed(_Args(input=dd0, inputs=inputs))
        for r in (1, 2):
            self.assertEqual(entry._row_joint_at(inputs, r, 'joint_e'), 'miter')

    def test_joint_offsets_applied_to_line(self):
        # A butt is a pure axial trim (no boolean): the incoming member stops
        # short at the through member's near face and the through member
        # extends past the vertex for a flush corner.  For two SHS members the
        # trim/grow is each section's half-depth.
        _cmd, inputs, tbl = _make_dialog()
        self._select_family(inputs, 'SHS')
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((0, -10, 0), (0, 0, 0))]
        entry._sync_table_rows(inputs, lines)
        self._set_joint(inputs, 1, 'joint_e', 'Butt')   # line 1's END is the corner
        family = entry._selected_family(inputs)
        geom = prof.section_geometry(prof.designations(family)[0])
        offs = entry._joint_offsets(inputs, lines, geom)
        half = jt.member_depth(geom) * jt.MM_TO_CM / 2.0
        self.assertAlmostEqual(offs[0][0], -half)   # through member extends
        self.assertEqual(offs[0][1], 0.0)
        self.assertEqual(offs[1][0], 0.0)
        self.assertAlmostEqual(offs[1][1], -half)   # butt member stops short
        # No boolean cut is planned for a butt.
        joints = entry._row_joints(inputs, lines)
        self.assertEqual(jt.corner_cuts(lines, joints), [])


class TestBendBuild(unittest.TestCase):
    """Swept-bend arc construction (revolve) + die-radius wiring."""

    def setUp(self):
        adsk_stub.reset()
        self.root = adsk_stub.FakeRoot()
        families = prof.annotate_families(prof.load_profiles())
        self.shs = next(f for f in families if f['abbreviation'] == 'SHS')
        self.geom = prof.section_geometry(prof.designations(self.shs)[0])
        entry._DIES = bd.load_bending_dies()
        entry._FAMILIES = families

    def _shs_dialog(self):
        """A dialog whose family dropdown is populated and set to SHS."""
        _cmd, inputs, tbl = _make_dialog()
        fam = inputs.itemById('family')
        for label in prof.family_labels(entry._FAMILIES):
            fam.listItems.add(label, False)
        for i in range(fam.listItems.count):
            fam.listItems.item(i).isSelected = fam.listItems.item(i).name.startswith('SHS')
        return inputs

    def test_bend_arc_revolve_is_new_body(self):
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        plans = jt.bend_plan(lines, ['bend', 'bend'], [150.0, 150.0])
        self.assertEqual(len(plans), 1)
        idx, _role, tangent = plans[0]['tangent'][0]
        objs = entry._build_bend_arc(self.root, lines[idx], tangent, plans[0],
                                     self.geom, ref=None)
        self.assertIsInstance(objs, tuple)
        fusion = adsk_stub.sys.modules["adsk.fusion"]
        ci = [c for c in adsk_stub.CALLS if c[0] == "RevolveFeatures.createInput"]
        self.assertEqual(ci[0][1][0], fusion.FeatureOperations.NewBodyFeatureOperation)
        self.assertIn("RevolveFeatures.add", _names())

    def test_bend_arc_revolve_angle_equals_theta(self):
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        plans = jt.bend_plan(lines, ['bend', 'bend'], [150.0, 150.0])
        idx, _role, tangent = plans[0]['tangent'][0]
        entry._build_bend_arc(self.root, lines[idx], tangent, plans[0],
                              self.geom, ref=None)
        ae = [c for c in adsk_stub.CALLS if c[0] == "RevolveFeatureInput.setAngleExtent"]
        self.assertAlmostEqual(ae[0][1][1].value, plans[0]['theta'])

    def test_bend_sweep_builds_arc_path_and_sweep(self):
        # Phase 3: a bend is a true swept body -- a die-radius centerline arc is
        # the path and the section is swept along it (not a revolve about the
        # bend axis).  The sweep builder draws the arc, makes a Path from it,
        # and adds a SweepFeature as a new body.
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        plans = jt.bend_plan(lines, ['bend', 'bend'], [150.0, 150.0])
        idx, _role, tangent = plans[0]['tangent'][0]
        objs = entry._build_bend_arc_sweep(self.root, lines[idx], tangent,
                                           plans[0], self.geom, ref=None)
        self.assertIsInstance(objs, tuple)
        names = _names()
        self.assertIn('SketchArcs.addByCenterStartEnd', names)  # the arc path
        self.assertIn('Path.create', names)                     # arc -> Path
        ci = [c for c in adsk_stub.CALLS
              if c[0] == 'SweepFeatures.createInput']
        self.assertEqual(len(ci), 1)
        fusion = adsk_stub.sys.modules["adsk.fusion"]
        self.assertEqual(ci[0][1][0],
                         fusion.FeatureOperations.NewBodyFeatureOperation)
        self.assertIn('SweepFeatures.add', names)
        self.assertNotIn('RevolveFeatures.add', names)         # sweep, not revolve

    def test_build_bend_arcs_prefers_sweep(self):
        # The dispatcher tries the sweep first; a healthy corner builds via
        # SweepFeatures and never falls through to the revolve builder.
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        arcs, trims = entry._build_bend_arcs(self.root, lines,
                                             ['bend', 'bend'],
                                             [150.0, 150.0], self.geom,
                                             ref=None)
        self.assertEqual(len(arcs), 1)
        names = _names()
        self.assertIn('SweepFeatures.add', names)
        self.assertNotIn('RevolveFeatures.add', names)

    def test_build_bend_arcs_falls_back_to_revolve(self):
        # When the sweep cannot be built (returns None), the corner is still
        # rounded by revolving the section about the bend axis.
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        orig = entry._build_bend_arc_sweep
        entry._build_bend_arc_sweep = lambda *a, **k: None
        try:
            arcs, trims = entry._build_bend_arcs(self.root, lines,
                                                 ['bend', 'bend'],
                                                 [150.0, 150.0], self.geom,
                                                 ref=None)
        finally:
            entry._build_bend_arc_sweep = orig
        self.assertEqual(len(arcs), 1)
        names = _names()
        self.assertIn('RevolveFeatures.add', names)
        self.assertNotIn('SweepFeatures.add', names)

    def test_bend_radii_from_die(self):
        # A bend leg resolves to the family's default (tightest) CLR; a non-bend
        # leg to 0.  Matching is by family alone, so any SHS designation works.
        des = dict(prof.designations(self.shs)[0])
        des['_abbreviation'] = 'SHS'
        self.assertIsNotNone(bd.die_for_designation(entry._DIES, des, 'SHS'))
        inputs = self._shs_dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        entry._sync_table_rows(inputs, lines)
        for r in (0, 1):
            # The corner is at both lines' START (0,0,0), so mark the start end.
            dd = inputs.itemById(entry._row_ids[r]['joint_s'])
            for i in range(dd.listItems.count):
                dd.listItems.item(i).isSelected = dd.listItems.item(i).name == 'Bend'
        radii = entry._bend_radii(inputs, lines, des)
        self.assertTrue(all(x > 0 for x in radii))

    def test_build_bend_arcs_returns_one_per_corner(self):
        des = dict(prof.designations(self.shs)[0])
        des['_abbreviation'] = 'SHS'
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        arcs, trims = entry._build_bend_arcs(self.root, lines,
                                            ['bend', 'bend'],
                                            [150.0, 150.0], self.geom, ref=None)
        self.assertEqual(len(arcs), 1)   # one rounded corner
        self.assertEqual(trims, [])      # both legs built here: nothing to trim

    def test_bend_onto_existing_splits_context_end(self):
        # A selected bend leg whose END meets an EXISTING member's END at a
        # corner: the arc is revolved from the new leg, and the existing body's
        # poking end is SPLIT at the tangent plane.  Split-only: both halves
        # stay (auto-Removing the waste mis-picks the kept half on members that
        # are part of a bent chain), so the user deletes the stray sliver.
        existing_body = adsk_stub.FakeBody(name="EXISTING", center=(15, 0, 0))
        # A realistic extent (0..30 along X) so the already-trimmed guard in
        # _trim_bend_context_end sees material past the tangent plane.
        existing_body.boundingBox = adsk_stub.FakeBoundingBox((15, 0, 0),
                                                              half=15)
        ctx = [{'line': entry._ContextLine((0, 0, 0), (30, 0, 0), existing_body),
                'geom': {'kind': 'circles', 'radii': [10.0, 9.0]},
                'basis': None, 'body': existing_body}]
        lines = [adsk_stub.FakeLine((30, 0, 0), (30, 0, 30))]
        # Sanity: bend_plan must see the context leg as the partner.
        plans = jt.bend_plan(lines, [('bend', 'none')], [150.0], context=ctx)
        self.assertEqual(len(plans), 1)
        self.assertLess(plans[0]['tangent'][1][0], 0)   # partner is a context leg
        arcs, trims = entry._build_bend_arcs(
            self.root, lines, [('bend', 'none')], [150.0], self.geom,
            ref=None, context=ctx)
        self.assertEqual(len(arcs), 1)
        names = [c[0] for c in adsk_stub.CALLS]
        # The existing body was split -- and NOTHING is Removed or deleted:
        # both halves stay for the user to clean up.
        self.assertIn('SplitBodyFeatures.add', names)
        self.assertNotIn('RemoveFeatures.add', names)
        self.assertNotIn('Body.deleteMe', names)
        # The split targeted the EXISTING member's body.
        ci = [c for c in adsk_stub.CALLS
              if c[0] == 'SplitBodyFeatures.createInput'][0]
        self.assertIs(ci[1][0], existing_body)
        # Trims come back as flat teardown objects (split, sketch, plane).
        self.assertEqual(len(trims), 3)

    def test_bend_bases_put_a_flat_face_in_the_bend_plane(self):
        # Point 3: a square tube can only be bent about a FLAT face, never a
        # rolled corner.  Each bend leg's basis must have axis_v parallel to the
        # bend axis (a = u x v), i.e. a pair of flat faces parallel to the bend
        # plane.  A non-bend leg gets None (uses compute_basis).
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0)),
                 adsk_stub.FakeLine((0, 30, 0), (0, 60, 0))]   # no bend here
        joints = [('bend', 'none'), ('bend', 'none'), ('none', 'none')]
        clr = [150.0, 150.0, 0.0]
        bases = entry._bend_bases(lines, joints, ref=(0, 0, 1),
                                  clr_by_line=clr)
        plans = jt.bend_plan(lines, joints, clr)
        a = plans[0]['axis']
        for idx in (0, 1):
            self.assertIsNotNone(bases[idx])
            _u, v = bases[idx]
            # axis_v parallel to the bend axis -> a flat face lies in the plane.
            cross = (v[1] * a[2] - v[2] * a[1], v[2] * a[0] - v[0] * a[2],
                     v[0] * a[1] - v[1] * a[0])
            self.assertAlmostEqual(sum(c * c for c in cross), 0.0, places=6)
        self.assertIsNone(bases[2])      # not a bend leg

    def test_bend_bases_planar_matches_compute_basis(self):
        # For a PLANAR bend the flat-face basis must equal compute_basis(dir,
        # ref) exactly -- so the fix is a no-op on existing planar frames (no
        # regression); it only changes non-planar (3D) bends.
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        joints = ['bend', 'bend']
        clr = [150.0, 150.0]
        ref = (0, 0, 1)                  # the bend plane's normal (planar)
        bases = entry._bend_bases(lines, joints, ref=ref, clr_by_line=clr)
        for i, line in enumerate(lines):
            expect = prof.compute_basis(entry._line_direction(line), ref)
            got = bases[i]
            for a, b in zip(expect, got):
                for k in range(3):
                    self.assertAlmostEqual(a[k], b[k], places=6)


class TestBendCopeColumns(unittest.TestCase):
    """Per-line Inverse / Bend Die / Cope Depth columns: enablement and reads."""

    def setUp(self):
        adsk_stub.reset()
        entry._row_ids = []
        entry._uid_counter[0] = 0
        entry._DIES = bd.load_bending_dies()
        entry._FAMILIES = prof.annotate_families(prof.load_profiles())
        self.shs = next(f for f in entry._FAMILIES
                        if f['abbreviation'] == 'SHS')

    def _dialog(self, designation='40x40x2.0'):
        """A dialog set to SHS / a designation that matches several dies."""
        _cmd, inputs, _tbl = _make_dialog()
        fam = inputs.itemById('family')
        for label in prof.family_labels(entry._FAMILIES):
            fam.listItems.add(label, False)
        for i in range(fam.listItems.count):
            fam.listItems.item(i).isSelected = fam.listItems.item(i).name.startswith('SHS')
        des = inputs.itemById('designation')
        for label in prof.designation_labels(self.shs):
            des.listItems.add(label, False)
        idx = prof.designation_labels(self.shs).index(designation)
        for i in range(des.listItems.count):
            des.listItems.item(i).isSelected = (i == idx)
        return inputs

    def _set_joint(self, inputs, r, key, label):
        dd = inputs.itemById(entry._row_ids[r][key])
        for i in range(dd.listItems.count):
            dd.listItems.item(i).isSelected = dd.listItems.item(i).name == label

    def test_inverse_and_die_enabled_only_for_bend(self):
        inputs = self._dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        entry._sync_table_rows(inputs, lines)
        ids = entry._row_ids[0]
        # Default (no bend): Inverse and Bend Die greyed out.
        self.assertFalse(inputs.itemById(ids['inv']).isEnabled)
        self.assertFalse(inputs.itemById(ids['die']).isEnabled)
        # Marking an end Bend re-enables both.
        self._set_joint(inputs, 0, 'joint_s', 'Bend')
        entry._update_bend_columns(inputs)
        self.assertTrue(inputs.itemById(ids['inv']).isEnabled)
        self.assertTrue(inputs.itemById(ids['die']).isEnabled)

    def test_cope_depth_enabled_for_cope_and_saddled_butt(self):
        inputs = self._dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        entry._sync_table_rows(inputs, lines)
        ids = entry._row_ids[0]
        self.assertFalse(inputs.itemById(ids['cd']).isEnabled)
        # A Cope end enables the depth spinner.
        self._set_joint(inputs, 0, 'joint_s', 'Cope')
        entry._update_bend_columns(inputs)
        self.assertTrue(inputs.itemById(ids['cd']).isEnabled)
        # A plain Butt disables it again; saddling the Butt re-enables it.
        self._set_joint(inputs, 0, 'joint_s', 'Butt')
        entry._update_bend_columns(inputs)
        self.assertFalse(inputs.itemById(ids['cd']).isEnabled)
        inputs.itemById(ids['saddle']).value = True
        entry._update_bend_columns(inputs)
        self.assertTrue(inputs.itemById(ids['cd']).isEnabled)

    def test_row_inverses_reads_checkboxes(self):
        inputs = self._dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        entry._sync_table_rows(inputs, lines)
        inputs.itemById(entry._row_ids[1]['inv']).value = True
        self.assertEqual(entry._row_inverses(inputs, lines), [False, True])

    def test_row_cope_depths_reads_spinners(self):
        inputs = self._dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        entry._sync_table_rows(inputs, lines)
        # The spinner's .value is in database units (cm); a 1.2 cm entry is the
        # 12 mm the joint layer expects, so _row_cope_depths converts cm -> mm.
        inputs.itemById(entry._row_ids[0]['cd']).value = 1.2
        self.assertEqual(entry._row_cope_depths(inputs, lines), [12.0, 0.0])

    def test_die_dropdown_lists_family_clrs(self):
        inputs = self._dialog('40x40x2.0')
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        entry._sync_table_rows(inputs, lines)
        dd = inputs.itemById(entry._row_ids[0]['die'])
        # The dropdown lists every CLR the SHS family offers (size no longer
        # filters -- the shop picks the radius it owns).
        self.assertEqual(dd.listItems.count,
                         len(bd.dies_for_family(entry._DIES, 'SHS')))
        self.assertGreater(dd.listItems.count, 1)
        # Sorted by ascending CLR and the tightest is pre-selected.
        self.assertIn('R57.15', dd.listItems.item(0).name)
        self.assertTrue(dd.listItems.item(0).isSelected)

    def test_row_die_clr_uses_selected_die(self):
        inputs = self._dialog('40x40x2.0')
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        entry._sync_table_rows(inputs, lines)
        des = entry._current_designation(inputs)
        dd = inputs.itemById(entry._row_ids[0]['die'])
        # Default selection (item 0) is the tightest die, CLR 57.15.
        self.assertAlmostEqual(entry._row_die_clr(inputs, 0, des), 57.15)
        # Choosing a looser die changes the radius the bend uses.
        for i in range(dd.listItems.count):
            dd.listItems.item(i).isSelected = 'R190.5' in dd.listItems.item(i).name
        self.assertAlmostEqual(entry._row_die_clr(inputs, 0, des), 190.5)

    def test_rebuild_dies_preserves_still_available_choice(self):
        inputs = self._dialog('40x40x2.0')
        lines = [adsk_stub.FakeLine((0, 0, 0), (30, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 30, 0))]
        entry._sync_table_rows(inputs, lines)
        dd = inputs.itemById(entry._row_ids[0]['die'])
        # Pick the R142.88 die, then rebuild for the same designation.
        for i in range(dd.listItems.count):
            dd.listItems.item(i).isSelected = 'R142.88' in dd.listItems.item(i).name
        entry._rebuild_dies(inputs)
        # The choice survives the rebuild (still offered for this size).
        idx = entry._dropdown_index(dd)
        self.assertIn('R142.88', dd.listItems.item(idx).name)


class TestCornerJointPropagation(unittest.TestCase):
    """A relationship joint (miter/bend) mirrors onto the partner at a corner.

    The user sets it on ONE member's end and the preview reacts -- they no
    longer have to edit the corresponding dropdown on the other line too.
    """

    def setUp(self):
        adsk_stub.reset()
        entry._row_ids = []
        entry._uid_counter[0] = 0
        entry._FAMILIES = prof.annotate_families(prof.load_profiles())

    def _corner(self, sync=True, family=None):
        """A dialog with two lines meeting at the origin (an L-corner)."""
        _cmd, inputs, _tbl = _make_dialog()
        inputs.itemById('sync_all').value = sync
        if family:
            fam = inputs.itemById('family')
            for label in prof.family_labels(entry._FAMILIES):
                fam.listItems.add(label, False)
            for i in range(fam.listItems.count):
                fam.listItems.item(i).isSelected = (
                    fam.listItems.item(i).name.startswith(family))
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))]
        sel = inputs.itemById('path')
        for ln in lines:
            sel.addSelection(ln)
        entry._sync_table_rows(inputs, lines)
        return inputs, lines

    def _set(self, inputs, r, key, label):
        dd = inputs.itemById(entry._row_ids[r][key])
        for i in range(dd.listItems.count):
            dd.listItems.item(i).isSelected = dd.listItems.item(i).name == label
        return dd

    def test_miter_mirrors_to_partner_start_end(self):
        # Both lines START at the origin, so setting row 0's start Miter must set
        # row 1's start too -- even with Sync all OFF (it is a corner link, not a
        # universal sync).
        inputs, _lines = self._corner(sync=False)
        dd = self._set(inputs, 0, 'joint_s', 'Miter')
        entry.command_input_changed(_Args(input=dd, inputs=inputs))
        self.assertEqual(entry._row_joint_at(inputs, 0, 'joint_s'), 'miter')
        self.assertEqual(entry._row_joint_at(inputs, 1, 'joint_s'), 'miter')

    def test_miter_maps_end_to_start_across_lines(self):
        # A real L: line 0 runs +X to (10,0,0), line 1 runs +Y FROM (10,0,0).
        # The shared vertex is line 0's END and line 1's START, so a miter set on
        # one must land on the OTHER end dropdown of the partner -- the corner
        # link maps start<->end per line, not blindly start->start.
        _cmd, inputs, _tbl = _make_dialog()
        inputs.itemById('sync_all').value = False
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((10, 0, 0), (10, 10, 0))]
        sel = inputs.itemById('path')
        for ln in lines:
            sel.addSelection(ln)
        entry._sync_table_rows(inputs, lines)
        dd = self._set(inputs, 0, 'joint_e', 'Miter')   # line 0's end at vertex
        entry.command_input_changed(_Args(input=dd, inputs=inputs))
        self.assertEqual(entry._row_joint_at(inputs, 0, 'joint_e'), 'miter')
        # Partner's START touches the same vertex -> its start dropdown changes.
        self.assertEqual(entry._row_joint_at(inputs, 1, 'joint_s'), 'miter')
        # And line 0's far end (the start, at the origin) is a different corner.
        self.assertEqual(entry._row_joint_at(inputs, 0, 'joint_s'), 'none')

    def test_bend_mirrors_to_partner(self):
        # A swept bend needs both legs; setting it on one line's end sets the
        # partner's matching end so the arc resolves from a single edit.
        inputs, _lines = self._corner(sync=False, family='SHS')
        dd = self._set(inputs, 0, 'joint_s', 'Bend')
        entry.command_input_changed(_Args(input=dd, inputs=inputs))
        self.assertEqual(entry._row_joint_at(inputs, 0, 'joint_s'), 'bend')
        self.assertEqual(entry._row_joint_at(inputs, 1, 'joint_s'), 'bend')

    def test_butt_does_not_mirror(self):
        # A butt is per-member (a lone butt already reads its neighbour as the
        # through member), so it must NOT be copied onto the partner.
        inputs, _lines = self._corner(sync=False)
        dd = self._set(inputs, 0, 'joint_s', 'Butt')
        entry.command_input_changed(_Args(input=dd, inputs=inputs))
        self.assertEqual(entry._row_joint_at(inputs, 0, 'joint_s'), 'butt')
        self.assertEqual(entry._row_joint_at(inputs, 1, 'joint_s'), 'none')

    def test_none_does_not_mirror(self):
        # Clearing a joint to None is also per-member: the partner keeps its own.
        inputs, _lines = self._corner(sync=False)
        # First link a miter on both, then set row 0 back to None.
        dd = self._set(inputs, 0, 'joint_s', 'Miter')
        entry.command_input_changed(_Args(input=dd, inputs=inputs))
        self.assertEqual(entry._row_joint_at(inputs, 1, 'joint_s'), 'miter')
        dd = self._set(inputs, 0, 'joint_s', 'None')
        entry.command_input_changed(_Args(input=dd, inputs=inputs))
        self.assertEqual(entry._row_joint_at(inputs, 0, 'joint_s'), 'none')
        # Row 1 keeps its miter -- None is not propagated.
        self.assertEqual(entry._row_joint_at(inputs, 1, 'joint_s'), 'miter')

    def test_corner_link_independent_of_the_other_end(self):
        # Only the end that touches the shared vertex changes; the far end of the
        # partner line is a different corner and stays put.
        inputs, _lines = self._corner(sync=False)
        self._set(inputs, 1, 'joint_e', 'Butt')   # line 1's far end (0,10,0)
        dd = self._set(inputs, 0, 'joint_s', 'Miter')
        entry.command_input_changed(_Args(input=dd, inputs=inputs))
        self.assertEqual(entry._row_joint_at(inputs, 1, 'joint_s'), 'miter')
        self.assertEqual(entry._row_joint_at(inputs, 1, 'joint_e'), 'butt')

    def test_no_lines_selected_is_a_noop(self):
        # With nothing in the path selection there are no lines to relate, so a
        # joint edit must not raise (corner detection has nothing to work with).
        _cmd, inputs, _tbl = _make_dialog()
        entry._sync_table_rows(inputs, [])
        self.assertEqual(entry._row_ids, [])
        # No rows exist, so there is nothing to change -- just assert no crash.
        entry._propagate_corner_joint(inputs, [], 0, 'joint_s', 'miter')


class TestReuniteBendContext(unittest.TestCase):
    """Context recovery of a swept-bend member's stubbed legs.

    A member built by an earlier bend run has its centreline legs trimmed to the
    die tangent points, so the two legs no longer share the drawn vertex.  A new
    member butting/copes into that corner finds no corner unless the stubs are
    extended back to their (virtual) meeting point.
    """

    def setUp(self):
        adsk_stub.reset()

    def _ctx(self, start, end):
        return {'line': entry._ContextLine(start, end, adsk_stub.FakeBody()),
                'geom': {'kind': 'circles', 'radii': [1.0]},
                'basis': None, 'body': None}

    def _ends(self, m):
        s, e = jt.line_endpoints(m['line'])
        return (tuple(round(x, 3) for x in s),
                tuple(round(x, 3) for x in e))

    def test_bend_stubs_reunite_at_the_vertex(self):
        # A 90-deg bend's legs, each trimmed back 0.3 cm to its tangent point.
        a = self._ctx((0, 0, 0), (59.7, 0, 0))
        b = self._ctx((60, 0, 0.3), (60, 0, 60))
        entry._reunite_bend_context([a, b])
        self.assertEqual(self._ends(a), ((0.0, 0.0, 0.0), (60.0, 0.0, 0.0)))
        self.assertEqual(self._ends(b), ((60.0, 0.0, 0.0), (60.0, 0.0, 60.0)))

    def test_corner_now_detected_by_a_new_member(self):
        a = self._ctx((0, 0, 0), (59.7, 0, 0))
        b = self._ctx((60, 0, 0.3), (60, 0, 60))
        entry._reunite_bend_context([a, b])
        new = adsk_stub.FakeLine((60, 30, 0), (60, 0, 0))
        corners = jt.detect_corners([new],
                                    context=[m['line'] for m in (a, b)])
        # The new member's END coincides with the reunited vertex, shared by
        # both context legs.
        self.assertEqual(len(corners), 1)
        self.assertEqual(corners[0]['members'],
                         [(0, 'end'), (-1, 'end'), (-2, 'start')])

    def test_collinear_run_is_left_alone(self):
        # Two straight segments of one run (no bend): collinear, so there is no
        # vertex to reconstruct and the legs must not be moved.
        a = self._ctx((0, 0, 0), (30, 0, 0))
        b = self._ctx((30, 0, 0), (60, 0, 0))
        entry._reunite_bend_context([a, b])
        self.assertEqual(self._ends(a), ((0.0, 0.0, 0.0), (30.0, 0.0, 0.0)))
        self.assertEqual(self._ends(b), ((30.0, 0.0, 0.0), (60.0, 0.0, 0.0)))

    def test_t_junction_is_not_reunited(self):
        # A leg whose END lands mid-run on another (a T): the meeting point is
        # not past both stubs, so nothing is a bend -- leave both alone.
        run = self._ctx((0, 0, 0), (60, 0, 0))
        incoming = self._ctx((30, 0, 20), (30, 0, 0))
        entry._reunite_bend_context([run, incoming])
        self.assertEqual(self._ends(run), ((0.0, 0.0, 0.0), (60.0, 0.0, 0.0)))
        self.assertEqual(self._ends(incoming), ((30.0, 0.0, 20.0),
                                                (30.0, 0.0, 0.0)))

    def test_far_apart_legs_are_not_joined(self):
        # Two unrelated members whose lines cross far from any endpoint (well
        # beyond a plausible bend setback) must not be stitched together.
        a = self._ctx((0, 0, 0), (10, 0, 0))
        b = self._ctx((5, -50, 0), (5, 50, 0))
        entry._reunite_bend_context([a, b])
        self.assertEqual(self._ends(a), ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)))
        self.assertEqual(self._ends(b), ((5.0, -50.0, 0.0), (5.0, 50.0, 0.0)))

    def test_non_bend_corner_endpoints_untouched(self):
        # Legs that already share the vertex exactly (a plain miter/butt corner
        # from an earlier run) have zero overshoot -- the guard leaves them.
        a = self._ctx((0, 0, 0), (60, 0, 0))
        b = self._ctx((60, 0, 0), (60, 0, 60))
        entry._reunite_bend_context([a, b])
        self.assertEqual(self._ends(a), ((0.0, 0.0, 0.0), (60.0, 0.0, 0.0)))
        self.assertEqual(self._ends(b), ((60.0, 0.0, 0.0), (60.0, 0.0, 60.0)))


class TestCornerCutBuild(unittest.TestCase):
    """Real corner geometry: miter waste-prism cuts and butt/cope combine-cuts."""

    def setUp(self):
        adsk_stub.reset()
        self.root = adsk_stub.FakeRoot()
        families = prof.annotate_families(prof.load_profiles())
        self.geom = prof.section_geometry(prof.designations(families[0])[0])
        # An L-corner: line 0 along +X, line 1 along +Y, meeting at (10,0,0).
        self.lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                      adsk_stub.FakeLine((10, 0, 0), (10, 10, 0))]

    def _build(self):
        objs, idx = [], []
        for ln in self.lines:
            idx.append(self.root.features.count)
            objs.append(entry._build_weldment(self.root, ln, self.geom, 'X'))
            # Position the built body at its line midpoint so the geometric
            # body lookup in _apply_corner_cuts can tell members apart (the
            # stub extrude defaults to the origin otherwise).
            mid = entry._line_midpoint(ln)
            body = objs[-1][0].bodies.item(0)
            body._center = mid
            body.boundingBox = adsk_stub.FakeBoundingBox(mid)
        return objs, idx

    def test_miter_cuts_both_members_with_a_wedge_prism(self):
        # A miter is a FINITE wedge prism on the bisector plane, Combine(Cut)-ed
        # against each member and dropped -- no infinite-plane Split Body and no
        # pointContainment guess (the two sources of the wrong-body regressions).
        objs, idx = self._build()
        cuts = entry._apply_corner_cuts(self.root, self.lines,
                                        ['miter', 'miter'], objs, idx, 0)
        names = [c[0] for c in adsk_stub.CALLS]
        self.assertNotIn('SplitBodyFeatures.add', names)
        self.assertNotIn('Body.pointContainment', names)
        self.assertNotIn('RemoveFeatures.add', names)
        # One cutter Combine per member, each a Cut that DROPS the wedge.
        self.assertEqual(names.count('CombineFeatures.add'), 2)
        for c in adsk_stub.CALLS:
            if c[0] == 'CombineFeatures.add':
                self.assertEqual(
                    c[1][0], adsk_stub.sys.modules['adsk.fusion']
                    .FeatureOperations.CutFeatureOperation)
                self.assertFalse(c[1][1])          # the wedge is consumed
        # Two extra cutter extrudes on top of the two member builds.
        self.assertEqual(names.count('ExtrudeFeatures.add'), 4)
        # Track (combine, extrude, sketch, plane, plane-sketch) per miter.
        self.assertEqual(len(cuts), 10)

    def test_butt_builds_no_cut(self):
        # A butt is a pure axial trim (handled in corner_offsets), so the cut
        # stage must not boolean anything.
        objs, idx = self._build()
        cuts = entry._apply_corner_cuts(self.root, self.lines,
                                        ['none', 'butt'], objs, idx, 0)
        names = [c[0] for c in adsk_stub.CALLS]
        self.assertNotIn('CombineFeatures.add', names)
        self.assertEqual(cuts, [])

    def test_cope_combines_incoming_member(self):
        # Cope fires at a T-junction: line 1's END lands on the interior of line
        # 0's run, so it is saddled to line 0's body (a combine, keep-tool).
        self.lines = [adsk_stub.FakeLine((0, 0, 0), (50, 0, 0)),
                      adsk_stub.FakeLine((25, 0, 20), (25, 0, 0))]
        objs, idx = self._build()
        cuts = entry._apply_corner_cuts(self.root, self.lines,
                                        ['none', 'cope'], objs, idx, 0)
        names = [c[0] for c in adsk_stub.CALLS]
        self.assertEqual(names.count('CombineFeatures.add'), 1)
        # Cut with the neighbour's body, keeping the tool.
        ci = [c for c in adsk_stub.CALLS if c[0] == 'CombineFeatures.add'][0]
        self.assertEqual(
            ci[1][0],
            adsk_stub.sys.modules['adsk.fusion'].FeatureOperations
            .CutFeatureOperation)
        self.assertTrue(ci[1][1])
        self.assertEqual(len(cuts), 1)

    def test_cope_at_corner_builds_saddle_cut(self):
        # A cope whose end coincides with a shared-vertex corner saddles into the
        # neighbour's END face (coping a tube over the open end of another), so
        # the cut stage combines the cope member against the through member.
        objs, idx = self._build()   # default self.lines is an L-corner
        cuts = entry._apply_corner_cuts(self.root, self.lines,
                                        ['none', 'cope'], objs, idx, 0)
        names = [c[0] for c in adsk_stub.CALLS]
        self.assertIn('CombineFeatures.add', names)
        self.assertEqual(len(cuts), 1)

    def test_none_joints_build_no_cuts(self):
        objs, idx = self._build()
        cuts = entry._apply_corner_cuts(self.root, self.lines,
                                        ['none', 'none'], objs, idx, 0)
        names = [c[0] for c in adsk_stub.CALLS]
        self.assertNotIn('SplitBodyFeatures.add', names)
        self.assertNotIn('CombineFeatures.add', names)
        self.assertEqual(cuts, [])

    def test_cuts_delete_cleanly_before_members(self):
        objs, idx = self._build()
        cuts = entry._apply_corner_cuts(self.root, self.lines,
                                        ['miter', 'miter'], objs, idx, 0)
        for obj in cuts:
            obj.deleteMe()
        for o in objs:
            for x in o:
                x.deleteMe()
        self.assertEqual(self.root.features.count, 0)

    def test_cope_against_existing_member_uses_its_body(self):
        # Phase J: a cope whose END lands on the interior of an EXISTING member
        # (passed as context) is saddled to that member's real body -- no shadow
        # line, no objs entry for the tool.  The combine's tool must be exactly
        # the context body.
        self.lines = [adsk_stub.FakeLine((25, 0, 20), (25, 0, 0))]
        objs, idx = self._build()
        existing_body = adsk_stub.FakeBody(name="EXISTING", center=(25, 0, 0))
        context = [{'line': entry._ContextLine((0, 0, 0), (50, 0, 0),
                                               existing_body),
                    'geom': {'kind': 'circles', 'radii': [21.2, 19.2]},
                    'basis': None, 'body': existing_body}]
        cuts = entry._apply_corner_cuts(self.root, self.lines, ['cope'],
                                        objs, idx, 0, context=context)
        names = [c[0] for c in adsk_stub.CALLS]
        self.assertEqual(names.count('CombineFeatures.add'), 1)
        ci = [c for c in adsk_stub.CALLS if c[0] == 'CombineFeatures.add'][0]
        # ci[1] = (operation, isKeepToolBodies, tool_bodies)
        self.assertTrue(ci[1][1])                 # keep the existing tool body
        self.assertIn(existing_body, ci[1][2])    # cut against the real body
        self.assertEqual(len(cuts), 1)

    def test_miter_against_existing_member(self):
        # A miter whose END meets an EXISTING member's END (a corner) is a
        # bisector wedge-prism Combine(Cut), same as a selected-vs-selected miter.
        self.lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0))]
        objs, idx = self._build()
        existing_body = adsk_stub.FakeBody(name="EXISTING", center=(0, 5, 0))
        context = [{'line': entry._ContextLine((0, 0, 0), (0, 10, 0),
                                               existing_body),
                    'geom': {'kind': 'circles', 'radii': [21.2, 19.2]},
                    'basis': None, 'body': existing_body}]
        cuts = entry._apply_corner_cuts(self.root, self.lines, ['miter'],
                                        objs, idx, 0, context=context)
        names = [c[0] for c in adsk_stub.CALLS]
        # One wedge cutter, Combine(Cut)-ed against the member -- no Split Body.
        self.assertNotIn('SplitBodyFeatures.add', names)
        self.assertNotIn('Body.pointContainment', names)
        self.assertEqual(names.count('CombineFeatures.add'), 1)
        self.assertEqual(len(cuts), 5)             # combine + extrude + 3 helpers


class TestRecoverExistingMembers(unittest.TestCase):
    """Body -> centreline recovery used to feed existing parts into detection."""

    def setUp(self):
        adsk_stub.reset()

    def test_context_line_exposes_world_geometry(self):
        ln = entry._ContextLine((0, 0, 0), (10, 0, 0), object())
        s, e = jt.line_endpoints(ln)
        self.assertEqual(s, (0.0, 0.0, 0.0))
        self.assertEqual(e, (10.0, 0.0, 0.0))
        self.assertAlmostEqual(ln.length, 10.0)
        self.assertEqual(jt.line_direction(ln), (1.0, 0.0, 0.0))

    def test_recover_round_tube(self):
        # A straight CHS tube: dominant cylindrical face -> centreline + radii.
        body = adsk_stub.make_round_tube((0, 0, 0), (50, 0, 0),
                                        outer_cm=2.12, inner_cm=1.92)
        start, end, geom, basis = entry._member_centerline(body)
        self.assertEqual(jt.line_direction(entry._ContextLine(start, end, body)),
                         (1.0, 0.0, 0.0))
        self.assertEqual(geom['kind'], 'circles')
        self.assertAlmostEqual(geom['radii'][0], 21.2)      # mm, outer first
        self.assertAlmostEqual(geom['radii'][1], 19.2)      # inner (hollow)
        self.assertIsNone(basis)                            # round = isotropic

    def test_recover_square_tube_is_detected(self):
        # Bug 2: an SHS/RHS tube has only planar faces -- it must still be
        # recovered (previously returned None, so a cope never saw it).
        body = adsk_stub.make_square_tube((0, 0, 0), (50, 0, 0),
                                         hw1_cm=2.0, hw2_cm=2.0, wall_cm=0.2)
        cl = entry._member_centerline(body)
        self.assertIsNotNone(cl)
        start, end, geom, basis = cl
        self.assertEqual(geom['kind'], 'polygons')
        self.assertEqual(len(geom['loops']), 2)             # outer + inner wall
        self.assertIsNotNone(basis)                         # prismatic has a roll

    def test_recover_respects_rotated_square_basis(self):
        # Bug 3: a tube rolled about its run must report a real (rotated) basis
        # so a cope against it uses the true section shape, not an isotropic
        # fallback.  The recovered basis is orthonormal and perpendicular to the
        # run, and rotates with the tube (its plane differs from a rolled-back
        # reference only by the roll).
        import math
        straight = adsk_stub.make_square_tube((0, 0, 0), (50, 0, 0), 3.0, 2.0)
        rolled = adsk_stub.make_square_tube((0, 0, 0), (50, 0, 0), 3.0, 2.0,
                                           roll=0.5)
        _, _, _, b0 = entry._member_centerline(straight)
        _, _, _, b1 = entry._member_centerline(rolled)
        for u, v in (b0, b1):
            # Orthonormal and perpendicular to the run (X).
            self.assertAlmostEqual(sum(c * c for c in u), 1.0, places=6)
            self.assertAlmostEqual(sum(c * c for c in v), 1.0, places=6)
            self.assertAlmostEqual(sum(u[k] * v[k] for k in range(3)), 0.0,
                                   places=6)
            self.assertAlmostEqual(abs(u[0]), 0.0, places=6)   # no X component
            self.assertAlmostEqual(abs(v[0]), 0.0, places=6)
        # The roll moved the basis: b1's first vector is b0's rotated by 0.5 rad
        # (their dot product is cos of the roll).
        dot = sum(b0[0][k] * b1[0][k] for k in range(3))
        self.assertAlmostEqual(dot, math.cos(0.5), places=6)

    def test_recover_skips_non_member_bodies(self):
        # A body with no planar/cylindrical faces (e.g. a lone torus) is skipped.
        body = adsk_stub.FakeBody(name="blob", center=(0, 0, 0), faces=[])
        self.assertIsNone(entry._member_centerline(body))

    def test_recover_filleted_square_not_misread_as_round(self):
        # Bug 1/2 root cause: a real SHS/RHS extrusion has r_mm corner fillets,
        # i.e. four small CYLINDRICAL corner faces alongside its planar sides.
        # "any cylinder -> round tube" misread it as a tiny tube, so a cope
        # against an existing square member never bit.  It must recover as a
        # polygons section (with the corner radius), not circles.
        body = adsk_stub.make_square_tube((0, 0, 0), (50, 0, 0),
                                         hw1_cm=3.0, hw2_cm=1.5, wall_cm=0.15,
                                         fillet_cm=0.3)   # 3 mm corner radius
        cl = entry._member_centerline(body)
        self.assertIsNotNone(cl)
        start, end, geom, basis = cl
        self.assertEqual(geom['kind'], 'polygons')         # NOT 'circles'
        self.assertEqual(len(geom['loops']), 2)            # outer + inner wall
        self.assertIsNotNone(basis)                        # prismatic has a roll
        # The outer loop's corner fillets carry the recovered radius (3 mm).
        self.assertTrue(geom['fillets'][0])
        self.assertAlmostEqual(geom['fillets'][0][0][1], 3.0, places=3)

    def test_recover_existing_members_finds_square_body(self):
        # End-to-end: a square body on a root feature is returned as context
        # with a polygons geom + a real basis (the cope-against-existing path).
        root = adsk_stub.FakeRoot()
        body = adsk_stub.make_square_tube((0, 0, 0), (50, 0, 0), 2.0, 2.0,
                                         wall_cm=0.2)
        feat = adsk_stub.FakeFeature("adsk::fusion::ExtrudeFeature", root,
                                     [body])
        root.features._items.append(feat)
        members = entry._recover_existing_members(root)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]['geom']['kind'], 'polygons')
        self.assertIsNotNone(members[0]['basis'])
        self.assertIs(members[0]['body'], body)


class TestCopeAgainstExistingSquareMember(unittest.TestCase):
    """Bug 2/3 end-to-end: coping a new member onto an EXISTING square tube."""

    def setUp(self):
        adsk_stub.reset()
        self.root = adsk_stub.FakeRoot()

    def test_cope_uses_existing_square_body_as_tool(self):
        # A new line's END lands on the interior of an existing SHS tube's run.
        existing = adsk_stub.make_square_tube((0, 0, 0), (50, 0, 0), 2.0, 2.0,
                                             wall_cm=0.2)
        feat = adsk_stub.FakeFeature("adsk::fusion::ExtrudeFeature", self.root,
                                     [existing])
        self.root.features._items.append(feat)
        context = entry._recover_existing_members(self.root)
        self.assertEqual(len(context), 1)
        lines = [adsk_stub.FakeLine((25, 0, 20), (25, 0, 0))]
        objs = [entry._build_weldment(self.root, lines[0],
                                      {'kind': 'polygons',
                                       'loops': [[(-2, -2), (2, -2), (2, 2),
                                                  (-2, 2)]],
                                       'fillets': [[]]}, 'SHS 40x40')]
        cuts = entry._apply_corner_cuts(self.root, lines, ['cope'], objs, [0], 0,
                                        context=context)
        names = [c[0] for c in adsk_stub.CALLS]
        self.assertEqual(names.count('CombineFeatures.add'), 1)
        ci = [c for c in adsk_stub.CALLS if c[0] == 'CombineFeatures.add'][0]
        self.assertIn(existing, ci[1][2])       # cut against the real square body

    def test_cope_offset_uses_existing_square_directional_extent(self):
        # The trim depth must come from the existing tube's polygons geom (via
        # its recovered basis), not an isotropic fallback.
        existing = adsk_stub.make_square_tube((0, 0, 0), (50, 0, 0), 3.0, 2.0)
        feat = adsk_stub.FakeFeature("adsk::fusion::ExtrudeFeature", self.root,
                                     [existing])
        self.root.features._items.append(feat)
        context = entry._recover_existing_members(self.root)
        lines = [adsk_stub.FakeLine((25, 0, 20), (25, 0, 0))]
        offs = jt.corner_offsets(lines, [{'kind': 'polygons',
                                         'loops': [[(-2, -2), (2, -2), (2, 2),
                                                    (-2, 2)]], 'fillets': [[]]}],
                                [('none', 'cope')], context=context)
        # The cope end (line 0's end at z=0) is pushed into the tool by its
        # half-extent along Z (2 cm) -> a nonzero end offset.
        self.assertNotAlmostEqual(offs[0][1], 0.0)


class TestCopeOrphanRemoval(unittest.TestCase):
    """A cope cut leaves a thin plug inside the tool's void; it must be Removed.

    The combine output is [tool, main run, orphan plug].  _remove_combine_orphans
    keeps the tool and the largest remaining body (the main run) and issues a
    Remove feature on the rest, without disturbing the parametric flow.
    """

    def setUp(self):
        adsk_stub.reset()
        self.root = adsk_stub.FakeRoot()

    def _combine(self, bodies):
        comb = adsk_stub.FakeFeature("adsk::fusion::CombineFeature", self.root,
                                     bodies)
        self.root.features._items.append(comb)
        return comb

    def test_removes_only_the_smallest_non_tool_body(self):
        tool = adsk_stub.FakeBody(name="TOOL", volume=90.0)
        main = adsk_stub.FakeBody(name="MAIN", volume=38.0)
        plug = adsk_stub.FakeBody(name="PLUG", volume=3.7)
        comb = self._combine([plug, main, tool])
        removes = entry._remove_combine_orphans(self.root, comb, tool)
        self.assertEqual(len(removes), 1)
        names = [c[0] for c in adsk_stub.CALLS]
        self.assertEqual(names.count("RemoveFeatures.add"), 1)
        # The plug is gone from the design; the tool and main run remain.
        self.assertNotIn(plug, comb.bodies._items)
        self.assertIn(tool, comb.bodies._items)
        self.assertIn(main, comb.bodies._items)

    def test_no_orphan_when_only_the_main_run_remains(self):
        # A solid tool (no void) yields [tool, main] -- nothing to remove.
        tool = adsk_stub.FakeBody(name="TOOL", volume=90.0)
        main = adsk_stub.FakeBody(name="MAIN", volume=38.0)
        comb = self._combine([main, tool])
        removes = entry._remove_combine_orphans(self.root, comb, tool)
        self.assertEqual(removes, [])
        names = [c[0] for c in adsk_stub.CALLS]
        self.assertNotIn("RemoveFeatures.add", names)

    def test_removing_then_deleting_the_remove_restores_the_body(self):
        tool = adsk_stub.FakeBody(name="TOOL", volume=90.0)
        main = adsk_stub.FakeBody(name="MAIN", volume=38.0)
        plug = adsk_stub.FakeBody(name="PLUG", volume=3.7)
        comb = self._combine([plug, main, tool])
        removes = entry._remove_combine_orphans(self.root, comb, tool)
        self.assertEqual(len(removes), 1)
        self.assertNotIn(plug, comb.bodies._items)
        removes[0].deleteMe()   # preview teardown
        self.assertIn(plug, comb.bodies._items)


class TestCopeOntoBendArc(unittest.TestCase):
    """A cope that T-joints onto a BEND's curved arc downgrades to a butt.

    A cope cutter is a straight box, but a swept bend replaces the corner with a
    die-radius arc.  Where a coping member's tip lands ON that arc, the straight
    cutter cannot match the curved surface (the garbage-cut edge case), so the
    joint must fall back to a plain butt.  A cope landing on the STRAIGHT part of
    a bend leg (past the tangent point) is legitimate and stays a cope.
    """

    def setUp(self):
        adsk_stub.reset()
        families = prof.annotate_families(prof.load_profiles())
        self.geom = prof.section_geometry(prof.designations(families[0])[0])

    def _spec(self, lines, joints, clr):
        return jt.joint_spec(lines, [self.geom] * len(lines), joints,
                             clr_by_line=clr)

    def _occ(self, spec, member):
        return [o for o in spec['occs'] if o['member'] == member]

    def test_cope_on_arc_downgrades_to_butt(self):
        # Tool = bend leg along +X (bend at its START, partner leg along +Z).
        # R=90mm, 90-deg turn -> setback 9cm, so the arc spans x in [0, 9].
        # The coping member's END lands at x=5 -- ON the arc -> butt, no cutter.
        lines = [adsk_stub.FakeLine((0, 0, 0), (40, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 0, 30)),
                 adsk_stub.FakeLine((5, 0, 20), (5, 0, 0))]
        joints = [('bend', 'none'), ('bend', 'none'), ('none', 'cope')]
        clr = [90.0, 90.0, 0.0]
        spec = self._spec(lines, joints, clr)
        occs = self._occ(spec, 2)
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0]['kind'], 'butt')
        self.assertIsNone(occs[0]['cutter'])
        self.assertTrue(occs[0].get('on_arc'))

    def test_cope_past_tangent_stays_cope(self):
        # Same bend tool, but the coping member lands at x=20 -- PAST the tangent
        # point (arc ends at 9), on the straight part of the leg -> real cope.
        lines = [adsk_stub.FakeLine((0, 0, 0), (40, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 0, 30)),
                 adsk_stub.FakeLine((20, 0, 20), (20, 0, 0))]
        joints = [('bend', 'none'), ('bend', 'none'), ('none', 'cope')]
        clr = [90.0, 90.0, 0.0]
        spec = self._spec(lines, joints, clr)
        occs = self._occ(spec, 2)
        self.assertEqual(len(occs), 1)
        self.assertIn(occs[0]['kind'], ('cope_t', 'cope_angle'))
        self.assertIsNotNone(occs[0]['cutter'])
        self.assertFalse(occs[0].get('on_arc'))

    def test_cope_corner_on_bend_leg_downgrades(self):
        # A cope whose END coincides with a bend leg's vertex (a corner) lands on
        # the arc (the vertex is the arc's far end) -> butt, no cutter.
        lines = [adsk_stub.FakeLine((0, 0, 0), (40, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 0, 30)),
                 adsk_stub.FakeLine((0, 20, 0), (0, 0, 0))]
        joints = [('bend', 'none'), ('bend', 'none'), ('none', 'cope')]
        clr = [90.0, 90.0, 0.0]
        spec = self._spec(lines, joints, clr)
        occs = self._occ(spec, 2)
        self.assertEqual(len(occs), 1)
        self.assertEqual(occs[0]['kind'], 'butt')
        self.assertIsNone(occs[0]['cutter'])

    def test_cope_onto_straight_member_unaffected(self):
        # No bend anywhere: a cope T-joint stays a cope (no false downgrade).
        lines = [adsk_stub.FakeLine((0, 0, 0), (40, 0, 0)),
                 adsk_stub.FakeLine((12, 0, 20), (12, 0, 0))]
        joints = ['none', ('none', 'cope')]
        spec = self._spec(lines, joints, [0.0, 0.0])
        occs = self._occ(spec, 1)
        self.assertEqual(len(occs), 1)
        self.assertIn(occs[0]['kind'], ('cope_t', 'cope_angle'))


class TestAngledTCutoffRemoval(unittest.TestCase):
    """An angled T cope leaves a wall plug the joint box must fully contain.

    The box is sized (via :func:`lib.joints._plug_reach`) to span a plug driven
    into the neighbour's tilted bore -- its axial reach grows as 1/sin(angle) --
    so the plug sits ENTIRELY inside the box while the member's run pokes out.
    The classifier removes bodies wholly within the box and keeps the rest.
    """

    def setUp(self):
        adsk_stub.reset()
        self.root = adsk_stub.FakeRoot()

    def _region(self):
        # A joint box centred at the origin, 2 cm half-extent on every axis.
        return {'center': (0.0, 0.0, 0.0),
                'axes': ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
                'half': (2.0, 2.0, 2.0)}

    def _combine(self, bodies):
        comb = adsk_stub.FakeFeature("adsk::fusion::CombineFeature", self.root,
                                     bodies)
        self.root.features._items.append(comb)
        return comb

    def test_plug_inside_box_is_removed(self):
        tool = adsk_stub.FakeBody(name="TOOL", volume=90.0, center=(0, 0, 0))
        run = adsk_stub.FakeBody(name="RUN", volume=38.0, center=(0, 0, 0))
        # A plug wholly inside the 2 cm box: a cutoff, removed.
        plug = adsk_stub.FakeBody(name="PLUG", volume=0.12, center=(1.0, 0, 0))
        plug.boundingBox = adsk_stub.FakeBoundingBox((1.0, 0, 0), half=0.5)
        comb = self._combine([tool, run, plug])
        removes = entry._remove_inside_region(self.root, comb, tool,
                                              self._region())
        self.assertEqual(len(removes), 1)
        names = [c[0] for c in adsk_stub.CALLS]
        self.assertEqual(names.count("RemoveFeatures.add"), 1)
        self.assertNotIn(plug, comb.bodies._items)
        self.assertIn(tool, comb.bodies._items)
        self.assertIn(run, comb.bodies._items)

    def test_run_poking_out_of_box_is_kept(self):
        # A fragment that reaches past the box is real member material: keep it
        # even though it is smaller than the tool (the survivor floor aside, the
        # containment test alone protects it).
        tool = adsk_stub.FakeBody(name="TOOL", volume=90.0, center=(0, 0, 0))
        run = adsk_stub.FakeBody(name="RUN", volume=5.0, center=(0, 0, 0))
        run.boundingBox = adsk_stub.FakeBoundingBox((0, 0, 0), half=10.0)
        comb = self._combine([tool, run])
        removes = entry._remove_inside_region(self.root, comb, tool,
                                              self._region())
        self.assertEqual(removes, [])
        self.assertIn(run, comb.bodies._items)

    def test_far_fragment_is_left_alone(self):
        # A fragment of the same size but sitting far from the joint (its bbox
        # outside the box) belongs to other material -- never touch.
        tool = adsk_stub.FakeBody(name="TOOL", volume=90.0, center=(0, 0, 0))
        run = adsk_stub.FakeBody(name="RUN", volume=38.0, center=(0, 0, 0))
        far = adsk_stub.FakeBody(name="FAR", volume=0.12, center=(50, 0, 0))
        far.boundingBox = adsk_stub.FakeBoundingBox((50, 0, 0), half=1.0)
        comb = self._combine([tool, run, far])
        removes = entry._remove_inside_region(self.root, comb, tool,
                                              self._region())
        self.assertEqual(removes, [])
        self.assertIn(far, comb.bodies._items)


if __name__ == "__main__":
    unittest.main()
