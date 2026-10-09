"""Bend tool (Phase D) -- explicit-selection swept bend between two members.

Same toolbox pattern as :mod:`commands.weldmentCope` and
:mod:`commands.weldmentMiter`: the user picks the two straight members that meet
at a corner, and we round that corner with a die-radius bend -- no line-set
selection and no frame detection.  The geometry is the SAME code the auto
builder runs, so the two never drift:

  1. each leg is trimmed back to its tangent point (a finite waste prism on the
     leg's own axis, Combine(Cut, keep_tool=False) -- the toolbox twin of the
     auto path's build-time ``-setback`` axial trim);
  2. the tube cross-section is SWEPT along the die-radius centerline arc from
     tangent to tangent (:func:`commands.weldment.entry._build_bend_arc_sweep`,
     falling back to the revolve builder), exactly what :func:`_build_bend_arcs`
     does for a planned corner.

The arc is a separate body (as in the auto path -- no Join), so the frame reads
as two trimmed members plus one swept bend piece.  The joint is recorded in the
registry as a ``bend`` with ``params={'clr_mm': ...}`` so the BOM lists it, a
re-run edits it, and ``plan_rebuild`` reproduces it (bend propagates to BOTH
referenced ends with the same CLR).

A live translucent preview shows the trimmed legs and the swept arc before OK;
Cancel tears it down (deleting the preview features restores the originals).
"""

import os

import adsk.core
import adsk.fusion

from ...lib import fusionAddInUtils as futil
from ...lib import bending_dies as bd
from ...lib import joints as jt
from ...lib import registry as reg
from ... import config

app = adsk.core.Application.get()
ui = app.userInterface

CMD_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_weldment_bend'
CMD_NAME = 'Weld Bend'
CMD_Description = 'Sweep a die-radius bend between two weldment members'
ICON_FOLDER = config.icon_folder(__file__)

WORKSPACE_ID = 'FusionSolidEnvironment'
# Shared Weldments panel on the dedicated Weldments tab (see
# weldment.ensure_weldments_panel).
PANEL_ID = f'{config.COMPANY_NAME}_{config.ADDIN_NAME}_panel'

local_handlers = []

# Opacity used to ghost the preview bodies (matches the auto command).
PREVIEW_OPACITY = 0.4
# Objects made for the current in-dialog preview, in delete order.  Rebuilt on
# every preview and torn down when the dialog closes or the next preview starts
# (deleting them restores the members' original bodies).
_preview_objs = []
# (body, original_opacity) pairs for pre-existing bodies we ghosted; restoring
# them on teardown means a Cancel never leaves a member translucent (deleting a
# combine restores the body's GEOMETRY but not the opacity we set on it).
_preview_ghosts = []
# The two member bodies captured during the LAST preview, while both were still
# valid.  A preview's Combine consumes (re-homes) the picked bodies and Fusion
# then DROPS the selection entries for them, so on OK the fields can read empty
# even though the user picked two.  We keep the objects here (a combine delete
# restores them whole) and fall back to them in command_execute. Overwritten on
# every preview where both selections are valid; never cleared on a partial one.
_preview_bodies = (None, None)


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
    inputs = args.command.commandInputs

    a = inputs.addSelectionInput('member_a', 'Member A',
                                 'Select the first tube at the corner')
    a.addSelectionFilter('SolidBodies')
    a.setSelectionLimits(1, 1)

    b = inputs.addSelectionInput('member_b', 'Member B',
                                 'Select the second tube at the corner')
    b.addSelectionFilter('SolidBodies')
    b.setSelectionLimits(1, 1)

    # The die dropdown is filled once a member is picked (its profile family
    # decides which centerline radii exist); until then it stays empty and the
    # execute path falls back to the tightest die for the family.
    die = inputs.addDropDownCommandInput(
        'die', 'Bend Die', adsk.core.DropDownStyles.TextListDropDownStyle)
    die.listItems.add('(auto: tightest die for the family)', True)

    futil.add_handler(args.command.execute, command_execute,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.executePreview, command_execute_preview,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.inputChanged, command_input_changed,
                      local_handlers=local_handlers)
    futil.add_handler(args.command.destroy, command_destroy,
                      local_handlers=local_handlers)


def _selected_body(sel):
    """The single BRepBody picked in a selection input, or None."""
    try:
        for i in range(sel.selectionCount):
            ent = sel.selection(i).entity
            if isinstance(ent, adsk.fusion.BRepBody):
                return ent
    except Exception:
        return None
    return None


