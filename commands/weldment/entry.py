import os

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ...lib import profiles as prof
from ...lib import joints as jt
from ...lib import bending_dies as bd
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

# Module-level cache of the bending-die catalogue (populated on start()).
_DIES = {"units": "mm", "dies": []}

# Opacity (0-1) used to ghost the live preview body so it reads as transient.
PREVIEW_OPACITY = 0.4

# Objects created for the current in-dialog preview: list of
# (feature, sketch, plane) tuples.  Rebuilt on every preview and torn down when
# the inputs change, the command is cancelled, or (for the committed ones) OK is
# pressed.  Only one weldment command can be active at a time, so a module-level
# list is sufficient.
_preview_objs = []

# Corner-cut features (CombineFeature) and their helper geometry (waste-prism
# extrudes, sketches, construction planes) made for the current preview.
# Deleting a cut feature restores the member's whole
# body, so cut features must be removed BEFORE the member
# features they reference.
_preview_cuts = []

# Re-entrancy guard for the "Sync all" propagation: setting a table cell's value
# programmatically can re-fire inputChanged, which would otherwise recurse.
_syncing = False

# One entry per DATA table row (row 0 is a read-only header, not tracked here),
# in row order: a dict of that row's cell input ids
# {'num','joint_s','joint_e','through','saddle'}.  Kept in sync with the selection
# by _sync_table_rows(); the ids carry a monotonic uid so a deleted-then-recreated
# row never collides with a lingering input.
_row_ids = []
_uid_counter = [0]

# Column titles for the header row (row 0), matching the table's 11 columns.
_TABLE_HEADERS = ('#', 'Joint Start', 'Joint End', 'Through', 'Saddle',
                  'Rotation', 'Offset Start', 'Offset End',
                  'Inverse', 'Bend Die', 'Cope Depth')

# Joints that describe a relationship BETWEEN the two members meeting at a
# corner, so both ends must agree for the geometry to resolve: a miter needs
# both members cut on the bisector, and a swept bend needs both legs to lay the
# arc.  Setting one on a member mirrors it onto its partner at the shared corner
# (see _propagate_corner_joint).  Butt/cope/none are per-member (a lone butt
# already reads the neighbour as the through member) and are never mirrored.
_RELATIONSHIP_JOINTS = ('miter', 'bend')


def _new_uid():
    _uid_counter[0] += 1
    return _uid_counter[0]


def _make_header_row(inputs, tbl):
    """Populate table row 0 with read-only column titles.

    Fusion tables expose no header API, so the idiomatic workaround is a first
    row of non-editable text boxes.  These are NOT tracked in ``_row_ids``; every
    data row is therefore offset by one table row (data row ``r`` lives at table
    row ``r + 1``).
    """
    for c, title in enumerate(_TABLE_HEADERS):
        box = inputs.addTextBoxCommandInput(
            f'hdr_{c}', '', title, 1, True)
        box.isEnabled = False
        tbl.addCommandInput(box, 0, c)


# --------------------------------------------------------------------------- #
# Add-in lifecycle
# --------------------------------------------------------------------------- #
def start():
    global _FAMILIES, _DIES
    try:
        _FAMILIES = prof.annotate_families(prof.load_profiles())
    except Exception:
        _FAMILIES = []
        futil.handle_error(f'{CMD_NAME} load profiles')
    try:
        _DIES = bd.load_bending_dies()
    except Exception:
        _DIES = {"units": "mm", "dies": []}
        futil.handle_error(f'{CMD_NAME} load bending dies')

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
    inputs = args.command.commandInputs

    # The table has 11 columns; at Fusion's default dialog width the text
    # dropdowns (Joint Start/End, Bend Die) and the numeric spinners are all
    # squeezed to a few pixels and the whole thing is unreadable.  Give the
    # dialog a roomy initial size and a minimum that still fits every column,
    # so resizing the window reflows the table instead of clipping it.
    args.command.setDialogInitialSize(1040, 520)
    args.command.setDialogMinimumSize(820, 360)

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

    # 4. Per-line joint + rotation + offset table.  One row per selected sketch
    #    line, with columns [#, Joint Start, Joint End, Through, Saddle, Rotation,
    #    Offset Start, Offset End].  A joint belongs to a line END, so each line
    #    gets TWO dropdowns -- one for its start vertex and one for its end
    #    vertex -- which may differ (e.g. a miter on one end, a butt on the
    #    other).  Each dropdown lists the corner treatments the selected profile
    #    family supports (from data/profiles.json 'joints'), defaulting to 'None'
    #    (full length to the vertex -- the historical behaviour).  Through /
    #    Saddle are per-row checkboxes that refine a Butt: Through marks this
    #    member as the one that runs past the corner (its neighbour backs off);
    #    Saddle notches the butt end to the neighbour's outer surface (a boolean)
    #    instead of a flat square.  Rotation and the start/end offsets are
    #    per-LINE (not per-end) and live in their own columns so each line can
    #    differ.  Row 0 is a read-only HEADER row giving the column titles
    #    (Fusion tables have no header API), so the first data row is row 1.  A
    #    "Sync all" checkbox lives in the table's bottom toolbar: when checked
    #    (default) editing any data row propagates its value to every row and the
    #    manipulators anchor to the first line; when unchecked each row keeps its
    #    own values and gets its own arrows.  The table starts with just the
    #    header row; _sync_table_rows() adds a data row per line as the selection
    #    changes.
    tbl: adsk.core.TableCommandInput = inputs.addTableCommandInput(
        'params', 'Per Line', 11, '1:3:3:1:1:2:2:2:1:4:2')
    # Show up to a dozen rows before scrolling (Fusion defaults to 4, which
    # buries a multi-member frame behind a scrollbar in an otherwise tall
    # dialog).  The columnRatio above widens the Bend Die dropdown so its
    # "CHS-CLR-114.3 (R114.3)" label is not clipped.
    tbl.maximumVisibleRows = 12
    sync: adsk.core.BoolValueCommandInput = inputs.addBoolValueInput(
        'sync_all', 'Sync all', True, '', True)
    tbl.addToolbarCommandInput(sync)
    # Fresh dialog: the header row plus no data rows yet (they are added as lines
    # are selected).
    global _row_ids
    _row_ids = []
    _make_header_row(inputs, tbl)

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


def _populate_joint_dropdown(dd, family):
    """Fill one row's Joint dropdown with the family's supported joints.

    The options are the joint ids the profile family declares (``none`` first),
    shown with their display labels.  ``none`` is pre-selected so a freshly
    added row keeps the historical full-length behaviour until the user opts in.
    """
    dd.listItems.clear()
    ids = jt.joints_for_family(family)
    for label in jt.joint_labels(ids):
        dd.listItems.add(label, label == jt.LABELS['none'])


