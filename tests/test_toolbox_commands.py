"""Behaviour-freeze tests for the toolbox commands' execute glue.

The four explicit-selection tools (Cope / Miter / Butt / Bend) each have a
``command_execute`` that: reads the selection inputs, recovers both members'
centrelines with :func:`commands.weldment.entry._member_centerline`, computes
the joint with the pure ``lib.joints`` cutter, performs the boolean(s), and
records the result in the registry.  Until now NOTHING tested that glue -- the
pure-geometry layer (test_joints / test_joint_spec) and the Auto builder
(test_command) were covered, but a careless edit to a toolbox ``entry.py``
broke silently.

These drive each command's ``command_execute`` against the adsk stub and pin
the observable contract:

* which booleans fire, in which class (Cut keep-tool vs Cut drop-tool);
* that the joint lands in the registry with the right kind, members, vertex
  and params (the BOM/re-run backbone);
* that both member bodies end up stamped (the body<->member spine);
* the guard rails (missing selection, non-member body, members that don't
  meet) that pop a messageBox and change NOTHING.

Run with:  python -m unittest discover -s tests
"""

import importlib
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adsk_stub  # noqa: E402  (installs nothing until .install())

adsk_stub.install()


def _make_package(pkg_name, dir_path):
    """Register a bare package (with __path__) WITHOUT executing its __init__.py."""
    import types
    mod = types.ModuleType(pkg_name)
    mod.__path__ = [dir_path]
    mod.__package__ = pkg_name
    sys.modules[pkg_name] = mod
    return mod


_make_package("Weldments", ROOT)
_make_package("Weldments.lib", os.path.join(ROOT, "lib"))
_make_package("Weldments.commands", os.path.join(ROOT, "commands"))
for _cmd in ("weldment", "weldmentCope", "weldmentMiter", "weldmentButt",
             "weldmentBend"):
    _make_package(f"Weldments.commands.{_cmd}",
                  os.path.join(ROOT, "commands", _cmd))

w = importlib.import_module("Weldments.commands.weldment.entry")
bd = importlib.import_module("Weldments.lib.bending_dies")
cope = importlib.import_module("Weldments.commands.weldmentCope.entry")
miter = importlib.import_module("Weldments.commands.weldmentMiter.entry")
butt = importlib.import_module("Weldments.commands.weldmentButt.entry")
bend = importlib.import_module("Weldments.commands.weldmentBend.entry")
jt = importlib.import_module("Weldments.lib.joints")
reg = importlib.import_module("Weldments.lib.registry")

adsk_fusion = sys.modules["adsk.fusion"]
adsk_core = sys.modules["adsk.core"]


class _Args:
    """Minimal stand-in for CommandEventArgs."""

    def __init__(self, command):
        self.command = command


def _tube_pair(root, a_start, a_end, b_start, b_end, hollow=True):
    """Two straight round-tube bodies registered as features on ``root``.

    Returns (design, body_a, body_b).  The bodies carry real cylindrical faces
    so _member_centerline recovers them, and live on ExtrudeFeatures so
    _recover_existing_members / the survivor logic can see them.
    """
    design = adsk_stub.FakeDesign()
    root = design.rootComponent
    body_a = adsk_stub.make_round_tube(a_start, a_end, 2.12, 1.92 if hollow else None)
    body_b = adsk_stub.make_round_tube(b_start, b_end, 2.12, 1.92 if hollow else None)
    fa = adsk_stub.FakeFeature("adsk::fusion::ExtrudeFeature", root, [body_a])
    fb = adsk_stub.FakeFeature("adsk::fusion::ExtrudeFeature", root, [body_b])
    root.features._items.extend([fa, fb])
    root.bRepBodies.append(body_a)
    root.bRepBodies.append(body_b)
    adsk_stub.set_active_design(design)
    return design, body_a, body_b


def _select(cmd, input_id, body):
    cmd.commandInputs.itemById(input_id).addSelection(body)


def _names():
    return [c[0] for c in adsk_stub.CALLS]


def _combines():
    # CALLS entries are (name, args); args = (operation, keep_tool, tool_bodies).
    return [c[1] for c in adsk_stub.CALLS if c[0] == "CombineFeatures.add"]


def _cut(op):
    return op == adsk_fusion.FeatureOperations.CutFeatureOperation