def _family_of(w, body):
    """The profile-family abbreviation (CHS/SHS/...) of a member body, or ''."""
    try:
        registry = w.load_registry(_design())
        m = w.resolve_member(registry, body)
        if m is not None and m.family:
            return m.family
        cl = w._member_centerline(body)
        if cl is not None:
            m = registry.member_near_point(reg.midpoint(cl[0], cl[1]), tol=0.5)
            if m is not None:
                return m.family or ''
    except Exception:
        pass
    return ''


def command_input_changed(args: adsk.core.InputChangedEventArgs):
    """Re-fill the Bend Die dropdown when a member is (de)selected."""
    if args.input.id not in ('member_a', 'member_b'):
        return
    inputs = args.inputs
    w = _weldment()
    body = _selected_body(inputs.itemById('member_a')) or \
        _selected_body(inputs.itemById('member_b'))
    dd = inputs.itemById('die')
    if body is None:
        return
    family = _family_of(w, body)
    if not family:
        return
    dies = bd.dies_for_family(w._DIES, family)
    if not dies:
        return
    dd.listItems.clear()
    dd.listItems.add('(auto: tightest die for the family)', True)
    for die in dies:
        dd.listItems.add(f"{die['die_id']} (R{bd.die_clr(die):g})", False)


def _clr_mm(w, inputs, family):
    """The chosen die's centerline radius (mm) from the dropdown.

    Index 0 is the '(auto)' entry: resolve the tightest die for the family (the
    pre-dropdown behaviour).  A family with no catalogue dies (open sections)
    yields 0.0, which the caller reports as unbendable.
    """
    dd = inputs.itemById('die')
    idx = w._dropdown_index(dd)
    if idx > 0:
        dies = bd.dies_for_family(w._DIES, family) if family else []
        # +1 because index 0 is the '(auto)' label.
        if idx - 1 < len(dies):
            return bd.die_clr(dies[idx - 1])
    if family:
        die = bd.die_for_designation(w._DIES, {}, family)
        return bd.die_clr(die) if die else 0.0
    return 0.0


def _corner_vertex(a_cl, b_cl):
    """The shared corner point of two members that meet end-to-end.

    The closest pair of endpoints (one per member); their midpoint, which for a
    real corner is the coincident vertex. Returns None if the members do not
    actually meet (their nearest ends are more than 1 cm apart).
    """
    best, best_d = None, None
    for pa in (a_cl[0], a_cl[1]):
        for pb in (b_cl[0], b_cl[1]):
            d = jt._dist(pa, pb)
            if best_d is None or d < best_d:
                best_d, best = d, reg.midpoint(pa, pb)
    if best is None or best_d > 1.0:
        return None
    return best


