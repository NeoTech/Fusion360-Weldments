"""Cope tool (Phase 4c) -- the pilot for the explicit-selection toolbox model.

The whole point of the Phase-4 rethink (docs/phase4-ui-rethink.md): instead of
selecting a line-set and letting :func:`lib.joints.joint_spec` *discover* that a
member's end lands on another, the user states intent directly -- pick the member
to cope, pick the member to sit on, set a depth -- and we compute exactly that
one joint. No frame detection, so none of the corner/T/arc-zone heuristics that
made the auto engine fragile.

Flow:
  1. Two body selections: the coping member and the target it saddles onto.
  2. The landing point is the coping member's tip nearest the target, projected
     onto the target's centreline (the vertex the auto path would have found).
  3. :func:`lib.joints.cope_cutter` builds the cutter for that explicit pair --
     the same box joint_spec produces (proven by TestCopeCutterExplicitPair), but
     computed from the two members alone.
  4. Combine(Cut) the coping body against the target body, Remove the plug inside
     the joint box (the same recipe _apply_corner_cuts uses).
  5. Record the joint in the registry so the BOM panel lists it and a re-run
     edits the record instead of re-detecting.

This command reuses the weldment command's helpers (body cut, region removal,
registry load/save) rather than duplicating them; it is the thin, explicit front
end over the same proven geometry.
"""

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
        CMD_ID, CMD_NAME, CMD_Description)
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
                                    'Select the tube to cope')
    subj.addSelectionFilter('SolidBodies')
    subj.setSelectionLimits(1, 1)

    tgt = inputs.addSelectionInput('target', 'Onto Member',
                                   'Select the tube to sit on')
    tgt.addSelectionFilter('SolidBodies')
    tgt.setSelectionLimits(1, 1)

    inputs.addValueInput('depth', 'Cope Depth', 'mm',
                         adsk.core.ValueInput.createByReal(0.0))

    futil.add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)


def _selected_body(sel):
    """The single BRepBody picked in a selection input, or None."""
    try:
        for i in range(sel.selectedCount):
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
    tgt_body = _selected_body(inputs.itemById('target'))
    depth_mm = inputs.itemById('depth').value / 0.1   # cm -> mm
    if subj_body is None or tgt_body is None:
        ui.messageBox('Select both a coping member and a target member.')
        return

    subj_cl = w._member_centerline(subj_body)
    tgt_cl = w._member_centerline(tgt_body)
    if subj_cl is None or tgt_cl is None:
        ui.messageBox('Both selections must be straight weldment members.')
        return
    ss, se, sgeom, sbasis = subj_cl
    ts, te, tgeom, tbasis = tgt_cl

    # Landing: the coping tip is the subject endpoint nearest the target line;
    # project it onto the target to get the vertex the auto path would find.
    def _gap(p):
        cp, _t = reg.closest_point_on_segment(p, ts, te)
        return jt._dist(p, cp)
    tip = se if _gap(se) <= _gap(ss) else ss
    landing, _t = reg.closest_point_on_segment(tip, ts, te)

    cut = jt.cope_cutter((ss, se), (ts, te), landing,
                         subject_geom=sgeom, tool_geom=tgeom,
                         subject_basis=sbasis, tool_basis=tbasis,
                         depth_mm=depth_mm)
    if cut is None:
        ui.messageBox('Could not compute the cope (degenerate geometry).')
        return

    region = cut['region']
    try:
        comb = w._combine_cut(root, subj_body, [tgt_body], keep_tool=True)
        w._remove_inside_region(root, comb, tgt_body, region)
    except Exception:
        futil.handle_error(f'{CMD_NAME} cut')
        return

    _record(design, subj_body, tgt_body, subj_cl, tgt_cl, landing)


def _record(design, subj_body, tgt_body, subj_cl, tgt_cl, landing):
    """Persist the cope as a registry joint (and its members), for re-run/BOM."""
    w = _weldment()
    registry = w.load_registry(design)
    sm = registry.upsert_member(subj_cl[0], subj_cl[1], geom=subj_cl[2],
                                basis=list(subj_cl[3]) if subj_cl[3] else None)
    tm = registry.upsert_member(tgt_cl[0], tgt_cl[1], geom=tgt_cl[2],
                               basis=list(tgt_cl[3]) if tgt_cl[3] else None)
    # Avoid a duplicate joint on the same subject/tool pair at the same vertex.
    for j in registry.joints:
        if j.kind == 'cope' and set(j.member_ids()) == {sm.mid, tm.mid} and \
                j.vertex and reg.distance(j.vertex, landing) < 0.05:
            w.save_registry(design, registry)
            return
    registry.add_joint('cope', [{'mid': sm.mid, 'role': None},
                               {'mid': tm.mid, 'role': None}],
                       vertex=landing, params={'depth_mm': 0.0},
                       selections={'subject_body': subj_body.name,
                                   'target_body': tgt_body.name})
    w.save_registry(design, registry)
