"""Profile Gusset tool -- a stiffener plate inside a construction profile.

Toolbox-only (never in the automatic generator).  For members whose section is a
solid-rolled profile with an open interior -- HEA/HEB/IPE (I-beams) and UPE/UPN
(channels) -- a plate can be seated *inside* the section so its edges follow the
inner faces of the flanges rather than a bounding box.  The user picks one such
member; we recover its catalogue designation, build the inner-contour plate with
:func:`lib.gussets.profile_inner_polygon`, and extrude it ``thickness`` along the
run at the chosen position.  The plate is a NEW standalone body; the member is
never booleaned.

``Depth`` controls how far the plate reaches across the section (an I-beam's
web stiffener spans +/-depth/2 between the flanges; a channel's plate runs from
the web's inner face toward the open tips).  ``Position`` places it along the
member from its start end.  The plate is oriented in the member's own drawn
section basis (from the registry record when available), so it respects the
profile's real height/width axes and any Rotation the builder applied.

Recorded as a ``gusset`` joint (single member ref) for the BOM; the preview
ghosts the plate (additive, never destructive).
"""

import os

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ...lib import gussets as gs
from ...lib import joints as jt
from ...lib import profiles as prof
from ...lib import registry as reg
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_gusset_profile'
CMD_NAME = 'Profile Gusset'
CMD_Description = 'Add a stiffener plate inside an I-beam or channel member'
ICON_FOLDER = config.icon_folder(__file__)

WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_panel'

# Families whose section has an interior a plate can follow.
_OPEN_FAMILIES = ('HEA', 'HEB', 'IPE', 'UPE', 'UPN')

local_handlers = []
_preview_objs = []


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
    global _preview_objs
    _preview_objs = []
    inputs = args.command.commandInputs

    m = inputs.addSelectionInput('member', 'Member',
                                 'Select an I-beam or channel member')
    m.addSelectionFilter('SolidBodies')
    m.setSelectionLimits(1, 1)

    inputs.addValueInput('depth', 'Depth', 'mm',
                         adsk.core.ValueInput.createByString('40 mm'))
    inputs.addValueInput('thickness', 'Thickness', 'mm',
                         adsk.core.ValueInput.createByString('6 mm'))
    inputs.addValueInput('position', 'Position', 'mm',
                         adsk.core.ValueInput.createByString('50 mm'))

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


def _designation_dict(family_abbr, label):
    """The catalogue designation dict for a family abbreviation + label."""
    for fam in prof.load_profiles():
        if fam['abbreviation'] == family_abbr:
            return prof.find_designation(fam, label)
    return None


def _resolve(w, body):
    """A member's ``(cl, record)`` -- registry record first, body as fallback.

    ``cl`` is ``(start, end, geom, basis)`` and ``record`` the registry Member
    (or None).  The record carries the *drawn* centreline, the *placed* basis,
    and -- crucially -- the ``family``/``designation`` the builder recorded (the
    same values the BOM lists).  Re-deriving the section from the body's faces
    (:func:`weldment._member_centerline` + :func:`profile_fields`) is unreliable
    on an open profile, so we prefer the record and only fall back to the body
    when it carries no stamp.
    """
    try:
        registry = w.load_registry(_design())
    except Exception:
        registry = None
    if registry is not None:
        probe = w._member_centerline(body)
        m = w.resolve_member(registry, body,
                             context_line=(probe[0], probe[1]) if probe else None)
        if m is not None:
            basis = [tuple(v) for v in m.basis] if m.basis else None
            return (tuple(m.start), tuple(m.end), m.geom, basis), m
    return w._member_centerline(body), None


def _profile_plan(w, body, depth_mm, thickness_mm, position_mm):
    """Resolve an open-profile member and its inner plate. ``(error, plan)``."""
    cl, record = _resolve(w, body)
    if cl is None:
        return 'The selection must be a straight weldment member.', None
    start, end, geom, basis = cl
    # Identify the section from the BOM/registry record when present; fall back
    # to re-deriving it from the recovered geometry for an unregistered body.
    if record is not None and record.family:
        family, label = record.family, record.designation
    else:
        fields = w.profile_fields(geom)
        family, label = fields['family'], fields['designation']
    if family not in _OPEN_FAMILIES:
        return ('Select an I-beam (HEA/HEB/IPE) or channel (UPE/UPN) member; '
                f'{family or "this"} has no interior for a plate.', None)
    des = _designation_dict(family, label)
    if des is None:
        return 'Could not resolve the member\'s profile designation.', None
    loop = gs.profile_inner_polygon(family, des, depth_mm)
    if loop is None:
        return 'Could not compute the inner plate for this profile.', None

    # Place the plate perpendicular to the run at `position` from the start end,
    # in the member's own drawn section basis so it follows the real height/width
    # axes.  Prefer the registry record's basis (the builder's true placement);
    # fall back to the body-recovered basis.
    d = jt.line_direction_from(start, end)
    if d is None:
        return 'The member is degenerate.', None
    axis_u, axis_v = _section_axes(w, body, cl, d)
    t = min(max(position_mm * gs.MM_TO_CM, 0.0), jt._dist(start, end))
    origin = jt._add(start, jt._scale(d, t))
    return None, {'cl': cl, 'family': family, 'designation': label,
                   'origin': origin, 'axis_u': axis_u, 'axis_v': axis_v,
                   'loop': loop, 'thickness_cm': thickness_mm * gs.MM_TO_CM,
                   'depth_mm': depth_mm}


