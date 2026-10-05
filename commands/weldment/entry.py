import math
import os

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ...lib import profiles as prof
from ...lib import joints as jt
from ...lib import bending_dies as bd
from ...lib import registry as reg
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

# --------------------------------------------------------------------------- #
# Command identity + placement.
#
# Phase 4-UI: weldments live in their OWN toolbar panel (like Sheet Metal's tab),
# not promoted into the shared Create panel. The panel is created on the active
# workspace's Tools tab; every toolbox tool (weldment now, bend/cope/miter in
# 4c/4d) adds its command into PANEL_ID.
# --------------------------------------------------------------------------- #
CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment'
CMD_NAME = 'Weldment'
# Ribbon label for the all-in-one builder. Now that the explicit per-joint
# tools (Cope / Miter / Butt) live alongside it on the Weldments tab, this
# whole-frame auto-detect command reads better as "Auto" than "Weldment".
CMD_LABEL = 'Auto'
CMD_Description = 'Auto-detect the frame and build all weldment members and joints'

WORKSPACE_ID = 'FusionSolidEnvironment'
# Unique tab + panel ids (must not collide with any other add-in's elements).
# A dedicated toolbar TAB (like Solid / Mesh / Sheet Metal), not a panel buried
# on the shared Tools tab: workspace.toolbarPanels.add() lands on Tools, so the
# panels must be created through the tab's own toolbarPanels collection.
TAB_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_tab'
PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_panel'
PANEL_NAME = 'Weldments'

# The tab is spread across several small panels rather than one big one: a
# panel holding more than a handful of buttons collapses into a "+" flyout
# (the "submenu" to avoid), so each tool gets its own visible button.
#   SKETCH panel  -- the standard sketch commands, for in-place editing
#   WELD panel    -- the builder (all-in-one) + the joint toolbox tools
#   DATA panel    -- the registry BOM
# PANEL_ID is the WELD panel that the builder and each joint tool's
# start()/stop() adds its button to; the others are created empty here and
# filled by the ribbon layout pass in ensure_weldments_panel().
SKETCH_PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_sketch_panel'
SKETCH_PANEL_NAME = 'Sketch'
DATA_PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_data_panel'
DATA_PANEL_NAME = 'Data'

# Native Fusion sketch commands placed on our tab (ids verified against the
# running Fusion's commandDefinitions). Reusing the built-in definitions means
# the buttons are the real tools, not stubs.
SKETCH_COMMAND_IDS = [
    'SketchCreate',      # Create Sketch
    'DrawPolyline',      # Line
    'DrawRectangle',     # Rectangle
    'DrawCircle',        # Circle
    'DrawArc',           # Arc
    'DrawSpline',        # Spline (fit point)
    'ProjectNewCmd',     # Project / Include
    'TrimSketchCmd',     # Trim
    'SketchDimension',   # Dimension
    'SketchStop',        # Finish Sketch
]

# Legacy: the Create panel the command used to be promoted into, cleaned up on
# stop() so an upgrade from the old layout leaves no orphan button behind.
LEGACY_PANEL_IDS = ['SolidCreatePanel', 'PlasticPartsCreatePanel']

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

# Count of cope->butt downgrades made in the current build because a coping tip
# landed on a bend's curved arc (a straight cutter cannot match a swept radius).
# :func:`_apply_corner_cuts` increments it; ``command_execute`` resets and reads
# it once to warn the user.
_arc_downgrades = 0

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
def _find_weldments_panel():
    """The existing Weldments panel (on the tab, or the workspace fallback).

    Returns None if it has not been created. Never creates anything, so it is
    safe to call from stop(). The panel now lives on the Weldments tab, so a
    plain ``workspace.toolbarPanels.itemById`` would miss it -- every command
    must resolve it through here.
    """
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    if workspace is None:
        return None
    tab = workspace.toolbarTabs.itemById(TAB_ID)
    if tab is not None:
        panel = tab.toolbarPanels.itemById(PANEL_ID)
        if panel is not None:
            return panel
    return workspace.toolbarPanels.itemById(PANEL_ID)


def _panel_on_tab(panel_id):
    """An existing panel on our tab by id (never creates; None if absent)."""
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    if workspace is None:
        return None
    tab = workspace.toolbarTabs.itemById(TAB_ID)
    if tab is None:
        return None
    return tab.toolbarPanels.itemById(panel_id)


def _populate_sketch_panel(panel):
    """Add the standard native sketch commands to the Sketch panel (idempotent).

    Reuses Fusion's own command definitions, so these are the real tools. A
    missing definition (different Fusion build) is skipped rather than fatal.
    """
    for cid in SKETCH_COMMAND_IDS:
        try:
            if panel.controls.itemById(cid) is not None:
                continue
            cmd_def = ui.commandDefinitions.itemById(cid)
            if cmd_def is not None:
                panel.controls.addCommand(cmd_def)
        except Exception:
            futil.log(f'{CMD_NAME} sketch command {cid} not added.')


def _sketch_panel_is_only_native(panel):
    """True if every control on the Sketch panel is one we added.

    Guards the teardown: a reload that (re)placed a tool button on the wrong
    panel must not have the whole Sketch panel deleted with it.
    """
    native = set(SKETCH_COMMAND_IDS)
    try:
        for i in range(panel.controls.count):
            if panel.controls.item(i).id not in native:
                return False
    except Exception:
        return False
    return True


def _cleanup_weldments_tab(tab):
    """Delete our panels once empty, then the tab once it has no panels left.

    The WELD and DATA panels go when their last tool button is gone; the Sketch
    panel goes only when it still holds nothing but the native commands we
    added. Order-independent: whichever command runs last tears the tab down.
    """
    if tab is None:
        return
    for pid in (PANEL_ID, DATA_PANEL_ID):
        p = tab.toolbarPanels.itemById(pid)
        if p is not None and p.controls.count == 0:
            try:
                p.deleteMe()
            except Exception:
                pass
    sketch = tab.toolbarPanels.itemById(SKETCH_PANEL_ID)
    if sketch is not None and _sketch_panel_is_only_native(sketch):
        try:
            sketch.deleteMe()
        except Exception:
            pass
    try:
        if tab.toolbarPanels.count == 0:
            tab.deleteMe()
    except Exception:
        pass


def remove_command_from_panel(cmd_id):
    """Delete one command's button, then the panel/tab if they are now empty.

    Shared by every toolbox command's stop(). commands/__init__ stops weldment
    BEFORE its siblings, so teardown cannot rely on ordering -- whichever
    command removes the LAST button cleans up the panel, and whichever empties
    the tab's last panel removes the tab. Idempotent and safe to call when the
    panel is already gone.
    """
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    tab = workspace.toolbarTabs.itemById(TAB_ID) if workspace else None
    for pid in (PANEL_ID, DATA_PANEL_ID):
        panel = _panel_on_tab(pid)
        if panel is None:
            continue
        control = panel.controls.itemById(cmd_id)
        if control is not None:
            control.deleteMe()
    _cleanup_weldments_tab(tab)


def ensure_weldments_panel():
    """Return the WELD panel, creating the tab and all ribbon panels as needed.

    Every toolbox command (weldment, cope, ...) calls this instead of
    ``workspace.toolbarPanels.itemById(PANEL_ID)`` directly. The old pattern
    silently dropped a command's button whenever the panel happened not to be
    found (e.g. a sibling command's start() ran before weldment's, or an
    add-in reload left the panel detached) -- which is how the buttons
    disappeared. This helper is idempotent: it reuses an existing tab, creates
    the three ribbon panels (Sketch / Weld / Data) in left-to-right order, and
    fills the Sketch panel with the native commands. A stray PANEL_ID a
    previous version parked on the Tools tab is migrated away first (panel ids
    are globally unique).
    """
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    if workspace is None:
        return None
    tab = workspace.toolbarTabs.itemById(TAB_ID)
    if tab is None:
        # A previous version parked PANEL_ID on the workspace (Tools tab). Panel
        # ids must be globally unique, so delete that stray before creating the
        # tab's own panel -- every command's start() re-adds its button, so no
        # button is lost by the migration.
        stray = workspace.toolbarPanels.itemById(PANEL_ID)
        if stray is not None:
            stray.deleteMe()
        tab = workspace.toolbarTabs.add(TAB_ID, PANEL_NAME)
    if tab is None:
        # Defensive fallback: no tab (older Fusion) -- single workspace panel.
        panel = workspace.toolbarPanels.itemById(PANEL_ID)
        if panel is None:
            panel = workspace.toolbarPanels.add(PANEL_ID, PANEL_NAME)
        return panel

    # Create the panels in display order: Sketch, Weld, Data.
    sketch = tab.toolbarPanels.itemById(SKETCH_PANEL_ID)
    if sketch is None:
        sketch = tab.toolbarPanels.add(SKETCH_PANEL_ID, SKETCH_PANEL_NAME)
    if sketch is not None:
        _populate_sketch_panel(sketch)

    panel = tab.toolbarPanels.itemById(PANEL_ID)
    if panel is None:
        panel = tab.toolbarPanels.add(PANEL_ID, PANEL_NAME)

    if tab.toolbarPanels.itemById(DATA_PANEL_ID) is None:
        tab.toolbarPanels.add(DATA_PANEL_ID, DATA_PANEL_NAME)

    return panel


def ensure_data_panel():
    """Return the Data panel (for the BOM button), creating the tab as needed."""
    if ensure_weldments_panel() is None:
        return None
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    tab = workspace.toolbarTabs.itemById(TAB_ID) if workspace else None
    if tab is None:
        return None
    panel = tab.toolbarPanels.itemById(DATA_PANEL_ID)
    if panel is None:
        panel = tab.toolbarPanels.add(DATA_PANEL_ID, DATA_PANEL_NAME)
    return panel


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
        CMD_ID, CMD_LABEL, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)

    panel = ensure_weldments_panel()
    if panel:
        if panel.controls.itemById(CMD_ID) is None:
            panel.controls.addCommand(cmd_def)
    else:
        futil.log(f'{CMD_NAME} could not create the {PANEL_NAME} panel.')


def stop():
    workspace = ui.workspaces.itemById(WORKSPACE_ID)
    command_definition = ui.commandDefinitions.itemById(CMD_ID)

    # Remove our button; the shared helper also tears down the panel and the
    # Weldments tab when this was the last button on them (order-independent --
    # commands/__init__ stops us before our siblings, and the helper copes).
    remove_command_from_panel(CMD_ID)

    # Clean up the legacy Create-panel button from before the own-panel move.
    for pid in LEGACY_PANEL_IDS:
        legacy = workspace.toolbarPanels.itemById(pid)
        if legacy:
            cc = legacy.controls.itemById(CMD_ID)
            if cc:
                cc.deleteMe()

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

    # 3b. Position dropdown: which point of the cross-section sits ON the picked
    #     line.  "Center" (default) centres the section on the line (the
    #     historical behaviour); the other eight slide it so a face or corner is
    #     tangent to the line -- e.g. members whose OUTER faces must be flush with
    #     a shared reference plane.  This is GLOBAL (one control for the whole
    #     creation): mixing alignments between members of one frame makes no
    #     sense, so it lives above the per-line table, not in it.
    pos: adsk.core.DropDownCommandInput = inputs.addDropDownCommandInput(
        'position', 'Position', adsk.core.DropDownStyles.TextListDropDownStyle)
    pos_items = pos.listItems
    for _key, label in prof.GRID_POSITIONS:
        pos_items.add(label, False)
    if pos_items.count > 0:
        pos_items.item(0).isSelected = True    # "Center"

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


