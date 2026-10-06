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
    if panel and panel.controls.itemById(CMD_ID) is None:
        panel.controls.addCommand(cmd_def)


def stop():
    _weldment().remove_command_from_panel(CMD_ID)
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def:
        cmd_def.deleteMe()
    global local_handlers
    local_handlers = []


def command_created(args: adsk.core.CommandCreatedEventArgs):
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


def _selected_body(sel):
    try:
        for i in range(sel.selectionCount):
            ent = sel.selection(i).entity
            if isinstance(ent, adsk.fusion.BRepBody):
                return ent
    except Exception:
        return None
    return None


def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    w = _weldment()
    design = _design()
    if design is None:
        return
    root = design.rootComponent

    subj_body = _selected_body(inputs.itemById('subject'))
    tool_body = _selected_body(inputs.itemById('tool'))
    # Linear .value is cm; the joints layer wants millimetres.
    depth_mm = inputs.itemById('depth').value * 10.0
    if subj_body is None or tool_body is None:
        ui.messageBox('Select both members.')
        return
    if subj_body is tool_body:
        ui.messageBox('Pick two different members.')
        return

    subj_cl = w._member_centerline(subj_body)
    tool_cl = w._member_centerline(tool_body)
    if subj_cl is None or tool_cl is None:
        ui.messageBox('Both selections must be straight weldment members.')
        return
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
        ui.messageBox('Could not compute the cope (members are collinear).')
        return
    trim_region = plan['region']
    # Step 2's boolean classifies the plug with the SAME symmetric joint box the
    # auto path uses (cope_cutter), not the asymmetric pull-back prism -- the
    # prism only bounds step 1's axial trim.
    cut = jt.cope_cutter((ss, se), (ts, te), landing,
                         subject_geom=sgeom, tool_geom=tgeom,
                         subject_basis=sbasis, tool_basis=tbasis,
                         depth_mm=depth_mm)
    saddle_region = cut['region'] if cut else trim_region

    try:
        # Step 1: pull the protruding tip back to the stopping face with a
        # finite prism (the tool is not a target of this cut).
        box = w._box_cutter(root, trim_region)
        if box is None or box[0] is None:
            ui.messageBox('Could not build the cope cutter.')
            return
        prism, track = box[0], box[1:]
        comb1 = w._combine_cut(root, subj_body, [prism], keep_tool=False)
        run = w._survivor_after_cut(comb1, None, trim_region) or subj_body
        # Step 2: carve the tool's cross-section out of the member -- the exact
        # boolean the auto path performs.
        _removes, run2 = w.cope_body_cut(root, run, tool_body, saddle_region)
        run = run2 or run
    except Exception:
        futil.handle_error(f'{CMD_NAME} cut')
        return

    # The prism's feature/sketch/plane form a chain the combine depends on, so
    # they cannot be deleted without breaking health -- hide them instead.
    for obj in track:
        for o in (obj if isinstance(obj, (tuple, list)) else (obj,)):
            try:
                o.isLightBulbOn = False
            except Exception:
                pass

    _record(design, subj_body, tool_body, subj_cl, tool_cl, landing, run,
            depth_mm)


def _record(design, subj_body, tool_body, subj_cl, tool_cl, landing, run,
            depth_mm):
    """Persist the cope as a registry joint and stamp the surviving bodies."""
    w = _weldment()
    registry = w.load_registry(design)
    sm = registry.upsert_member(subj_cl[0], subj_cl[1], geom=subj_cl[2],
                                basis=list(subj_cl[3]) if subj_cl[3] else None)
    tm = registry.upsert_member(tool_cl[0], tool_cl[1], geom=tool_cl[2],
                                basis=list(tool_cl[3]) if tool_cl[3] else None)
    # The coping member's body was re-homed by the cut; stamp the survivor so
    # the spine points at the live body.  Never clobber an existing stamp.
    for body, mem in ((run, sm), (tool_body, tm)):
        if body is not None and w.body_mid(body) is None:
            w.stamp_body(body, mem.mid)
    params = {'depth_mm': depth_mm} if depth_mm else {}
    for j in registry.joints:
        if j.kind == 'cope' and set(j.member_ids()) == {sm.mid, tm.mid} and \
                j.vertex and reg.distance(j.vertex, landing) < 0.05:
            j.params.update(params)
            w.save_registry(design, registry)
            return
    registry.add_joint('cope', [{'mid': sm.mid, 'role': None},
                               {'mid': tm.mid, 'role': None}],
                       vertex=landing, params=params,
                       selections={'subject_body': subj_body.name,
                                   'tool_body': tool_body.name})
    w.save_registry(design, registry)