def _join(op):
    return op == adsk_fusion.FeatureOperations.JoinFeatureOperation


def _cuts():
    return [c for c in _combines() if _cut(c[0])]


class _CopeDialog:
    """The cope tool's dialog: subject + tool + depth spinner."""

    def __init__(self):
        self.command = adsk_stub.FakeCommand()
        i = self.command.commandInputs
        i.addSelectionInput("subject", "Coping Member", "")
        i.addSelectionInput("tool", "Tool Member", "")
        i.addValueInput("depth", "Depth", "mm",
                        adsk_core.ValueInput.createByReal(0.0))


class TestCopeExecute(unittest.TestCase):
    """The toolbar Cope command's commit path (two-step bounded cut)."""

    def setUp(self):
        adsk_stub.reset()
        self.design, self.subj, self.tool = _tube_pair(
            None, (25, 0, 20), (25, 0, 0), (0, 0, 0), (50, 0, 0))
        # subject's END (25,0,0) lands on the tool's run: a T cope.

    def _run(self, subj, tool, depth_cm=0.0):
        dlg = _CopeDialog()
        subj and _select(dlg.command, "subject", subj)
        tool and _select(dlg.command, "tool", tool)
        dlg.command.commandInputs.itemById("depth").value = depth_cm
        cope.command_execute(_Args(dlg.command))
        return dlg

    def test_cope_performs_a_keep_tool_combine(self):
        self._run(self.subj, self.tool)
        combs = _combines()
        self.assertTrue(combs, "cope must run at least one Combine")
        # The final saddle cut keeps the tool body (the neighbour survives).
        self.assertTrue(any(_cut(op) and keep for op, keep, _t in combs),
                        "a cope must Combine(Cut, keep_tool=True)")

    def test_cope_records_a_t_cope_joint(self):
        self._run(self.subj, self.tool)
        registry = w.load_registry(self.design)
        joints = [j for j in registry.joints if j.kind.startswith("cope")]
        self.assertEqual(len(joints), 1)
        self.assertEqual(joints[0].kind, "cope_t")   # perpendicular -> T
        self.assertEqual(len(joints[0].member_ids()), 2)
        # vertex is the landing point on the tool's run
        self.assertAlmostEqual(joints[0].vertex[0], 25.0, places=3)
        self.assertAlmostEqual(joints[0].vertex[2], 0.0, places=3)

    def test_cope_stamps_both_member_bodies(self):
        self._run(self.subj, self.tool)
        self.assertIsNotNone(w.body_mid(self.tool))
        # The coping member's body may be re-homed by the cut; at least the
        # tool is stamped and BOTH members are recorded.
        registry = w.load_registry(self.design)
        self.assertEqual(len(registry.members), 2)

    def test_cope_depth_is_recorded(self):
        self._run(self.subj, self.tool, depth_cm=2.0)   # 2 cm spinner = 20 mm
        registry = w.load_registry(self.design)
        j = [j for j in registry.joints if j.kind.startswith("cope")][0]
        self.assertAlmostEqual(j.params.get("depth_mm"), 20.0, places=3)

    def test_rerun_edits_the_same_joint(self):
        self._run(self.subj, self.tool)
        self._run(self.subj, self.tool)
        registry = w.load_registry(self.design)
        self.assertEqual(
            len([j for j in registry.joints if j.kind.startswith("cope")]), 1,
            "a re-run must edit, not duplicate")
        self.assertEqual(len(registry.members), 2)

    def test_missing_selection_does_nothing(self):
        before = len(w.load_registry(self.design).joints)
        self._run(self.subj, None)
        self.assertEqual(len(w.load_registry(self.design).joints), before)
        self.assertNotIn("CombineFeatures.add", _names())