def _position_key(inputs):
    """Selected section-alignment key ('center', 'top', 'bottom-right', ...).

    Reads the global Position dropdown and maps its label back to the
    :data:`profiles.GRID_POSITIONS` key.  Defaults to 'center' (the historical
    centred placement) when the dropdown is missing or nothing is selected.
    """
    dd: adsk.core.DropDownCommandInput = inputs.itemById('position')
    if dd is None:
        return 'center'
    idx = _dropdown_index(dd)
    if 0 <= idx < len(prof.GRID_POSITIONS):
        return prof.GRID_POSITIONS[idx][0]
    return 'center'


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


def _section_anchor(inputs, geom):
    """Local ``(u, v)`` mm section anchor for the global Position alignment grid.

    Maps the Position dropdown to the point of ``geom`` that should sit ON the
    picked line (see :func:`profiles.grid_anchor`).  Returns ``None`` for
    'center' (the default) so both the build placement and the joint trim take
    the exact centred (historical) path with no offset arithmetic.
    """
    anchor = prof.grid_anchor(geom, _position_key(inputs))
    return None if anchor == (0.0, 0.0) else anchor


def _joint_offsets(inputs, lines, geom, clr_by_line=None, ref=None,
                   cope_depth_by_line=None, context=None):
    """Per-line (offset_start, offset_end) in cm from the chosen corner joints.

    Auto-detects the corners among ``lines`` and turns each row's joint type into
    the length trim that removes the corner overlap (see lib/joints).  ``ref`` is
    the shared profile "up" reference; when given, each line's placed section
    basis is computed so a butt/cope trim stops at the neighbour's *directional*
    face (an I-beam's flange width, not its web depth).  ``cope_depth_by_line``
    (mm) deepens each saddled/cope end's bite into the neighbour.  ``context``
    (optional) is a list of existing-member dicts (see
    :func:`_recover_existing_members`); a selected end meeting one is trimmed
    against it.  The result is added to the user's manual start/end offsets in
    the build loop.
    """
    joints = _row_joints(inputs, lines)
    through = _row_through(inputs, lines)
    saddle = _row_saddle(inputs, lines)
    geoms = [geom for _ in lines]
    bases = None
    if ref is not None:
        try:
            # The PLACED basis: compute_basis rolled by each row's Rotation,
            # exactly as _build_weldment places it.  Using the rotated basis (not
            # the raw one) is what makes a rotated member's miter/butt trim see
            # its true orientation -- an I-beam mitered on its flange vs its web.
            bases = [prof.rotate_basis(*prof.compute_basis(_line_direction(l), ref),
                                       angle_rad=_row_params(inputs, i)[0])
                     for i, l in enumerate(lines)]
        except Exception:
            bases = None
    # Off-centre placement (the Position grid): the butt/cope/miter trim must
    # measure the neighbour's extent from the reference line (the anchor), not its
    # displaced centroid.  Both the basis and the anchor handed to corner_offsets
    # are the PLACED ones -- the same rotated basis and plain local anchor
    # _build_weldment uses -- so the trim sees the member's real orientation.
    # None for 'center' -> exact centred behaviour.
    anchor_local = _section_anchor(inputs, geom)
    anchor_by_line = [anchor_local for _ in lines] if anchor_local else None
    try:
        return jt.corner_offsets(lines, geoms, joints, clr_by_line=clr_by_line,
                                 through_by_line=through, bases=bases,
                                 saddle_by_line=saddle,
                                 cope_depth_by_line=cope_depth_by_line,
                                 context=context, anchor_by_line=anchor_by_line)
    except Exception:
        futil.handle_error(f'{CMD_NAME} joint offsets')
        return [(0.0, 0.0) for _ in lines]


def _joint_frame(inputs, lines, geom, ref):
    """The PLACED ``(bases, anchor_by_line)`` a joint cut must measure extents in.

    Identical to what :func:`_joint_offsets` derives internally -- each line's
    section basis (``compute_basis`` rolled by the row's Rotation) and the
    Position-grid anchor -- so the cutter geometry sees the same orientation and
    offset the builder used.  Returns ``(None, None)`` when no reference plane
    is available (isotropic extents).
    """
    bases = None
    if ref is not None:
        try:
            bases = [prof.rotate_basis(*prof.compute_basis(_line_direction(l), ref),
                                       angle_rad=_row_params(inputs, i)[0])
                     for i, l in enumerate(lines)]
        except Exception:
            bases = None
    anchor_local = _section_anchor(inputs, geom)
    anchor_by_line = [anchor_local for _ in lines] if anchor_local else None
    return bases, anchor_by_line


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


def _bend_bases(lines, joints, ref, clr_by_line=None,
                inverse_by_line=None, context=None):
    """Per-line section basis forcing every bend leg to lie flat in the bend plane.

    A square/rectangular tube can only be rotary-draw-bent about a **flat face**
    (the die groove bears on a face parallel to the bend plane) -- never a rolled
    corner, which would collapse the tube.  So for each line that is a leg of a
    swept bend, return a basis whose ``axis_v`` is the bend-plane normal (the bend
    axis ``a = u x v``), placing a pair of flat faces parallel to the bend plane;
    ``None`` for lines that are not a bend leg (they use the normal
    ``compute_basis(dir, ref)``).

    For a **planar** bend this equals ``compute_basis`` exactly (there ``a`` is
    the shared ``ref``, so ``axis_v`` already comes out as ``a``) -- no change.
    For a **non-planar (3D)** bend it overrides the global reference, which would
    otherwise roll the corner into the bend plane and twist the leg out of the arc
    (the reported bug).  A round tube (CHS) is isotropic so the choice is
    immaterial, but the same basis is harmless there.
    """
    n = len(lines)
    bases = [None] * n
    try:
        plans = jt.bend_plan(lines, joints, clr_by_line or [1.0] * n,
                             inverse_by_line=inverse_by_line, context=context)
    except Exception:
        futil.handle_error(f'{CMD_NAME} bend bases')
        return bases
    for plan in plans:
        a = prof._norm(plan['axis'])
        for idx, _role, _tangent in plan['tangent']:
            if not 0 <= idx < n:
                continue                  # existing (context) leg, not built here
            d = _line_direction(lines[idx])
            u = prof._norm(prof._cross(a, d))
            if u == (0.0, 0.0, 0.0):
                continue                  # leg parallel to the bend axis: no plane
            bases[idx] = (u, a)
    return bases


def _trim_bend_context_end(root, plan, context):
    """Split an EXISTING member's end so a new bend arc can blend into it.

    When a selected ``bend`` leg rounds into a member that already exists (built
    in a previous weldment run), :func:`lib.joints.bend_plan` plans the corner
    and the arc is revolved from the new leg -- but the existing member's square
    end still pokes through the arc, because ``corner_offsets`` can only trim
    lines built this run (its ``bump`` skips context members).  So the builder
    side does what the geometry layer cannot: split the existing body by a plane
    through that corner's context tangent point, normal to the existing member's
    axis.

    The split is SPLIT-ONLY: both halves stay in the design and the stray
    sliver past the tangent plane is left for the user to delete.  Auto-Removing
    the waste proved unreliable on members that are part of a bent chain --
    pointContainment can report the wrong half (it matched a pattern/arc body
    instead of the original run), deleting solid material.  A leftover sliver is
    a far cheaper mistake than a wrongly-removed member, so we never Remove
    here.

    ``plan`` is a :func:`lib.joints.bend_plan` entry; only its second tangent
    leg (the partner) can be a context member (index ``< 0``).  Returns the
    created objects (Split, sketch, plane) in teardown order, or ``[]`` when
    there is no context leg, the member is already trimmed, or anything fails.
    """
    tangent = plan.get('tangent') or []
    if len(tangent) < 2:
        return []
    t_idx, t_role, t_pt = tangent[1]
    if t_idx >= 0:
        return []                       # both legs built this run: nothing to do
    k = ~t_idx
    if k >= len(context or []):
        return []
    member = context[k]
    body = member.get('body')
    if body is None:
        return []
    try:
        s, e = jt.line_endpoints(member['line'])
        d = prof._norm(jt._add(e, jt._scale(s, -1.0)))
        # Idempotence guard: if the member no longer reaches past the tangent
        # plane along its own axis, it is already trimmed -- re-splitting would
        # fail with SPLIT_TARGET_TOOL_NOT_INTERSECT (e.g. a committed member
        # whose bend was trimmed in an earlier run of this same command).
        # Project the body's bbox extremes onto the axis and compare with the
        # tangent point's.
        try:
            bb = body.boundingBox
            hi = None
            for kx in (0, 1):
                for ky in (0, 1):
                    for kz in (0, 1):
                        p = ((bb.minPoint.x if not kx else bb.maxPoint.x),
                             (bb.minPoint.y if not ky else bb.maxPoint.y),
                             (bb.minPoint.z if not kz else bb.maxPoint.z))
                        t = sum(p[q] * d[q] for q in range(3))
                        hi = t if hi is None else max(hi, t)
            tp = sum(t_pt[q] * d[q] for q in range(3))
            if hi is not None and hi <= tp + 1e-4:
                return []               # nothing left to trim
        except Exception:
            pass                        # no usable bbox: attempt the split
        # Split only: a construction plane through the tangent point, normal to
        # the member axis.  Both halves stay; the user deletes the stray.
        plane, sk = _miter_plane(root, t_pt, d)
        sbf = root.features.splitBodyFeatures.add(
            root.features.splitBodyFeatures.createInput(body, plane, True))
        created = []
        if sbf is not None:
            created.append(sbf)
        for helper in (sk, plane):
            try:
                helper.isLightBulbOn = False
            except Exception:
                pass
        created.extend([sk, plane])
        return created
    except Exception:
        futil.handle_error(f'{CMD_NAME} bend context trim')
        return []


