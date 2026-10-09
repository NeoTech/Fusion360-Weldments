"""Tube tool -- profile creation without joints.

Everything the automatic (Auto) builder does to *create* members -- pick 3D
sketch lines, choose a profile family/designation, align via the Position grid,
and set per-line Rotation / Offset Start / Offset End -- but with NO joint
machinery: no Joint Start/End dropdowns, no corner detection, no miter/cope/
bend cuts.  Joint work lives in the dedicated toolbox commands (Weld Cope,
Weld Miter, Weld Butt, Weld Bend), so this command only lays down the plain
full-length tubes.

The builder itself is not duplicated: every helper here delegates to
:mod:`commands.weldment.entry` (``_build_weldment``, ``persist_members``,
``_resolve``, ``_section_anchor``, ``placed_basis``, the selection/preview
utilities), so the two commands can never drift on how a section is drawn or
how a member is recorded in the registry.  Members built here are stamped and
persisted exactly like Auto's, so the toolbox tools find them unchanged.
"""

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ...lib import profiles as prof
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_tube'
CMD_NAME = 'Tube'
CMD_Description = 'Create weldment tube profiles along 3D sketch lines (no joints)'
ICON_FOLDER = config.icon_folder(__file__)

WORKSPACE_ID = 'FusionSolidEnvironment'
# Shared Weldments panel on the dedicated Weldments tab (see
# weldment.ensure_weldments_panel).
PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_panel'

local_handlers = []

# One entry per DATA table row (row 0 is the read-only header, not tracked
# here), in row order: {'num','rot','os','oe'}.  Mirrors weldment._row_ids but
# with only the three profile-placement columns.
_row_ids = []
_uid_counter = [0]

_TABLE_HEADERS = ('#', 'Rotation', 'Offset Start', 'Offset End')

# The throwaway preview bodies (each a (feature, sketch, plane) tuple) built
# for the current dialog session; torn down on the next preview, on cancel,
# and after OK (mirrors weldment._preview_objs).
_preview_objs = []

# Re-entrancy guard for "Sync all" propagation (see command_input_changed).
_syncing = False


def _design():
    return adsk.fusion.Design.cast(app.activeProduct)


def _weldment():
    from ..weldment import entry as weldment
    return weldment


def _new_uid():
    _uid_counter[0] += 1
    return _uid_counter[0]


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
    inputs = args.command.commandInputs

    args.command.setDialogInitialSize(640, 420)
    args.command.setDialogMinimumSize(520, 300)

    # 1. The path: one or more 3D sketch lines.
    sel: adsk.core.SelectionCommandInput = inputs.addSelectionInput(
        'path', 'Lines', 'Select 3D sketch line(s)')
    sel.addSelectionFilter('SketchLines')
    sel.setSelectionLimits(1, 0)

    # 2. Profile family + designation (same catalogues as Auto).
    fam: adsk.core.DropDownCommandInput = inputs.addDropDownCommandInput(
        'family', 'Profile', adsk.core.DropDownStyles.TextListDropDownStyle)
    w = _weldment()
    for label in prof.family_labels(w._FAMILIES):
        fam.listItems.add(label, False)
    if fam.listItems.count > 0:
        fam.listItems.item(0).isSelected = True

    inputs.addDropDownCommandInput(
        'designation', 'Designation', adsk.core.DropDownStyles.TextListDropDownStyle)

    # 3. Position grid: which point of the cross-section sits ON the picked
    #    line (global, exactly like Auto -- see weldment command_created).
    pos: adsk.core.DropDownCommandInput = inputs.addDropDownCommandInput(
        'position', 'Position', adsk.core.DropDownStyles.TextListDropDownStyle)
    for _key, label in prof.GRID_POSITIONS:
        pos.listItems.add(label, False)
    if pos.listItems.count > 0:
        pos.listItems.item(0).isSelected = True    # "Center"

    # 4. Per-line placement table: Rotation / Offset Start / Offset End only.
    #    No joint columns -- that is the whole point of Tube vs Auto.
    tbl: adsk.core.TableCommandInput = inputs.addTableCommandInput(
        'params', 'Per Line', 4, '1:2:2:2')
    tbl.maximumVisibleRows = 12
    sync: adsk.core.BoolValueCommandInput = inputs.addBoolValueInput(
        'sync_all', 'Sync all', True, '', True)
    tbl.addToolbarCommandInput(sync)
    global _row_ids
    _row_ids = []
    _make_header_row(inputs, tbl)

    _rebuild_designations(inputs)

    futil.add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_execute_preview,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.select, command_select,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy,
                      local_handlers=local_handlers)


