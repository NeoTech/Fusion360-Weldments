"""Miter tool (Phase 4E) -- explicit-selection corner miter.

Same toolbox pattern as :mod:`commands.weldmentCope`: instead of selecting a
line-set and letting :func:`lib.joints.joint_spec` *discover* a corner, the user
picks the two members that meet at it. We find their shared vertex, compute the
bisector-plane cutter for each member with :func:`lib.joints.miter_cutter` (the
same box math the auto path uses, proven equal by TestMiterCutterExplicitPair),
and Combine(Cut) each member's waste prism -- exactly the ``cutter.type ==
'plane'`` branch of ``_apply_corner_cuts``, but for one explicit pair.

Both members are trimmed on the shared bisector plane, so their diagonal faces
coincide for any rotation or Position offset. The joint is recorded in the
registry so the BOM lists it and a re-run edits the record.
"""

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ...lib import joints as jt
from ...lib import registry as reg
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_miter'
CMD_NAME = 'Weld Miter'
CMD_Description = 'Miter the corner where two weldment members meet'

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

    a = inputs.addSelectionInput('member_a', 'Member A',
                                 'Select the first tube at the corner')
    a.addSelectionFilter('SolidBodies')
    a.setSelectionLimits(1, 1)

    b = inputs.addSelectionInput('member_b', 'Member B',
                                 'Select the second tube at the corner')
    b.addSelectionFilter('SolidBodies')
    b.setSelectionLimits(1, 1)

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


def _corner_vertex(a_cl, b_cl):
    """The shared corner point of two members that meet end-to-end.

    The closest pair of endpoints (one per member); their midpoint, which for a
    real corner is the coincident vertex. Returns None if the members do not
    actually meet (their nearest ends are far apart).
    """
    best, best_d = None, None
    for pa in (a_cl[0], a_cl[1]):
        for pb in (b_cl[0], b_cl[1]):
            d = jt._dist(pa, pb)
            if best_d is None or d < best_d:
                best_d, best = d, reg.midpoint(pa, pb)
    if best is None or best_d > 1.0:      # > 1 cm apart: not a corner
        return None
    return best


def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    w = _weldment()
    design = _design()
    if design is None:
        return
    root = design.rootComponent

    body_a = _selected_body(inputs.itemById('member_a'))
    body_b = _selected_body(inputs.itemById('member_b'))
    if body_a is None or body_b is None:
        ui.messageBox('Select both members meeting at the corner.')
        return

    cl_a = w._member_centerline(body_a)
    cl_b = w._member_centerline(body_b)
    if cl_a is None or cl_b is None:
        ui.messageBox('Both selections must be straight weldment members.')
        return
    a_s, a_e, a_geom, a_basis = cl_a
    b_s, b_e, b_geom, b_basis = cl_b

    vertex = _corner_vertex(cl_a, cl_b)
    if vertex is None:
        ui.messageBox('The two members do not meet at a corner.')
        return

    # One cutter per member, each oriented along that member's into-vertex
    # direction -- exactly the auto path's per-occurrence miter.
    for body, own_cl, neigh_cl, own_geom, neigh_geom, own_basis, neigh_basis in (
            (body_a, (a_s, a_e), (b_s, b_e), a_geom, b_geom, a_basis, b_basis),
            (body_b, (b_s, b_e), (a_s, a_e), b_geom, a_geom, b_basis, a_basis)):
        cut = jt.miter_cutter(own_cl, neigh_cl, vertex,
                              a_geom=own_geom, b_geom=neigh_geom,
                              a_basis=own_basis, b_basis=neigh_basis)
        if cut is None:
            continue
        region = cut['region']
        perp = max(region['half'][1], region['half'][2])
        depth = 2.0 * region['half'][0] + perp
        try:
            prism, _track = w._miter_cutter(root, vertex, cut['normal'],
                                            perp, depth)
            if prism is None:
                continue
            w._combine_cut(root, body, [prism], keep_tool=False)
        except Exception:
            futil.handle_error(f'{CMD_NAME} cut')

    _record(design, body_a, body_b, cl_a, cl_b, vertex)


def _record(design, body_a, body_b, cl_a, cl_b, vertex):
    """Persist the miter as a registry joint (and its members), for re-run/BOM."""
    w = _weldment()
    registry = w.load_registry(design)
    ma = registry.upsert_member(cl_a[0], cl_a[1], geom=cl_a[2],
                               basis=list(cl_a[3]) if cl_a[3] else None)
    mb = registry.upsert_member(cl_b[0], cl_b[1], geom=cl_b[2],
                               basis=list(cl_b[3]) if cl_b[3] else None)
    for j in registry.joints:
        if j.kind == 'miter' and set(j.member_ids()) == {ma.mid, mb.mid} and \
                j.vertex and reg.distance(j.vertex, vertex) < 0.05:
            w.save_registry(design, registry)
            return
    registry.add_joint('miter', [{'mid': ma.mid, 'role': None},
                                {'mid': mb.mid, 'role': None}],
                       vertex=vertex,
                       selections={'member_a': body_a.name,
                                   'member_b': body_b.name})
    w.save_registry(design, registry)
