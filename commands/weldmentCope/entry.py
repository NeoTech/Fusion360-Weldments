"""Cope tool (Phase 4c / C2) -- explicit-selection cope onto an existing member.

The user picks the coping member (the one whose tip lands on the other) and the
tool it sits on, and we cut exactly that joint -- no frame detection.  Unlike
the automatic command, where the coping member is BUILT to its stopping face by
an axial offset, here the member already exists with its tip poking into the
tool, so the cope is a two-step cut:

  1. a finite pull-back prism (see :func:`lib.joints.cope_trim_box`) trims the
     protruding tip back to the same stopping face the auto path builds to
     (Combine(Cut, keep_tool=False) against the member alone);
  2. :func:`commands.weldment.cope_body_cut` then carves the tool's
     cross-section out of the member (Combine(Cut, keep_tool=True) against the
     tool, Remove the plug inside the joint box) -- the SAME code the auto path
     runs, so the two never drift.

No Split Body and no pointContainment guessing: every cut is a bounded boolean.
The joint is recorded in the registry so the BOM lists it and a re-run edits it.
"""

import os

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ...lib import joints as jt
from ...lib import registry as reg
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_cope'
CMD_NAME = 'Weld Cope'
CMD_Description = 'Cope one weldment member onto another (explicit selection)'
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'resources', '')

WORKSPACE_ID = 'FusionSolidEnvironment'
# Shared Weldments panel on the dedicated Weldments tab (see
# weldment.ensure_weldments_panel).
PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_panel'

local_handlers = []

# Opacity used to ghost the preview cutter prism so it reads as transient
# (matches the auto command's PREVIEW_OPACITY).
PREVIEW_OPACITY = 0.4
# The throwaway objects the current preview built, in creation order: the two
# CopyPasteBody features (the ghost is cut on COPIES of the members, never the
# selected bodies themselves -- a destructive boolean on a selected body inside
# executePreview makes Fusion roll the preview back on OK and SKIP
# command_execute entirely, the "preview shows, nothing commits" bug), the
# prism cutter and its sketches/planes, and the Combine/Remove features.  The
# preview ghosts the cut COPY so the user sees the real saddle result; every
# object here is deleted by _clear_preview() at the start of the next
# preview/execute and on destroy (mirrors the working weldment command, whose
# preview likewise only cuts its own throwaway bodies).
_preview_objs = []
# Real member bodies the preview hid while its ghost stands in for them
# (_clear_preview shows them again).  Hiding is reversible and never consumes
# a selection, unlike a Combine.
_preview_hidden = []
# The two member bodies captured during the last preview, kept as a fallback
# in case Fusion drops a selection entry before execute reads it.
_preview_bodies = (None, None)


def _design():
    return adsk.fusion.Design.cast(app.activeProduct)


def _weldment():
    from ..weldment import entry as weldment
    return weldment


def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created,
                      local_handlers=local_handlers)
    # Shared Weldments tab/panel; create it if needed so the button is never
    # dropped when this start() runs before weldment's.
    panel = _weldment().ensure_weldments_panel()
    _weldment().add_pinned_command(panel, cmd_def, CMD_ID)


def stop():
    _weldment().remove_command_from_panel(CMD_ID)
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def:
        cmd_def.deleteMe()
    global local_handlers
    local_handlers = []


def command_created(args: adsk.core.CommandCreatedEventArgs):
    global _preview_objs, _preview_bodies, _preview_hidden
    # Fresh dialog session: no leftover preview ghosts or cached bodies.
    # (Module globals persist across dialog sessions, so a stale ghost from a
    # previous OK/Cancel must not leak in.)
    _preview_objs = []
    _preview_hidden = []
    _preview_bodies = (None, None)
    inputs = args.command.commandInputs

    subj = inputs.addSelectionInput('subject', 'Coping Member',
                                    'Select the member whose end is coped')
    subj.addSelectionFilter('SolidBodies')
    subj.setSelectionLimits(1, 1)

    tgt = inputs.addSelectionInput('tool', 'Tool Member',
                                   'Select the member it sits on')
    tgt.addSelectionFilter('SolidBodies')
    tgt.setSelectionLimits(1, 1)

    # Extra penetration into the tool beyond the default stopping face (mm).
    inputs.addValueInput('depth', 'Depth', 'mm',
                         adsk.core.ValueInput.createByReal(0.0))

    futil.add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_execute_preview,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy,
                      local_handlers=local_handlers)


