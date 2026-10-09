"""Stub tests for the Tube command (``commands/weldmentTube/entry.py``).

Tube is the Weldments-tab profile builder: everything Auto does to *create*
members (family/designation/Position + per-line Rotation/Offset) but with NO
joint machinery.  These pin the two properties that define it:

* the dialog has no Joint Start/End (or Through/Saddle/Inverse/Bend Die/Cope
  Depth) columns -- only #, Rotation, Offset Start, Offset End;
* execute builds the plain profiles (one NewBody extrude per line) and persists
  the members, but NEVER booleans anything (no Combine/Split/Remove) and writes
  NO joint records -- joints are the toolbox tools' job.

Run with:  python -m unittest discover -s tests
"""

import importlib
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adsk_stub  # noqa: E402

adsk_stub.install()


def _make_package(pkg_name, dir_path):
    import types
    mod = types.ModuleType(pkg_name)
    mod.__path__ = [dir_path]
    mod.__package__ = pkg_name
    sys.modules[pkg_name] = mod
    return mod


_make_package("Weldments", ROOT)
_make_package("Weldments.lib", os.path.join(ROOT, "lib"))
_make_package("Weldments.commands", os.path.join(ROOT, "commands"))
for _cmd in ("weldment", "weldmentTube"):
    _make_package(f"Weldments.commands.{_cmd}",
                  os.path.join(ROOT, "commands", _cmd))

w = importlib.import_module("Weldments.commands.weldment.entry")
tube = importlib.import_module("Weldments.commands.weldmentTube.entry")
prof = importlib.import_module("Weldments.lib.profiles")
reg = importlib.import_module("Weldments.lib.registry")

adsk_core = sys.modules["adsk.core"]
adsk_fusion = sys.modules["adsk.fusion"]


class _Args:
    def __init__(self, command):
        self.command = command


def _make_dialog():
    """A FakeCommand whose inputs mirror tube.command_created's layout."""
    cmd = adsk_stub.FakeCommand()
    inputs = cmd.commandInputs
    inputs.addSelectionInput('path', 'Lines', '')
    inputs.addDropDownCommandInput('family', 'Profile', 0)
    inputs.addDropDownCommandInput('designation', 'Designation', 0)
    inputs.addDropDownCommandInput('position', 'Position', 0)
    tbl = inputs.addTableCommandInput('params', 'Per Line', 4, '1:2:2:2')
    sync = inputs.addBoolValueInput('sync_all', 'Sync all', True, '', True)
    tbl.addToolbarCommandInput(sync)
    tube._make_header_row(inputs, tbl)
    # Populate the family/designation dropdowns the way command_created does.
    for label in prof.family_labels(w._FAMILIES):
        inputs.itemById('family').listItems.add(label, False)
    if inputs.itemById('family').listItems.count:
        inputs.itemById('family').listItems.item(0).isSelected = True
    tube._rebuild_designations(inputs)
    return cmd, inputs, tbl


def _pick(inputs, lines):
    sel = inputs.itemById('path')
    for ln in lines:
        sel.addSelection(ln)
    return sel


def _names():
    return [c[0] for c in adsk_stub.CALLS]