def _build_bend_arcs(root, lines, joints, clr_by_line, geom, ref,
                     inverse_by_line=None, preview=False, context=None,
                     bases=None, anchor=None):
    """Build the swept-bend arc bodies for every ``bend`` corner among ``lines``.

    Returns a list of (feature, sketch, plane) tuples for the caller to track.
    Legs whose joint is ``bend`` were already trimmed to their tangent points by
    :func:`_joint_offsets`; here the arc that fills each rounded corner is
    revolved into place, once per corner (built from that corner's first leg).
    ``inverse_by_line`` flips a corner's sweep direction (see
    :func:`lib.joints.bend_plan`) for corners whose legs were picked in reverse
    order.  ``context`` (existing members, see
    :func:`_recover_existing_members`) lets a bend leg round into an already
    placed member's END: the arc is revolved from the new leg and the existing
    member's poking end is trimmed to the tangent plane (see
    :func:`_trim_bend_context_end`).  ``bases`` (optional, from
    :func:`_bend_bases`) gives each leg its flat-in-the-bend-plane section basis so
    the arc sweeps from a face the die can bear on (a square tube bends about a
    flat face, never a rolled corner).  ``anchor`` (optional) is the global
    Position-grid section anchor; an off-centre leg's whole arc is shifted by it
    so the bend follows the displaced member.

    Returns ``(arcs, trims)``: the arc ``(feature, sketch, plane)`` tuples (the
    caller's existing tracking list) and the flat context-trim objects, which
    must be torn down like corner cuts -- BEFORE the member features they cut.
    """
    arcs, trims = [], []
    try:
        plans = jt.bend_plan(lines, joints, clr_by_line,
                             inverse_by_line=inverse_by_line, context=context)
    except Exception:
        futil.handle_error(f'{CMD_NAME} bend plan')
        return arcs, trims
    for plan in plans:
        idx, _role, tangent = plan['tangent'][0]
        if not 0 <= idx < len(lines):
            continue  # a context leg is never the one we sweep/revolve from
        # Prefer a true swept bend (sketch arc + Sweep): the die-radius centerline
        # arc is the path and the tube section is swept along it, which is what a
        # press-brake/roll bender actually produces.  Fall back to revolving the
        # section about the bend axis when the sweep cannot be built.
        arc = _build_bend_arc_sweep(root, lines[idx], tangent, plan, geom, ref,
                                    preview=preview,
                                    basis=(bases[idx] if bases else None),
                                    anchor=anchor)
        if arc is None:
            arc = _build_bend_arc(root, lines[idx], tangent, plan, geom, ref,
                                  preview=preview,
                                  basis=(bases[idx] if bases else None),
                                  anchor=anchor)
        if arc:
            arcs.append(arc)
        # If the other leg of this corner is an EXISTING member, its square end
        # pokes through the arc -- trim it to the tangent plane (see
        # _trim_bend_context_end).  Done whether or not the arc built, so a
        # failed arc still leaves the member trimmed consistently.
        trims.extend(_trim_bend_context_end(root, plan, context or []))
    return arcs, trims


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
    for objs in _preview_objs:
        for obj in objs:
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
    # Recover the members already in the design (from earlier weldment runs) so
    # a new line's butt/cope/miter/bend end can join an EXISTING part directly,
    # instead of the user drawing a shadow line and deleting a duplicate.  Done
    # before building so the bodies created this run are not mistaken for
    # pre-existing ones.
    context = _recover_existing_members(root)
    clr_by_line = _bend_radii(inputs, saved, designation)
    cope_depths = _row_cope_depths(inputs, saved)
    inverses = _row_inverses(inputs, saved)
    joint_offs = _joint_offsets(inputs, saved, geom, clr_by_line, ref,
                                cope_depth_by_line=cope_depths,
                                context=context)
    # Force each bend leg flat in the bend plane (a square tube bends about a
    # flat face, never a rolled corner; see _bend_bases).
    tbases = _bend_bases(saved, _row_joints(inputs, saved), ref,
                         clr_by_line=clr_by_line,
                         inverse_by_line=inverses, context=context)
    # Global Position-grid anchor: the section point that sits ON each picked
    # line.  None for 'center' (the default) so placement stays centred.
    anchor = _section_anchor(inputs, geom)
    objs, feat_idx = [], []
    f_start = root.features.count
    for i, line in enumerate(saved):
        angle, off_s, off_e = _row_params(inputs, i)
        js, je = joint_offs[i] if i < len(joint_offs) else (0.0, 0.0)
        idx = root.features.count
        built = _build_weldment(root, line, geom, label, angle, ref,
                                off_s + js, off_e + je, preview=True,
                                basis=tbases[i], anchor=anchor)
        objs.append(built)
        feat_idx.append(idx if built else None)
        if built:
            _preview_objs.append(built)
    bend_arcs, bend_trims = _build_bend_arcs(
        root, saved, _row_joints(inputs, saved), clr_by_line, geom, ref,
        inverse_by_line=inverses, preview=True, context=context,
        bases=tbases, anchor=anchor)
    _preview_objs.extend(bend_arcs)
    # Context-end trims (a bend rounding into an existing member) are cut
    # features: tear them down with the other cuts, before the members.
    _preview_cuts.extend(bend_trims)
    _bases, _anchor = _joint_frame(inputs, saved, geom, ref)
    _preview_cuts.extend(
        _apply_corner_cuts(root, saved, _row_joints(inputs, saved), objs,
                           feat_idx, f_start,
                           saddle=_row_saddle(inputs, saved),
                           through=_row_through(inputs, saved),
                           context=context,
                           geoms=[geom for _ in saved],
                           cope_depth_by_line=cope_depths,
                           clr_by_line=clr_by_line,
                           bases=_bases, anchor_by_line=_anchor))
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
    # See the preview path: recover the design's existing members so a new
    # line's joint end can join them directly (no shadow part / duplicate).
    context = _recover_existing_members(root)
    clr_by_line = _bend_radii(inputs, saved, designation)
    cope_depths = _row_cope_depths(inputs, saved)
    inverses = _row_inverses(inputs, saved)
    joint_offs = _joint_offsets(inputs, saved, geom, clr_by_line, ref,
                                cope_depth_by_line=cope_depths,
                                context=context)
    # Force each bend leg flat in the bend plane (a square tube bends about a
    # flat face, never a rolled corner; see _bend_bases).
    tbases = _bend_bases(saved, _row_joints(inputs, saved), ref,
                         clr_by_line=clr_by_line,
                         inverse_by_line=inverses, context=context)
    # Global Position-grid anchor (see the preview path): None for 'center'.
    anchor = _section_anchor(inputs, geom)

    created = 0
    objs, feat_idx = [], []
    placements = {}
    f_start = root.features.count
    for i, line in enumerate(saved):
        angle, off_s, off_e = _row_params(inputs, i)
        js, je = joint_offs[i] if i < len(joint_offs) else (0.0, 0.0)
        idx = root.features.count
        built = _build_weldment(root, line, geom, label, angle, ref,
                                off_s + js, off_e + je, basis=tbases[i],
                                anchor=anchor)
        if built:
            created += 1
            placements[i] = {'angle_rad': angle,
                             'offset_start': off_s + js,
                             'offset_end': off_e + je}
        objs.append(built)
        feat_idx.append(idx if built else None)
    bend_arcs, bend_trims = _build_bend_arcs(
        root, saved, _row_joints(inputs, saved), clr_by_line, geom, ref,
        inverse_by_line=inverses, context=context, bases=tbases, anchor=anchor)
    created += len(bend_arcs)
    _bases, _anchor = _joint_frame(inputs, saved, geom, ref)
    global _arc_downgrades
    _arc_downgrades = 0
    occs, cut_bodies = [], {}
    _apply_corner_cuts(root, saved, _row_joints(inputs, saved), objs,
                       feat_idx, f_start,
                       saddle=_row_saddle(inputs, saved),
                       through=_row_through(inputs, saved),
                       context=context,
                       geoms=[geom for _ in saved],
                       cope_depth_by_line=cope_depths,
                       clr_by_line=clr_by_line,
                       bases=_bases, anchor_by_line=_anchor,
                       spec_out=occs, bodies_out=cut_bodies)

    if _arc_downgrades:
        ui.messageBox(
            f'{_arc_downgrades} cope joint{"s were" if _arc_downgrades != 1 else " was"} '
            'changed to a flat butt because the coping member lands on the '
            'curved part of a bend, where a straight cope cut cannot follow the '
            'bend radius. Move the member onto the straight section (past the '
            'tangent point) to keep the cope.')
        _arc_downgrades = 0

    if created == 0:
        ui.messageBox('No weldments were created. Select 3D sketch line(s) first.')
        return

    # Persist the built members so the next run edits records instead of
    # re-detecting the frame (Phase 4a re-run pickup).
    try:
        registry = persist_members(_design(), saved, geom, designation,
                                   tbases, feat_idx, placements=placements,
                                   ref=ref, anchor=anchor, objs=objs,
                                   bodies=cut_bodies)
        # Record the joints that were just built (A3): the BOM becomes the
        # frame's history -- every miter/cope/bend is a re-runnable record.
        record_joints(registry, occs, saved, context=context)
        save_registry(_design(), registry)
    except Exception:
        futil.handle_error(f'{CMD_NAME} registry persist')


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
    # The two long helper lines exist only to define the plane (setByTwoEdges
    # references them, so the sketch must stay in the tree), but they are never
    # wanted on screen.  Hide the sketch centrally so every caller gets a clean
    # browser -- the plane it feeds is the only thing that should show.
    try:
        sk.isLightBulbOn = False
    except Exception:
        pass
    return plane, sk


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


class _CtxPoint:
    """Minimal ``(x, y, z)`` holder matching a Fusion Point3D read interface."""

    __slots__ = ('x', 'y', 'z')

    def __init__(self, p):
        self.x, self.y, self.z = p


class _CtxGeometry:
    """``worldGeometry``-shim exposing ``startPoint`` / ``endPoint``."""

    def __init__(self, start, end):
        self.startPoint = _CtxPoint(start)
        self.endPoint = _CtxPoint(end)


class _ContextLine:
    """A recovered existing weldment member, shaped like a sketch line.

    :mod:`lib.joints` only ever reads ``worldGeometry.startPoint`` /
    ``.endPoint`` (each with ``.x/.y/.z`` in cm) and ``.length`` from a line, so
    this shim lets an *existing* body participate in corner / T-junction
    detection without a real sketch entity.  ``body`` is the BRepBody to use as a
    boolean tool when a new member copes/miters/butts against it.
    """

    def __init__(self, start, end, body):
        self.worldGeometry = _CtxGeometry(start, end)
        self.length = sum((start[k] - end[k]) ** 2 for k in range(3)) ** 0.5
        self.body = body


def _v3(v):
    """A Vector3D/Point3D as a plain ``(x, y, z)`` tuple."""
    return (v.x, v.y, v.z)


def _face_area(face):
    try:
        return face.area
    except Exception:
        return 0.0