def _make_header_row(inputs, tbl):
    """Row 0 of read-only column titles (Fusion tables have no header API)."""
    for c, title in enumerate(_TABLE_HEADERS):
        box = inputs.addTextBoxCommandInput(f'thdr_{c}', '', title, 1, True)
        box.isEnabled = False
        tbl.addCommandInput(box, 0, c)


def _selected_family(inputs):
    return _weldment()._selected_family(inputs)


def _dropdown_index(dropdown):
    return _weldment()._dropdown_index(dropdown)


def _rebuild_designations(inputs):
    _weldment()._rebuild_designations(inputs)


def _resolve(inputs):
    return _weldment()._resolve(inputs)


def _section_anchor(inputs, geom):
    return _weldment()._section_anchor(inputs, geom)


def _row_params(inputs, r):
    """(angle_rad, offset_start, offset_end) for table row ``r``."""
    if r >= len(_row_ids):
        return (0.0, 0.0, 0.0)
    ids = _row_ids[r]
    ang: adsk.core.FloatSpinnerCommandInput = inputs.itemById(ids['rot'])
    ofs: adsk.core.DistanceValueCommandInput = inputs.itemById(ids['os'])
    ofe: adsk.core.DistanceValueCommandInput = inputs.itemById(ids['oe'])
    return (ang.value if ang else 0.0,
            ofs.value if ofs else 0.0,
            ofe.value if ofe else 0.0)


def _row_index_of(input_id):
    for r, ids in enumerate(_row_ids):
        if input_id in ids.values():
            return r
    return None


def _cell_column(input_id):
    """Column key ('rot'/'os'/'oe') for a table-cell id, else None.

    The cell ids carry a 't' prefix (trot_/tos_/toe_) so they never collide with
    the Auto command's identically-keyed table inputs in the shared input store;
    the prefix is stripped here so the column keys stay 'rot'/'os'/'oe' (the
    same keys :func:`_row_params` and the row dicts use).
    """
    for col in ('rot', 'os', 'oe'):
        if input_id.startswith('t' + col + '_'):
            return col
    return None


def _sync_table_rows(inputs, lines):
    """One data row per selected line; existing rows keep their values."""
    tbl: adsk.core.TableCommandInput = inputs.itemById('params')
    if tbl is None:
        return
    n = len(lines)
    while len(_row_ids) > n:
        _row_ids.pop()
        try:
            tbl.deleteRow(len(_row_ids) + 1)   # +1: row 0 is the header
        except Exception:
            pass
    while len(_row_ids) < n:
        r = len(_row_ids)
        uid = _new_uid()
        ids = {'num': f'tnum_{uid}', 'rot': f'trot_{uid}',
               'os': f'tos_{uid}', 'oe': f'toe_{uid}'}
        num = inputs.addTextBoxCommandInput(ids['num'], '', str(r + 1), 1, True)
        rot = inputs.addFloatSpinnerCommandInput(
            ids['rot'], '', 'degree', -1000, 1000, 15, 0)
        os_ = inputs.addDistanceValueCommandInput(
            ids['os'], '', adsk.core.ValueInput.createByString('0 mm'))
        oe_ = inputs.addDistanceValueCommandInput(
            ids['oe'], '', adsk.core.ValueInput.createByString('0 mm'))
        tbl.addCommandInput(num, r + 1, 0)
        tbl.addCommandInput(rot, r + 1, 1)
        tbl.addCommandInput(os_, r + 1, 2)
        tbl.addCommandInput(oe_, r + 1, 3)
        _row_ids.append(ids)
    sync: adsk.core.BoolValueCommandInput = inputs.itemById('sync_all')
    if sync is not None:
        sync.isVisible = len(_row_ids) >= 2
    _update_manipulators(inputs, lines)


def _update_manipulators(inputs, lines):
    """Anchor the offset arrows (same policy as Auto: synced -> row 0 only)."""
    sync: adsk.core.BoolValueCommandInput = inputs.itemById('sync_all')
    synced = bool(sync and sync.value)
    for r, ids in enumerate(_row_ids):
        if synced:
            anchor = lines[0] if lines else None
            show = (r == 0)
        else:
            anchor = lines[r] if r < len(lines) else None
            show = anchor is not None
        _apply_row_manipulators(
            inputs.itemById(ids['rot']), inputs.itemById(ids['os']),
            inputs.itemById(ids['oe']), anchor, show)