def _selected_body(sel):
    try:
        for i in range(sel.selectionCount):
            ent = sel.selection(i).entity
            if isinstance(ent, adsk.fusion.BRepBody):
                return ent
    except Exception:
        return None
    return None


def _cope_plan(w, subj_body, tool_body, depth_mm):
    """Resolve the two members' centerlines and the cope's two cut regions.

    Returns ``(error, plan)``.  On success ``plan`` carries the centerlines,
    the landing vertex, the step-1 pull-back prism region and the step-2
    saddle region.  Shared by the non-destructive preview (which ghosts only
    the prism) and :func:`_do_cut` (which performs both cuts), so the two never
    drift.
    """
    subj_cl = w._member_centerline(subj_body)
    tool_cl = w._member_centerline(tool_body)
    if subj_cl is None or tool_cl is None:
        return 'Both selections must be straight weldment members.', None
    ss, se, sgeom, sbasis = subj_cl
    ts, te, tgeom, tbasis = tool_cl

    # Landing: the coping member's tip nearest the tool's line, projected onto
    # it -- the vertex the auto path would find at this T.
    def _gap(p):
        cp, _t = reg.closest_point_on_segment(p, ts, te)
        return jt._dist(p, cp)
    tip = se if _gap(se) <= _gap(ss) else ss
    landing, _t = reg.closest_point_on_segment(tip, ts, te)

    plan = jt.cope_trim_box((ss, se), (ts, te), landing,
                            subject_geom=sgeom, tool_geom=tgeom,
                            subject_basis=sbasis, tool_basis=tbasis,
                            depth_mm=depth_mm)
    if plan is None:
        return 'Could not compute the cope (members are collinear).', None
    trim_region = plan['region']
    # Step 2's boolean classifies the plug with the SAME symmetric joint box the
    # auto path uses (cope_cutter), not the asymmetric pull-back prism -- the
    # prism only bounds step 1's axial trim.
    cut = jt.cope_cutter((ss, se), (ts, te), landing,
                         subject_geom=sgeom, tool_geom=tgeom,
                         subject_basis=sbasis, tool_basis=tbasis,
                         depth_mm=depth_mm)
    saddle_region = cut['region'] if cut else trim_region
    return None, {'subj_cl': subj_cl, 'tool_cl': tool_cl, 'landing': landing,
                  'trim_region': trim_region, 'saddle_region': saddle_region}


def _do_cut(w, root, subj_body, tool_body, depth_mm, preview):
    """Run the two-step cope cut (called from command_execute on OK).

    Returns ``(error, payload)``.  On success ``error`` is None and ``payload``
    carries the resolved centerlines, the landing vertex, the surviving run
    body and the prism helper objects (``track``).  On failure ``error`` is a
    user-facing message.  The preview never calls this -- it ghosts the prism
    only (see command_execute_preview), because a destructive Combine in
    executePreview suppresses command_execute.
    """
    err, plan = _cope_plan(w, subj_body, tool_body, depth_mm)
    if err:
        return err, None
    subj_cl, tool_cl = plan['subj_cl'], plan['tool_cl']
    landing = plan['landing']
    trim_region, saddle_region = plan['trim_region'], plan['saddle_region']
    try:
        # Step 1: pull the protruding tip back to the stopping face with a
        # finite prism (the tool is not a target of this cut).
        box = w._box_cutter(root, trim_region, preview=preview)
        if box is None or box[0] is None:
            return 'Could not build the cope cutter.', None
        prism, track = box[0], box[1:]
        comb1 = w._combine_cut(root, subj_body, [prism], keep_tool=False)
        run = w._survivor_after_cut(comb1, None, trim_region) or subj_body
        # Step 2: carve the tool's cross-section out of the member -- the exact
        # boolean the auto path performs.
        removes, run2 = w.cope_body_cut(root, run, tool_body, saddle_region)
        run = run2 or run
    except Exception:
        futil.handle_error(f'{CMD_NAME} cut')
        return 'The cope cut failed.', None

    payload = {'subj_cl': subj_cl, 'tool_cl': tool_cl, 'landing': landing,
               'run': run, 'track': track, 'feats': removes + [comb1]}
    return None, payload


