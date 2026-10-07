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

Two styles, chosen in the dialog:
  * Open          -- cut the members in place (the default). The tips stop at
    the vertex, so for sections that do not tile the corner square (an I-beam,
    say) the outer corner is left open.
  * Edge to edge  -- grow each tip by its setback past the vertex, then cut, so
    the two poked ends meet at the outer corner and fill it -- the same result
    the auto builder produces (which grows members by the setback at build time
    before trimming). A cut alone can never ADD the missing corner material.
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

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_miter'
CMD_NAME = 'Weld Miter'
CMD_Description = 'Miter the corner where two weldment members meet'
ICON_FOLDER = config.icon_folder(__file__)

WORKSPACE_ID = 'FusionSolidEnvironment'
# Shared Weldments panel on the dedicated Weldments tab (see
# weldment.ensure_weldments_panel).
PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_panel'

# Style dropdown labels -> the key the execute path understands.
_STYLES = [('Edge to edge', 'edge'), ('Open', 'open')]

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
    inputs = args.command.commandInputs

    a = inputs.addSelectionInput('member_a', 'Member A',
                                 'Select the first tube at the corner')
    a.addSelectionFilter('SolidBodies')
    a.setSelectionLimits(1, 1)

    b = inputs.addSelectionInput('member_b', 'Member B',
                                 'Select the second tube at the corner')
    b.addSelectionFilter('SolidBodies')
    b.setSelectionLimits(1, 1)

    style = inputs.addDropDownCommandInput(
        'style', 'Style', adsk.core.DropDownStyles.TextListDropDownStyle)
    for label, _key in _STYLES:
        style.listItems.add(label, label == 'Edge to edge')

    futil.add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)


def _selected_body(sel):
    """The single BRepBody picked in a selection input, or None."""
    for i in range(sel.selectionCount):
        ent = sel.selection(i).entity
        if isinstance(ent, adsk.fusion.BRepBody):
            return ent
    return None


def _style_key(dd):
    try:
        for i in range(dd.listItems.count):
            it = dd.listItems.item(i)
            if it.isSelected:
                for label, key in _STYLES:
                    if label == it.name:
                        return key
    except Exception:
        pass
    return 'edge'


def _grow_tip(root, body, vertex, own, setback_cm):
    """Grow a member's end cap ``setback_cm`` past the vertex, into that member.

    The auto builder grows each member by the setback at build time so the two
    poked tips tile the corner's outer square; the toolbox must do the same to
    get an edge-to-edge miter (a cut alone can never ADD the missing material).
    ``own`` points from the vertex INTO the member, so the tip grows along
    ``-own``. We extrude the cap face as a SEPARATE prism and Join it into
    ``body`` only -- a direct Join-feature extrude merges every overlapping body
    and would swallow the neighbour. Returns the combine feature, or None.
    """
    if setback_cm <= 1e-6:
        return None
    best, best_d = None, None
    for k in range(body.faces.count):
        f = body.faces.item(k)
        g = f.geometry
        if not isinstance(g, adsk.core.Plane):
            continue
        nrm = (g.normal.x, g.normal.y, g.normal.z)
        if abs(abs(jt._dot(nrm, own)) - 1.0) > 1e-3:
            continue                       # not perpendicular to the run
        p = f.pointOnFace
        d = jt._dist((p.x, p.y, p.z), vertex)
        if best_d is None or d < best_d:
            best, best_d = f, d
    if best is None:
        return None
    grow = jt._scale(own, -1.0)            # past the vertex, away from the body
    ex = root.features.extrudeFeatures
    # Plane.normal has an ARBITRARY orientation (it is not the face's outward
    # normal), so the extrude direction cannot be derived from it -- guessing
    # wrong extrudes the prism INTO the member and the Join adds nothing.
    # Build the prism, check which side of the vertex its bbox centre landed
    # on, and flip if it grew the wrong way.
    try:
        for direction in (adsk.fusion.ExtentDirections.PositiveExtentDirection,
                          adsk.fusion.ExtentDirections.NegativeExtentDirection):
            ei = ex.createInput(
                best, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
            ei.setOneSideExtent(
                adsk.fusion.DistanceExtentDefinition.create(
                    adsk.core.ValueInput.createByReal(setback_cm)), direction)
            prism = ex.add(ei)
            tool = prism.bodies.item(0)
            bb = tool.boundingBox
            ctr = ((bb.minPoint.x + bb.maxPoint.x) / 2.0,
                   (bb.minPoint.y + bb.maxPoint.y) / 2.0,
                   (bb.minPoint.z + bb.maxPoint.z) / 2.0)
            if jt._dot(jt._sub(ctr, vertex), grow) >= 0.0:
                return _weldment()._combine(
                    root, body, [tool],
                    adsk.fusion.FeatureOperations.JoinFeatureOperation, False)
            prism.deleteMe()               # grew into the member; try the other way
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

    style = _style_key(inputs.itemById('style'))

    # One cutter per member, each oriented along that member's into-vertex
    # direction -- exactly the auto path's per-occurrence miter.  Edge-to-edge
    # grows each tip by its setback first (so the poked ends fill the outer
    # corner), then trims on the shared bisector plane.
    pairs = ((body_a, (a_s, a_e), (b_s, b_e), a_geom, b_geom, a_basis, b_basis),
             (body_b, (b_s, b_e), (a_s, a_e), b_geom, a_geom, b_basis, a_basis))
    cuts = []
    for body, own_cl, neigh_cl, own_geom, neigh_geom, own_basis, neigh_basis in pairs:
        cut = jt.miter_cutter(own_cl, neigh_cl, vertex,
                              a_geom=own_geom, b_geom=neigh_geom,
                              a_basis=own_basis, b_basis=neigh_basis)
        cuts.append((body, own_cl, cut))
        if style == 'edge' and cut is not None:
            own, _tip = jt._tip_and_own(own_cl, vertex)
            _grow_tip(root, body, vertex, own, cut['setback'])

    for body, own_cl, cut in cuts:
        if cut is None:
            continue
        region = cut['region']
        perp = max(region['half'][1], region['half'][2])
        depth = 2.0 * region['half'][0] + perp
        try:
            prism, track = w._miter_cutter(root, vertex, cut['normal'],
                                           perp, depth)
            if prism is None:
                continue
            w._combine_cut(root, body, [prism], keep_tool=False)
            # The cutter's extrude/sketch/plane form a reference chain the
            # combine depends on, so they cannot be deleted without breaking
            # feature health -- hide them instead to keep the viewport clean.
            for obj in track:
                try:
                    obj.isLightBulbOn = False
                except Exception:
                    pass
        except Exception:
            futil.handle_error(f'{CMD_NAME} cut')

    _record(design, body_a, body_b, cl_a, cl_b, vertex)


def _record(design, body_a, body_b, cl_a, cl_b, vertex):
    """Persist the miter as a registry joint (and its members), for re-run/BOM."""
    w = _weldment()
    registry = w.load_registry(design)
    ma = registry.upsert_member(cl_a[0], cl_a[1], geom=cl_a[2],
                               basis=list(cl_a[3]) if cl_a[3] else None,
                               **w.profile_fields(cl_a[2]))
    mb = registry.upsert_member(cl_b[0], cl_b[1], geom=cl_b[2],
                               basis=list(cl_b[3]) if cl_b[3] else None,
                               **w.profile_fields(cl_b[2]))
    for body, mem in ((body_a, ma), (body_b, mb)):
        if w.body_mid(body) is None:         # keep an existing stamp's owner
            w.stamp_body(body, mem.mid)
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