class TestCopeBendTool(unittest.TestCase):
    """Coping onto a swept-bend ARC (candidate #4's toolbox path).

    The historical bug: a bent tool has no single straight centreline, so
    _member_centerline returned None and the command hard-errored "Both
    selections must be straight weldment members."  It now saddles onto the
    arc body with a single keep-tool boolean and records the joint against the
    nearest bend LEG member.
    """

    def setUp(self):
        adsk_stub.reset()
        self.design = adsk_stub.FakeDesign()
        root = self.design.rootComponent
        # A straight coping member whose tip lands on a corner arc.
        self.subj = adsk_stub.make_round_tube((25, 0, 20), (5, 25, 0), 2.12, 1.92)
        fs = adsk_stub.FakeFeature("adsk::fusion::ExtrudeFeature", root, [self.subj])
        root.features._items.append(fs)
        root.bRepBodies.append(self.subj)
        # The bend's corner arc (a torus body) near the tip, unstamped (the
        # geometric fallback must find it).
        self.arc = adsk_stub.FakeBody(
            name="Arc", center=(5.0, 25.0, 0.0), volume=8.0,
            faces=[adsk_stub.FakeFace(adsk_stub.Torus((0, 0, 1), (5, 25, 0), 9.0, 1.0)),
                   adsk_stub.FakeFace(adsk_stub.Plane((1, 0, 0), (5, 25, 0)), 1.0)])
        fa = adsk_stub.FakeFeature("adsk::fusion::SweepFeature", root, [self.arc])
        root.features._items.append(fa)
        root.bRepBodies.append(self.arc)
        # Registry: the corner's two legs + the bend joint rounding it.
        registry = reg.Registry()
        leg1 = registry.add_member((5, 30, 0), (30, 30, 0), designation="20x1", family="CHS")
        leg2 = registry.add_member((0, 5, 0), (0, 30, 0), designation="20x1", family="CHS")
        registry.add_joint("bend", [{"mid": leg1.mid, "role": "start"},
                                    {"mid": leg2.mid, "role": "end"}],
                           vertex=(0.0, 30.0, 0.0), params={"clr_mm": 89.9})
        w.save_registry(self.design, registry)
        adsk_stub.set_active_design(self.design)

    def _run(self, subj, tool):
        dlg = _CopeDialog()
        subj and _select(dlg.command, "subject", subj)
        tool and _select(dlg.command, "tool", tool)
        cope.command_execute(_Args(dlg.command))
        return dlg

    def test_bend_tool_does_not_error(self):
        self._run(self.subj, self.arc)
        boxes = [c for c in adsk_stub.CALLS
                 if c[0].endswith("messageBox")]
        self.assertFalse(boxes, "a bent tool must not pop the straight-only error")

    def test_bend_tool_cuts_against_the_arc(self):
        self._run(self.subj, self.arc)
        combs = _combines()
        self.assertTrue(any(_cut(op) and keep for op, keep, _t in combs),
                        "a cope onto a bend must Combine(Cut, keep_tool=True)")
        # The tool of that cut is the arc body itself.
        self.assertTrue(any(_cut(op) and keep and self.arc in tools
                            for op, keep, tools in combs))

    def test_bend_tool_records_a_cope_joint(self):
        self._run(self.subj, self.arc)
        registry = w.load_registry(self.design)
        joints = [j for j in registry.joints if j.kind.startswith("cope")]
        self.assertEqual(len(joints), 1)
        # The tool side names a real bend LEG member (mid 1 or 2), never the arc.
        mids = set(joints[0].member_ids())
        self.assertTrue(mids & {1, 2},
                        "the cope joint must reference a bend leg member")


class _MiterDialog:
    def __init__(self):
        self.command = adsk_stub.FakeCommand()
        i = self.command.commandInputs
        i.addSelectionInput("member_a", "Member A", "")
        i.addSelectionInput("member_b", "Member B", "")
        dd = i.addDropDownCommandInput("style", "Style", 0)
        dd.listItems.add("Edge to edge", True)
        dd.listItems.add("Open", False)