class TestTubeDialog(unittest.TestCase):
    def setUp(self):
        adsk_stub.reset()
        tube._row_ids = []
        tube._uid_counter[0] = 0
        w._FAMILIES = prof.annotate_families(prof.load_profiles())

    def test_table_has_only_placement_columns(self):
        # The defining property: no joint/through/saddle/inverse/die/depth
        # columns -- just #, Rotation, Offset Start, Offset End.
        self.assertEqual(tube._TABLE_HEADERS,
                         ('#', 'Rotation', 'Offset Start', 'Offset End'))

    def test_sync_table_rows_one_row_per_line(self):
        _cmd, inputs, tbl = _make_dialog()
        lines = [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                 adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))]
        tube._sync_table_rows(inputs, lines)
        self.assertEqual(len(tube._row_ids), 2)
        self.assertEqual(tbl.rowCount, 3)   # header + 2 data rows

    def test_row_has_no_joint_cells(self):
        _cmd, inputs, _tbl = _make_dialog()
        tube._sync_table_rows(inputs, [adsk_stub.FakeLine()])
        ids = tube._row_ids[0]
        self.assertEqual(set(ids), {'num', 'rot', 'os', 'oe'})
        for joint_key in ('joint_s', 'joint_e', 'through', 'saddle',
                          'inv', 'die', 'cd'):
            self.assertNotIn(joint_key, ids)

    def test_row_params_reads_rotation_and_offsets(self):
        _cmd, inputs, _tbl = _make_dialog()
        tube._sync_table_rows(inputs, [adsk_stub.FakeLine(),
                                       adsk_stub.FakeLine()])
        # A row past the table defaults to zeros.
        self.assertEqual(tube._row_params(inputs, 9), (0.0, 0.0, 0.0))

    def test_sync_all_propagates_rotation(self):
        _cmd, inputs, _tbl = _make_dialog()
        tube._sync_table_rows(inputs, [adsk_stub.FakeLine(),
                                       adsk_stub.FakeLine()])
        rot0 = inputs.itemById(tube._row_ids[0]['rot'])
        rot0.value = 0.5
        args = _Args(None)
        args.input = rot0
        args.inputs = inputs
        tube.command_input_changed(args)
        rot1 = inputs.itemById(tube._row_ids[1]['rot'])
        self.assertAlmostEqual(rot1.value, 0.5)

    def test_no_propagation_when_unsynced(self):
        _cmd, inputs, _tbl = _make_dialog()
        tube._sync_table_rows(inputs, [adsk_stub.FakeLine(),
                                       adsk_stub.FakeLine()])
        inputs.itemById('sync_all').value = False
        rot0 = inputs.itemById(tube._row_ids[0]['rot'])
        rot0.value = 0.75
        args = _Args(None)
        args.input = rot0
        args.inputs = inputs
        tube.command_input_changed(args)
        rot1 = inputs.itemById(tube._row_ids[1]['rot'])
        self.assertAlmostEqual(rot1.value, 0.0)