def _centerline_round(faces):
    """Recover ``(start, end, geom, None)`` for a round (cylindrical) tube.

    The dominant (largest-area) cylindrical face's axis is the run direction;
    projecting every face origin onto it gives the centreline ends.  ``geom`` is
    a ``circles`` descriptor in mm (outer radius first, inner appended when the
    radii differ, i.e. a hollow tube).  ``basis`` is None -- a round section is
    isotropic, so its roll is irrelevant to a joint.
    """
    best, best_area = None, -1.0
    for face, geo in faces:
        if isinstance(geo, adsk.core.Cylinder):
            area = _face_area(face)
            if area > best_area:
                best_area, best = area, geo
    if best is None:
        return None
    axis = _v3(best.axis)
    origin = _v3(best.origin)
    ts, radii = [], []
    for _face, geo in faces:
        if isinstance(geo, (adsk.core.Plane, adsk.core.Cylinder)):
            p = _v3(geo.origin)
            ts.append(sum((p[c] - origin[c]) * axis[c] for c in range(3)))
            if isinstance(geo, adsk.core.Cylinder):
                radii.append(geo.radius)
    if not ts or not radii:
        return None

    def on_axis(t):
        return tuple(origin[c] + axis[c] * t for c in range(3))

    outer = max(radii)
    inner = min(radii) if len(set(round(r, 6) for r in radii)) > 1 else None
    radii_mm = [outer * 10.0]
    if inner is not None:
        radii_mm.append(inner * 10.0)
    return (on_axis(min(ts)), on_axis(max(ts)),
            {'kind': 'circles', 'radii': radii_mm}, None)


def _centerline_prismatic(body, faces):
    """Recover ``(start, end, geom, basis)`` for a square/rectangular tube.

    A prismatic hollow section's planar faces fall into three normal *axes*: the
    two small section caps (whose common normal is the run direction ``d``) and
    two pairs of large side faces (whose normals span the section's in-plane
    directions).  Clustering the face normals by axis and taking the least-total-
    area axis as ``d`` recovers the run; the other two axes are the section's
    ``(axis_u, axis_v)`` basis -- read straight from the *actual* side-face
    normals, so a tube rotated about its own run keeps its true orientation and a
    cope against it respects that shape (not an isotropic fallback).

    ``geom`` is a ``polygons`` descriptor in mm: an outer rectangle of the two
    half-extents, plus an inner one when the min-projection faces sit inside the
    max-projection faces (a hollow wall).
    """
    centroid = _body_centroid(body)
    axes = []   # [unit_normal, total_area, [(offset_along_normal, face)]]
    for face, geo in faces:
        if not isinstance(geo, adsk.core.Plane):
            continue
        n = prof._norm(_v3(geo.normal))
        off = prof._dot(prof._sub(_v3(geo.origin), centroid), n)
        for a in axes:
            if abs(abs(prof._dot(a[0], n)) - 1.0) < 1e-3:
                a[1] += _face_area(face)
                a[2].append((off, n))
                break
        else:
            axes.append([n, _face_area(face), [(off, n)]])
    if len(axes) < 3:
        return None
    axes.sort(key=lambda a: a[1])          # smallest total area first = the caps
    d = axes[0][0]                          # run direction (cap normal)
    e1, e2 = axes[1][0], axes[2][0]         # section in-plane directions
    ts = []
    for a in axes:
        for off, n in a[2]:
            # Reconstruct the face point's projection onto d from its offset.
            p = jt._add(centroid, prof._scale(n, off))
            ts.append(prof._dot(prof._sub(p, centroid), d))
    if not ts:
        return None
    start = jt._add(centroid, prof._scale(d, min(ts)))
    end = jt._add(centroid, prof._scale(d, max(ts)))

    def half_extents(group):
        vals = [abs(o) for o, _n in group]
        return max(vals), (min(vals) if len(vals) > 1 else max(vals))
    o1, i1 = half_extents(axes[1][2])
    o2, i2 = half_extents(axes[2][2])
    hw1, hw2 = o1 * 10.0, o2 * 10.0          # mm outer half-widths
    loops = [[(-hw1, -hw2), (hw1, -hw2), (hw1, hw2), (-hw1, hw2)]]
    fillets = [[]]
    if (i1 < o1 - 1e-6) or (i2 < o2 - 1e-6):  # hollow: an inner wall loop
        iw1, iw2 = i1 * 10.0, i2 * 10.0
        loops.append([(-iw1, -iw2), (iw1, -iw2), (iw1, iw2), (-iw1, iw2)])
        fillets.append([])
    # A filleted section (SHS/RHS carry r_mm corner radii) has small corner
    # cylinders whose axes run parallel to ``d``; their radius is the outer
    # corner radius.  Recover it so the cope boolean matches the real rounded
    # tube instead of a sharp rectangle (the flats still sit at the full
    # half-extent, so this only rounds the corners -- the reach math is
    # unchanged, but the cut face now follows the tube's true outline).
    r_mm = max((geo.radius * 10.0 for _f, geo in faces
                if isinstance(geo, adsk.core.Cylinder)), default=0.0)
    if 0.0 < r_mm < 2.0 * min(hw1, hw2):
        fillets[0] = [(i, r_mm) for i in range(4)]
    geom = {'kind': 'polygons', 'loops': loops, 'fillets': fillets}
    return start, end, geom, (e1, e2)


def _member_centerline(body):
    """Recover ``(start, end, geom, basis)`` for a straight tube body.

    ``geom`` is a :mod:`lib.joints` section descriptor in millimetres (``circles``
    for a round tube, ``polygons`` for a square/rectangular one); ``basis`` is the
    member's placed ``(axis_u, axis_v)`` world unit vectors (None for a round
    tube).  Returns ``None`` for a body that is not a straight prismatic member
    (a swept-bend torus, a solid block, ...) so such bodies are skipped -- they
    are not members a new part can butt/cope/miter against.
    """
    try:
        faces = []
        for k in range(body.faces.count):
            face = body.faces.item(k)
            faces.append((face, face.geometry))
        if not faces:
            return None
        # Route by the DOMINANT face kind, not mere presence of a cylinder: a
        # filleted square/rectangular tube (SHS/RHS carry r_mm corner radii) has
        # four flat side faces PLUS four small corner cylinders, so "any
        # cylinder" would misread it as a round tube and compute the cope reach
        # against a tiny circle instead of the real section.  Try the prismatic
        # path first: it clusters the PLANAR faces by normal axis and returns
        # None unless they span three axes (two caps + two side pairs), which is
        # exactly a square/rectangular tube.  A round tube's planes are only its
        # two caps (one axis), so it falls through to the round path.
        if any(isinstance(g, adsk.core.Plane) for _f, g in faces):
            prism = _centerline_prismatic(body, faces)
            if prism is not None:
                return prism
        if any(isinstance(g, adsk.core.Cylinder) for _f, g in faces):
            return _centerline_round(faces)
        return None
    except Exception:
        futil.handle_error(f'{CMD_NAME} member centerline')
        return None


# --------------------------------------------------------------------------- #
# Registry persistence (Phase 4a) -- the toolbox data backbone.
#
# The whole frame's member/joint records are parked as one JSON string on a
# design attribute, so re-running a tool edits records instead of re-detecting
# topology from bodies. Pure record logic lives in lib/registry.py; the two
# functions below are the only adsk glue (attribute read/write + turning a
# member record into the context dict joint_spec consumes).
# --------------------------------------------------------------------------- #
REGISTRY_ATTR = 'WeldmentsRegistry'


def _design():
    return adsk.fusion.Design.cast(app.activeProduct)


def _find_attribute(design, name):
    """The design attribute ``name``, or None."""
    attrs = design.attributes
    for i in range(attrs.count):
        a = attrs.item(i)
        if a.name == name:
            return a
    return None


def load_registry(design):
    """The design's :class:`lib.registry.Registry` (empty if none stored)."""
    a = _find_attribute(design, REGISTRY_ATTR)
    if a is None:
        return reg.Registry()
    try:
        return reg.Registry.from_json(a.value)
    except Exception:
        futil.handle_error(f'{CMD_NAME} registry load')
        return reg.Registry()


def save_registry(design, registry):
    """Write ``registry`` back to the design attribute (create if absent)."""
    a = _find_attribute(design, REGISTRY_ATTR)
    if a is None:
        a = design.attributes.add('UserParameters', REGISTRY_ATTR, 'String')
    a.value = registry.to_json()


# --------------------------------------------------------------------------- #
# Body <-> member identity (A2: the attribute spine).
#
# Feature indices drift and body names are unstable, so the reliable link from a
# live BRepBody back to its registry Member record is an ATTRIBUTE STAMPED ON THE
# BODY ITSELF: (group 'Weldments', name 'Member', value str(mid)).  Attributes
# survive rename/recolour and -- unlike a Combine's output -- a body keeps them
# wherever it moves in the timeline.  Waste fragments produced by a boolean are
# NEW bodies and therefore have NO stamp, which doubles as the classifier for
# "remove the leftovers" (A4/C2).  Everything resolves through here instead of
# re-guessing sections from faces.
# --------------------------------------------------------------------------- #
BODY_ATTR_GROUP = 'Weldments'
BODY_ATTR_NAME = 'Member'


def stamp_body(body, mid):
    """Attach (or update) the member-id stamp on ``body``. Best-effort."""
    try:
        body.attributes.add(BODY_ATTR_GROUP, BODY_ATTR_NAME, str(mid))
        return True
    except Exception:
        futil.handle_error(f'{CMD_NAME} stamp body')
        return False


def body_mid(body):
    """The member id stamped on ``body``, or None when it carries no stamp."""
    try:
        a = body.attributes.itemByName(BODY_ATTR_GROUP, BODY_ATTR_NAME)
    except Exception:
        return None
    if a is None:
        return None
    try:
        return int(a.value)
    except (TypeError, ValueError):
        return None


def resolve_member(registry, body, context_line=None):
    """The Member record a live ``body`` belongs to, or None.

    Primary key is the body's attribute stamp (see above).  When a body carries
    no stamp -- built before the spine existed, or by a plain extrude -- fall
    back to centreline proximity: pass ``context_line`` = ``(start, end)`` cm
    (e.g. from :func:`_member_centerline`) and the nearest member within 0.5 cm
    wins.  Returns None when neither identifies a member.
    """
    mid = body_mid(body)
    if mid is not None:
        m = registry.member(mid)
        if m is not None:
            return m
    if context_line is not None:
        return registry.member_near_point(
            reg.midpoint(context_line[0], context_line[1]), tol=0.5)
    return None


def registry_context(root, registry):
    """Member records as ``joint_spec`` context dicts, bodies re-resolved live.

    Mirrors :func:`_recover_existing_members`' output shape
    ``{'line','geom','basis','body'}`` but sourced from stored records: the
    centreline comes from the record (so a bend member keeps its full virtual
    corner, no stub-reunion needed) and the ``body`` is found through the
    attribute spine -- the body whose stamp names this member (see
    :func:`stamp_body`) -- falling back to the stored feature index for bodies
    built before the spine existed. A record whose body is gone is skipped.
    """
    by_mid = {}
    try:
        feats = root.features
        for i in range(feats.count):
            try:
                f = feats.item(i)
                for j in range(f.bodies.count):
                    b = f.bodies.item(j)
                    mid = body_mid(b)
                    if mid is not None:
                        by_mid[mid] = b
            except Exception:
                continue
    except Exception:
        futil.handle_error(f'{CMD_NAME} registry context')
    members = []
    for m in registry.members:
        body = by_mid.get(m.mid)
        if body is None and m.feature is not None:
            try:
                f = root.features.item(m.feature)
                body = f.bodies.item(m.body_index or 0)
            except Exception:
                body = None
        if body is None:
            continue
        members.append({'line': _ContextLine(m.start, m.end, body),
                        'geom': m.geom, 'basis': m.basis, 'body': body})
    return members


