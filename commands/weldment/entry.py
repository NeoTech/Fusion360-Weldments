import os

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ...lib import profiles as prof
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

# --------------------------------------------------------------------------- #
# Command identity + placement (Create panel, right after the Pipe tool).
# --------------------------------------------------------------------------- #
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment'
CMD_NAME = 'Weldment'
CMD_Description = 'Create a weldment profile along 3D sketch lines'

WORKSPACE_ID = 'FusionSolidEnvironment'
# The Create panel id depends on the active design type; try them in order.
PANEL_IDS = ['SolidCreatePanel', 'PlasticPartsCreatePanel']
COMMAND_BESIDE_ID = 'PrimitivePipe'

ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources', '')

local_handlers = []

# Module-level cache of the loaded catalogue (populated on start()).
_FAMILIES = []

# Opacity (0-1) used to ghost the live preview body so it reads as transient.
PREVIEW_OPACITY = 0.4

# Objects created for the current in-dialog preview: list of
# (feature, sketch, plane) tuples.  Rebuilt on every preview and torn down when
# the inputs change, the command is cancelled, or (for the committed ones) OK is
# pressed.  Only one weldment command can be active at a time, so a module-level
# list is sufficient.
_preview_objs = []


# --------------------------------------------------------------------------- #
# Add-in lifecycle
# --------------------------------------------------------------------------- #
def start():
    global _FAMILIES
    try:
        _FAMILIES = prof.annotate_families(prof.load_profiles())
    except Exception:
        _FAMILIES = []
        futil.handle_error(f'{CMD_NAME} load profiles')

    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)

    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    # The "Create" panel id differs by design type; use the first that exists.
    panel = None
    for pid in PANEL_IDS:
        panel = workspace.toolbarPanels.itemById(pid)
        if panel:
            break
    if panel:
        control = panel.controls.addCommand(cmd_def, COMMAND_BESIDE_ID, False)
        control.isPromoted = True
    else:
        futil.log(f'{CMD_NAME} Create panel not found; command not promoted.')


def stop():
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    for pid in PANEL_IDS:
        panel = workspace.toolbarPanels.itemById(pid)
        if panel:
            command_control = panel.controls.itemById(CMD_ID)
            if command_control:
                command_control.deleteMe()

    if command_definition:
        command_definition.deleteMe()


# --------------------------------------------------------------------------- #
# Dialog definition
# --------------------------------------------------------------------------- #
def command_created(args: adsk.core.CommandCreatedEventArgs):
    futil.log(f'{CMD_NAME} Command Created Event')

    inputs = args.command.commandInputs

    # 1. The path: one or more 3D sketch lines.
    sel: adsk.core.SelectionCommandInput = inputs.addSelectionInput(
        'path', 'Lines', 'Select 3D sketch line(s)')
    sel.addSelectionFilter('SketchLines')
    sel.setSelectionLimits(1, 0)

    # 2. Profile family dropdown.
    fam: adsk.core.DropDownCommandInput = inputs.addDropDownCommandInput(
        'family', 'Profile', adsk.core.DropDownStyles.TextListDropDownStyle)
    fam_items = fam.listItems
    for label in prof.family_labels(_FAMILIES):
        fam_items.add(label, False)
    if fam_items.count > 0:
        fam_items.item(0).isSelected = True

    # 3. Designation dropdown (rebuilt whenever the family changes).
    inputs.addDropDownCommandInput(
        'designation', 'Designation', adsk.core.DropDownStyles.TextListDropDownStyle)

    # 4. Rotation of the profile about the selected line (its own axis).
    #    Hidden until a line is picked: with no anchor, Fusion would draw the
    #    wheel at its default origin (0,0,0). _update_rotation_manipulator
    #    reveals it once it has been anchored to the selected line.
    ang: adsk.core.AngleValueCommandInput = inputs.addAngleValueCommandInput(
        'rotation', 'Rotation', adsk.core.ValueInput.createByString('0 deg'))
    ang.hasMinimumValue = False
    ang.hasMaximumValue = False
    ang.isVisible = False

    _rebuild_designations(inputs)

    futil.add_handler(args.command.execute, command_execute, local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_execute_preview, local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed, local_handlers=local_handlers)
    futil.add_handler(args.command.select, command_select, local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy, local_handlers=local_handlers)


