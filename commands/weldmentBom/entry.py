"""Weldment BOM palette (Phase 4b) -- the registry surfaced as an editable list.

Shows every member (cut length, designation) and joint (kind, params) stored in
the design's registry (see :mod:`lib.registry`), and lets the user edit a
record's settings in place. This is the read/write face of the registry: it
replaces "reopen the builder and re-detect the frame" with "edit the row".

The palette is a docked HTML panel. Python pushes the BOM as JSON via
``sendInfoToHTML('render', ...)``; the JS posts edits back via
``adsk.fusionSendData(action, json)``, handled in :func:`palette_incoming`,
which mutates the registry through the pure ``set_member``/``set_joint_*``
methods and saves it to the design attribute.
"""

import json
import os

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ...lib import registry as reg
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_bom'
CMD_NAME = 'Weldment BOM'
CMD_Description = 'List and edit the weldment members and joints'
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'resources', '')
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
    if panel and panel.controls.itemById(CMD_ID) is None:
        panel.controls.addCommand(cmd_def)


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
        summary = load_registry(design).summary()
    except Exception:
        futil.handle_error(f'{CMD_NAME} summary')
        summary = {'members': [], 'joints': []}
    palette.sendInfoToHTML('render', json.dumps(summary))


def load_registry(design):
    """The design's registry (delegates to the weldment command's loader)."""
    from ..weldment import entry as weldment
    return weldment.load_registry(design)


def save_registry(design, registry):
    from ..weldment import entry as weldment
    weldment.save_registry(design, registry)


def palette_incoming(html_args: adsk.core.HTMLEventArgs):
    action = html_args.action
    data = json.loads(html_args.data) if html_args.data else {}
    design = _design()
    if design is None:
        html_args.returnData = 'No active design.'
        return
    registry = load_registry(design)
    changed = False
    if action == 'refresh':
        _push_bom(html_args.firingEvent.sender)
        html_args.returnData = 'OK'
        return
    elif action == 'rebuild':
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
    elif action == 'editMember':
        changed = registry.set_member(data.get('mid'),
                                      designation=data.get('designation'),
                                      name=data.get('name'))
    elif action == 'editJointKind':
        changed = registry.set_joint_kind(data.get('jid'), data.get('kind'))
    elif action == 'editJointParam':
        changed = registry.set_joint_param(data.get('jid'),
                                           data.get('key'), data.get('value'))
    if changed:
        save_registry(design, registry)
        _push_bom(html_args.firingEvent.sender)
        html_args.returnData = 'OK'
    else:
        html_args.returnData = 'No change (unknown id or field).'


def command_destroy(args: adsk.core.CommandEventArgs):
    global local_handlers
    local_handlers = []