def _do_bend(w, root, body_a, body_b, clr_mm, preview):
    """Resolve the corner and run the two-step bend, shared by execute/preview.

    Returns ``(error, payload)``.  On success ``error`` is None and ``payload``
    carries the centerlines, the vertex, the per-leg survivors, and the objects
    to track (``feats``: cut/sweep features; ``track``: helper sketches/planes).
    On failure ``error`` is a user-facing message (execute shows it; preview
    ignores it so a half-picked selection does not pop a dialog per redraw).
    """
    cl_a = w._member_centerline(body_a)
    cl_b = w._member_centerline(body_b)
    if cl_a is None or cl_b is None:
        return 'Both selections must be straight weldment members.', None
    a_s, a_e, a_geom, a_basis = cl_a
    b_s, b_e, b_geom, b_basis = cl_b

    vertex = _corner_vertex(cl_a, cl_b)
    if vertex is None:
        return 'The two members do not meet at a corner.', None

    own_a, _ta = jt._tip_and_own((a_s, a_e), vertex)
    own_b, _tb = jt._tip_and_own((b_s, b_e), vertex)
    if own_a is None or own_b is None:
        return 'The members are collinear -- there is no corner to bend.', None

    path = jt.bend_path(vertex, own_a, own_b, clr_mm * jt.MM_TO_CM)
    if path is None:
        return ('Could not compute the bend (degenerate corner or zero '
                'radius).'), None
    t1, t2 = path['t1'], path['t2']
    sb = jt.bend_setback(clr_mm, path['theta'])   # cm, vertex -> tangent point

    # The plan dict _build_bend_arc_sweep consumes (same shape as bend_plan).
    plan = {'point': vertex, 'center': path['center'], 'axis': path['axis'],
            'theta': path['theta'], 'radius_cm': path['radius_cm'],
            'direction': 1.0,
            'tangent': [(0, None, t1), (1, None, t2)]}

    # Materialize the swept leg's centreline as a real sketch line: the sweep
    # builder needs a path-able curve for the profile plane (setByPath), exactly
    # as a rebuild does (see _materialize_line).
    ln_a, sk_a = w._materialize_line(root, a_s, a_e)

    feats, track = [], [sk_a]
    survivors = []
    if preview:
        # Remember the members' real opacities: a combine's delete restores the
        # original body but NOT the display state we set on it during the cut,
        # so teardown must put these back (see _clear_preview).
        for body in (body_a, body_b):
            try:
                _preview_ghosts.append((body, body.opacity))
            except Exception:
                pass
    try:
        # Step 1: trim each leg to its tangent point with a finite waste prism.
        # Unlike a cope (where the tool BODY defines the final shape and the box
        # only classifies fragments), here the prism IS the cut, so its stopping
        # face must land exactly ON the tangent point -- no _region_box safety
        # inflation.  The prism spans from 1 cm past the vertex (swallowing the
        # poking tip) to exactly sb into the member.
        perp = max(jt._perp_extent_cm(a_geom, a_basis, None, own_a),
                   jt._perp_extent_cm(b_geom, b_basis, None, own_b)) * 1.2
        for body, own in ((body_a, own_a), (body_b, own_b)):
            lo, hi = -(sb + 1.0), sb
            region = {'center': jt._add(vertex, jt._scale(own, 0.5 * (lo + hi))),
                      'axes': jt._frame(own),
                      'half': (0.5 * (hi - lo), perp, perp)}
            box = w._box_cutter(root, region, preview=preview)
            if box is None or box[0] is None:
                continue
            prism, bx_track = box[0], box[1:]
            comb = w._combine_cut(root, body, [prism], keep_tool=False)
            feats.append(comb)
            for o in bx_track:
                track.extend(o if isinstance(o, (tuple, list)) else (o,))
            surv = w._survivor_after_cut(comb, None, region)
            if surv is not None:
                # Ghost the re-homed survivor NOW: the sweep added later re-homes
                # body wrappers again, and opacity set on a stale wrapper is lost.
                # (The arc bodies are ghosted inside the builders via preview=.)
                if preview:
                    try:
                        surv.opacity = PREVIEW_OPACITY
                    except Exception:
                        pass
                survivors.append(surv)
        # Step 2: sweep the section along the die-radius arc, t1 -> t2.  Prefer
        # the physically-correct sweep; fall back to the revolve builder (the
        # same order _build_bend_arcs uses).
        arc = w._build_bend_arc_sweep(root, ln_a, t1, plan, a_geom, None,
                                      preview=preview, basis=a_basis)
        if arc is None:
            arc = w._build_bend_arc(root, ln_a, t1, plan, a_geom, None,
                                    preview=preview, basis=a_basis)
        if arc is None:
            return 'Could not build the bend arc.', None
        feats.append(arc[0])
        track.extend(arc[1:])
    except Exception:
        futil.handle_error(f'{CMD_NAME} bend')
        return 'The bend failed.', None

    payload = {'cl_a': cl_a, 'cl_b': cl_b, 'vertex': vertex,
               'feats': feats, 'track': track, 'arc': arc,
               'survivors': survivors}
    return None, payload


def _clear_preview():
    """Tear down the in-dialog preview: delete the cut/sweep features (restoring
    the members' original bodies), then the helper sketches/planes, then put
    back the opacity of any pre-existing body we ghosted."""
    global _preview_objs, _preview_ghosts
    for obj in _preview_objs:
        for o in (obj if isinstance(obj, (tuple, list)) else (obj,)):
            try:
                o.deleteMe()
            except Exception:
                pass
    _preview_objs = []
    for body, op in _preview_ghosts:
        try:
            body.opacity = op
        except Exception:
            pass
    _preview_ghosts = []


def command_execute_preview(args: adsk.core.CommandEventArgs):
    """Ghost the trimmed legs and swept arc so the bend reads as transient."""
    global _preview_objs, _preview_bodies
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
    # Cache the two bodies NOW: _do_bend's Combine consumes them and Fusion
    # drops the selection entries, so this is the last moment they are both
    # reachable through a live reference.  command_execute reuses this cache.
    _preview_bodies = (body_a, body_b)
    family = _family_of(w, body_a) or _family_of(w, body_b)
    clr_mm = _clr_mm(w, inputs, family)
    if clr_mm <= 0.0:
        return
    err, payload = _do_bend(w, root, body_a, body_b, clr_mm, preview=True)
    if err:
        return
    # _do_bend(preview=True) already ghosted everything it made or re-homed:
    # the swept arc (inside the builders) and each trimmed survivor (tracked in
    # _preview_ghosts for restore).  Track the features for teardown: deleting
    # them restores the members' original geometry.
    _preview_objs = list(payload['feats']) + list(payload['track'])