def _apply_row_manipulators(ang, ofs, ofe, line, show):
    w = _weldment()
    for inp in (ang, ofs, ofe):
        if inp is not None:
            try:
                inp.isVisible = True
            except Exception:
                pass
    if not show or line is None:
        for inp in (ofs, ofe):
            if inp is not None:
                try:
                    inp.isEnabled = False
                except Exception:
                    pass
        return
    try:
        world = line.worldGeometry
        start = world.startPoint
        end = world.endPoint
        direction = (end.x - start.x, end.y - start.y, end.z - start.z)
        dir_vec = adsk.core.Vector3D.create(*prof._norm(direction))
        if ofs is not None:
            ofs.setManipulator(
                adsk.core.Point3D.create(start.x, start.y, start.z), dir_vec)
            ofs.isEnabled = True
        if ofe is not None:
            ofe.setManipulator(
                adsk.core.Point3D.create(end.x, end.y, end.z), dir_vec)
            ofe.isEnabled = True
    except Exception:
        futil.handle_error(f'{CMD_NAME} row manipulators')


def _clear_preview():
    global _preview_objs
    for objs in _preview_objs:
        for obj in objs:
            try:
                obj.deleteMe()
            except Exception:
                pass
    _preview_objs = []


def _build_all(inputs, lines, preview):
    """Lay the plain profiles: build each line, no joint offsets, no cuts.

    Returns ``(created, objs, feat_idx, placements)``.  The per-line placement
    is exactly the user's Rotation/Offset cells (Auto adds its joint offsets on
    top of these; Tube has none to add).
    """
    w = _weldment()
    resolved = _resolve(inputs)
    if not resolved:
        return 0, [], [], {}
    root, _sel, geom, label, designation = resolved
    ref = w._selection_reference(lines)
    anchor = _section_anchor(inputs, geom)
    created = 0
    objs, feat_idx = [], []
    placements = {}
    for i, line in enumerate(lines):
        angle, off_s, off_e = _row_params(inputs, i)
        idx = root.features.count
        built = w._build_weldment(root, line, geom, label, angle, ref,
                                  off_s, off_e, preview=preview,
                                  basis=None, anchor=anchor)
        if built:
            created += 1
            placements[i] = {'angle_rad': angle,
                             'offset_start': off_s, 'offset_end': off_e}
            if preview:
                _preview_objs.append(built)
        objs.append(built)
        feat_idx.append(idx if built else None)
    return created, objs, feat_idx, placements


def command_execute_preview(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    w = _weldment()
    sel = inputs.itemById('path')
    saved = w._snapshot_selection(sel)
    _clear_preview()
    _sync_table_rows(inputs, saved)
    _build_all(inputs, saved, preview=True)
    w._restore_selection(sel, saved)


def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    w = _weldment()
    sel = inputs.itemById('path')
    saved = w._snapshot_selection(sel)
    _clear_preview()
    resolved = _resolve(inputs)
    if not resolved:
        return
    created, objs, feat_idx, placements = _build_all(inputs, saved,
                                                     preview=False)
    if created == 0:
        ui.messageBox('No tubes were created. Select 3D sketch line(s) first.')
        return
    # Persist (and prune ghosts) exactly like Auto so the registry spine stays
    # whole for the toolbox tools -- but no joint records are written.
    try:
        design = _design()
        _root, _sel, geom, _label, designation = resolved
        ref = w._selection_reference(saved)
        registry = w.persist_members(design, saved, geom, designation,
                                     None, feat_idx, placements=placements,
                                     ref=ref, anchor=None, objs=objs,
                                     bodies=None)
        w.prune_ghosts(design.rootComponent, registry)
        w.save_registry(design, registry)
    except Exception:
        futil.handle_error(f'{CMD_NAME} registry persist')


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    global _syncing
    inp = args.input
    if inp.id == 'family':
        _rebuild_designations(args.inputs)
        return
    if inp.id == 'sync_all':
        _update_manipulators(args.inputs,
                             _weldment()._selected_lines(args.inputs))
        return
    if _syncing:
        return
    col = _cell_column(inp.id)
    if col is None:
        return
    sync: adsk.core.BoolValueCommandInput = args.inputs.itemById('sync_all')
    if not (sync and sync.value):
        return
    _syncing = True
    try:
        value = inp.value
        for ids in _row_ids:
            other = args.inputs.itemById(ids[col])
            if other is not None and other.id != inp.id:
                other.value = value
    finally:
        _syncing = False


def command_select(args: adsk.core.SelectionEventArgs):
    """Match rows to the selection on the click itself (before the redraw)."""
    sel = args.activeInput
    if sel is None or sel.id != 'path':
        return
    lines = [sel.selection(i).entity for i in range(sel.selectionCount)]
    inputs = sel.parentCommand.commandInputs
    _sync_table_rows(inputs, lines)


def command_destroy(args: adsk.core.CommandEventArgs):
    _clear_preview()
    global _row_ids, local_handlers
    _row_ids = []
    local_handlers = []