class TestTubeExecute(unittest.TestCase):
    """Commit path: plain profiles, no booleans, no joint records."""

    def setUp(self):
        adsk_stub.reset()
        tube._row_ids = []
        tube._uid_counter[0] = 0
        w._FAMILIES = prof.annotate_families(prof.load_profiles())
        self.design = adsk_stub.FakeDesign()
        adsk_stub.set_active_design(self.design)

    def _run(self, lines):
        cmd, inputs, _tbl = _make_dialog()
        _pick(inputs, lines)
        # In Fusion, executePreview fires before execute and syncs the rows;
        # drive that step explicitly so the table cells exist for execute.
        tube._sync_table_rows(inputs, lines)
        tube.command_execute(_Args(cmd))
        return cmd, inputs

    def test_builds_one_new_body_extrude_per_line(self):
        self._run([adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                   adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))])
        self.assertEqual(_names().count('ExtrudeFeatures.add'), 2)
        for c in adsk_stub.CALLS:
            if c[0] == 'ExtrudeFeatures.createInput':
                self.assertEqual(c[1][0],
                                 adsk_fusion.FeatureOperations.NewBodyFeatureOperation)

    def test_never_booleans_anything(self):
        # The whole point of Tube vs Auto: no corner cuts, no joint machinery.
        self._run([adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                   adsk_stub.FakeLine((10, 0, 0), (10, 10, 0))])
        for banned in ('CombineFeatures.add', 'SplitBodyFeatures.add',
                       'RemoveFeatures.add'):
            self.assertNotIn(banned, _names())

    def test_persists_members_but_no_joints(self):
        self._run([adsk_stub.FakeLine((0, 0, 0), (10, 0, 0)),
                   adsk_stub.FakeLine((0, 0, 0), (0, 10, 0))])
        registry = w.load_registry(self.design)
        self.assertEqual(len(registry.members), 2)
        self.assertEqual(len(registry.joints), 0)

    def test_member_bodies_are_stamped(self):
        self._run([adsk_stub.FakeLine((0, 0, 0), (10, 0, 0))])
        registry = w.load_registry(self.design)
        self.assertEqual(len(registry.members), 1)
        # The built body carries the member id so the toolbox tools find it.
        m = registry.members[0]
        self.assertIsNotNone(m.mid)

    def test_offset_cells_move_the_extrude(self):
        # A non-zero Offset End lengthens the body beyond the line; the plane
        # still sits at the (offset) start.  Verify the extrude distance grew.
        cmd, inputs, _tbl = _make_dialog()
        _pick(inputs, [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0))])
        tube._sync_table_rows(inputs, [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0))])
        inputs.itemById(tube._row_ids[0]['oe']).value = 5.0   # +5 cm
        tube.command_execute(_Args(cmd))
        dists = [c[1][0] for c in adsk_stub.CALLS
                 if c[0] == 'ValueInput.createByReal']
        # line.length (10) + offset_end (5) = 15 cm extrude distance present.
        self.assertTrue(any(abs(d - 15.0) < 1e-6 for d in dists),
                        f'expected a 15 cm extrude distance in {dists}')

    def test_empty_selection_creates_nothing(self):
        cmd, inputs, _tbl = _make_dialog()
        tube.command_execute(_Args(cmd))
        self.assertNotIn('ExtrudeFeatures.add', _names())


class TestTubePreview(unittest.TestCase):
    def setUp(self):
        adsk_stub.reset()
        tube._row_ids = []
        tube._uid_counter[0] = 0
        tube._preview_objs = []
        w._FAMILIES = prof.annotate_families(prof.load_profiles())
        self.design = adsk_stub.FakeDesign()
        adsk_stub.set_active_design(self.design)

    def test_preview_builds_ghosts_and_no_booleans(self):
        cmd, inputs, _tbl = _make_dialog()
        _pick(inputs, [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0))])
        tube.command_execute_preview(_Args(cmd))
        self.assertEqual(_names().count('ExtrudeFeatures.add'), 1)
        self.assertEqual(len(tube._preview_objs), 1)
        for banned in ('CombineFeatures.add', 'SplitBodyFeatures.add',
                       'RemoveFeatures.add'):
            self.assertNotIn(banned, _names())

    def test_clear_preview_deletes_the_trio(self):
        cmd, inputs, _tbl = _make_dialog()
        _pick(inputs, [adsk_stub.FakeLine((0, 0, 0), (10, 0, 0))])
        tube.command_execute_preview(_Args(cmd))
        adsk_stub.reset()
        tube._clear_preview()
        self.assertEqual(tube._preview_objs, [])
        self.assertEqual(len([c for c in adsk_stub.CALLS
                              if c[0].endswith('deleteMe')]), 3)


class TestTubeRegistration(unittest.TestCase):
    def test_dialog_wires_no_joint_inputs(self):
        # command_created's layout (mirrored by _make_dialog): the placement
        # inputs are present and NONE of Auto's joint columns exist.
        adsk_stub.reset()
        w._FAMILIES = prof.annotate_families(prof.load_profiles())
        tube._row_ids = []
        _cmd, inputs, _tbl = _make_dialog()
        for gone in ('joint_s', 'joint_e', 'through', 'saddle', 'inv', 'die',
                     'cd'):
            self.assertIsNone(inputs.itemById(gone))
        for keep in ('path', 'family', 'designation', 'position', 'params',
                     'sync_all'):
            self.assertIsNotNone(inputs.itemById(keep), keep)


if __name__ == '__main__':
    unittest.main()
