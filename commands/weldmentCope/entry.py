"""Cope tool (Phase 4c) -- DISABLED / not functional.

The explicit-selection toolbar cope has not yet reproduced the automatic
engine's two-step result (offset construction plane -> Split -> boolean ->
Remove), so it is temporarily disabled: the toolbar button now only shows a
"not functional" message.  The working cope remains the automatic weldment
command.  This file keeps just the command registration so the button exists
and can be re-implemented later without touching the panel wiring.
"""

import os

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_cope'
CMD_NAME = 'Weld Cope'
CMD_Description = 'Cope one weldment member onto another (explicit selection)'
ICON_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           'resources', '')

WORKSPACE_ID = 'FusionSolidEnvironment'
# Shared Weldments panel on the dedicated Weldments tab (see
# weldment.ensure_weldments_panel).
PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_panel'

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
    # Shared Weldments tab/panel; create it if needed so the button is never
    # dropped when this start() runs before weldment's.
    panel = _weldment().ensure_weldments_panel()
    if panel and panel.controls.itemById(CMD_ID) is None:
        panel.controls.addCommand(cmd_def)


def stop():
    _weldment().remove_command_from_panel(CMD_ID)
    cmd_def = ui.commandDefinitions.itemById(CMD_ID)
    if cmd_def:
        cmd_def.deleteMe()
    global local_handlers
    local_handlers = []


def command_created(args: adsk.core.CommandCreatedEventArgs):
    # No dialog and no inputs: with an empty command Fusion fires `execute`
    # immediately, so the popup shows as soon as the button is pressed.
    futil.add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)


def command_execute(args: adsk.core.CommandEventArgs):
    ui.messageBox(
        'The toolbar Cope tool is not functional yet.\n\n'
        'Please use the automatic Weldment command to create copes.',
        CMD_NAME)