def _rebuild_joints(inputs):
    """Re-populate every row's two Joint dropdowns for the current family.

    Called when the profile family changes: a family that cannot be bent drops
    the Bend option, and so on.  Rows whose previous selection is no longer
    offered fall back to ``None``.
    """
    family = _selected_family(inputs)
    for ids in _row_ids:
        for key in ('joint_s', 'joint_e'):
            dd: adsk.core.DropDownCommandInput = inputs.itemById(ids[key])
            if dd is None:
                continue
            prev = _dropdown_index(dd)
            prev_label = (dd.listItems.item(prev).name
                          if 0 <= prev < dd.listItems.count else None)
            _populate_joint_dropdown(dd, family)
            # Keep the user's choice when the new family still supports it.
            if prev_label is not None:
                for i in range(dd.listItems.count):
                    if dd.listItems.item(i).name == prev_label:
                        dd.listItems.item(i).isSelected = True
                        break


# --------------------------------------------------------------------------- #
# Bend / cope per-line columns
# --------------------------------------------------------------------------- #
def _current_designation(inputs):
    """The selected designation dict (with '_abbreviation'), or None if invalid."""
    family = _selected_family(inputs)
    if not family:
        return None
    des: adsk.core.DropDownCommandInput = inputs.itemById('designation')
    idx = _dropdown_index(des)
    designations = prof.designations(family)
    if idx < 0 or idx >= len(designations):
        return None
    d = dict(designations[idx])
    d['_abbreviation'] = family['abbreviation']
    return d


def _die_label(die):
    """Dropdown label for a die: its id plus the centerline radius it produces."""
    return f"{die['die_id']} (R{bd.die_clr(die):g})"


def _populate_die_dropdown(dd, inputs):
    """Fill one row's Bend Die dropdown with the CLRs offered for the family.

    A family can be swept to several centerline radii and a shop may own a
    different one than the default, so every CLR the catalogue lists for the
    selected family is offered here, sorted by ascending radius; the tightest
    (first) is pre-selected to match the previous automatic behaviour.
    """
    dd.listItems.clear()
    des = _current_designation(inputs)
    if not des:
        return
    for die in bd.dies_for_designation(_DIES, des, des['_abbreviation']):
        dd.listItems.add(_die_label(die), False)
    if dd.listItems.count > 0:
        dd.listItems.item(0).isSelected = True


def _rebuild_dies(inputs):
    """Re-populate every row's Bend Die dropdown for the current designation.

    Called when the designation changes: the compatible dies (and their CLRs)
    depend on the tube size.  Rows whose previously-selected die is still offered
    keep it; otherwise the tightest die becomes the default.
    """
    for ids in _row_ids:
        dd: adsk.core.DropDownCommandInput = inputs.itemById(ids['die'])
        if dd is None:
            continue
        prev = _dropdown_index(dd)
        prev_label = (dd.listItems.item(prev).name
                      if 0 <= prev < dd.listItems.count else None)
        _populate_die_dropdown(dd, inputs)
        if prev_label is not None:
            for i in range(dd.listItems.count):
                if dd.listItems.item(i).name == prev_label:
                    dd.listItems.item(i).isSelected = True
                    break


def _row_has_bend(inputs, r):
    """True when either end of row ``r`` is a swept Bend."""
    if r >= len(_row_ids):
        return False
    return 'bend' in (_row_joint_at(inputs, r, 'joint_s'),
                      _row_joint_at(inputs, r, 'joint_e'))


def _row_has_cope(inputs, r):
    """True when row ``r`` has a cope/saddle end that bites into a neighbour.

    Either end is a Cope, or a Butt with the Saddle checkbox on -- the cases
    where the cope/fishmouth depth applies.
    """
    if r >= len(_row_ids):
        return False
    js = _row_joint_at(inputs, r, 'joint_s')
    je = _row_joint_at(inputs, r, 'joint_e')
    if 'cope' in (js, je):
        return True
    sa: adsk.core.BoolValueCommandInput = inputs.itemById(_row_ids[r]['saddle'])
    return bool(sa and sa.value) and 'butt' in (js, je)


def _update_bend_columns(inputs):
    """Enable each row's Inverse / Bend Die only for bends, Cope Depth for copes.

    These three columns are meaningless unless the row actually bends or copes,
    so their cells are greyed out otherwise (kept visible so the row does not
    collapse).  Called after rows are built and whenever a joint or saddle cell
    changes.
    """
    for r, ids in enumerate(_row_ids):
        bend = _row_has_bend(inputs, r)
        for key in ('inv', 'die'):
            cell = inputs.itemById(ids[key])
            if cell is not None:
                cell.isEnabled = bend
        cope = _row_has_cope(inputs, r)
        cd = inputs.itemById(ids['cd'])
        if cd is not None:
            cd.isEnabled = cope


def _row_inverses(inputs, lines):
    """The per-line Inverse flag (index-aligned with ``lines``)."""
    out = []
    for i in range(len(lines)):
        inv = (inputs.itemById(_row_ids[i]['inv'])
               if i < len(_row_ids) else None)
        out.append(bool(inv and inv.value))
    return out


def _row_cope_depths(inputs, lines):
    """The per-line cope/saddle extra depth in mm (index-aligned with ``lines``).

    The spinner's ``.value`` is in Fusion's database length unit (cm), but the
    joint layer's ``cope_depth_by_line`` is documented in mm, so convert cm->mm
    here (x10).  Without this a 20 mm entry reached the centreline only after
    ~200 mm was typed -- the reported 10x bug.
    """
    out = []
    for i in range(len(lines)):
        cd = (inputs.itemById(_row_ids[i]['cd'])
              if i < len(_row_ids) else None)
        out.append(cd.value * 10.0 if cd else 0.0)
    return out


def _row_index_of(input_id):
    """The data-row index owning the table cell with ``input_id`` (or None)."""
    for r, ids in enumerate(_row_ids):
        if input_id in ids.values():
            return r
    return None


def _propagate_corner_joint(inputs, lines, row, key, jid):
    """Mirror a relationship joint onto the member(s) sharing this corner.

    A miter or a swept bend is a property of the *joint* between two members,
    not of one member's end: the geometry only resolves when both members
    request it (a lone bend leg draws no arc; a lone miter leaves the
    neighbour's square end poking through).  So when the user sets such a joint
    on one end, we set the matching end on every other line that meets at the
    same vertex, and the preview updates from a single edit.

    ``key`` is 'joint_s'/'joint_e' (the edited end) and ``jid`` its new id.  Only
    :data:`_RELATIONSHIP_JOINTS` propagate; butt/cope/none are per-member and
    left alone.  Guarded by ``_syncing`` so the programmatic writes below do not
    re-fire this handler.
    """
    global _syncing
    if jid not in _RELATIONSHIP_JOINTS or row >= len(_row_ids):
        return
    line = lines[row] if row < len(lines) else None
    if line is None:
        return
    role = 'start' if key == 'joint_s' else 'end'
    # The vertex this end sits at, and the other lines touching it.
    s, e = jt.line_endpoints(line)
    corner = s if role == 'start' else e
    label = jt.LABELS[jid]
    _syncing = True
    try:
        for other in jt.detect_corners(lines):
            if _dist2(other['point'], corner) > jt._CORNER_TOL * jt._CORNER_TOL:
                continue
            for oidx, orole in other['members']:
                if oidx == row or oidx >= len(_row_ids):
                    continue
                okey = 'joint_s' if orole == 'start' else 'joint_e'
                dd = inputs.itemById(_row_ids[oidx][okey])
                if dd is None:
                    continue
                for i in range(dd.listItems.count):
                    dd.listItems.item(i).isSelected = (
                        dd.listItems.item(i).name == label)
    finally:
        _syncing = False