def _selected_family(inputs):
    fam: adsk.core.DropDownCommandInput = inputs.itemById('family')
    idx = _dropdown_index(fam)
    if 0 <= idx < len(_FAMILIES):
        return _FAMILIES[idx]
    return _FAMILIES[0] if _FAMILIES else None


def _dropdown_index(dropdown):
    """Return the selected index of a DropDownCommandInput (no selectedItem attr)."""
    items = dropdown.listItems
    for i in range(items.count):
        if items.item(i).isSelected:
            return i
    return -1


def _rebuild_designations(inputs):
    des: adsk.core.DropDownCommandInput = inputs.itemById('designation')
    des.listItems.clear()
    family = _selected_family(inputs)
    if not family:
        return
    for label in prof.designation_labels(family):
        des.listItems.add(label, False)
    if des.listItems.count > 0:
        des.listItems.item(0).isSelected = True


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
def _resolve(inputs):
    """Resolve the current dialog inputs to (root, selection, geom, label, angle).

    ``angle`` is the profile rotation (radians) about the selected line.
    Returns None when the designation is not (yet) valid.
    """
    family = _selected_family(inputs)
    if not family:
        return None
    des: adsk.core.DropDownCommandInput = inputs.itemById('designation')
    des_idx = _dropdown_index(des)
    designations = prof.designations(family)
    if des_idx < 0 or des_idx >= len(designations):
        return None
    designation = designations[des_idx]
    designation['_abbreviation'] = family['abbreviation']
    geom = prof.section_geometry(designation)
    sel: adsk.core.SelectionCommandInput = inputs.itemById('path')
    ang: adsk.core.AngleValueCommandInput = inputs.itemById('rotation')
    angle_rad = ang.value if ang else 0.0
    design = adsk.fusion.Design.cast(app.activeProduct)
    return design.rootComponent, sel, geom, designation['designation'], angle_rad


def _clear_preview():
    """Delete the transient preview objects (feature, then sketch, then plane)."""
    global _preview_objs
    for feature, sketch, plane in _preview_objs:
        for obj in (feature, sketch, plane):
            try:
                obj.deleteMe()
            except Exception:
                pass
    _preview_objs = []


def _snapshot_selection(sel):
    """Return the entities currently picked in ``sel`` (for later restore)."""
    try:
        return [sel.selection(i).entity for i in range(sel.selectionCount)]
    except Exception:
        return []


def _restore_selection(sel, saved):
    """Re-add ``saved`` entities if the preview churn dropped the selection.

    Creating and deleting the preview's construction plane / sketch / body can
    reset the canvas selection, which would otherwise "deselect" the chosen
    line(s) every time the profile or designation changes.
    """
    if not saved:
        return
    try:
        if sel.selectionCount == len(saved):
            return
        sel.clearSelection()
        for entity in saved:
            sel.addSelection(entity)
    except Exception:
        pass


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    # Only rebuild the designation list here.  The preview itself is torn down
    # and rebuilt in executePreview (which Fusion fires right after this event),
    # so we deliberately do NOT clear it here -- clearing here would drop the
    # user's line selection before executePreview can preserve it.
    if args.input.id == 'family':
        _rebuild_designations(args.inputs)


def command_select(args: adsk.core.SelectionEventArgs):
    """Anchor the rotation wheel the instant a line is picked.

    This fires on the actual click -- before the preview redraw -- so the
    manipulator is already positioned on the selected line when it first
    appears.  Anchoring inside executePreview is too late: setManipulator only
    takes effect on the following redraw, so the wheel would briefly show at
    its default origin (0,0,0), which is the sketch's first point.
    """
    sel = args.activeInput
    if sel is None or sel.id != 'path':
        return
    lines = [sel.selection(i).entity for i in range(sel.selectionCount)]
    _update_rotation_manipulator(sel.parentCommand.commandInputs, lines)


