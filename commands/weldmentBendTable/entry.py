"""Bend Table tool -- rotary-draw bending instructions for a bent tube.

Where the BOM lists *what* is in the frame, this lists *how to bend one tube*:
for each bend, in order from a chosen datum end, where to scribe the two
tangent marks on the straight tube, how far to feed it through the die, the
geometric bend angle, and how far to rotate (clock) the tube about its own
axis for the next bend.  The math lives in :mod:`lib.bend_sequence` (pure,
unit-tested); this command is the thin glue that turns a live selection into
that module's :func:`lib.bend_sequence.plan` inputs (member records + joints)
and renders the rows.

The user picks ONE leg of the tube.  The rest of the tube is found by
following the ``bend`` joints, each of which names both legs it joins, so
there is nothing to select in order and nothing to select at all beyond the
one body.  The datum -- where the machine starts reading -- is the free end of
the tube nearest the point they clicked, because a rotary bender always feeds
from an open end.  A single ``Reverse`` checkbox covers the case where they
clicked the end they did not mean, which is cheaper than re-picking.

Springback is deliberately out of scope -- the angle is the geometric turn; the
operator compensates on the machine.

The results table is read-only (a header row of disabled text boxes plus one
row per bend, the same Fusion table idiom the auto command uses).  OK writes
the table to a CSV next to the design (or the documents folder) so the shop
has a paper instruction sheet.
"""

import csv
import os

import adsk.core
import adsk.fusion

from ...lib import bend_sequence as bseq
from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_bend_table'
CMD_NAME = 'Bend Table'
CMD_Description = 'Bending instructions (marks, feed, angle, clock) for a tube'
ICON_FOLDER = config.icon_folder(__file__)

WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_panel'

# Column titles for the read-only header row (row 0) of the results table.
_HEADERS = ['Step', 'After', 'Before', 'Start mark', 'End mark', 'Feed',
            'Angle', 'Clock', 'Die']

local_handlers = []

# The last table computed, kept only so OK can write it to CSV.  Every rebuild
# recomputes it from the live pick, so nothing here caches selection state.
_pick_state = {'rows': [], 'developed': 0.0, 'complete': True}


def _design():
    return adsk.fusion.Design.cast(app.activeProduct)


def _weldment():
    from ..weldment import entry as weldment
    return weldment


def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)
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
    global _pick_state
    _pick_state = {'rows': [], 'developed': 0.0, 'complete': True}
    inputs = args.command.commandInputs

    sel = inputs.addSelectionInput('tube_leg', 'Tube leg',
                                   'Pick any one straight leg of the tube')
    sel.addSelectionFilter('SolidBodies')
    # Minimum one; extra picks are harmless (the chain is discovered from the
    # first one), and a hard maximum would reject a tube pre-selected in the
    # canvas before the dialog opened.
    sel.setSelectionLimits(1, 0)

    inputs.addBoolValueInput('reverse', 'Read from the other end', True,
                             '', False)

    # Read-only results table: one column per header, sized so the numeric
    # columns are wide enough to read without scrolling.  Row 0 is a persistent
    # header of disabled text boxes (Fusion tables have no header API), so data
    # rows live at row r+1 -- the same idiom the auto command uses.
    tbl = inputs.addTableCommandInput('table', 'Bend instructions',
                                      len(_HEADERS), '1:1:1:2:2:2:2:2:3')
    tbl.maximumVisibleRows = 12
    _ensure_header(inputs, tbl)

    futil.add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.activate, command_activate,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy,
                      local_handlers=local_handlers)


def command_activate(args: adsk.core.CommandEventArgs):
    """Populate the dialog from a selection made *before* it opened.

    Fusion does not fire ``inputChanged`` for the initial body a command
    inherits from the canvas, so without this a pre-picked tube would leave
    the table empty until the user re-picked.  ``activate`` is the first event
    that runs once the inputs (and their inherited selection) exist.
    """
    _try_rebuild(args.command.commandInputs)


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    """Rebuild whenever the leg pick or the Reverse checkbox changes."""
    _try_rebuild(args.inputs)