def record_joints(registry, occs, lines, context=None):
    """Store ``joint_spec`` occurrences (A3) as Joint records, deduped.

    ``occs`` is what :func:`_apply_corner_cuts` computed for the committed
    build; ``lines``/``context`` are the same index spaces joint_spec used, so
    an occurrence's ``member``/``partner`` indices map back to member records
    (by stamp first, centreline proximity second).  A miter is ONE joint
    between two members even though joint_spec emits an occurrence per leg, so
    occurrences sharing (kind, vertex, member set) merge into a single record,
    and a record the registry already carries (same kind, vertex within 0.05 cm,
    same member set) is not duplicated -- the re-run pickup.  Plain butts
    (cutter None, kind 'butt') are pure axial trims -- nothing geometric
    happened, and they are not recorded.  Returns the records made.
    """
    ctx = context or []

    def mid_of(i):
        if 0 <= i < len(lines):
            s, e = jt.line_endpoints(lines[i])
            m = registry.member_near_point(reg.midpoint(s, e), tol=0.5)
            return m.mid if m else None
        if i < 0 and ~(i) < len(ctx):      # joint_spec encodes context as ~k
            cl = ctx[~i].get('line') if isinstance(ctx[~i], dict) else ctx[~i]
            s, e = jt.line_endpoints(cl)
            m = registry.member_near_point(reg.midpoint(s, e), tol=0.5)
            return m.mid if m else None
        return None

    made, seen = [], {}
    for occ in occs:
        kind = occ.get('kind')
        if kind == 'bend' and occ.get('cutter') is None and not occ.get('legs'):
            continue
        if kind == 'butt' and occ.get('cutter') is None:
            continue                      # pure axial trim: no joint geometry
        subj = mid_of(occ['member'])
        partner = occ.get('partner')
        pmid = mid_of(partner[0]) if partner else None
        if subj is None:
            continue
        refs = [{'mid': subj, 'role': occ.get('role')}]
        if pmid is not None and pmid != subj:
            refs.append({'mid': pmid, 'role': partner[1] if partner else None})
        V = occ.get('vertex')
        key = (kind if kind != 'miter' else 'miter',
               tuple(round(c, 4) for c in V) if V else None,
               frozenset(r['mid'] for r in refs))
        if key in seen:
            j = seen[key]
            for r in refs:                # merge the other leg's subject
                if r['mid'] not in j.member_ids():
                    j.refs.append(r)
            continue
        params = {}
        if occ.get('clr_mm'):
            params['clr_mm'] = occ['clr_mm']
        if occ.get('setback'):
            params['setback_cm'] = occ['setback']
        old = _find_joint(registry, kind, V, {r['mid'] for r in refs})
        if old is not None:               # re-run pickup: edit, don't duplicate
            old.params.update(params)
            seen[key] = old
            continue
        j = registry.add_joint(kind, refs, vertex=V, params=params)
        seen[key] = j
        made.append(j)
    return made


def _find_joint(registry, kind, vertex, mids):
    """An existing joint of ``kind`` at ``vertex`` (±0.05 cm) over ``mids``."""
    for j in registry.joints:
        if j.kind != kind or set(j.member_ids()) != set(mids):
            continue
        if vertex is None or j.vertex is None:
            return j
        if reg.distance(j.vertex, vertex) <= 0.05:
            return j
    return None


def placed_basis(line, ref, angle_rad, bend_basis=None):
    """The section basis :func:`_build_weldment` actually draws with.

    ``_build_weldment`` starts from ``bend_basis`` when it is a bend leg (flat in
    the bend plane) else :func:`profiles.compute_basis` against the global
    reference, and then spins it by the row's Rotation.  The registry must store
    THIS -- not the raw bend basis -- or a rebuilt/consulted member reads as
    isotropic (the ``basis: null`` bug: ``_bend_bases`` returns None for every
    non-bend leg even though the placement used a real basis).
    """
    d = _line_direction(line)
    u, v = (tuple(bend_basis[0]), tuple(bend_basis[1])) if bend_basis is not None \
        else prof.compute_basis(d, ref)
    return prof.rotate_basis(u, v, angle_rad)


def persist_members(design, lines, geom, designation, bases, feat_idx,
                    placements=None, ref=None, anchor=None, objs=None,
                    bodies=None):
    """Upsert each built line into the design's registry (re-run pickup).

    Matches an existing member by centreline so rebuilding the same tube edits
    its record instead of duplicating.  Stores the PLACED basis (see
    :func:`placed_basis`), the family abbreviation, and the per-row placement
    (Rotation, joint+manual offsets) so the member is rebuildable from the
    record alone, and stamps the built body with the member id (see
    :func:`stamp_body`) so any tool can resolve body -> record without face
    guessing. ``objs``/``bodies`` give the body to stamp: the post-cut survivor
    when a cut re-homed it (A4), else the extruded body. Returns the (mutated)
    registry. Joint records are written by the toolbox tools (Phase 4c) and by
    :func:`record_joints`; the auto command persists members here.
    """
    registry = load_registry(design)
    for i, line in enumerate(lines):
        if feat_idx[i] is None:
            continue
        s, e = jt.line_endpoints(line)
        pl = (placements or {}).get(i, {})
        m = registry.upsert_member(
            s, e, geom=geom,
            basis=[list(a) for a in placed_basis(
                line, ref, pl.get('angle_rad', 0.0),
                bases[i] if bases and i < len(bases) else None)],
            designation=designation.get('designation', '') if designation else '',
            family=designation.get('_abbreviation', '') if designation else '',
            feature=feat_idx[i], body_index=0,
            angle_rad=pl.get('angle_rad', 0.0),
            ref=list(ref) if ref else None,
            anchor=list(anchor) if anchor else None,
            offset_start=pl.get('offset_start', 0.0),
            offset_end=pl.get('offset_end', 0.0))
        if objs and i < len(objs) and objs[i]:
            try:
                built = objs[i][0].bodies.item(0)
            except Exception:
                built = None
            if built is not None:
                stamp_body(built, m.mid)
            # A cut may re-home the member to a new survivor body; stamp that too
            # ONLY when it carries no stamp yet -- never overwrite another
            # member's owner (a cope's kept-tool survivor can be mis-picked, and
            # clobbering its stamp would corrupt the spine).
            surv = (bodies or {}).get(i)
            if surv is not None and surv is not built and body_mid(surv) is None:
                stamp_body(surv, m.mid)
    save_registry(design, registry)
    return registry


def _recover_existing_members(root, exclude_feats=None):
    """Enumerate existing weldment members as context lines for joint detection.

    Scans every body in ``root`` (skipping the ones just built this run, whose
    feature indices are in ``exclude_feats``) and recovers each straight member's
    centreline, section geometry, and placed basis from its faces (see
    :func:`_member_centerline`).  Returns a list of dicts shaped for
    :func:`lib.joints.corner_offsets`::

        {'line': _ContextLine, 'geom': <section descriptor in mm>,
         'basis': (axis_u, axis_v) or None, 'body': <BRepBody>}

    ``geom`` is a ``circles`` descriptor for a round tube (outer radius first,
    inner appended when hollow) or a ``polygons`` descriptor for a square /
    rectangular one (outer rectangle + inner wall loop).  ``basis`` is the
    member's real in-plane ``(axis_u, axis_v)`` for a prismatic section -- so a
    cope against an existing rotated tube respects its true shape -- or None for
    a round tube (isotropic).  All lengths are in millimetres, the joints layer's
    unit, so a cope against an existing member saddles through its wall exactly
    as against a freshly previewed one.
    """
    exclude = set(exclude_feats or ())
    members = []
    try:
        feats = root.features
        for i in range(feats.count):
            if i in exclude:
                continue
            try:
                f = feats.item(i)
                count = f.bodies.count
            except Exception:
                continue
            for j in range(count):
                try:
                    body = f.bodies.item(j)
                except Exception:
                    continue
                cl = _member_centerline(body)
                if cl is None:
                    continue
                start, end, geom, basis = cl
                if start == end:
                    continue
                members.append({'line': _ContextLine(start, end, body),
                                'geom': geom, 'basis': basis, 'body': body})
    except Exception:
        futil.handle_error(f'{CMD_NAME} recover members')
    _reunite_bend_context(members)
    return members


# How far (cm) a recovered context leg may be extended to a bend's virtual
# corner vertex.  A swept bend trims its legs to the die tangent points, so the
# recovered centreline ends stop short of the vertex the user actually drew;
# the setback is clr*tan(theta/2) -- a few cm for real dies.  Anything larger
# is not a bend stub but two unrelated members, and must not be joined.
_BEND_REUNITE_MAX_CM = 20.0


def _reunite_bend_context(members):
    """Extend bend-stubbed context legs to their shared (virtual) corner.

    A member built by an earlier swept-bend run ends at its TANGENT point, not
    at the drawn vertex -- the arc fills the gap.  Corner detection matches
    coincident ENDPOINTS, so a new member butting/copes into that corner finds
    nothing: the stub ends sit centimetres short of the vertex along both legs.

    For every pair of context legs, solve the closest points of their centre
    lines; when both legs must extend FORWARD from an endpoint (small positive
    parameters) to meet at a point that is genuinely on both lines, rewrite both
    context lines to end at that virtual vertex.  Collinear pairs (a straight
    run split into segments) and unrelated members (skew lines, or an endpoint
    already at the meeting point) are left untouched.  Mutates ``members`` in
    place.
    """
    def _ends(m):
        s, e = jt.line_endpoints(m['line'])
        return s, e

    n = len(members)
    for i in range(n):
        for j in range(i + 1, n):
            try:
                si, ei = _ends(members[i])
                sj, ej = _ends(members[j])
                di = prof._norm(jt._add(ei, jt._scale(si, -1.0)))
                dj = prof._norm(jt._add(ej, jt._scale(sj, -1.0)))
                # Cross product ~ 0: collinear legs -- no vertex to reconstruct.
                cx = prof._cross(di, dj)
                if sum(c * c for c in cx) < 0.03:   # sin < ~10 deg
                    continue
                # A stub can only reach the vertex by a plausible bend setback.
                li = members[i]['line'].length
                lj = members[j]['line'].length
                cap_i = min(_BEND_REUNITE_MAX_CM, 0.5 * li)
                cap_j = min(_BEND_REUNITE_MAX_CM, 0.5 * lj)
                # The two centre LINES must cross at a point just PAST one
                # endpoint of each leg (the tangent stub stopped short of the
                # vertex).  Solve the closest approach of the infinite lines;
                # each leg's parameter t runs from its start along its unit
                # direction, so t just over the length means the vertex sits
                # past the END, t just negative means it sits before the START.
                w = jt._add(si, jt._scale(sj, -1.0))
                aa = sum(di[q] * di[q] for q in range(3))
                bb = sum(di[q] * dj[q] for q in range(3))
                cc = sum(dj[q] * dj[q] for q in range(3))
                dd = sum(di[q] * w[q] for q in range(3))
                ee = sum(dj[q] * w[q] for q in range(3))
                dn = aa * cc - bb * bb
                if abs(dn) < 1e-9:
                    continue
                t_i = (bb * ee - cc * dd) / dn
                t_j = (aa * ee - bb * dd) / dn
                pa = jt._add(si, jt._scale(di, t_i))
                pb = jt._add(sj, jt._scale(dj, t_j))
                gap = sum((pa[q] - pb[q]) ** 2 for q in range(3)) ** 0.5
                if gap > 0.05:
                    continue            # lines do not actually meet
                v = jt._scale(jt._add(pa, pb), 0.5)

                def _stub(t, tlen, cap):
                    """('start'|'end', overshoot) when v sits just outside."""
                    if t > tlen + 1e-6 and t - tlen <= cap:
                        return 'end', t - tlen
                    if t < -1e-6 and -t <= cap:
                        return 'start', -t
                    return None, 0.0

                ri, ai = _stub(t_i, li, cap_i)
                rj, aj = _stub(t_j, lj, cap_j)
                if ri is None or rj is None:
                    continue            # not a bend-stub pair (T-joint, skew...)
                members[i]['line'] = _stub_line(members[i]['line'], ri, v)
                members[j]['line'] = _stub_line(members[j]['line'], rj, v)
            except Exception:
                futil.handle_error(f'{CMD_NAME} reunite bend context')