def _section_axes(w, body, cl, d):
    """The plate's in-plane ``(axis_u, axis_v)``: the member's drawn section axes.

    The registry record carries the PLACED basis the builder drew with (see
    ``placed_basis``), which maps the designation's local ``(u, v)`` correctly;
    use it when the body resolves to a member.  Otherwise fall back to the
    body-recovered basis, and failing that a frame perpendicular to the run.
    """
    _start, _end, _geom, rec_basis = cl
    try:
        registry = w.load_registry(_design())
        m = w.resolve_member(registry, body, context_line=(_start, _end))
        if m is not None and m.basis:
            u = jt._norm(tuple(m.basis[0]))
            v = jt._norm(tuple(m.basis[1]))
            if u and v:
                return u, v
    except Exception:
        pass
    if rec_basis:
        u = jt._norm(tuple(rec_basis[0]))
        v = jt._norm(tuple(rec_basis[1]))
        if u and v:
            return u, v
    _n, e1, e2 = jt._frame(d)
    return e1, e2


def _build_plate(w, root, plan, member_body, preview):
    """Build the inner plate and boolean the member out of it (saddle-style).

    The plate starts as a full rectangle spanning the section; subtracting the
    member body trims it to the interior void between the flanges/web, so its
    edges follow the profile instead of poking straight through.  The member is
    the Combine *tool* with keep-tool bodies on, so it is never modified -- only
    the (new, throwaway) plate body is cut.
    """
    try:
        body, track = w._plate_body(root, plan['origin'], plan['axis_u'],
                                    plan['axis_v'], plan['loop'],
                                    plan['thickness_cm'], preview=preview,
                                    name='WeldGussetProfile')
    except Exception:
        futil.handle_error(f'{CMD_NAME} plate')
        return 'Could not build the gusset plate.', None
    if body is None:
        return 'Could not build the gusset plate.', None
    if member_body is not None:
        try:
            comb = w._combine_cut(root, body, [member_body], keep_tool=True)
            track = track + [comb]
        except Exception:
            futil.handle_error(f'{CMD_NAME} plate boolean')
            return 'Could not trim the plate to the profile.', None
    if preview and body is not None:
        try:
            body.opacity = w.PREVIEW_OPACITY
        except Exception:
            pass
    return None, {'body': body, 'track': track}


def _clear_preview():
    global _preview_objs
    for obj in reversed(_preview_objs):
        try:
            obj.deleteMe()
        except Exception:
            pass
    _preview_objs = []


def _plan_from_inputs(w, inputs, body):
    depth = inputs.itemById('depth').value * 10.0
    thickness = inputs.itemById('thickness').value * 10.0
    position = inputs.itemById('position').value * 10.0
    return _profile_plan(w, body, depth, thickness, position)


def command_execute_preview(args: adsk.core.CommandEventArgs):
    global _preview_objs
    inputs = args.command.commandInputs
    w = _weldment()
    design = _design()
    _clear_preview()
    if design is None:
        return
    root = design.rootComponent
    body = _selected_body(inputs.itemById('member'))
    if body is None:
        return
    err, plan = _plan_from_inputs(w, inputs, body)
    if err:
        return
    err, payload = _build_plate(w, root, plan, body, preview=True)
    if err:
        return
    _preview_objs = list(payload['track'])


def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    w = _weldment()
    design = _design()
    if design is None:
        return
    root = design.rootComponent
    _clear_preview()

    body = _selected_body(inputs.itemById('member'))
    if body is None:
        ui.messageBox('Select an I-beam or channel member.')
        return
    err, plan = _plan_from_inputs(w, inputs, body)
    if err:
        ui.messageBox(err)
        return
    err, payload = _build_plate(w, root, plan, body, preview=False)
    if err:
        ui.messageBox(err)
        return
    _record(design, w, body, plan, payload['body'], inputs)


def _record(design, w, body, plan, plate_body, inputs):
    """Persist the profile gusset as a single-member registry joint."""
    registry = w.load_registry(design)
    cl = plan['cl']
    mem = registry.upsert_member(cl[0], cl[1], geom=cl[2],
                                 basis=list(cl[3]) if cl[3] else None,
                                 designation=plan['designation'],
                                 family=plan['family'])
    if body is not None and w.body_mid(body) is None:
        w.stamp_body(body, mem.mid)
    params = {'depth_mm': round(plan['depth_mm'], 3),
              'thickness_mm': round(inputs.itemById('thickness').value * 10.0, 3),
              'position_mm': round(inputs.itemById('position').value * 10.0, 3)}
    for j in registry.joints:
        if j.kind == 'gusset' and set(j.member_ids()) == {mem.mid} and \
                j.vertex and reg.distance(j.vertex, plan['origin']) < 0.05:
            j.params.update(params)
            w.save_registry(design, registry)
            return
    registry.add_joint('gusset', [{'mid': mem.mid, 'role': None}],
                       vertex=plan['origin'], params=params,
                       selections={'member': body.name,
                                   'plate_body': plate_body.name})
    w.save_registry(design, registry)


def command_destroy(args: adsk.core.CommandEventArgs):
    # Only drop the preview ghost.  Do NOT clear local_handlers here: that list
    # also holds the commandCreated handler, and commandCreated fires once per
    # command-definition lifetime -- dropping it means the next button press
    # never re-registers execute/preview/destroy (the dialog would work only
    # once until reload).  local_handlers is reset in stop().
    _clear_preview()