class TestMiterExecute(unittest.TestCase):
    """The toolbar Miter command's commit path (wedge prism per member)."""

    def setUp(self):
        adsk_stub.reset()
        # An L: A along +X ending at (10,0,0); B along +Y starting there.
        self.design, self.a, self.b = _tube_pair(
            None, (0, 0, 0), (10, 0, 0), (10, 0, 0), (10, 10, 0))

    def _run(self, style="Edge to edge"):
        dlg = _MiterDialog()
        _select(dlg.command, "member_a", self.a)
        _select(dlg.command, "member_b", self.b)
        dd = dlg.command.commandInputs.itemById("style")
        for k in range(dd.listItems.count):
            dd.listItems.item(k).isSelected = dd.listItems.item(k).name == style
        miter.command_execute(_Args(dlg.command))
        return dlg

    def test_miter_cuts_both_members_with_wedge_prisms(self):
        self._run("Open")
        combs = _combines()
        self.assertEqual(len(combs), 2, "one wedge Combine per member")
        for op, keep, _t in combs:
            self.assertTrue(_cut(op))
            self.assertFalse(keep, "the wedge cutter is consumed")
        # No Split Body, no pointContainment guess (the regression sources).
        self.assertNotIn("SplitBodyFeatures.add", _names())
        self.assertNotIn("Body.pointContainment", _names())

    def test_edge_style_joins_a_grown_tip(self):
        # Edge-to-edge must ADD material before trimming: the grow path
        # extrudes a prism and Joins it into the member (a cut alone can
        # never fill the corner's outer square).  Open style never Joins.
        self._run("Edge to edge")
        self.assertTrue(any(_join(op) for op, _k, _t in _combines()),
                        "edge style must Join a grown tip into a member")

    def test_open_style_skips_the_grow(self):
        self._run("Open")
        self.assertFalse(any(_join(op) for op, _k, _t in _combines()),
                         "open style must not grow any tip")
        # Only the two wedge prisms are extruded.
        self.assertEqual(_names().count("ExtrudeFeatures.add"), 2)

    def test_miter_records_one_joint_at_the_corner(self):
        self._run()
        registry = w.load_registry(self.design)
        miters = [j for j in registry.joints if j.kind == "miter"]
        self.assertEqual(len(miters), 1)
        self.assertEqual(set(miters[0].member_ids()),
                         {m.mid for m in registry.members})
        self.assertAlmostEqual(miters[0].vertex[0], 10.0, places=3)
        self.assertAlmostEqual(miters[0].vertex[1], 0.0, places=3)

    def test_rerun_does_not_duplicate(self):
        self._run()
        self._run()
        registry = w.load_registry(self.design)
        self.assertEqual(len([j for j in registry.joints
                              if j.kind == "miter"]), 1)
        self.assertEqual(len(registry.members), 2)

    def test_members_that_do_not_meet_are_rejected(self):
        # Move B far from A's end: no corner within 1 cm -> guard, no cuts.
        adsk_stub.reset()
        self.design, self.a, self.b = _tube_pair(
            None, (0, 0, 0), (10, 0, 0), (40, 0, 0), (40, 10, 0))
        self._run()
        self.assertNotIn("CombineFeatures.add", _names())
        self.assertEqual(w.load_registry(self.design).joints, [])


class _ButtDialog:
    def __init__(self):
        self.command = adsk_stub.FakeCommand()
        i = self.command.commandInputs
        i.addSelectionInput("subject", "Backing Member", "")
        i.addSelectionInput("target", "Through Member", "")
        dd = i.addDropDownCommandInput("mode", "Mode", 0)
        dd.listItems.add("Butt", True)
        dd.listItems.add("Saddle", False)
        dd.listItems.add("Through", False)


class TestButtExecute(unittest.TestCase):
    """The toolbar Butt command's commit path (trim / saddle / through)."""

    def setUp(self):
        adsk_stub.reset()
        # subject's END (25,0,0) lands on the target's run: a T.
        self.design, self.subj, self.tgt = _tube_pair(
            None, (25, 0, 20), (25, 0, 0), (0, 0, 0), (50, 0, 0))

    def _run(self, mode="Butt"):
        dlg = _ButtDialog()
        _select(dlg.command, "subject", self.subj)
        _select(dlg.command, "target", self.tgt)
        dd = dlg.command.commandInputs.itemById("mode")
        for k in range(dd.listItems.count):
            dd.listItems.item(k).isSelected = dd.listItems.item(k).name == mode
        butt.command_execute(_Args(dlg.command))
        return dlg

    def test_butt_cuts_the_subject_against_the_target(self):
        self._run("Butt")
        combs = _combines()
        self.assertTrue(combs)
        # The subject is cut against the target keeping the target alive.
        self.assertTrue(any(_cut(op) and keep for op, keep, _t in combs))

    def test_butt_records_a_butt_joint(self):
        self._run("Butt")
        registry = w.load_registry(self.design)
        butts = [j for j in registry.joints if j.kind == "butt"]
        self.assertEqual(len(butts), 1)
        self.assertAlmostEqual(butts[0].vertex[0], 25.0, places=3)

    def test_saddle_mode_records_saddle(self):
        self._run("Saddle")
        registry = w.load_registry(self.design)
        self.assertTrue(any(j.kind == "saddle" for j in registry.joints))

    def test_through_mode_swaps_the_cut_target(self):
        self._run("Through")
        registry = w.load_registry(self.design)
        self.assertTrue(any(j.kind == "through" for j in registry.joints))
        # In through mode the TARGET is the body being cut: the combine's
        # target must be the target body, not the subject.
        ci = [c for c in adsk_stub.CALLS
              if c[0] == "CombineFeatures.createInput"][0]
        self.assertEqual(ci[1][0].name, self.tgt.name)

    def test_rerun_does_not_duplicate(self):
        self._run("Butt")
        self._run("Butt")
        registry = w.load_registry(self.design)
        self.assertEqual(len([j for j in registry.joints
                              if j.kind == "butt"]), 1)