def _stub_line(line, role, vertex):
    """A copy of ``line`` whose ``role`` endpoint is moved to ``vertex``."""
    s, e = jt.line_endpoints(line)
    body = getattr(line, 'body', None)
    if role == 'start':
        return _ContextLine(vertex, e, body)
    return _ContextLine(s, vertex, body)


def _region_axes(region):
    """Orthonormal axes ``(d, e1, e2)`` of a joint region box (from joint_spec)."""
    return region['axes']


def _box_corners(region):
    """The 8 corners (cm tuples) of a joint region box."""
    c = region['center']
    d, e1, e2 = region['axes']
    ha, hb, hc = region['half']
    out = []
    for sa in (-1, 1):
        for sb in (-1, 1):
            for sc in (-1, 1):
                out.append((c[0] + sa * ha * d[0] + sb * hb * e1[0] + sc * hc * e2[0],
                            c[1] + sa * ha * d[1] + sb * hb * e1[1] + sc * hc * e2[1],
                            c[2] + sa * ha * d[2] + sb * hb * e1[2] + sc * hc * e2[2]))
    return out


def _point_in_region(point, region, tol=1e-6):
    """True when ``point`` lies inside the (rotated) joint region box."""
    c = region['center']
    d, e1, e2 = region['axes']
    ha, hb, hc = region['half']
    v = (point[0] - c[0], point[1] - c[1], point[2] - c[2])
    return (abs(jt._dot(v, d)) <= ha + tol and
            abs(jt._dot(v, e1)) <= hb + tol and
            abs(jt._dot(v, e2)) <= hc + tol)


def _region_aabb(region):
    """The world-axis-aligned envelope ``((lo), (hi))`` of a (rotated) joint box.

    The joint box is oriented along the member, so on an angled joint it is
    rotated in the world.  Comparing a body's world-aligned bounding box against
    the rotated box corner-by-corner is wrong: an axis-aligned fragment inside a
    rotated box has world-AABB corners that project PAST the rotated faces, so a
    genuine cutoff reads as "outside" and is wrongly kept.  Instead take the
    box's own world-AABB (the extent along each world axis is the sum of
    ``|axis_component| * half`` over the box's three axes) and compare like for
    like -- both are world-aligned boxes.
    """
    c = region['center']
    d, e1, e2 = region['axes']
    ha, hb, hc = region['half']
    lo, hi = [], []
    for k in range(3):
        ext = abs(d[k]) * ha + abs(e1[k]) * hb + abs(e2[k]) * hc
        lo.append(c[k] - ext)
        hi.append(c[k] + ext)
    return tuple(lo), tuple(hi)


def _body_in_region(body, region):
    """True when a body's whole bounding box lies inside the joint region.

    A fragment that stays entirely within the box is a cutoff (waste); a body
    that pokes outside it is real member material and must never be touched.
    Both the body and the region are reduced to world-axis-aligned boxes first
    (see :func:`_region_aabb`) so the test is orientation-independent.
    """
    try:
        bb = body.boundingBox
        lo, hi = bb.minPoint, bb.maxPoint
    except Exception:
        return False
    blo = (lo.x, lo.y, lo.z)
    bhi = (hi.x, hi.y, hi.z)
    rlo, rhi = _region_aabb(region)
    return all(blo[k] >= rlo[k] - 1e-6 and bhi[k] <= rhi[k] + 1e-6
               for k in range(3))


def _remove_inside_region(root, comb, keep_body, region):
    """Remove the cope/saddle cutoff fragments from a combine's output.

    After a cope/saddle boolean the output is {tool, member run, waste}.  The
    joint box (from :func:`lib.joints.joint_spec`) is sized to span the WHOLE
    joint -- including a plug driven into the neighbour's tilted bore (see
    :func:`lib.joints._plug_reach`) -- so a cutoff sits ENTIRELY inside it while
    the member's main run always reaches PAST it.  Classify by containment: keep
    the tool and every body poking outside the box, Remove the ones wholly
    within.  As a defensive floor the largest non-tool body (the run) is never
    removed, in case a degenerate box would otherwise swallow it.  Returns the
    Remove features (tracked BEFORE the combine so teardown deletes them first).
    """
    removed = []
    try:
        bodies = comb.bodies
        # Snapshot BEFORE removing: a Remove shrinks the live collection, so
        # iterating it by index would skip the body after each removal (two
        # symmetric plugs -> only one deleted).
        cand = [bodies.item(bi) for bi in range(bodies.count)]
    except Exception:
        return removed
    non_tool = [b for b in cand if keep_body is None or b is not keep_body]
    survivor = None
    best_v = None
    for b in non_tool:
        try:
            v = b.volume
        except Exception:
            v = 0.0
        if best_v is None or v > best_v:
            survivor, best_v = b, v
    for b in non_tool:
        if b is survivor:
            continue                       # never remove the main run
        if not _body_in_region(b, region):
            continue                       # pokes outside the box: real material
        try:
            rm = root.features.removeFeatures.add(b)
            if rm is not None:
                removed.append(rm)
        except Exception:
            futil.handle_error(f'{CMD_NAME} remove region cutoff')
    return removed


def _combine(root, target, tools, operation, keep_tool, preview=False):
    """Combine ``target`` against ``tools`` with ``operation`` (Cut/Intersect).

    ``keep_tool`` keeps the tool body(ies) after the operation (a cope saddles
    against a neighbour it must not consume, or an Intersect keeps the real tool
    body while reshaping the target to the overlap) or drops them (a disposable
    cutter box).  Returns the CombineFeature.
    """
    coll = adsk.core.ObjectCollection.create()
    for b in tools:
        coll.add(b)
    ci = root.features.combineFeatures.createInput(target, coll)
    ci.operation = operation
    ci.isKeepToolBodies = keep_tool
    return root.features.combineFeatures.add(ci)


def _combine_cut(root, target, tools, keep_tool, preview=False):
    """Combine(Cut) ``target`` against ``tools`` (a list of bodies)."""
    return _combine(root, target, tools,
                    adsk.fusion.FeatureOperations.CutFeatureOperation,
                    keep_tool, preview)


def _draw_rect(sketch, center, e1, e2, r1, r2):
    """Draw an axis-aligned-in-(e1,e2) rectangle of half-sizes r1,r2 on a sketch.

    ``center`` is a model-space point; the rectangle lies in the sketch plane
    spanned by the model directions ``e1``/``e2``.  Points are pushed through
    the sketch's inverse transform (model -> sheet), as in :func:`_draw_section`.
    """
    to_sheet = sketch.transform.copy()
    to_sheet.invert()

    def sheet(p):
        q = adsk.core.Point3D.create(*p)
        q.transformBy(to_sheet)
        return q

    def corner(s1, s2):
        return (center[0] + s1 * r1 * e1[0] + s2 * r2 * e2[0],
                center[1] + s1 * r1 * e1[1] + s2 * r2 * e2[1],
                center[2] + s1 * r1 * e1[2] + s2 * r2 * e2[2])

    pts = [corner(-1, -1), corner(1, -1), corner(1, 1), corner(-1, 1)]
    for j in range(4):
        sketch.sketchCurves.sketchLines.addByTwoPoints(
            sheet(pts[j]), sheet(pts[(j + 1) % 4]))