def _try_rebuild(inputs):
    """Rebuild the table, surfacing any failure into the status box.

    add_handler swallows exceptions into the log only, so a broken pick would
    otherwise show an empty dialog; the traceback in the status box is the
    difference between a bug report and a guess.
    """
    try:
        _rebuild_table(inputs)
    except Exception:
        import traceback
        _set_status(inputs, 'Bend Table error:\n' + traceback.format_exc())


def _resolve_member(w, registry, body):
    """The Member record behind a picked body, or None.

    Stamps first (the reliable link), then centreline proximity -- the same
    two-step the Bend command uses -- because a leg trimmed by a swept bend
    can lose its attribute stamp while still sitting on its member line.
    """
    m = w.resolve_member(registry, body)
    if m is not None:
        return m
    cl = w._member_centerline(body)
    if cl is None:
        return None
    mid = tuple((cl[0][i] + cl[1][i]) / 2.0 for i in range(3))
    return registry.member_near_point(mid, tol=0.5)


def _pick(inputs):
    """``(member, click_point)`` for the leg chosen, or ``(None, None)``.

    Scans the selection for the first body that resolves to a member -- if the
    whole tube was pre-selected, index 0 may be a bend sweep (a torus with no
    member record), and the click point of a real leg is what we need.  The
    click point is the 3D position the user selected at, which fixes the datum
    end without asking.  It is None when Fusion reports no point (a body picked
    from the browser rather than the canvas), in which case the planner takes
    the tube's lowest-numbered free end.
    """
    w = _weldment()
    sel = inputs.itemById('tube_leg')
    registry = w.load_registry(_design())
    for i in range(sel.selectionCount):
        try:
            s = sel.selection(i)
            ent = s.entity
        except Exception:
            continue
        if not isinstance(ent, adsk.fusion.BRepBody):
            continue
        m = _resolve_member(w, registry, ent)
        if m is None:
            continue
        try:
            p = s.point
            click = (p.x, p.y, p.z) if p is not None else None
        except Exception:
            click = None
        return m, click
    return None, None


def _rebuild_table(inputs):
    """Run the planner for the current pick and repopulate the results table."""
    global _pick_state
    w = _weldment()
    tbl = inputs.itemById('table')
    _clear_table(inputs, tbl)
    member, click = _pick(inputs)
    if member is None:
        _pick_state.update(rows=[])
        _set_status(inputs, 'Pick one straight leg of a bent tube.')
        return
    registry = w.load_registry(_design())
    reverse = bool(inputs.itemById('reverse').value)
    try:
        plan = bseq.plan_from_pick(
            registry.members, registry.joints, member.mid, click=click,
            reverse=reverse, die_id=lambda j: _die_id(w, j))
    except Exception:
        futil.handle_error(f'{CMD_NAME} plan')
        _set_status(inputs, 'Could not plan this tube.')
        return
    if plan.get('error'):
        _pick_state.update(rows=[])
        _set_status(inputs, plan['error'])
        return
    _pick_state.update(rows=plan['rows'], developed=plan['developed_mm'],
                       complete=plan['complete'])
    _fill_table(inputs, tbl, plan['rows'])
    note = '' if plan['complete'] else ' (chain incomplete: a leg branches)'
    _set_status(inputs, f"Developed length {plan['developed_mm']:.1f} mm"
                        f" -- {len(plan['rows'])} bend(s)"
                        f" -- from member #{member.mid}{note}")


# --------------------------------------------------------------------------- #
# bend joint -> die label
# --------------------------------------------------------------------------- #
def _die_id(w, joint):
    """The die label recorded on a bend joint, if any (else None).

    The auto/bend commands store ``clr_mm`` on the joint; the die id is not
    always kept, so fall back to matching the radius to the family's catalogue.
    """
    params = joint.params or {}
    if params.get('die_id'):
        return params['die_id']
    bd = _bending_dies()
    if bd is None:
        return None
    clr = params.get('clr_mm')
    if not clr:
        return None
    family = _joint_family(w, joint)
    if not family:
        return None
    best = None
    for die in bd.dies_for_family(w._DIES, family):
        if abs(bd.die_clr(die) - clr) < 1e-6:
            best = die['die_id']
            break
    return best


def _bending_dies():
    try:
        from ...lib import bending_dies as bd
        return bd
    except Exception:
        return None


