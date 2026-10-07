"""Corner Gusset tool -- an additive stiffener plate at a corner of two members.

Toolbox-only: gussets never appear in the automatic generator's joint
vocabulary.  The user picks the two members meeting at a corner; we find their
shared vertex (the same rule the Miter tool uses), build a flat plate in the
plane containing both runs, and record it as a ``gusset`` joint so the BOM lists
it.  The plate is a NEW standalone body -- the members are never booleaned.

Shapes (see :func:`lib.gussets.gusset_polygon`):
  * Triangle           -- a right triangle with its legs along the two members.
  * Triangle extended  -- the triangle plus a band of extra material pushed out
    along the hypotenuse (a wider lap to weld).
  * Rectangle          -- a w x h plate filling the corner square.

``Width`` runs along member A, ``Height`` along the in-plane perpendicular,
``Thickness`` is the plate gauge (extruded symmetrically about the plate plane),
and ``Extension`` only applies to the extended triangle.  ``Alignment`` mirrors
the plate into the opposite quadrant (an outside gusset vs the default inside).

The preview ghosts the plate (a new body, so it is never destructive -- unlike
the cope, no body copies are needed); command_execute builds the real one.
"""

import os

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ...lib import gussets as gs
from ...lib import registry as reg
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_gusset'
CMD_NAME = 'Corner Gusset'
CMD_Description = 'Add a gusset plate at the corner where two members meet'
ICON_FOLDER = config.icon_folder(__file__)

WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_panel'

# Dialog labels -> the keys the geometry layer understands.
_SHAPES = [('Triangle', 'triangle'), ('Triangle extended', 'triangle_extended'),
           ('Rectangle', 'rectangle')]
_ALIGNMENTS = [('Inside', 'inside'), ('Outside', 'outside')]

local_handlers = []

# The ghosted plate + helpers the current preview built (deleted on the next
# preview/execute and on destroy).  Additive, so nothing to un-hide.
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

    a = inputs.addSelectionInput('member_a', 'Member A',
                                 'Select the first member at the corner')
    a.addSelectionFilter('SolidBodies')
    a.setSelectionLimits(1, 1)

    b = inputs.addSelectionInput('member_b', 'Member B',
                                 'Select the second member at the corner')
    b.addSelectionFilter('SolidBodies')
    b.setSelectionLimits(1, 1)

    shape = inputs.addDropDownCommandInput(
        'shape', 'Shape', adsk.core.DropDownStyles.TextListDropDownStyle)
    for label, _key in _SHAPES:
        shape.listItems.add(label, label == 'Triangle')

    inputs.addValueInput('width', 'Width', 'mm',
                         adsk.core.ValueInput.createByString('100 mm'))
    inputs.addValueInput('height', 'Height', 'mm',
                         adsk.core.ValueInput.createByString('80 mm'))
    inputs.addValueInput('thickness', 'Thickness', 'mm',
                         adsk.core.ValueInput.createByString('6 mm'))
    ext = inputs.addValueInput('extension', 'Extension', 'mm',
                               adsk.core.ValueInput.createByString('20 mm'))
    # Extension only shapes the 'Triangle extended' hypotenuse band; grey it out
    # for the other shapes so it never looks broken.
    ext.isEnabled = False

    align = inputs.addDropDownCommandInput(
        'alignment', 'Alignment', adsk.core.DropDownStyles.TextListDropDownStyle)
    for label, _key in _ALIGNMENTS:
        align.listItems.add(label, label == 'Inside')

    futil.add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_execute_preview,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy,
                      local_handlers=local_handlers)


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    """Enable Extension only when the shape is the extended triangle."""
    if args.input.id != 'shape':
        return
    shape = _pick(args.inputs.itemById('shape'), _SHAPES, 'triangle')
    args.inputs.itemById('extension').isEnabled = (shape == 'triangle_extended')


def _selected_body(sel):
    try:
        for i in range(sel.selectionCount):
            ent = sel.selection(i).entity
            if isinstance(ent, adsk.fusion.BRepBody):
                return ent
    except Exception:
        return None
    return None


def _pick(dd, pairs, default):
    try:
        for i in range(dd.listItems.count):
            it = dd.listItems.item(i)
            if it.isSelected:
                for label, key in pairs:
                    if label == it.name:
                        return key
    except Exception:
        pass
    return default


def _member_cl(w, body):
    """A member's ``(start, end, geom, basis)`` -- registry record first.

    The registry stores the *drawn* centreline and the *placed* section basis the
    builder used, which is exact.  Re-deriving them from the body's faces
    (:func:`weldment._member_centerline`) is unreliable on an open profile (an
    I-beam/channel has many faces and no clean cap pair), so we prefer the record
    and only fall back to the body when it carries no stamp.
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
            return (tuple(m.start), tuple(m.end), m.geom, basis)
    return w._member_centerline(body)


def _gusset_plan(w, body_a, body_b, shape, width, height, thickness,
                extension, alignment):
    """Resolve the two members and the plate's placement. ``(error, plan)``."""
    cl_a = _member_cl(w, body_a)
    cl_b = _member_cl(w, body_b)
    if cl_a is None or cl_b is None:
        return 'Both selections must be straight weldment members.', None
    vertex = gs.corner_vertex(cl_a, cl_b)
    if vertex is None:
        return 'The two members do not meet at a corner.', None
    frame = gs.plate_frame(vertex, cl_a, cl_b)
    if frame is None:
        return 'The members are collinear; there is no corner to gusset.', None
    normal, e1, e2 = frame
    origin = gs.corner_placement(cl_a, cl_b, vertex)
    loop = gs.gusset_polygon(shape, width, height, extension, alignment)
    return None, {'cl_a': cl_a, 'cl_b': cl_b, 'vertex': vertex,
                  'origin': origin,
                  'e1': e1, 'e2': e2, 'loop': loop,
                  'thickness_cm': thickness * gs.MM_TO_CM}


