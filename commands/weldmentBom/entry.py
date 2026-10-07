"""Weldment BOM palette (Phase 4b) -- the registry surfaced as a read-only list.

Shows every member (body name, cut length, designation) and joint (kind, members,
parameters) stored in the design's registry (see :mod:`lib.registry`). The panel
is the frame's *bill of materials*: a list to read and export from, not an
editor -- the tools (builder, cope, butt, miter, bend) are the write path, and
secondary data (bend charts, cut lists) is generated from these records.

The palette is a docked HTML panel. Python pushes the BOM as JSON via
``sendInfoToHTML('render', ...)``; the JS posts Refresh/Rebuild back via
``adsk.fusionSendData(action, json)``, handled in :func:`palette_incoming`.

Member rows carry the *live body name* when resolvable: the command layer maps
each registry member to its BRepBody through the attribute stamp (see
``weldment.stamp_body``), so the BOM reads in the names the user sees in the
browser tree instead of blank record names.
"""

import json
import os

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_bom'
CMD_NAME = 'Weldment BOM'
CMD_Description = 'List the weldment members and joints (read-only BOM)'
ICON_FOLDER = config.icon_folder(__file__)
PALETTE_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_bom_palette'
PALETTE_NAME = 'Weldment BOM'

WORKSPACE_ID = 'FusionSolidEnvironment'
# The button lives in the Weldments panel on the dedicated Weldments tab; the
# panel is created/resolved through weldment.ensure_weldments_panel().
PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_panel'

PALETTE_URL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'resources', 'html', 'index.html').replace('\\', '/')
PALETTE_DOCKING = adsk.core.PaletteDockingStates.PaletteDockStateRight

local_handlers = []


def _design():
    return adsk.fusion.Design.cast(app.activeProduct)


def start():
    cmd_def = ui.commandDefinitions.addButtonDefinition(
        CMD_ID, CMD_NAME, CMD_Description, ICON_FOLDER)
    futil.add_handler(cmd_def.commandCreated, command_created)

    # The BOM lives on the Data panel of the Weldments tab; ensure_data_panel
    # creates the tab/panels if weldment's start() has not run yet, so the
    # button is never silently dropped (the bug that lost the buttons).
    from ..weldment import entry as weldment
    panel = weldment.ensure_data_panel()
    weldment.add_pinned_command(panel, cmd_def, CMD_ID)


def stop():
    from ..weldment import entry as weldment
    weldment.remove_command_from_panel(CMD_ID)
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def:
        cmd_def.deleteMe()
    palette = ui.palettes.itemById(PALETTE_ID)
    if palette:
        palette.deleteMe()


def command_created(args: adsk.core.CommandCreatedEventArgs):
    futil.add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy,
                      local_handlers=local_handlers)


def command_execute(args: adsk.core.CommandEventArgs):
    palette = _ensure_palette()
    if palette is None:
        return
    palette.isVisible = True
    _push_bom(palette)


def _ensure_palette():
    palettes = ui.palettes
    palette = palettes.itemById(PALETTE_ID)
    if palette is None:
        palette = palettes.add(
            id=PALETTE_ID, name=PALETTE_NAME, htmlFileURL=PALETTE_URL,
            isVisible=False, showCloseButton=True, isResizable=True,
            width=420, height=520, useNewWebBrowser=True)
        # Register the HTML->Python handler on the *global* handler list, not
        # this command's local_handlers: the button command has no inputs, so
        # it is destroyed (and would clear local_handlers, GC'ing the handler)
        # the instant it runs. The palette outlives the command, so its handler
        # must too -- otherwise Refresh and every edit silently do nothing.
        futil.add_handler(palette.incomingFromHTML, palette_incoming)
    if palette.dockingState == adsk.core.PaletteDockingStates.PaletteDockStateFloating:
        palette.dockingState = PALETTE_DOCKING
    return palette


def _push_bom(palette):
    """Send the current registry as the BOM view model to the HTML."""
    design = _design()
    if design is None:
        palette.sendInfoToHTML('render', json.dumps({'error': 'No active design.'}))
        return
    try:
        summary = load_registry(design).summary(body_names=_body_names(design))
    except Exception:
        futil.handle_error(f'{CMD_NAME} summary')
        summary = {'members': [], 'joints': []}
    palette.sendInfoToHTML('render', json.dumps(summary))


def _body_names(design):
    """Map member id -> live body name, via the attribute stamp on each body.

    One body per member wins when a cut left several stamped bodies (the
    timeline order is stable, so the name shown is reproducible). Bodies that
    carry no stamp (waste fragments, plain extrudes) are skipped.
    """
    from ..weldment import entry as weldment
    names = {}
    try:
        bodies = design.rootComponent.bRepBodies
    except Exception:
        return names
    for k in range(bodies.count):
        try:
            body = bodies.item(k)
            mid = weldment.body_mid(body)
        except Exception:
            continue
        if mid is not None and mid not in names:
            names[mid] = body.name
    return names


def load_registry(design):
    """The design's registry (delegates to the weldment command's loader)."""
    from ..weldment import entry as weldment
    return weldment.load_registry(design)


def palette_incoming(html_args: adsk.core.HTMLEventArgs):
    action = html_args.action
    design = _design()
    if design is None:
        html_args.returnData = 'No active design.'
        return
    if action == 'refresh':
        _push_bom(html_args.firingEvent.sender)
        html_args.returnData = 'OK'
        return
    if action == 'rebuild':
        from ..weldment import entry as weldment
        try:
            n = weldment.rebuild_from_registry(design)
        except Exception:
            futil.handle_error(f'{CMD_NAME} rebuild')
            html_args.returnData = 'Rebuild failed (see log).'
            return
        _push_bom(html_args.firingEvent.sender)
        html_args.returnData = f'Rebuilt {n} member(s).'
        return
    # The panel is read-only: it lists the records the tools wrote, and the
    # tools are the only write path. An "edit" postback is a stale panel (or a
    # hand-modified HTML); answer without touching the registry.
    html_args.returnData = 'The BOM is read-only; edit with the weldment tools.'


def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers
    local_handlers = []