def _joint_family(w, joint):
    """The profile family of the joint's subject member (for die matching)."""
    registry = w.load_registry(_design())
    mid = joint.subject()
    m = registry.member(mid) if mid is not None else None
    if m is not None and m.family:
        return m.family
    if m is not None and m.geom is not None:
        from ...lib import profiles as prof
        fam, _desig = prof.profile_from_geom(m.geom)
        return fam
    return ''


# --------------------------------------------------------------------------- #
# read-only table plumbing
# --------------------------------------------------------------------------- #
def _clear_table(inputs, tbl):
    """Delete every data row (row 0 is the persistent header)."""
    while tbl.rowCount > 1:
        try:
            tbl.deleteRow(tbl.rowCount - 1)
        except Exception:
            break


def _fill_table(inputs, tbl, rows):
    """Append one read-only data row per bend, after the header row."""
    for r, row in enumerate(rows):
        cells = [str(row['step']),
                 f"#{row['after_mid']}" if row['after_mid'] is not None else '',
                 f"#{row['before_mid']}" if row['before_mid'] is not None else '',
                 f"{row['start_mark_mm']:.1f}",
                 f"{row['end_mark_mm']:.1f}",
                 f"{row['feed_mm']:.1f}",
                 f"{row['angle_deg']:.1f}",
                 f"{row['clock_deg']:+.1f}",
                 row['die_id'] or '']
        for c, text in enumerate(cells):
            box = inputs.addTextBoxCommandInput(
                f'cell_{r}_{c}', '', text, 1, True)
            box.isEnabled = False
            tbl.addCommandInput(box, r + 1, c)


def _ensure_header(inputs, tbl):
    """Row 0 of column titles, added once (idempotent across rebuilds)."""
    if tbl.rowCount >= 1 and _has_header(tbl):
        return
    for c, title in enumerate(_HEADERS):
        box = inputs.addTextBoxCommandInput(f'hdr_{c}', '', title, 1, True)
        box.isEnabled = False
        tbl.addCommandInput(box, 0, c)


def _has_header(tbl):
    try:
        return tbl.commandInputAt(0, 0) is not None
    except Exception:
        return False


def _set_status(inputs, text):
    box = inputs.itemById('status')
    if box is None:
        box = inputs.addTextBoxCommandInput('status', '', text, 2, True)
        box.isFullWidth = True
    else:
        box.text = text


# --------------------------------------------------------------------------- #
# execute: export the current table to CSV
# --------------------------------------------------------------------------- #
def command_execute(args: adsk.core.CommandEventArgs):
    rows = _pick_state.get('rows') or []
    if not rows:
        return
    try:
        path = _write_csv(rows, _pick_state)
    except Exception:
        futil.handle_error(f'{CMD_NAME} export')
        return
    ui.messageBox(f'Bend table written to\n{path}')


def _write_csv(rows, state):
    """One CSV per tube, next to the design (or in the documents folder)."""
    header = ['step', 'after_member', 'before_member', 'start_mark_mm',
              'end_mark_mm', 'feed_mm', 'angle_deg', 'clock_deg', 'die_id']
    folder = _output_folder()
    name = f'bend_table_{rows[0].get("after_mid", 0)}.csv'
    path = os.path.join(folder, name)
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow([f'# developed length {state["developed"]:.1f} mm'])
        writer.writerow(header)
        for row in rows:
            writer.writerow([row['step'], row['after_mid'], row['before_mid'],
                             f"{row['start_mark_mm']:.2f}",
                             f"{row['end_mark_mm']:.2f}",
                             f"{row['feed_mm']:.2f}",
                             f"{row['angle_deg']:.2f}",
                             f"{row['clock_deg']:.2f}",
                             row['die_id'] or ''])
    return path


def _output_folder():
    """The folder to write the CSV into: the design's, else Documents.

    ``Document.path`` is the full file path of a saved document (empty for an
    unsaved one), so its directory is where the sheet belongs.  Any failure
    (no document, cloud-only path) falls back to the user's Documents folder.
    """
    try:
        doc = app.activeDocument
        p = doc.path
        if p and os.path.isdir(os.path.dirname(p)):
            return os.path.dirname(p)
    except Exception:
        pass
    return os.path.join(os.path.expanduser('~'), 'Documents')


def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers, _pick_state
    local_handlers = []
    _pick_state = {'rows': [], 'developed': 0.0, 'complete': True}