def _build_plate(w, root, plan, preview):
    """Extrude the plate as a new body. ``(error, payload)``."""
    try:
        body, track = w._plate_body(root, plan['origin'], plan['e1'], plan['e2'],
                                    plan['loop'], plan['thickness_cm'],
                                    preview=preview, name='WeldGusset')
    except Exception:
        futil.handle_error(f'{CMD_NAME} plate')
        return 'Could not build the gusset plate.', None
    if body is None:
        return 'Could not build the gusset plate.', None
    return None, {'body': body, 'track': track}


def _clear_preview():
    global _preview_objs
    for obj in reversed(_preview_objs):
        try:
            obj.deleteMe()
        except Exception:
            pass
    _preview_objs = []


def command_execute_preview(args: adsk.core.CommandEventArgs):
    """Ghost the plate as a new body (additive, so never destructive)."""
    global _preview_objs
    inputs = args.command.commandInputs
    w = _weldment()
    design = _design()
    _clear_preview()
    if design is None:
        return
    root = design.rootComponent
    body_a = _selected_body(inputs.itemById('member_a'))
    body_b = _selected_body(inputs.itemById('member_b'))
    if body_a is None or body_b is None or body_a is body_b:
        return
    err, plan = _plan_from_inputs(w, inputs, body_a, body_b)
    if err:
        return
    err, payload = _build_plate(w, root, plan, preview=True)
    if err:
        return
    _preview_objs = list(payload['track'])


def _plan_from_inputs(w, inputs, body_a, body_b):
    shape = _pick(inputs.itemById('shape'), _SHAPES, 'triangle')
    alignment = _pick(inputs.itemById('alignment'), _ALIGNMENTS, 'inside')
    width = inputs.itemById('width').value * 10.0
    height = inputs.itemById('height').value * 10.0
    thickness = inputs.itemById('thickness').value * 10.0
    extension = inputs.itemById('extension').value * 10.0
    return _gusset_plan(w, body_a, body_b, shape, width, height, thickness,
                        extension, alignment)


def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    w = _weldment()
    design = _design()
    if design is None:
        return
    root = design.rootComponent
    _clear_preview()

    body_a = _selected_body(inputs.itemById('member_a'))
    body_b = _selected_body(inputs.itemById('member_b'))
    if body_a is None or body_b is None:
        ui.messageBox('Select both members meeting at the corner.')
        return
    if body_a is body_b:
        ui.messageBox('Select two different members.')
        return

    err, plan = _plan_from_inputs(w, inputs, body_a, body_b)
    if err:
        ui.messageBox(err)
        return
    err, payload = _build_plate(w, root, plan, preview=False)
    if err:
        ui.messageBox(err)
        return

    shape = _pick(inputs.itemById('shape'), _SHAPES, 'triangle')
    alignment = _pick(inputs.itemById('alignment'), _ALIGNMENTS, 'inside')
    _record(design, w, body_a, body_b, plan, payload['body'], shape, alignment,
            inputs)


def _record(design, w, body_a, body_b, plan, plate_body, shape, alignment,
            inputs):
    """Persist the gusset as a registry joint and stamp the plate + members."""
    registry = w.load_registry(design)
    ma = registry.upsert_member(plan['cl_a'][0], plan['cl_a'][1],
                                geom=plan['cl_a'][2],
                                basis=list(plan['cl_a'][3]) if plan['cl_a'][3] else None,
                                **w.profile_fields(plan['cl_a'][2]))
    mb = registry.upsert_member(plan['cl_b'][0], plan['cl_b'][1],
                                geom=plan['cl_b'][2],
                                basis=list(plan['cl_b'][3]) if plan['cl_b'][3] else None,
                                **w.profile_fields(plan['cl_b'][2]))
    for body, mem in ((body_a, ma), (body_b, mb)):
        if body is not None and w.body_mid(body) is None:
            w.stamp_body(body, mem.mid)
    params = {'shape': shape, 'alignment': alignment,
              'width_mm': round(inputs.itemById('width').value * 10.0, 3),
              'height_mm': round(inputs.itemById('height').value * 10.0, 3),
              'thickness_mm': round(inputs.itemById('thickness').value * 10.0, 3)}
    if shape == 'triangle_extended':
        params['extension_mm'] = round(inputs.itemById('extension').value * 10.0, 3)
    # Re-run pickup: a gusset at the same vertex between the same pair edits
    # its record instead of adding a duplicate.
    for j in registry.joints:
        if j.kind == 'gusset' and set(j.member_ids()) == {ma.mid, mb.mid} and \
                j.vertex and reg.distance(j.vertex, plan['vertex']) < 0.05:
            j.params.update(params)
            w.save_registry(design, registry)
            return
    registry.add_joint('gusset', [{'mid': ma.mid, 'role': None},
                                  {'mid': mb.mid, 'role': None}],
                       vertex=plan['vertex'], params=params,
                       selections={'member_a': body_a.name,
                                   'member_b': body_b.name,
                                   'plate_body': plate_body.name})
    w.save_registry(design, registry)


def command_destroy(args: adsk.core.CommandEventArgs):
    # Only drop the preview ghost.  Do NOT clear local_handlers here: that list
    # also holds the commandCreated handler, and commandCreated fires once per
    # command-definition lifetime -- dropping it means the next button press
    # never re-registers execute/preview/destroy (the dialog would work only
    # once until reload).  local_handlers is reset in stop().
    _clear_preview()