class _BendDialog:
    def __init__(self):
        self.command = adsk_stub.FakeCommand()
        i = self.command.commandInputs
        i.addSelectionInput("member_a", "Member A", "")
        i.addSelectionInput("member_b", "Member B", "")
        dd = i.addDropDownCommandInput("die", "Bend Die", 0)
        dd.listItems.add("(auto: tightest die for the family)", True)


class TestBendExecute(unittest.TestCase):
    """The toolbar Bend command's commit path (trim legs + swept arc)."""

    def setUp(self):
        adsk_stub.reset()
        # run() normally populates the die catalogue; do the same here.
        w._DIES = bd.load_bending_dies()
        # An L corner at (10,0,0): A along +X, B along +Y.
        self.design, self.a, self.b = _tube_pair(
            None, (0, 0, 0), (10, 0, 0), (10, 0, 0), (10, 10, 0))
        # Give the members a CHS family record so the die lookup finds a CLR.
        registry = w.load_registry(self.design)
        m_a = registry.add_member((0, 0, 0), (10, 0, 0), family="CHS",
                                  designation="CHS 42.4x2.6")
        m_b = registry.add_member((10, 0, 0), (10, 10, 0), family="CHS",
                                  designation="CHS 42.4x2.6")
        w.stamp_body(self.a, m_a.mid)
        w.stamp_body(self.b, m_b.mid)
        w.save_registry(self.design, registry)

    def _run(self):
        dlg = _BendDialog()
        _select(dlg.command, "member_a", self.a)
        _select(dlg.command, "member_b", self.b)
        bend.command_execute(_Args(dlg.command))
        return dlg

    def test_bend_trims_both_legs_and_builds_an_arc(self):
        self._run()
        combs = _combines()
        # Two leg trims (drop the waste prism) + the arc's own build.
        self.assertGreaterEqual(len(combs), 2)
        for op, keep, _t in combs[:2]:
            self.assertTrue(_cut(op))
            self.assertFalse(keep, "the trim prism is consumed")
        # An arc is either a sweep (preferred) or a revolve (fallback).
        names = _names()
        self.assertTrue("SweepFeatures.add" in names or
                        "RevolveFeatures.add" in names,
                        "the bend must build a swept or revolved arc")

    def test_bend_records_a_bend_with_clr(self):
        self._run()
        registry = w.load_registry(self.design)
        bends = [j for j in registry.joints if j.kind == "bend"]
        self.assertEqual(len(bends), 1)
        self.assertAlmostEqual(bends[0].vertex[0], 10.0, places=3)
        self.assertGreater(bends[0].params.get("clr_mm", 0.0), 0.0,
                           "the auto die must resolve a real CLR")

    def test_rerun_updates_the_same_bend(self):
        self._run()
        self._run()
        registry = w.load_registry(self.design)
        self.assertEqual(len([j for j in registry.joints
                              if j.kind == "bend"]), 1)

    def test_collinear_members_are_rejected(self):
        adsk_stub.reset()
        self.design, self.a, self.b = _tube_pair(
            None, (0, 0, 0), (10, 0, 0), (10, 0, 0), (20, 0, 0))
        self._run()
        self.assertNotIn("SweepFeatures.add", _names())
        self.assertNotIn("RevolveFeatures.add", _names())
        self.assertEqual(w.load_registry(self.design).joints, [])


if __name__ == "__main__":
    unittest.main()