def _flatten(objs):
    """Flatten arbitrarily nested tuples/lists of objects to a flat list."""
    out = []
    for o in objs:
        if isinstance(o, (tuple, list)):
            out.extend(_flatten(o))
        elif o is not None:
            out.append(o)
    return out


def _clear_preview():
    """Delete the throwaway preview objects in REVERSE creation order (the
    cut features first, then the prism, then the body copies).  Nothing here
    ever touched a selected body, so teardown is a plain deleteMe -- no undo
    transaction, no rollback of the user's work."""
    global _preview_objs, _preview_hidden
    for obj in reversed(_flatten(_preview_objs)):
        try:
            obj.deleteMe()
        except Exception:
            pass
    _preview_objs = []
    for body in _preview_hidden:
        try:
            body.isVisible = True
        except Exception:
            pass
    _preview_hidden = []


def command_execute_preview(args: adsk.core.CommandEventArgs):
    """Ghost the coping member's CUT RESULT so the user sees where it lands.

    The cut runs on throwaway COPIES of both members (CopyPasteBody features),
    never on the selected bodies: a destructive boolean on a selection inside
    executePreview makes Fusion skip command_execute on OK (the "preview
    shows, nothing commits" bug, proven by A/B).  The copies are cut with the
    exact same two-step _do_cut geometry the commit runs, ghosted, and deleted
    by the next preview or on close.  command_execute performs the real cut.
    """
    global _preview_objs, _preview_bodies
    inputs = args.command.commandInputs
    w = _weldment()
    design = _design()
    _clear_preview()
    if design is None:
        return
    root = design.rootComponent
    subj_body = _selected_body(inputs.itemById('subject'))
    tool_body = _selected_body(inputs.itemById('tool'))
    depth_mm = inputs.itemById('depth').value * 10.0
    if subj_body is None or tool_body is None or subj_body is tool_body:
        return
    _preview_bodies = (subj_body, tool_body)
    err, plan = _cope_plan(w, subj_body, tool_body, depth_mm)
    if err:
        return
    # Work on copies so the real members are never consumed or re-homed.  If
    # either copy fails, abort: falling back to the REAL body would make the
    # preview destructive again -- the exact bug this whole design avoids.
    cp_s = root.features.copyPasteBodies.add(subj_body)
    cp_t = root.features.copyPasteBodies.add(tool_body)
    if (cp_s is None or cp_s.bodies.count == 0 or
            cp_t is None or cp_t.bodies.count == 0):
        for cp in (cp_s, cp_t):
            if cp is not None:
                try:
                    cp.deleteMe()
                except Exception:
                    pass
        return
    _preview_objs.append((cp_s, cp_t))
    subj_copy = cp_s.bodies.item(0)
    tool_copy = cp_t.bodies.item(0)
    try:
        box = w._box_cutter(root, plan['trim_region'], preview=True)
        if box is None or box[0] is None:
            return
        _preview_objs.append(box)
        comb1 = w._combine_cut(root, subj_copy, [box[0]], keep_tool=False)
        _preview_objs.append(comb1)
        run = w._survivor_after_cut(comb1, None,
                                    plan['trim_region']) or subj_copy
        removes, run2 = w.cope_body_cut(root, run, tool_copy,
                                        plan['saddle_region'])
        _preview_objs.append(removes)
        run = run2 or run
    except Exception:
        futil.handle_error(f'{CMD_NAME} preview cut')
        return
    # Ghost the cut copy and hide the REAL member it stands in for (the ghost
    # is coincident with it except at the cope, and two overlapping copies of
    # one member read as a render glitch).  _clear_preview restores it.
    try:
        run.opacity = PREVIEW_OPACITY
    except Exception:
        pass
    try:
        subj_body.isVisible = False
        _preview_hidden.append(subj_body)
    except Exception:
        pass
    try:
        tool_copy.isVisible = False
    except Exception:
        pass