def command_destroy(args: adsk.core.CommandEventArgs):
    # After OK, execute already cleared the preview and committed the bend, so
    # this is a no-op.  On Cancel the preview is still live -- remove it.
    _clear_preview()


def command_execute(args: adsk.core.CommandEventArgs):
    inputs = args.command.commandInputs
    w = _weldment()
    design = _design()
    if design is None:
        return
    root = design.rootComponent

    # Tear the preview down FIRST.  The preview's Combine features consumed
    # (re-homed) the two member bodies; deleting those features restores the
    # original bodies the selection inputs reference, so the reads below get
    # whole members again.  (If the preview were left up, the picked bodies
    # would still be consumed and _selected_body would spuriously return None,
    # popping "select both members" even though the user picked two.)
    _clear_preview()

    body_a = _selected_body(inputs.itemById('member_a'))
    body_b = _selected_body(inputs.itemById('member_b'))
    # The preview's Combine consumed (re-homed) the picked bodies and Fusion
    # dropped their selection entries, so a field can read empty here even
    # though the user picked two.  Fall back to the bodies cached during the
    # last preview -- _clear_preview above restored them whole, so the cached
    # references are live again.
    if body_a is None:
        body_a = _preview_bodies[0]
    if body_b is None:
        body_b = _preview_bodies[1]
    if body_a is None or body_b is None:
        ui.messageBox('Select both members meeting at the corner.')
        return
    if body_a is body_b:
        ui.messageBox('Pick two different members.')
        return
    family = _family_of(w, body_a) or _family_of(w, body_b)
    clr_mm = _clr_mm(w, inputs, family)
    if clr_mm <= 0.0:
        ui.messageBox('No bend die is available for this profile family.')
        return

    err, payload = _do_bend(w, root, body_a, body_b, clr_mm, preview=False)
    if err:
        ui.messageBox(err)
        return

    # The prisms' and the arc's sketches/planes form reference chains the
    # features depend on, so they cannot be deleted without breaking health --
    # hide them to keep the viewport clean.
    for obj in payload['track']:
        for o in (obj if isinstance(obj, (tuple, list)) else (obj,)):
            try:
                o.isLightBulbOn = False
            except Exception:
                pass

    _record(design, w, body_a, body_b, payload['cl_a'], payload['cl_b'],
            payload['vertex'], clr_mm, arc=payload.get('arc'))


def _record(design, w, body_a, body_b, cl_a, cl_b, vertex, clr_mm, arc=None):
    """Persist the bend as a registry joint (and its members), for re-run/BOM.

    Also stamps the swept ``arc`` body with the joint id (Weldments.Joint) so a
    later member coping into this corner can find the arc to cut against
    (candidate #4 phase B; see weldment.entry.arc_body_for_joint).
    """
    registry = w.load_registry(design)
    ma = registry.upsert_member(cl_a[0], cl_a[1], geom=cl_a[2],
                                basis=list(cl_a[3]) if cl_a[3] else None,
                                **w.profile_fields(cl_a[2]))
    mb = registry.upsert_member(cl_b[0], cl_b[1], geom=cl_b[2],
                                basis=list(cl_b[3]) if cl_b[3] else None,
                                **w.profile_fields(cl_b[2]))
    for body, mem in ((body_a, ma), (body_b, mb)):
        if w.body_mid(body) is None:      # keep an existing stamp's owner
            w.stamp_body(body, mem.mid)
    params = {'clr_mm': clr_mm}
    joint = None
    for j in registry.joints:
        if j.kind == 'bend' and set(j.member_ids()) == {ma.mid, mb.mid} and \
                j.vertex and reg.distance(j.vertex, vertex) < 0.05:
            j.params.update(params)
            joint = j
            break
    if joint is None:
        joint = registry.add_joint(
            'bend', [{'mid': ma.mid, 'role': None},
                     {'mid': mb.mid, 'role': None}],
            vertex=vertex, params=params,
            selections={'member_a': body_a.name, 'member_b': body_b.name})
    if arc is not None and arc[0] is not None:
        try:
            w.stamp_joint(arc[0].bodies.item(0), joint.jid)
        except Exception:
            pass
    w.save_registry(design, registry)