def _update_rotation_manipulator(inputs, lines):
    """Anchor the rotation wheel to the (first) selected line.

    Without this, Fusion draws the angle manipulator at its default origin
    (0,0,0) with fixed X/Y directions -- the "random point" away from the
    geometry.  We place the wheel in the profile's own plane (normal = the line
    direction) centred on the line's start point, so dragging it spins the
    profile about that line exactly as the Rotation value does.
    """
    ang: adsk.core.AngleValueCommandInput = inputs.itemById('rotation')
    if ang is None:
        return
    if not lines:
        # No line to anchor to -- keep the wheel hidden rather than leaving it
        # stranded at its last (or default) position.
        ang.isVisible = False
        return
    try:
        world = lines[0].worldGeometry
        start = world.startPoint
        end = world.endPoint
        direction = (end.x - start.x, end.y - start.y, end.z - start.z)
        axis_u, axis_v = prof.compute_basis(
            direction, prof.selection_reference([_line_direction(l) for l in lines]))
        ang.setManipulator(
            adsk.core.Point3D.create(start.x, start.y, start.z),
            adsk.core.Vector3D.create(*axis_u),
            adsk.core.Vector3D.create(*axis_v))
        ang.isVisible = True
        ang.isEnabled = True
    except Exception:
        futil.handle_error(f'{CMD_NAME} rotation manipulator')


def command_execute_preview(args: adsk.core.CommandEventArgs):
    """Rebuild the ghosted preview body for the current selection + profile."""
    inputs = args.command.commandInputs
    sel = inputs.itemById('path')
    # Snapshot the picked lines first: creating/deleting the preview geometry
    # below can reset the canvas selection, so we build from this snapshot and
    # restore the selection at the end rather than trusting the live count.
    saved = _snapshot_selection(sel)
    _clear_preview()
    _update_rotation_manipulator(inputs, saved)
    resolved = _resolve(inputs)
    if not resolved:
        _restore_selection(sel, saved)
        return
    root, _sel, geom, label, angle = resolved
    ref = _selection_reference(saved)
    for line in saved:
        objs = _build_weldment(root, line, geom, label, angle, ref, preview=True)
        if objs:
            _preview_objs.append(objs)
    # Make sure the user's lines are still highlighted after the churn.
    _restore_selection(sel, saved)


