"""Butt tool (Phase 4E) -- explicit-selection butt / saddle / through.

Same toolbox pattern as :mod:`commands.weldmentCope`: the user picks the two
members meeting at a T (or a corner) and a mode, and we cut exactly that joint
with :func:`lib.joints.butt_trim` -- no frame detection.

Modes (mirroring the auto path's three butt behaviours):
  * Butt    -- the subject's tip is trimmed flush to the target's near face
    (a flat square end sitting on the tube).
  * Saddle  -- the subject is run into the target and the target's cross-section
    is carved out of it (the full plug box, same as a cope).
  * Through -- the roles swap: the SUBJECT runs through and the TARGET is
    trimmed to the subject's face.

The joint is recorded in the registry so the BOM lists it and a re-run edits it.
"""

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ...lib import joints as jt
from ...lib import registry as reg
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_butt'
CMD_NAME = 'Weld Butt'
CMD_Description = 'Butt, saddle, or run through where two weldment members meet'

WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_panel'

# Dropdown labels -> the mode the execute path understands.
_MODES = [('Butt', 'butt'), ('Saddle', 'saddle'), ('Through', 'through')]

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

    subj = inputs.addSelectionInput('subject', 'Backing Member',
                                    'Select the tube that stops at the other')
    subj.addSelectionFilter('SolidBodies')
    subj.setSelectionLimits(1, 1)

    tgt = inputs.addSelectionInput('target', 'Through Member',
                                   'Select the tube it meets')
    tgt.addSelectionFilter('SolidBodies')
    tgt.setSelectionLimits(1, 1)

    mode = inputs.addDropDownCommandInput('mode', 'Mode',
                                          adsk.core.DropDownStyles.TextListDropDownStyle)
    for label, _key in _MODES:
        mode.listItems.add(label, label == 'Butt')

    futil.add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)


def _selected_body(sel):
    try:
        for i in range(sel.selectedCount):
            ent = sel.selection(i).entity
            if isinstance(ent, adsk.fusion.BRepBody):
                return ent
    except Exception:
        return None
    return None


def _mode_key(dd):
    try:
        for i in range(dd.listItems.count):
            it = dd.listItems.item(i)
            if it.isSelected:
                for label, key in _MODES:
                    if label == it.name:
                        return key
    except Exception:
        pass
    return 'butt'


def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    w = _weldment()
    design = _design()
    if design is None:
        return
    root = design.rootComponent

    subj_body = _selected_body(inputs.itemById('subject'))
    tgt_body = _selected_body(inputs.itemById('target'))
    mode = _mode_key(inputs.itemById('mode'))
    if subj_body is None or tgt_body is None:
        ui.messageBox('Select both members.')
        return

    subj_cl = w._member_centerline(subj_body)
    tgt_cl = w._member_centerline(tgt_body)
    if subj_cl is None or tgt_cl is None:
        ui.messageBox('Both selections must be straight weldment members.')
        return
    ss, se, sgeom, sbasis = subj_cl
    ts, te, tgeom, tbasis = tgt_cl

    # Through swaps the roles: the target is trimmed to the subject's face.
    if mode == 'through':
        cut_body, tool_body = tgt_body, subj_body
        cl_cut, cl_tool = tgt_cl, subj_cl
        geom_cut, basis_cut = tgeom, tbasis
        geom_tool, basis_tool = sgeom, sbasis
        saddle = False
    else:
        cut_body, tool_body = subj_body, tgt_body
        cl_cut, cl_tool = subj_cl, tgt_cl
        geom_cut, basis_cut = sgeom, sbasis
        geom_tool, basis_tool = tgeom, tbasis
        saddle = (mode == 'saddle')

    # Landing: the backing member's tip nearest the through member's line,
    # projected onto it (the vertex the auto path would find at this T).
    cs, ce = cl_cut[:2]
    ks, ke = cl_tool[:2]

    def _gap(p):
        cp, _t = reg.closest_point_on_segment(p, ks, ke)
        return jt._dist(p, cp)
    tip = ce if _gap(ce) <= _gap(cs) else cs
    landing, _t = reg.closest_point_on_segment(tip, ks, ke)

    res = jt.butt_trim(cl_cut[:2], cl_tool[:2], landing,
                       subject_geom=geom_cut, tool_geom=geom_tool,
                       subject_basis=basis_cut, tool_basis=basis_tool,
                       saddle=saddle)
    if res is None or res['cutter'] is None:
        ui.messageBox('Could not compute the butt (members are collinear).')
        return

    region = res['cutter']['region']
    try:
        comb = w._combine_cut(root, cut_body, [tool_body], keep_tool=True)
        w._remove_inside_region(root, comb, tool_body, region)
    except Exception:
        futil.handle_error(f'{CMD_NAME} cut')
        return

    _record(design, subj_body, tgt_body, subj_cl, tgt_cl, landing, mode)


def _record(design, subj_body, tgt_body, subj_cl, tgt_cl, landing, mode):
    w = _weldment()
    registry = w.load_registry(design)
    sm = registry.upsert_member(subj_cl[0], subj_cl[1], geom=subj_cl[2],
                                basis=list(subj_cl[3]) if subj_cl[3] else None)
    tm = registry.upsert_member(tgt_cl[0], tgt_cl[1], geom=tgt_cl[2],
                               basis=list(tgt_cl[3]) if tgt_cl[3] else None)
    for j in registry.joints:
        if j.kind == mode and set(j.member_ids()) == {sm.mid, tm.mid} and \
                j.vertex and reg.distance(j.vertex, landing) < 0.05:
            w.save_registry(design, registry)
            return
    registry.add_joint(mode, [{'mid': sm.mid, 'role': None},
                              {'mid': tm.mid, 'role': None}],
                       vertex=landing,
                       selections={'subject_body': subj_body.name,
                                   'target_body': tgt_body.name})
    w.save_registry(design, registry)