def _box_cutter(root, region, preview=False):
    """Build a finite box solid filling a joint region (NewBody extrude).

    The box's faces are normal to the region axes; it is the disposable cutter
    for a bounded joint cut.  Returns ``(body, feature, sketch, plane)``.
    """
    c = region['center']
    d, e1, e2 = region['axes']
    ha, hb, hc = region['half']
    # A construction plane through the box centre spanned by e1,e2 (normal d).
    plane, psk = _miter_plane(root, c, d)
    sk = root.sketches.add(plane)
    sk.name = 'WeldCutter'
    _draw_rect(sk, c, e1, e2, hb, hc)
    if sk.profiles.count == 0:
        sk.deleteMe()
        psk.deleteMe()
        plane.deleteMe()
        return None
    ei = root.features.extrudeFeatures.createInput(
        sk.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    # Symmetric about the profile plane (which sits at the box centre), so the
    # box spans c +/- ha along d regardless of the plane normal's sign.
    ei.setDistanceExtent(True, adsk.core.ValueInput.createByReal(2.0 * ha))
    feat = root.features.extrudeFeatures.add(ei)
    body = feat.bodies.item(0) if feat.bodies.count else None
    if preview and body is not None:
        try:
            body.opacity = PREVIEW_OPACITY
        except Exception:
            pass
    return body, feat, sk, (plane, psk)


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


def _split_miter(root, body, V, normal, test_pt):
    """Trim a member to a miter plane with Split Body + Remove (no boolean).

    A construction plane through the vertex ``V`` with the bisector ``normal``
    splits ``body`` into the kept half (the member's own side) and a waste
    sliver beyond the miter face.  The waste is dropped with a ``Remove``
    feature -- reversible (deleting it restores the body) and it leaves the
    parametric flow intact, unlike deleting the body outright.  The kept half
    is whichever piece still contains ``test_pt`` (a point deep inside the
    member's own run), so no normal-sign logic is needed and the diagonal face
    lands exactly on the shared bisector plane -- coincident with the
    neighbour's face for any rotation or Position offset.

    Returns ``(created, kept)``: the objects to track for preview teardown in
    delete order (Remove, then Split, then the plane and its helper sketch), and
    the member's surviving body -- the half that still contains ``test_pt``.
    The caller writes ``kept`` back into its body map so a later cut on the same
    member (its other end) targets the re-homed body instead of the stale one.
    """
    plane, sk = _miter_plane(root, V, normal)
    sbf = root.features.splitBodyFeatures.add(
        root.features.splitBodyFeatures.createInput(body, plane, True))
    # The real API returns a SplitBodyFeature whose ``bodies`` are the two
    # halves; fall back to enumerating every body if it hands back None.
    halves = sbf.bodies if sbf is not None else _all_bodies(root)
    inside = adsk.fusion.PointContainment.PointInsidePointContainment
    probe = adsk.core.Point3D.create(*test_pt)
    waste = kept = None
    for i in range(halves.count):
        b = halves.item(i)
        try:
            if b.pointContainment(probe) != inside:
                waste = b
            else:
                kept = b
        except Exception:
            continue
    if kept is None:
        kept = body          # no Inside half reported: assume the original
    created = []
    if waste is not None:
        rm = root.features.removeFeatures.add(waste)
        if rm is not None:
            created.append(rm)
    if sbf is not None:
        created.append(sbf)
    # The plane and its two long helper lines are live inputs to the split, so
    # hide them (light bulb off) rather than delete them, and track them for
    # teardown.
    for helper in (sk, plane):
        try:
            helper.isLightBulbOn = False
        except Exception:
            pass
    created.extend([sk, plane])
    return created, kept


def _miter_cutter(root, V, nrm, perp, depth, preview=False):
    """Build the finite wedge cutter for a miter: a prism on the bisector plane.

    A construction plane through the vertex ``V`` with the bisector ``nrm``
    carries a rectangle (centred on V, in-plane half-size ``perp`` so it covers
    both members' sections); extruding it ``depth`` to the waste side of the
    plane yields a solid whose ``+nrm`` face lies exactly ON the miter face.
    Combine(Cut)-ing a member with it produces the flat diagonal miter face with
    no Split Body and no pointContainment guess.  The extrude direction is
    chosen from the plane's actual normal sign so the prism grows toward the
    waste (the ``-nrm`` side, away from the kept member).

    Returns ``(body, [combine-trackables])`` or ``(None, [])``.
    """
    plane, psk = _miter_plane(root, V, nrm)
    sk = root.sketches.add(plane)
    sk.name = 'WeldMiterCutter'
    # In-plane rectangle axes: any orthonormal pair perpendicular to nrm.
    d, e1, e2 = jt._frame(nrm)
    _draw_rect(sk, V, e1, e2, perp, perp)
    if sk.profiles.count == 0:
        sk.deleteMe()
        psk.deleteMe()
        plane.deleteMe()
        return None, []
    ei = root.features.extrudeFeatures.createInput(
        sk.profiles.item(0), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    ei.startExtent = adsk.fusion.ProfilePlaneStartDefinition.create()
    # Grow toward the waste (-nrm).  PositiveExtentDirection follows the plane
    # normal; flip when the plane normal points the same way as nrm.
    try:
        pn = plane.geometry.normal
        same = jt._dot((pn.x, pn.y, pn.z), nrm) >= 0.0
    except Exception:
        same = True
    direction = (adsk.fusion.ExtentDirections.NegativeExtentDirection if same
                 else adsk.fusion.ExtentDirections.PositiveExtentDirection)
    ei.setOneSideExtent(
        adsk.fusion.DistanceExtentDefinition.create(
            adsk.core.ValueInput.createByReal(depth)), direction)
    feat = root.features.extrudeFeatures.add(ei)
    body = feat.bodies.item(0) if feat.bodies.count else None
    if preview and body is not None:
        try:
            body.opacity = PREVIEW_OPACITY
        except Exception:
            pass
    # Track: the combine (drops the cutter body) then the extrude + helpers.
    return body, [feat, sk, plane, psk]


def _survivor_after_cut(comb, keep_body, region):
    """The member's main run in a combine output: the largest non-tool body.

    A cope/saddle cut fragments the member inside the joint box; the surviving
    run always carries the bulk of the member's volume, so the largest body that
    is not the kept tool is the run.  (Classifying by box containment is wrong on
    an angled T, where a tilted bore leaves a plug whose bbox overhangs the box.)
    Returns None when it cannot be determined, so the caller keeps its previous
    reference.
    """
    best, best_v = None, None
    try:
        bodies = comb.bodies
    except Exception:
        return None
    for k in range(bodies.count):
        try:
            b = bodies.item(k)
        except Exception:
            continue
        if keep_body is not None and b is keep_body:
            continue
        try:
            v = b.volume
        except Exception:
            v = 0.0
        if best_v is None or v > best_v:
            best, best_v = b, v
    return best


def _apply_corner_cuts(root, lines, joints, objs, feat_idx, f_start,
                       saddle=None, through=None, context=None,
                       geoms=None, bases=None, anchor_by_line=None,
                       cope_depth_by_line=None, clr_by_line=None,
                       spec_out=None, bodies_out=None):
    """Shape member ends with real geometry using bounded cutter solids.

    Every joint is realised by a FINITE cutter confined to a joint box, so
    material outside the box can never be touched -- no infinite-plane Split
    Body and no pointContainment guess (the two sources of the wrong-body and
    NOT_INTERSECT regressions).  The plan comes from
    :func:`lib.joints.joint_spec`, the single source of truth:

    * ``cutter.type == 'plane'`` (miter): a prism on the bisector plane through
      the vertex (see :func:`_miter_cutter`), Combine(Cut)-ed against the
      member and dropped.
    * ``cutter.type == 'body'`` (cope / saddled butt): the neighbour's body
      bounded to the joint box (Intersect), Combine(Cut)-ed against the member;
      fragments wholly inside the box are Removed as cutoffs.
    * ``cutter is None`` (plain butt): a pure axial trim already applied by the
      caller's offsets -- nothing to cut.

    ``objs[i]`` is the ``(feature, sketch, plane)`` tuple built for line ``i``
    (or None).  ``context`` (optional) is the list of existing members from
    :func:`_recover_existing_members`; a negative tool index names one, whose
    recovered ``body`` is the boolean tool directly.  ``geoms``/``bases``/
    ``anchor_by_line``/``cope_depth_by_line``/``clr_by_line`` feed joint_spec
    (see :func:`lib.joints.joint_spec`).

    Returns the objects to track for preview cleanup, in delete order: each
    Remove/Combine/cutter feature first (removing it restores the member body),
    then the helper sketches/planes they consumed.  The caller deletes all of
    these BEFORE the member features.  ``spec_out`` (optional list) receives the
    computed ``occs`` so the committed build can record them as joint records
    (see :func:`record_joints`); the preview path leaves it unset.
    ``bodies_out`` (optional dict) receives each line index's FINAL body after
    all cuts (see :func:`stamp_body`): a Combine re-homes a member's body to the
    cut feature, so the pre-cut ``objs[i][0].bodies`` reference is stale once a
    member has been coped/mitered -- stamping the survivor keeps the attribute
    spine intact across the cut (A4).
    """
    created = []
    ctx = context or []
    try:
        spec = jt.joint_spec(lines, geoms or [None] * len(lines), joints,
                             clr_by_line=clr_by_line, through_by_line=through,
                             saddle_by_line=saddle,
                             cope_depth_by_line=cope_depth_by_line,
                             bases=bases, context=ctx,
                             anchor_by_line=anchor_by_line)
        occs = spec['occs']
        if spec_out is not None:
            spec_out.extend(occs)
    except Exception:
        futil.handle_error(f'{CMD_NAME} joint spec')
        return created
    # Note any cope that had to fall back to a butt because its tip landed on a
    # bend's curved arc (a straight cutter cannot match a swept radius). The
    # execute path reads/resets this once to warn the user; the preview path
    # leaves it (it is reset before the committed build).
    global _arc_downgrades
    _arc_downgrades += sum(1 for o in occs if o.get('on_arc'))
    # Track each member's CURRENT body by reference: a cut re-homes body
    # identity, so the map is written back after every cut.  This is the state
    # the geometric probe kept guessing wrong when members touch.
    body_of = {}
    for i, o in enumerate(objs):
        if o is not None:
            try:
                body_of[i] = o[0].bodies.item(0)
            except Exception:
                body_of[i] = None
    for occ in occs:
        cut = occ.get('cutter')
        if cut is None:
            continue
        m = occ['member']
        if m >= len(objs) or objs[m] is None:
            continue
        body = body_of.get(m)
        if body is None:
            continue
        region = cut['region']
        try:
            if cut['type'] == 'plane':
                # Miter: a finite prism on the bisector plane, cut and dropped.
                perp = max(region['half'][1], region['half'][2])
                depth = 2.0 * region['half'][0] + perp
                cutter, track = _miter_cutter(root, occ['vertex'],
                                              cut['normal'], perp, depth)
                if cutter is None:
                    continue
                comb = _combine_cut(root, body, [cutter], keep_tool=False)
                created = [comb] + track + created
                body_of[m] = _survivor_after_cut(comb, None, region) or body
                continue
            # Body cut (cope / saddled butt): combine the member against the
            # neighbour's body (keep-tool), saddling it to the through member.
            # The member's tip only overlaps the tool near the joint, so the cut
            # is inherently local; the joint box is used only to CLASSIFY the
            # result -- a fragment wholly inside it is a cutoff (the plug pushed
            # into the tool's void), one that pokes out is the surviving run.
            t = cut['tool']
            if t < 0:
                if ~t >= len(ctx) or ctx[~t].get('body') is None:
                    continue
                tool_body = ctx[~t]['body']
            else:
                if t >= len(objs) or objs[t] is None:
                    continue
                tool_body = body_of.get(t)
            if tool_body is None:
                continue
            removes, run = cope_body_cut(root, body, tool_body, region)
            created = removes + created
            body_of[m] = run or body
        except Exception:
            futil.handle_error(f'{CMD_NAME} corner cut')
    if bodies_out is not None:
        bodies_out.update(body_of)
    # Cut features and helpers FIRST (delete order), then the rest.
    return created


def cope_body_cut(root, body, tool_body, region):
    """The single cope/saddle cut the auto path performs -- the reusable core.

    Combine(Cut) ``body`` against ``tool_body`` (keep-tool, so the neighbour it
    saddles onto survives) and Remove the cutoff fragments wholly inside the
    joint box (the plug pushed into the tube's void).  This is exactly what
    :func:`_apply_corner_cuts` does for a ``cutter.type == 'body'`` occurrence;
    factored out so the standalone Cope toolbar command runs the SAME code the
    auto path does (rather than reimplementing the tip logic and drifting from
    it).  ``body`` is already at auto's build-time length (trimmed to
    ``-trim + wall + depth``), so no extra axial trim is needed -- applying one
    double-trims the tip and pulls it off the wall.

    Returns ``(removes, run)``: the Remove/Combine features created (in delete
    order -- Removes before the Combine so teardown restores the whole body)
    and the member's surviving run body.
    """
    comb = _combine_cut(root, body, [tool_body], keep_tool=True)
    removes = _remove_inside_region(root, comb, tool_body, region)
    run = _survivor_after_cut(comb, tool_body, region)
    return removes + [comb], run


def _combine_survivor(comb, tool_body):
    """The member's main-run body in a combine's output (largest non-tool piece).

    After a cope/saddle cut the combine holds the kept tool plus the member's
    fragments; the plug(s) have already been Removed, so the largest body that is
    not the tool is the member's surviving run.  Returns None when it cannot be
    determined, so the caller keeps its previous reference.
    """
    best, best_v = None, None
    try:
        for k in range(comb.bodies.count):
            b = comb.bodies.item(k)
            if tool_body is not None and b == tool_body:
                continue
            try:
                v = b.volume
            except Exception:
                v = 0.0
            if best_v is None or v > best_v:
                best, best_v = b, v
    except Exception:
        return None
    return best


def _draw_model_line(sketch, p_from, p_to):
    """Add a sketch line between two model-space points (cm tuples)."""
    to_sheet = sketch.transform.copy()
    to_sheet.invert()
    a = adsk.core.Point3D.create(*p_from)
    a.transformBy(to_sheet)
    b = adsk.core.Point3D.create(*p_to)
    b.transformBy(to_sheet)
    return sketch.sketchCurves.sketchLines.addByTwoPoints(a, b)


def _draw_model_arc(sketch, center, p_start, p_end, normal=None):
    """Add a sketch arc (center + two endpoints, all model-space cm tuples).

    The arc lies in the plane through ``center`` with the given ``normal`` and
    sweeps counter-clockwise (right-hand rule around ``normal``) from ``p_start``
    to ``p_end``.  Pass ``normal`` = normalize((start-center) x (end-center)) to
    force the MINOR arc from start to end; omitting it lets Fusion use the
    sketch's own plane normal, which can pick the 270-degree major arc.  Points
    are mapped model -> sheet like :func:`_draw_model_line`.  Returns the new
    SketchArc.
    """
    to_sheet = sketch.transform.copy()
    to_sheet.invert()

    def sp(p):
        q = adsk.core.Point3D.create(*p)
        q.transformBy(to_sheet)
        return q

    if normal is not None:
        nrm = adsk.core.Vector3D.create(*normal)
        nrm.transformBy(to_sheet)   # keep the normal in the same sheet frame
        return sketch.sketchCurves.sketchArcs.addByCenterStartEnd(
            sp(center), sp(p_start), sp(p_end), nrm)
    return sketch.sketchCurves.sketchArcs.addByCenterStartEnd(
        sp(center), sp(p_start), sp(p_end))


def _build_bend_arc_sweep(root, leg_line, tangent, plan, geom, ref,
                          preview=False, basis=None, anchor=None):
    """Build one swept-bend body by sweeping the section along a die-radius arc.

    This is the physically-correct bend: the tube's cross-section is *swept*
    along the centerline arc the bending die produces, exactly as a press brake
    or roll bender deforms the member.  The arc (radius ``plan['radius_cm']``,
    from tangent ``t1`` to ``t2`` about centre ``center``) lies in the bend plane
    (normal ``plan['axis']``); the section sits at ``t1`` on a plane normal to
    the leg, so it starts perpendicular to the path and stays so along the arc.

    Returns a flat tuple of teardown objects ``(feature, path_sketch, path_plane,
    profile_sketch, profile_plane)`` on success, or ``None`` when the sweep
    cannot be built (the caller then falls back to :func:`_build_bend_arc`).
    ``basis``/``anchor``/``preview`` mirror :func:`_build_bend_arc`.
    """
    try:
        t2 = plan['tangent'][1][2]
        center = plan['center']
        axis = plan['axis']
        leg_dir = _line_direction(leg_line)

        # Off-centre leg (Position grid): shift the whole bend rigidly, exactly
        # as the revolve builder does, so the swept arc tracks the displaced run.
        if anchor:
            axis_u0, axis_v0 = basis if basis is not None \
                else prof.compute_basis(leg_dir, ref)
            disp = prof.displace_origin((0.0, 0.0, 0.0), axis_u0, axis_v0, anchor)
            tangent = jt._add(tangent, disp)
            t2 = jt._add(t2, disp)
            center = jt._add(center, disp)

        # --- path: an arc in the bend plane, t1 -> t2 about centre C ---------
        path_plane, path_helper = _miter_plane(root, center, axis)
        path_sketch = root.sketches.add(path_plane)
        path_sketch.name = 'WeldBendPath'
        # Force the MINOR arc: the normal (t1-C) x (t2-C) makes the arc sweep
        # counter-clockwise (the short way, < 180 deg) from t1 to t2.  Without
        # it Fusion uses the sketch plane's own normal, whose sign is arbitrary,
        # and can build the 270-degree major arc instead.
        arc_nrm = prof._norm(prof._cross(
            prof._sub(tangent, center), prof._sub(t2, center)))
        _draw_model_arc(path_sketch, center, tangent, t2, normal=arc_nrm)
        if path_sketch.sketchCurves.sketchArcs.count == 0:
            for o in (path_sketch, path_helper, path_plane):
                try:
                    o.deleteMe()
                except Exception:
                    pass
            return None
        path = adsk.fusion.Path.create(
            path_sketch.sketchCurves.sketchArcs.item(0),
            adsk.fusion.ChainedCurveOptions.noChainedCurves)

        # --- profile: the section at t1, on a plane normal to the leg --------
        world = leg_line.worldGeometry
        start_pt = world.startPoint
        off = prof._dot(prof._sub(tangent,
                                  (start_pt.x, start_pt.y, start_pt.z)), leg_dir)
        leg_path = adsk.fusion.Path.create(
            leg_line, adsk.fusion.ChainedCurveOptions.noChainedCurves)
        cp_input = root.constructionPlanes.createInput()
        cp_input.setByPath(
            leg_path, adsk.fusion.PathDistanceTypes.PhysicalPathDistanceType,
            adsk.core.ValueInput.createByReal(off))
        prof_plane = root.constructionPlanes.add(cp_input)

        axis_u, axis_v = basis if basis is not None \
            else prof.compute_basis(leg_dir, ref)
        prof_sketch = root.sketches.add(prof_plane)
        prof_sketch.name = 'WeldBendProfile'
        _draw_section(prof_sketch, tangent, axis_u, axis_v, geom)
        if prof_sketch.profiles.count == 0:
            for o in (prof_sketch, prof_plane, path_sketch, path_helper,
                      path_plane):
                try:
                    o.deleteMe()
                except Exception:
                    pass
            return None

        sweep_input = root.features.sweepFeatures.createInput(
            prof_sketch.profiles.item(0), path,
            adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        try:
            sweep_input.orientation = \
                adsk.fusion.SweepOrientationTypes.PerpendicularOrientationType
            sweep_input.profileScaling = \
                adsk.fusion.SweepProfileScalingOptions.SweepProfileNoScalingOption
        except Exception:
            pass
        feature = root.features.sweepFeatures.add(sweep_input)

        if preview:
            try:
                for bi in range(feature.bodies.count):
                    feature.bodies.item(bi).opacity = PREVIEW_OPACITY
            except Exception:
                pass
        return (feature, path_sketch, path_helper, path_plane,
                prof_sketch, prof_plane)
    except Exception:
        futil.handle_error(f'{CMD_NAME} build bend sweep')
        return None


def _build_bend_arc(root, leg_line, tangent, plan, geom, ref, preview=False,
                    basis=None, anchor=None):
    """Build one swept-bend arc body by revolving the section about the bend axis.

    ``plan`` is a dict from :func:`lib.joints.bend_plan` (center, axis, theta);
    ``tangent`` is this leg's tangent point (cm) where the arc meets the leg.
    The section is placed at the tangent point T on a plane normal to the leg,
    then revolved by ``theta`` about the bend axis (which lies in that plane,
    through the arc centre C, at distance R from T).  ``basis`` (optional) is the
    leg's flat-in-the-bend-plane section basis (see :func:`_bend_bases`) so the
    arc sweeps from a face the die bears on; when omitted the normal
    ``compute_basis(leg_dir, ref)`` is used.  ``anchor`` (optional, local
    ``(u, v)`` mm) places the leg off the reference line for the Position grid:
    the whole arc (section AND revolve axis) is shifted by the leg's centroid
    displacement so the swept bend follows the displaced member.  Returns
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

        axis_u, axis_v = basis if basis is not None \
            else prof.compute_basis(leg_dir, ref)
        # Off-centre leg (Position grid): shift the tangent point AND the arc
        # centre by the same centroid displacement so the swept arc tracks the
        # displaced member (a rigid translation of the whole bend).
        if anchor:
            disp = prof.displace_origin((0.0, 0.0, 0.0), axis_u, axis_v, anchor)
            tangent = jt._add(tangent, disp)
            center = jt._add(center, disp)
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
                    ref=None, offset_start=0.0, offset_end=0.0, preview=False,
                    basis=None, anchor=None):
    """Build one weldment body along ``line`` using cross-section ``geom``.

    ``angle_rad`` rotates the profile about the selected line (its own axis),
    turning it around its centre point without tilting about any other axis.
    ``ref`` is the shared "up" reference for the selection so that profiles on
    differently-oriented lines stay rolled consistently and their ends align.
    ``basis`` (optional) overrides ``ref`` with a bend leg's flat-in-the-bend-plane
    section basis (see :func:`_bend_bases`); when given, the profile starts from it
    and only ``angle_rad`` is applied on top.
    ``offset_start``/``offset_end`` are signed distances (cm) along the line:
    the profile is created ``offset_start`` from the line's start and the body
    extends to ``line.length + offset_end`` from that same start, so both 0
    gives the full line length.
    ``anchor`` (optional, local ``(u, v)`` mm) is the section point that must sit
    ON the picked line for the Position alignment grid (see
    :func:`profiles.grid_anchor`); the centroid is displaced so that point lands
    on the line.  None / ``(0, 0)`` centres the section on the line (default).

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
        if basis is not None:
            # A bend leg's flat-in-the-bend-plane basis (see _bend_bases): a
            # face is already aligned with the bend plane, so skip the global-
            # reference basis and only apply the user's own Rotation on top.
            axis_u, axis_v = basis
        else:
            axis_u, axis_v = prof.compute_basis(direction, ref)
        # Spin the profile about the line axis (its own centre point).
        axis_u, axis_v = prof.rotate_basis(axis_u, axis_v, angle_rad)
        # Off-centre placement (the Position grid): slide the centroid so the
        # chosen section point lands on the picked line.  The anchor is in the
        # section's own local frame, so it is applied against the rotated basis.
        if anchor:
            origin_t = prof.displace_origin(origin_t, axis_u, axis_v, anchor)

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
