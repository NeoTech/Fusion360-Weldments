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
    # Rotation + start/end offsets are global (per-line) inputs, not table cells.
    inputs.addFloatSpinnerCommandInput('rotation', 'Rotation', 'degree',
                                       -1000, 1000, 15, 0)
    inputs.addDistanceValueCommandInput('offset_start', 'Offset Start', None)
    inputs.addDistanceValueCommandInput('offset_end', 'Offset End', None)
    tbl = inputs.addTableCommandInput('params', 'Per Line', 5, '1:3:3:1:1')
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

    def test_row_params_reads_global_values(self):
        _cmd, inputs, tbl = _make_dialog()
        entry._sync_table_rows(inputs, [adsk_stub.FakeLine()] * 2)
        inputs.itemById('rotation').value = 0.5
        inputs.itemById('offset_start').value = 2.0
        # Rotation/offset are global: every row reads the same values.
        self.assertEqual(entry._row_params(inputs, 0)[0], 0.5)
        self.assertEqual(entry._row_params(inputs, 1)[1], 2.0)

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

    def test_offset_arrows_anchor_line_zero(self):
        # Rotation/offset are global inputs: one universal pair of arrows,
        # anchored to line 0 regardless of the Sync-all state.
        _cmd, inputs, tbl = _make_dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))]
        entry._sync_table_rows(inputs, lines)
        entry._update_manipulators(inputs, lines)
        self.assertEqual(inputs.itemById('rotation').manipulatorCount, 0)
        self.assertEqual(inputs.itemById('offset_start').manipulatorCount, 1)
        self.assertEqual(inputs.itemById('offset_end').manipulatorCount, 1)

    def test_select_builds_rows_and_anchors(self):
        cmd, inputs, tbl = _make_dialog()
        sel = self._pick(inputs, [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0))])
        entry.command_select(_Args(activeInput=sel))
        self.assertEqual(len(entry._row_ids), 1)
        self.assertEqual(inputs.itemById('offset_start').manipulatorCount, 1)


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

    def test_bend_radii_from_die(self):
        # A bend leg resolves to the catalogue CLR; a non-bend leg to 0.
        des = dict(prof.designations(self.shs)[0])
        des['_abbreviation'] = 'SHS'
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
        objs = entry._build_bend_arcs(self.root, lines, ['bend', 'bend'],
                                      [150.0, 150.0], self.geom, ref=None)
        self.assertEqual(len(objs), 1)   # one rounded corner


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

    def test_miter_combines_both_members_against_a_waste_prism(self):
        objs, idx = self._build()
        cuts = entry._apply_corner_cuts(self.root, self.lines,
                                        ['miter', 'miter'], objs, idx, 0)
        names = [c[0] for c in adsk_stub.CALLS]
        # Split is unusable in parametric designs (deleting the waste half
        # cascade-deletes the kept half), so a miter is a combine against a
        # waste prism instead.
        self.assertNotIn('SplitBodyFeatures.add', names)
        self.assertEqual(names.count('CombineFeatures.add'), 2)
        for ci in [c for c in adsk_stub.CALLS if c[0] == 'CombineFeatures.add']:
            self.assertEqual(
                ci[1][0],
                adsk_stub.sys.modules['adsk.fusion'].FeatureOperations
                .CutFeatureOperation)
            # The prism is pure waste, so it must be consumed, not kept.
            self.assertFalse(ci[1][1])
        # No body is deleted directly: the combine does all the trimming.
        self.assertNotIn('Body.deleteMe', names)
        # One one-sided prism extrude per member end.
        self.assertEqual(
            names.count('ExtrudeFeatureInput.setDistanceExtent'), 2)
        # Returned objects come in (combine, prism, sketch, helper, plane)
        # fives and lead with the combine features, which must go before the
        # members.
        self.assertEqual(len(cuts), 10)
        self.assertTrue(all('Combine' in f.objectType for f in cuts[0::5]))

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

    def test_cope_at_corner_builds_no_cut(self):
        # A cope whose end coincides with a shared-vertex corner is a butt trim,
        # not a saddle -- the cut stage must not boolean anything.
        objs, idx = self._build()   # default self.lines is an L-corner
        cuts = entry._apply_corner_cuts(self.root, self.lines,
                                        ['none', 'cope'], objs, idx, 0)
        names = [c[0] for c in adsk_stub.CALLS]
        self.assertNotIn('CombineFeatures.add', names)
        self.assertEqual(cuts, [])

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


if __name__ == "__main__":
    unittest.main()