def command_execute(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Execute Event')

    inputs = args.command.commandInputs
    sel = inputs.itemById('path')
    # Snapshot the lines before tearing down the preview: the delete churn can
    # reset the canvas selection, and we must still build the committed bodies.
    saved = _snapshot_selection(sel)

    # Replace the ghosted preview with real, fully-opaque bodies.
    _clear_preview()
    resolved = _resolve(inputs)
    if not resolved:
        return
    root, _sel, geom, label, angle = resolved
    ref = _selection_reference(saved)

    created = 0
    for line in saved:
        if _build_weldment(root, line, geom, label, angle, ref):
            created += 1

    if created == 0:
        ui.messageBox('No weldments were created. Select 3D sketch line(s) first.')


def command_destroy(args: adsk.core.CommandEventArgs):
    futil.log(f'{CMD_NAME} Command Destroy Event')
    # On cancel (destroy without a preceding execute) the preview is still live;
    # remove it.  After OK, _preview_objs is already empty so this is a no-op.
    _clear_preview()
    global local_handlers
    local_handlers = []


# --------------------------------------------------------------------------- #
# Geometry construction (the only place that touches adsk.fusion)
# --------------------------------------------------------------------------- #
def _line_direction(line):
    """Unit direction of a sketch line entity in model space."""
    world = line.worldGeometry
    start_pt = world.startPoint
    end_pt = world.endPoint
    return prof._norm((end_pt.x - start_pt.x, end_pt.y - start_pt.y,
                       end_pt.z - start_pt.z))


def _selection_reference(lines):
    """Shared profile "up" reference for a whole selection (see selection_reference)."""
    return prof.selection_reference([_line_direction(l) for l in lines])


def _build_weldment(root, line, geom, designation_label='', angle_rad=0.0,
                    ref=None, preview=False):
    """Build one weldment body along ``line`` using cross-section ``geom``.

    ``angle_rad`` rotates the profile about the selected line (its own axis),
    turning it around its centre point without tilting about any other axis.
    ``ref`` is the shared "up" reference for the selection so that profiles on
    differently-oriented lines stay rolled consistently and their ends align.

    Returns ``(feature, sketch, plane)`` on success, or ``None`` on failure.
    When ``preview`` is True the resulting body is ghosted (semi-transparent)
    so it reads as a transient preview rather than a committed part.
    """
    try:
        world = line.worldGeometry
        start_pt = world.startPoint
        end_pt = world.endPoint
        direction = (end_pt.x - start_pt.x, end_pt.y - start_pt.y, end_pt.z - start_pt.z)

        # Construction plane normal to the line at its start, chaining OFF.
        path = adsk.fusion.Path.create(
            line, adsk.fusion.ChainedCurveOptions.noChainedCurves)
        cp_input = root.constructionPlanes.createInput()
        cp_input.setByPath(
            path, adsk.fusion.PathDistanceTypes.ProportionalPathDistanceType,
            adsk.core.ValueInput.createByReal(0.0))
        plane = root.constructionPlanes.add(cp_input)

        origin = plane.geometry.origin
        origin_t = (origin.x, origin.y, origin.z)
        axis_u, axis_v = prof.compute_basis(direction, ref)
        # Spin the profile about the line axis (its own centre point).
        axis_u, axis_v = prof.rotate_basis(axis_u, axis_v, angle_rad)

        sketch = root.sketches.add(plane)
        sketch.name = f'WeldProfile_{designation_label}'

        _draw_section(sketch, origin_t, axis_u, axis_v, geom)

        if sketch.profiles.count == 0:
            futil.log(f'{CMD_NAME} No closed profile found in section sketch')
            plane.deleteMe()
            return None

        extrude_input = root.features.extrudeFeatures.createInput(
            sketch.profiles.item(0),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        extrude_input.startExtent = adsk.fusion.ProfilePlaneStartDefinition.create()
        # Extrude exactly the line's length along the plane normal (which is the
        # line direction), so the body ends on the line's far point.
        extrude_input.setOneSideExtent(
            adsk.fusion.DistanceExtentDefinition.create(
                adsk.core.ValueInput.createByReal(line.length)),
            adsk.fusion.ExtentDirections.PositiveExtentDirection)
        feature = root.features.extrudeFeatures.add(extrude_input)

        if preview:
            try:
                for bi in range(feature.bodies.count):
                    feature.bodies.item(bi).opacity = PREVIEW_OPACITY
            except Exception:
                pass
        return (feature, sketch, plane)
    except Exception:
        futil.handle_error(f'{CMD_NAME} build weldment')
        return None


def _draw_section(sketch, origin_t, axis_u, axis_v, geom):
    """Draw the cross-section loops onto ``sketch`` (in sheet space).

    ``sketch.transform`` maps sheet -> model, so model-space points must be
    pushed through its INVERSE to obtain sheet coordinates.
    """
    to_sheet = sketch.transform.copy()
    to_sheet.invert()

    def sheet_pt(u, v):
        model = prof.map_local_to_model(origin_t, axis_u, axis_v, u, v)
        p = adsk.core.Point3D.create(*model)
        p.transformBy(to_sheet)
        return p

    if geom['kind'] == 'polygons':
        fillets = geom.get('fillets', [])
        for li, loop in enumerate(geom['loops']):
            # Draw the sharp polygon first, remembering each edge.
            n = len(loop)
            lines = []
            for j in range(n):
                p1 = sheet_pt(*loop[j])
                p2 = sheet_pt(*loop[(j + 1) % n])
                lines.append(sketch.sketchCurves.sketchLines.addByTwoPoints(p1, p2))
            # Then round the requested corners with the sketch fillet tool,
            # which trims the two edges and inserts a tangent arc for us.
            for corner, r_mm in (fillets[li] if li < len(fillets) else []):
                prev = lines[(corner - 1) % n]   # ends at the corner
                cur = lines[corner]              # starts at the corner
                # addFillet wants a Point3D (the shared vertex) to indicate the
                # side, NOT a SketchPoint -- passing line.endSketchPoint raises
                # "argument 3 of type Point3D".
                cp = sheet_pt(*loop[corner])
                sketch.sketchCurves.sketchArcs.addFillet(
                    prev, cp, cur, cp, r_mm * prof.MM_TO_CM)
    elif geom['kind'] == 'circles':
        # The plane origin is the sketch's own sheet origin (0, 0).
        center = adsk.core.Point3D.create(0.0, 0.0, 0.0)
        for r_mm in geom['radii']:
            sketch.sketchCurves.sketchCircles.addByCenterRadius(
                center, r_mm * prof.MM_TO_CM)