def command_destroy(args: adsk.core.CommandEventArgs):
    # On Cancel, delete the preview ghost.  On OK, command_execute already
    # cleared it and committed the real cut, so this is a harmless no-op.
    _clear_preview()


def command_execute(args: adsk.core.CommandEventArgs):
    """Perform the real cope cut on OK (the preview is non-destructive)."""
    global _preview_bodies
    inputs = args.command.commandInputs
    w = _weldment()
    design = _design()
    if design is None:
        return
    root = design.rootComponent
    # Linear .value is cm; the joints layer wants millimetres.
    depth_mm = inputs.itemById('depth').value * 10.0

    # Replace the non-destructive preview ghost with the real cut.
    _clear_preview()

    subj_body = _selected_body(inputs.itemById('subject'))
    tool_body = _selected_body(inputs.itemById('tool'))
    # The preview never consumed the bodies, so the selections are live; fall
    # back to the cached refs only if Fusion dropped an entry.
    if subj_body is None:
        subj_body = _preview_bodies[0]
    if tool_body is None:
        tool_body = _preview_bodies[1]
    if subj_body is None or tool_body is None:
        ui.messageBox('Select both members.')
        return
    if subj_body is tool_body:
        ui.messageBox('Pick two different members.')
        return

    err, payload = _do_cut(w, root, subj_body, tool_body, depth_mm,
                           preview=False)
    if err:
        ui.messageBox(err)
        return

    # The prism's feature/sketch/plane form a chain the combine depends on, so
    # they cannot be deleted without breaking health -- hide them instead.
    for obj in payload['track']:
        for o in (obj if isinstance(obj, (tuple, list)) else (obj,)):
            try:
                o.isLightBulbOn = False
            except Exception:
                pass

    _record(design, subj_body, tool_body, payload['subj_cl'],
            payload['tool_cl'], payload['landing'], payload['run'], depth_mm)


def _record(design, subj_body, tool_body, subj_cl, tool_cl, landing, run,
            depth_mm):
    """Persist the cope as a registry joint and stamp the surviving bodies."""
    w = _weldment()
    registry = w.load_registry(design)
    # The toolbox recovers sections from BODIES, so unlike the builder it must
    # identify the catalogue designation itself -- otherwise the BOM shows a
    # member row with blank name/designation (the "last tube has no
    # designation" complaint).
    sm = registry.upsert_member(subj_cl[0], subj_cl[1], geom=subj_cl[2],
                                basis=list(subj_cl[3]) if subj_cl[3] else None,
                                **w.profile_fields(subj_cl[2]))
    tm = registry.upsert_member(tool_cl[0], tool_cl[1], geom=tool_cl[2],
                                basis=list(tool_cl[3]) if tool_cl[3] else None,
                                **w.profile_fields(tool_cl[2]))
    # The coping member's body was re-homed by the cut; stamp the survivor so
    # the spine points at the live body.  Never clobber an existing stamp.
    for body, mem in ((run, sm), (tool_body, tm)):
        if body is not None and w.body_mid(body) is None:
            w.stamp_body(body, mem.mid)
    params = {'depth_mm': depth_mm} if depth_mm else {}
    for j in registry.joints:
        if j.kind in ('cope', 'cope_t', 'cope_angle') and \
                set(j.member_ids()) == {sm.mid, tm.mid} and \
                j.vertex and reg.distance(j.vertex, landing) < 0.05:
            j.params.update(params)
            w.save_registry(design, registry)
            return
    # Classify like the auto path (lib.joints.corner_cuts): a coped member
    # meeting its tool at a right angle is a T cope, anything else an angled
    # cope -- a bare 'cope' made the BOM's joint list unreadable.
    kind = jt.cope_kind(jt.line_direction_from(*subj_cl[:2]),
                        jt.line_direction_from(*tool_cl[:2]))
    registry.add_joint(kind, [{'mid': sm.mid, 'role': None},
                              {'mid': tm.mid, 'role': None}],
                       vertex=landing, params=params,
                       selections={'subject_body': subj_body.name,
                                   'tool_body': tool_body.name})
    w.save_registry(design, registry)