def _dist2(a, b):
    """Squared distance between two 3-tuples (cm)."""
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
def _resolve(inputs):
    """Resolve the current dialog inputs to (root, selection, geom, label, designation).

    The per-line rotation/offset values live in the params table and are read
    separately via :func:`_row_params`.  Returns None when the designation is
    not (yet) valid.
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
    design = adsk.fusion.Design.cast(app.activeProduct)
    return (design.rootComponent, sel, geom, designation['designation'],
            designation)


def _row_params(inputs, r):
    """Return (angle_rad, offset_start, offset_end) for table row ``r``.

    Rotation and the manual start/end offsets are per-LINE properties (not
    per-end) and live in that row's own cells (looked up by the ids recorded in
    ``_row_ids[r]``), so each line can differ.  A row past the current table (or
    a not-yet-built row) defaults to 0, i.e. no rotation and full length.
    """
    if r >= len(_row_ids):
        return (0.0, 0.0, 0.0)
    ids = _row_ids[r]
    ang: adsk.core.FloatSpinnerCommandInput = inputs.itemById(ids['rot'])
    ofs: adsk.core.DistanceValueCommandInput = inputs.itemById(ids['os'])
    ofe: adsk.core.DistanceValueCommandInput = inputs.itemById(ids['oe'])
    return (ang.value if ang else 0.0,
            ofs.value if ofs else 0.0,
            ofe.value if ofe else 0.0)


def _row_joint_at(inputs, r, key):
    """Return the joint id chosen for table row ``r``'s end dropdown ``key``.

    ``key`` is 'joint_s' (the line's start vertex) or 'joint_e' (its end vertex).
    A row past the current table (or a not-yet-built row) defaults to 'none'.
    """
    if r >= len(_row_ids):
        return 'none'
    dd: adsk.core.DropDownCommandInput = inputs.itemById(_row_ids[r][key])
    if dd is None:
        return 'none'
    idx = _dropdown_index(dd)
    if 0 <= idx < dd.listItems.count:
        return jt.joint_id_from_label(dd.listItems.item(idx).name)
    return 'none'


def _row_joints(inputs, lines):
    """The per-end joint pair chosen for each line (index-aligned with ``lines``).

    Each entry is a ``(start_id, end_id)`` tuple -- the joint chosen for the
    line's start vertex and its end vertex, which may differ.
    """
    return [(_row_joint_at(inputs, i, 'joint_s'),
             _row_joint_at(inputs, i, 'joint_e')) for i in range(len(lines))]


def _row_flags(inputs, r):
    """Return (through, saddle) booleans for table row ``r``.

    These refine a ``butt`` joint and apply to BOTH of the line's ends (a scalar
    per line; see :func:`lib.joints._ends`): ``through`` marks the member that
    runs past the corner (its neighbour backs off); ``saddle`` adds a boolean
    notch so a butt end conforms to the neighbour's outer surface.  A row past
    the current table (or a not-yet-built row) defaults to (False, False).
    """
    if r >= len(_row_ids):
        return (False, False)
    ids = _row_ids[r]
    th: adsk.core.BoolValueCommandInput = inputs.itemById(ids['through'])
    sa: adsk.core.BoolValueCommandInput = inputs.itemById(ids['saddle'])
    return (bool(th and th.value), bool(sa and sa.value))


def _row_through(inputs, lines):
    """The per-line ``through`` flag (index-aligned with ``lines``)."""
    return [_row_flags(inputs, i)[0] for i in range(len(lines))]


def _row_saddle(inputs, lines):
    """The per-line ``saddle`` flag (index-aligned with ``lines``)."""
    return [_row_flags(inputs, i)[1] for i in range(len(lines))]


def _joint_offsets(inputs, lines, geom, clr_by_line=None, ref=None,
                   cope_depth_by_line=None):
    """Per-line (offset_start, offset_end) in cm from the chosen corner joints.

    Auto-detects the corners among ``lines`` and turns each row's joint type into
    the length trim that removes the corner overlap (see lib/joints).  ``ref`` is
    the shared profile "up" reference; when given, each line's placed section
    basis is computed so a butt/cope trim stops at the neighbour's *directional*
    face (an I-beam's flange width, not its web depth).  ``cope_depth_by_line``
    (mm) deepens each saddled/cope end's bite into the neighbour.  The result is
    added to the user's manual start/end offsets in the build loop.
    """
    joints = _row_joints(inputs, lines)
    through = _row_through(inputs, lines)
    saddle = _row_saddle(inputs, lines)
    geoms = [geom for _ in lines]
    bases = None
    if ref is not None:
        try:
            bases = [prof.compute_basis(_line_direction(l), ref) for l in lines]
        except Exception:
            bases = None
    try:
        return jt.corner_offsets(lines, geoms, joints, clr_by_line=clr_by_line,
                                 through_by_line=through, bases=bases,
                                 saddle_by_line=saddle,
                                 cope_depth_by_line=cope_depth_by_line)
    except Exception:
        futil.handle_error(f'{CMD_NAME} joint offsets')
        return [(0.0, 0.0) for _ in lines]


def _row_die_clr(inputs, r, designation):
    """Centerline radius (mm) of the die chosen in row ``r``'s Bend Die dropdown.

    Resolves the selected dropdown label back to a catalogue die; falls back to
    the automatic best die for the designation when the row has no dropdown (or
    nothing is selected), preserving the pre-dropdown behaviour.
    """
    abbr = designation.get('_abbreviation') if designation else None
    if r < len(_row_ids):
        dd: adsk.core.DropDownCommandInput = inputs.itemById(_row_ids[r]['die'])
        if dd is not None:
            idx = _dropdown_index(dd)
            if 0 <= idx < dd.listItems.count:
                label = dd.listItems.item(idx).name
                for die in bd.dies_for_designation(_DIES, designation, abbr):
                    if _die_label(die) == label:
                        return bd.die_clr(die)
    if abbr:
        die = bd.die_for_designation(_DIES, designation, abbr)
        return bd.die_clr(die) if die else 0.0
    return 0.0


def _bend_radii(inputs, lines, designation):
    """Per-line die centerline radius (mm) for ``bend`` legs (0 where not bending).

    A leg only gets a radius when one of its ends is ``bend``; the value is the
    die picked in that row's Bend Die dropdown (or the automatic best die when
    no dropdown selection is available).  Non-bend legs get 0 (no swept arc).
    """
    abbr = designation.get('_abbreviation') if designation else None
    radii = []
    for i in range(len(lines)):
        ends = (_row_joint_at(inputs, i, 'joint_s'),
                _row_joint_at(inputs, i, 'joint_e'))
        if 'bend' in ends and abbr:
            radii.append(_row_die_clr(inputs, i, designation))
        else:
            radii.append(0.0)
    return radii


def _build_bend_arcs(root, lines, joints, clr_by_line, geom, ref,
                     inverse_by_line=None, preview=False):
    """Build the swept-bend arc bodies for every ``bend`` corner among ``lines``.

    Returns a list of (feature, sketch, plane) tuples for the caller to track.
    Legs whose joint is ``bend`` were already trimmed to their tangent points by
    :func:`_joint_offsets`; here the arc that fills each rounded corner is
    revolved into place, once per corner (built from that corner's first leg).
    ``inverse_by_line`` flips a corner's sweep direction (see
    :func:`lib.joints.bend_plan`) for corners whose legs were picked in reverse
    order.
    """
    objs = []
    try:
        plans = jt.bend_plan(lines, joints, clr_by_line,
                             inverse_by_line=inverse_by_line)
    except Exception:
        futil.handle_error(f'{CMD_NAME} bend plan')
        return objs
    for plan in plans:
        idx, _role, tangent = plan['tangent'][0]
        arc = _build_bend_arc(root, lines[idx], tangent, plan, geom, ref,
                              preview=preview)
        if arc:
            objs.append(arc)
    return objs


def _clear_preview():
    """Delete the transient preview objects (corner cuts first, then each
    member's feature, sketch, and plane)."""
    global _preview_objs, _preview_cuts
    for obj in _preview_cuts:
        try:
            obj.deleteMe()
        except Exception:
            pass
    _preview_cuts = []
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


def _selected_lines(inputs):
    """The entities currently picked in the path selection (may be empty)."""
    sel = inputs.itemById('path')
    return _snapshot_selection(sel) if sel else []


def _cell_column(input_id):
    """Map a table-cell input id to its column key.

    Returns 'joint_s', 'joint_e', 'through', 'saddle', 'rot', 'os', 'oe', 'inv',
    'die' or 'cd' for the editable value columns, or None for the read-only
    row-number / header cells and every non-table input.
    """
    for col in ('joint_s', 'joint_e', 'through', 'saddle', 'rot', 'os', 'oe',
                'inv', 'die', 'cd'):
        if input_id.startswith(col + '_'):
            return col
    return None


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    # Rebuild the designation list when the family changes.  The preview itself
    # is torn down and rebuilt in executePreview (which Fusion fires right after
    # this event), so we deliberately do NOT clear it here -- clearing here would
    # drop the user's line selection before executePreview can preserve it.
    global _syncing
    inp = args.input
    if inp.id == 'family':
        _rebuild_designations(args.inputs)
        _rebuild_joints(args.inputs)
        _rebuild_dies(args.inputs)
        return
    # The compatible dies depend on the tube size, so changing the designation
    # re-populates every row's Bend Die dropdown.
    if inp.id == 'designation':
        _rebuild_dies(args.inputs)
        return
    # Toggling Sync all changes which rows show their manipulators.
    if inp.id == 'sync_all':
        _update_manipulators(args.inputs, _selected_lines(args.inputs))
        return
    # A table cell was edited.  With Sync all on, mirror the value into every
    # other row so the controls stay universal.  The _syncing guard stops the
    # programmatic writes below from re-firing this handler.
    if _syncing:
        return
    col = _cell_column(inp.id)
    if col is None:
        return
    # A joint or saddle cell changed: the Inverse / Bend Die / Cope Depth columns
    # are only meaningful for rows that actually bend or cope, so refresh their
    # enabled state.  (Runs regardless of Sync all.)
    if col in ('joint_s', 'joint_e', 'saddle'):
        _update_bend_columns(args.inputs)
    # A relationship joint (miter/bend) is a property of the corner, not one
    # member's end, so mirror it onto the partner(s) sharing the vertex -- the
    # preview then reacts to a single edit instead of needing both dropdowns.
    if col in ('joint_s', 'joint_e'):
        row = _row_index_of(inp.id)
        idx = _dropdown_index(inp)
        label = inp.listItems.item(idx).name if 0 <= idx < inp.listItems.count else None
        if row is not None:
            _propagate_corner_joint(args.inputs, _selected_lines(args.inputs),
                                    row, col, jt.joint_id_from_label(label))
    sync: adsk.core.BoolValueCommandInput = args.inputs.itemById('sync_all')
    if not (sync and sync.value):
        return
    _syncing = True
    try:
        if col in ('joint_s', 'joint_e', 'die'):
            # Dropdowns carry no .value; propagate the selected option by label,
            # within the same column (start stays start, end stays end).
            idx = _dropdown_index(inp)
            label = inp.listItems.item(idx).name if 0 <= idx < inp.listItems.count else None
            for ids in _row_ids:
                other = args.inputs.itemById(ids[col])
                if other is None or other.id == inp.id:
                    continue
                for i in range(other.listItems.count):
                    other.listItems.item(i).isSelected = (
                        label is not None and other.listItems.item(i).name == label)
        else:
            value = inp.value
            for ids in _row_ids:
                other = args.inputs.itemById(ids[col])
                if other is not None and other.id != inp.id:
                    other.value = value
    finally:
        _syncing = False


def command_select(args: adsk.core.SelectionEventArgs):
    """Match the table rows to the selection and anchor manipulators on the click.

    This fires on the actual click -- before the preview redraw -- so the table
    gains/loses a row and the rotation wheel / offset arrows are already
    positioned on the selected line(s) when they first appear.  Anchoring inside
    executePreview is too late: setManipulator only takes effect on the
    following redraw, so they would briefly show at their default origin
    (0,0,0), i.e. the sketch's first point.
    """
    sel = args.activeInput
    if sel is None or sel.id != 'path':
        return
    lines = [sel.selection(i).entity for i in range(sel.selectionCount)]
    inputs = sel.parentCommand.commandInputs
    _sync_table_rows(inputs, lines)
    _update_manipulators(inputs, lines)


def _sync_table_rows(inputs, lines):
    """Grow/shrink the params table so it has exactly one data row per line.

    Row 0 is the read-only header (column titles); data row ``r`` (driving line
    ``r``) lives at table row ``r + 1``.  Existing rows keep their values; new
    rows are appended with defaults; rows past the current line count are deleted
    from the end.  Each row's cell inputs get unique ids (from a monotonic
    counter) so a deleted-then-recreated row never collides with a lingering
    input.
    """
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
        ids = {'num': f'num_{uid}', 'joint_s': f'joint_s_{uid}',
               'joint_e': f'joint_e_{uid}',
               'through': f'through_{uid}', 'saddle': f'saddle_{uid}',
               'rot': f'rot_{uid}', 'os': f'os_{uid}', 'oe': f'oe_{uid}',
               'inv': f'inv_{uid}', 'die': f'die_{uid}',
               'cd': f'cd_{uid}'}
        num = inputs.addTextBoxCommandInput(
            ids['num'], '', str(r + 1), 1, True)
        # Joint Start / Joint End: the corner treatment for each END of this
        # line, limited to what the selected profile family supports (see
        # data/profiles.json 'joints').  A joint belongs to a line end, so the
        # two ends are configured independently and may differ.
        joint_s = inputs.addDropDownCommandInput(
            ids['joint_s'], '', adsk.core.DropDownStyles.TextListDropDownStyle)
        _populate_joint_dropdown(joint_s, _selected_family(inputs))
        joint_e = inputs.addDropDownCommandInput(
            ids['joint_e'], '', adsk.core.DropDownStyles.TextListDropDownStyle)
        _populate_joint_dropdown(joint_e, _selected_family(inputs))
        # Through / Saddle: per-row checkboxes that refine a Butt joint (they
        # apply to the line as a whole).  Through marks this member as the one
        # that runs past the corner (its neighbour backs off); Saddle notches a
        # butt end to the neighbour's outer surface instead of a flat square.
        through = inputs.addBoolValueInput(
            ids['through'], '', True, '', False)
        through.description = 'This member runs through the corner'
        saddle = inputs.addBoolValueInput(
            ids['saddle'], '', True, '', False)
        saddle.description = 'Notch this butt end to clear the neighbour'
        # Rotation is a plain spinner (degrees): an editable box with NO
        # on-canvas manipulator.  An AngleValueCommandInput would always draw a
        # rotation wheel, and in a table cell that wheel does not write back to
        # the box, so it is redundant -- hence a spinner here.  Its .value is in
        # radians (the database angle unit), matching _build_weldment.
        rot = inputs.addFloatSpinnerCommandInput(
            ids['rot'], '', 'degree', -1000, 1000, 15, 0)
        os_ = inputs.addDistanceValueCommandInput(
            ids['os'], '', adsk.core.ValueInput.createByString('0 mm'))
        oe_ = inputs.addDistanceValueCommandInput(
            ids['oe'], '', adsk.core.ValueInput.createByString('0 mm'))
        # Inverse: flips this line's swept-bend direction.  The arc centre is
        # symmetric in the two legs but the sweep is the sign of the revolve
        # angle, so a corner whose lines were picked in reverse order sweeps the
        # wrong way (+90 instead of -90); this checkbox flips that sign.  Only
        # meaningful when one of the line's ends is a Bend, so it is enabled
        # solely in that case (see _update_bend_columns).
        inv = inputs.addBoolValueInput(ids['inv'], '', True, '', False)
        inv.description = 'Flip the bend sweep direction (reversed line order)'
        # Bend Die: the tooling used for this line's bend.  A tube size is
        # formable on several dies (different CLRs) and a shop may own a
        # different one than the default, so the compatible dies for the current
        # designation are offered here; the selection sets the centerline radius.
        die = inputs.addDropDownCommandInput(
            ids['die'], '', adsk.core.DropDownStyles.TextListDropDownStyle)
        _populate_die_dropdown(die, inputs)
        die.description = 'Bending die (centerline radius) for this line'
        # Cope Depth: how far (mm) a saddled/cope end bites INTO the neighbour
        # beyond its default stopping face -- deepening the fishmouth.  Only
        # meaningful when an end is saddled/copes, so it is enabled in that case.
        # min/max are database units (cm), so 0..20 cm = 0..200 mm; the value is
        # read back in cm and converted to mm in _row_cope_depths.
        cd = inputs.addFloatSpinnerCommandInput(
            ids['cd'], '', 'mm', 0, 20, 1, 0)
        cd.description = 'Extra depth a cope/saddle bites into the neighbour'
        tbl.addCommandInput(num, r + 1, 0)
        tbl.addCommandInput(joint_s, r + 1, 1)
        tbl.addCommandInput(joint_e, r + 1, 2)
        tbl.addCommandInput(through, r + 1, 3)
        tbl.addCommandInput(saddle, r + 1, 4)
        tbl.addCommandInput(rot, r + 1, 5)
        tbl.addCommandInput(os_, r + 1, 6)
        tbl.addCommandInput(oe_, r + 1, 7)
        tbl.addCommandInput(inv, r + 1, 8)
        tbl.addCommandInput(die, r + 1, 9)
        tbl.addCommandInput(cd, r + 1, 10)
        _row_ids.append(ids)
    # "Sync all" only means something once there are 2+ rows to keep in step, so
    # hide the toolbar checkbox otherwise rather than float a lone control.
    sync: adsk.core.BoolValueCommandInput = inputs.itemById('sync_all')
    if sync is not None:
        sync.isVisible = len(_row_ids) >= 2
    _update_bend_columns(inputs)


def _update_manipulators(inputs, lines):
    """Show each row's cells and anchor its offset arrows.

    Rotation is a plain spinner (no canvas handle); only the offset arrows draw
    on the canvas.  Synced (default): only row 0's arrows are shown, anchored to
    line 0, because editing any row propagates to all -- one universal set of
    handles.  Unsynced: row r's arrows anchor to line r, so every created
    element carries its own arrows on the canvas.
    """
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
    """Keep a row's cells visible and anchor (or disable) its offset arrows.

    The Rotation cell is a plain spinner (no on-canvas handle), so it is always
    just an editable box.  The Offset Start / End arrows are the only canvas
    manipulators: Offset Start sits at the line start and Offset End at its end,
    both along the line direction.

    A table row auto-hides when ALL of its cells are invisible, so every cell is
    kept isVisible=True.  The offset arrows are toggled via isEnabled (the
    manipulator draws only when isVisible AND isEnabled are both true): when
    ``show`` is False (or there is no line) they are disabled so they never
    strand at the default origin (0,0,0), while the row still shows.
    """
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


def command_execute_preview(args: adsk.core.CommandEventArgs):
    """Rebuild the ghosted preview body for the current selection + profile."""
    inputs = args.command.commandInputs
    sel = inputs.itemById('path')
    # Snapshot the picked lines first: creating/deleting the preview geometry
    # below can reset the canvas selection, so we build from this snapshot and
    # restore the selection at the end rather than trusting the live count.
    saved = _snapshot_selection(sel)
    _clear_preview()
    _sync_table_rows(inputs, saved)
    _update_manipulators(inputs, saved)
    resolved = _resolve(inputs)
    if not resolved:
        _restore_selection(sel, saved)
        return
    root, _sel, geom, label, designation = resolved
    ref = _selection_reference(saved)
    clr_by_line = _bend_radii(inputs, saved, designation)
    cope_depths = _row_cope_depths(inputs, saved)
    inverses = _row_inverses(inputs, saved)
    joint_offs = _joint_offsets(inputs, saved, geom, clr_by_line, ref,
                                cope_depth_by_line=cope_depths)
    objs, feat_idx = [], []
    f_start = root.features.count
    for i, line in enumerate(saved):
        angle, off_s, off_e = _row_params(inputs, i)
        js, je = joint_offs[i] if i < len(joint_offs) else (0.0, 0.0)
        idx = root.features.count
        built = _build_weldment(root, line, geom, label, angle, ref,
                                off_s + js, off_e + je, preview=True)
        objs.append(built)
        feat_idx.append(idx if built else None)
        if built:
            _preview_objs.append(built)
    _preview_objs.extend(
        _build_bend_arcs(root, saved, _row_joints(inputs, saved),
                         clr_by_line, geom, ref, inverse_by_line=inverses,
                         preview=True))
    _preview_cuts.extend(
        _apply_corner_cuts(root, saved, _row_joints(inputs, saved), objs,
                           feat_idx, f_start,
                           saddle=_row_saddle(inputs, saved),
                           through=_row_through(inputs, saved)))
    # Make sure the user's lines are still highlighted after the churn.
    _restore_selection(sel, saved)


def command_execute(args: adsk.core.CommandEventArgs):
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
    root, _sel, geom, label, designation = resolved
    ref = _selection_reference(saved)
    clr_by_line = _bend_radii(inputs, saved, designation)
    cope_depths = _row_cope_depths(inputs, saved)
    inverses = _row_inverses(inputs, saved)
    joint_offs = _joint_offsets(inputs, saved, geom, clr_by_line, ref,
                                cope_depth_by_line=cope_depths)

    created = 0
    objs, feat_idx = [], []
    f_start = root.features.count
    for i, line in enumerate(saved):
        angle, off_s, off_e = _row_params(inputs, i)
        js, je = joint_offs[i] if i < len(joint_offs) else (0.0, 0.0)
        idx = root.features.count
        built = _build_weldment(root, line, geom, label, angle, ref,
                                off_s + js, off_e + je)
        if built:
            created += 1
        objs.append(built)
        feat_idx.append(idx if built else None)
    created += len(_build_bend_arcs(root, saved, _row_joints(inputs, saved),
                                    clr_by_line, geom, ref,
                                    inverse_by_line=inverses))
    _apply_corner_cuts(root, saved, _row_joints(inputs, saved), objs,
                       feat_idx, f_start,
                       saddle=_row_saddle(inputs, saved),
                       through=_row_through(inputs, saved))

    if created == 0:
        ui.messageBox('No weldments were created. Select 3D sketch line(s) first.')


def command_destroy(args: adsk.core.CommandEventArgs):
    # On cancel (destroy without a preceding execute) the preview is still live;
    # remove it.  After OK, _preview_objs is already empty so this is a no-op.
    _clear_preview()
    global _row_ids, local_handlers
    _row_ids = []
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


def _body_centroid(body):
    """Centre of ``body``'s bounding box as a 3-tuple (cm)."""
    bb = body.boundingBox
    return ((bb.minPoint.x + bb.maxPoint.x) / 2.0,
            (bb.minPoint.y + bb.maxPoint.y) / 2.0,
            (bb.minPoint.z + bb.maxPoint.z) / 2.0)


def _bbox_of(body):
    """``((min_x, min_y, min_z), (max_x, max_y, max_z))`` of ``body``."""
    bb = body.boundingBox
    return ((bb.minPoint.x, bb.minPoint.y, bb.minPoint.z),
            (bb.maxPoint.x, bb.maxPoint.y, bb.maxPoint.z))


def _line_midpoint(line):
    """Midpoint of a sketch line in model space (cm 3-tuple)."""
    s, e = jt.line_endpoints(line)
    return ((s[0] + e[0]) / 2.0, (s[1] + e[1]) / 2.0, (s[2] + e[2]) / 2.0)


def _line_far_end(line, near_point):
    """The endpoint of ``line`` farthest from ``near_point`` (cm 3-tuple).

    Used to locate a member's own body: the far end is always deep inside the
    member's material, whereas its midpoint can be nearer a neighbour's body for
    a short member (e.g. a T-junction stub).
    """
    s, e = jt.line_endpoints(line)
    ds = sum((s[k] - near_point[k]) ** 2 for k in range(3))
    de = sum((e[k] - near_point[k]) ** 2 for k in range(3))
    return e if de >= ds else s


def _miter_plane(root, point, normal):
    """Construction plane through ``point`` with the given ``normal``.

    Built as a sketch with two long lines spanning the plane plus
    ``setByTwoEdges`` -- the only angled-plane method that works reliably in
    parametric designs.  Returns ``(plane, sketch)``.
    """
    a = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = prof._norm(prof._cross(normal, a))
    w = prof._norm(prof._cross(normal, u))
    sk = root.sketches.add(root.xYConstructionPlane)
    sk.name = 'WeldMiterPlane'

    def line3(d):
        return sk.sketchCurves.sketchLines.addByTwoPoints(
            adsk.core.Point3D.create(point[0] + d[0] * 1e3,
                                      point[1] + d[1] * 1e3,
                                      point[2] + d[2] * 1e3),
            adsk.core.Point3D.create(point[0] - d[0] * 1e3,
                                      point[1] - d[1] * 1e3,
                                      point[2] - d[2] * 1e3))

    l1 = line3(u)
    l2 = line3(w)
    ci = root.constructionPlanes.createInput()
    ci.setByTwoEdges(l1, l2)
    plane = root.constructionPlanes.add(ci)
    return plane, sk


def _side(pt, origin, normal):
    """Sign of the offset of ``pt`` from the plane (origin, normal): +1/-1/0."""
    d = sum((pt[k] - origin[k]) * normal[k] for k in range(3))
    return 1 if d > 1e-6 else (-1 if d < -1e-6 else 0)


def _all_bodies(root):
    """Every BRepBody currently in ``root``, re-fetched by feature index.

    Split/combine prune or reparent features, so a cached feature index can
    point past the end; always enumerate fresh.  A feature whose bodies raise
    (mid-edit) is skipped.
    """
    out = []
    for i in range(root.features.count):
        try:
            f = root.features.item(i)
            for j in range(f.bodies.count):
                out.append(f.bodies.item(j))
        except Exception:
            continue
    return out


def _find_body_near(root, point, max_dist):
    """The body whose bbox-centre is nearest ``point`` (within ``max_dist``).

    Used to locate a member's (possibly re-homed) body without a feature index:
    the point is the member's line midpoint, deep inside its kept material and
    far from any neighbour, so the nearest body is unambiguous.
    """
    best, best_d = None, None
    for b in _all_bodies(root):
        try:
            ctr = _body_centroid(b)
        except Exception:
            continue
        d = sum((ctr[k] - point[k]) ** 2 for k in range(3)) ** 0.5
        if best_d is None or d < best_d:
            best, best_d = b, d
    if best is not None and best_d <= max_dist:
        return best
    return None


def _remove_combine_orphans(root, comb, tool_body):
    """Delete the disconnected slivers a cope/saddle cut leaves inside the tool.

    A cope runs the member's tip just past the tool's near wall, so the boolean
    shaves a thin plug off the member that ends up floating in the tube's hollow
    void -- a body that is neither the (kept) tool nor the member's main run.
    Those are pure waste.  We cannot tell them apart by body identity (Fusion
    re-homes the member's identity onto the wrong fragment), so we keep the tool
    and the single largest remaining body (the member's main run -- a plug is
    always a small sliver of it) and issue a ``Remove`` feature on the rest.
    ``Remove`` deletes bodies without disturbing the parametric flow, and
    deleting it later (preview teardown) restores them.  Returns the Remove
    features created.
    """
    removed = []
    try:
        bodies = comb.bodies
    except Exception:
        return removed
    # Partition the combine's output: the tool (kept) vs. the member's pieces.
    pieces = []
    for bi in range(bodies.count):
        try:
            b = bodies.item(bi)
        except Exception:
            continue
        if tool_body is not None and b == tool_body:
            continue  # the neighbour we cut against -- keep it
        pieces.append(b)
    if len(pieces) <= 1:
        return removed  # nothing to separate: the lone piece is the main run
    # The main run is by far the largest; every smaller piece is a waste plug.
    def vol(b):
        try:
            return b.volume
        except Exception:
            return 0.0
    keep = max(pieces, key=vol)
    for b in pieces:
        if b is keep:
            continue
        try:
            removed.append(root.features.removeFeatures.add(b))
        except Exception:
            futil.handle_error(f'{CMD_NAME} remove cope orphan')
    return removed


# Half-size (cm) of the waste prism drawn on a miter plane; must exceed any
# section reach so the prism fully covers the corner's waste half-space.
_WASTE_RADIUS = 100.0
# Extrusion depth (cm) of the waste prism; deep enough to swallow the corner.
_WASTE_DEPTH = 200.0


def _waste_prism(root, V, normal, keep_side):
    """Build a big box occupying the waste half-space beyond a miter plane.

    A construction plane through ``V`` with the bisector ``normal`` hosts a
    square sketch (side ``2*_WASTE_RADIUS``) extruded ``_WASTE_DEPTH`` to the
    waste side (opposite ``keep_side``).  The resulting body is the cutting
    tool for a combine-cut that trims a member to the bisector -- the miter
    primitive.  SplitBodyFeature is unusable here: in a parametric design its
    waste half stays shared with the member's extrude, so deleting it cascades
    and removes the kept half too.  Returns
    ``(feature, sketch, plane, helper_sketch)``.
    """
    plane, sk = _miter_plane(root, V, normal)
    skb = root.sketches.add(plane)
    skb.name = 'WeldWaste'
    a1 = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    u = prof._norm(prof._cross(normal, a1))
    w = prof._norm(prof._cross(normal, u))
    R = _WASTE_RADIUS
    pts = [tuple(V[c] + su * R * u[c] + sw * R * w[c] for c in range(3))
           for su in (-1, 1) for sw in (-1, 1)]
    loop = [pts[0], pts[1], pts[3], pts[2]]  # order into a non-self-crossing rect
    for i in range(4):
        _draw_model_line(skb, loop[i], loop[(i + 1) % 4])
    ei = root.features.extrudeFeatures.createInput(
        skb.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ei.startExtent = adsk.fusion.ProfilePlaneStartDefinition.create()
    # Signed distance along the plane normal: negative extrudes to the waste
    # side when keep_side is +1 (and vice-versa).
    ei.setDistanceExtent(False, adsk.core.ValueInput.createByReal(
        (-keep_side) * _WASTE_DEPTH))
    ext = root.features.extrudeFeatures.add(ei)
    return ext, skb, plane, sk


def _apply_corner_cuts(root, lines, joints, objs, feat_idx, f_start,
                       saddle=None, through=None):
    """Shape member ends with real geometry for miter/cope (and saddled-butt)
    corners.

    ``objs[i]`` is the ``(feature, sketch, plane)`` tuple built for line ``i``
    (or None); ``feat_idx`` and ``f_start`` are retained only for the caller's
    bookkeeping.  Bodies are located *geometrically* (nearest bbox-centre to
    each line's midpoint) rather than by feature index, because a cut prunes or
    reparents features and shifts every later index.  Per
    :func:`lib.joints.corner_cuts`:

    * ``kind='plane'`` (miter): combine-cut the member against a waste prism
      occupying the half-space beyond the bisector plane through the vertex
      (see :func:`_waste_prism`); the prism is consumed by the cut.
    * ``kind='body'`` (cope, or a saddled butt): combine-cut the member against
      the neighbour's body (keep-tool-bodies), saddling it to the through
      member.  A plain butt (no saddle) produces no cut -- it is a pure axial
      trim handled by :func:`lib.joints.corner_offsets`.

    Returns the objects to track for preview cleanup, in delete order: each
    combine feature first (removing it restores the member body), then the
    prism extrudes/sketches/planes they consumed.  The caller must delete all
    of these BEFORE the member features.
    """
    created = []
    try:
        cuts = jt.corner_cuts(lines, joints, saddle_by_line=saddle,
                              through_by_line=through)
    except Exception:
        futil.handle_error(f'{CMD_NAME} corner cut plan')
        return created
    for cut in cuts:
        m, t = cut['member'], cut['tool']
        if m >= len(objs) or objs[m] is None:
            continue
        if t >= len(objs) or objs[t] is None:
            continue
        try:
            reach = max(lines[m].length, 1.0)
            # Locate the member's body by its FAR end (the endpoint away from
            # the junction), which is always deep inside the member's own
            # material.  The line midpoint is unreliable for a short T-junction
            # member, whose midpoint can sit nearer the tool's body than its own.
            body = _find_body_near(root, _line_far_end(lines[m], cut['point']),
                                   reach)
            if body is None:
                continue
            if cut['kind'] == 'plane':
                keep_side = _side(_body_centroid(body), cut['point'],
                                  cut['normal'])
                prism, skb, plane, sk = _waste_prism(root, cut['point'],
                                                     cut['normal'], keep_side)
                tools = adsk.core.ObjectCollection.create()
                tools.add(prism.bodies.item(0))
                ci = root.features.combineFeatures.createInput(body, tools)
                ci.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
                # The prism is pure waste: consume it instead of leaving a
                # giant box in the view (unlike butt/cope, whose tool is a
                # real neighbour body).
                ci.isKeepToolBodies = False
                comb = root.features.combineFeatures.add(ci)
                # The prism is consumed, but its helper geometry -- the two very
                # long lines in the miter-plane sketch (sk), the waste sketch
                # (skb) and the construction plane -- would otherwise linger in
                # the browser and clutter the canvas.  They are still needed as
                # live inputs to the combine (deleting them breaks the feature),
                # so turn their light bulbs off instead: hidden from the view but
                # intact, and still tracked in ``created`` for preview teardown.
                for helper in (skb, sk, plane):
                    try:
                        helper.isLightBulbOn = False
                    except Exception:
                        pass
                created.extend([comb, prism, skb, sk, plane])
            else:
                tool_reach = max(lines[t].length, 1.0)
                tool_body = _find_body_near(root, _line_midpoint(lines[t]),
                                            tool_reach)
                if tool_body is None:
                    continue
                tools = adsk.core.ObjectCollection.create()
                tools.add(tool_body)
                ci = root.features.combineFeatures.createInput(body, tools)
                ci.operation = adsk.fusion.FeatureOperations.CutFeatureOperation
                ci.isKeepToolBodies = True
                comb = root.features.combineFeatures.add(ci)
                created.append(comb)
                # The cope tip overshoots the near wall, shaving a thin plug off
                # the member that floats in the tool's hollow void.  Remove those
                # orphans (a Remove feature, so the parametric flow is intact and
                # deleting it on teardown restores them).  Track them BEFORE the
                # combine so teardown deletes the Remove first, then the combine.
                removes = _remove_combine_orphans(root, comb, tool_body)
                created = removes + created
        except Exception:
            futil.handle_error(f'{CMD_NAME} corner cut')
    # Cut features and prism helpers FIRST (delete order), then the rest.
    return created


def _draw_model_line(sketch, p_from, p_to):
    """Add a sketch line between two model-space points (cm tuples)."""
    to_sheet = sketch.transform.copy()
    to_sheet.invert()
    a = adsk.core.Point3D.create(*p_from)
    a.transformBy(to_sheet)
    b = adsk.core.Point3D.create(*p_to)
    b.transformBy(to_sheet)
    return sketch.sketchCurves.sketchLines.addByTwoPoints(a, b)


def _build_bend_arc(root, leg_line, tangent, plan, geom, ref, preview=False):
    """Build one swept-bend arc body by revolving the section about the bend axis.

    ``plan`` is a dict from :func:`lib.joints.bend_plan` (center, axis, theta);
    ``tangent`` is this leg's tangent point (cm) where the arc meets the leg.
    The section is placed at the tangent point T on a plane normal to the leg,
    then revolved by ``theta`` about the bend axis (which lies in that plane,
    through the arc centre C, at distance R from T).  Returns
    ``(feature, sketch, plane)`` or ``None``.
    """
    try:
        world = leg_line.worldGeometry
        start_pt = world.startPoint
        leg_dir = _line_direction(leg_line)
        center = plan['center']
        axis = plan['axis']
        theta = plan['theta']

        # Offset of the tangent point from the leg's own start, along the leg.
        off = prof._dot(prof._sub(tangent,
                                  (start_pt.x, start_pt.y, start_pt.z)), leg_dir)

        path = adsk.fusion.Path.create(
            leg_line, adsk.fusion.ChainedCurveOptions.noChainedCurves)
        cp_input = root.constructionPlanes.createInput()
        cp_input.setByPath(
            path, adsk.fusion.PathDistanceTypes.PhysicalPathDistanceType,
            adsk.core.ValueInput.createByReal(off))
        plane = root.constructionPlanes.add(cp_input)

        axis_u, axis_v = prof.compute_basis(leg_dir, ref)
        sketch = root.sketches.add(plane)
        sketch.name = 'WeldBend'

        # Section centred on the tangent point T (in this plane, at distance R
        # from the bend axis).  Revolving it about the axis sweeps the arc; if
        # it were centred on C it would sit on the axis and sweep nothing.
        _draw_section(sketch, tangent, axis_u, axis_v, geom)
        # Revolve axis: a sketch line through C along `axis` (in-plane, since
        # the bend axis is perpendicular to the leg direction).
        span = max(plan.get('radius_cm', 1.0), 1.0) * 2.0
        _draw_model_line(sketch,
                         jt._add(center, jt._scale(axis, -span)),
                         jt._add(center, jt._scale(axis, span)))

        if sketch.profiles.count == 0:
            futil.log(f'{CMD_NAME} No closed profile found in bend sketch')
            plane.deleteMe()
            return None

        lines = sketch.sketchCurves.sketchLines
        rev_input = root.features.revolveFeatures.createInput(
            sketch.profiles.item(0), lines.item(lines.count - 1),
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        # Fusion takes the sweep direction from the SIGN of the angle, not the
        # axis-line direction (a line has none), so Inverse is applied here as
        # +/- theta via the plan's direction.  theta is always >= 0 (a turn
        # angle), so this is the only place the sign is introduced.
        rev_input.setAngleExtent(
            False,
            adsk.core.ValueInput.createByReal(theta * plan.get('direction', 1.0)))
        feature = root.features.revolveFeatures.add(rev_input)

        if preview:
            try:
                for bi in range(feature.bodies.count):
                    feature.bodies.item(bi).opacity = PREVIEW_OPACITY
            except Exception:
                pass
        return (feature, sketch, plane)
    except Exception:
        futil.handle_error(f'{CMD_NAME} build bend arc')
        return None


def _build_weldment(root, line, geom, designation_label='', angle_rad=0.0,
                    ref=None, offset_start=0.0, offset_end=0.0, preview=False):
    """Build one weldment body along ``line`` using cross-section ``geom``.

    ``angle_rad`` rotates the profile about the selected line (its own axis),
    turning it around its centre point without tilting about any other axis.
    ``ref`` is the shared "up" reference for the selection so that profiles on
    differently-oriented lines stay rolled consistently and their ends align.
    ``offset_start``/``offset_end`` are signed distances (cm) along the line:
    the profile is created ``offset_start`` from the line's start and the body
    extends to ``line.length + offset_end`` from that same start, so both 0
    gives the full line length.

    Returns ``(feature, sketch, plane)`` on success, or ``None`` on failure.
    When ``preview`` is True the resulting body is ghosted (semi-transparent)
    so it reads as a transient preview rather than a committed part.
    """
    try:
        world = line.worldGeometry
        start_pt = world.startPoint
        end_pt = world.endPoint
        direction = (end_pt.x - start_pt.x, end_pt.y - start_pt.y, end_pt.z - start_pt.z)

        # Construction plane normal to the line, placed offset_start along it
        # (physical distance, so negative extends before the line's start).
        path = adsk.fusion.Path.create(
            line, adsk.fusion.ChainedCurveOptions.noChainedCurves)
        cp_input = root.constructionPlanes.createInput()
        cp_input.setByPath(
            path, adsk.fusion.PathDistanceTypes.PhysicalPathDistanceType,
            adsk.core.ValueInput.createByReal(offset_start))
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
        # Extrude along the plane normal (= line direction) from the offset
        # start point to the offset end point: length + offset_end - offset_start.
        extrude_input.setOneSideExtent(
            adsk.fusion.DistanceExtentDefinition.create(
                adsk.core.ValueInput.createByReal(
                    line.length + offset_end - offset_start)),
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
