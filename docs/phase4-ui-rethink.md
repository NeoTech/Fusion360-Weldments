# Phase 4 — UI rethink: from "one button does all" to a joint toolbox

## Status: design note (agreed direction, not yet built)

## The problem this addresses

The current model is **line-set + auto-detect**: the user selects a group of 3D
sketch lines, and `lib/joints.py` infers every joint from geometry — which ends
touch which, which are corners vs T-junctions, which partner carries a bend,
whether a cope tip lands on a bend arc, and so on. `joint_spec` /
`corner_offsets` have grown to **~73 functions / ~1600 lines** of interlocking
heuristics (`detect_corners`, `detect_t_junctions`, `_bend_arc_zones`,
`_point_in_arc_zones`, setback/region/plug-reach math, context-member recovery).

Every new real-world case (e.g. *a cope landing on the curved part of a swept
bend*) forces another special-case detector, and the detectors interact in ways
that are hard to test and easy to get wrong. The cope-onto-bend-arc downgrade
(commit `dc0fe1a`) is a symptom: correct, but it only works on freshly-created
bodies, and editing the driving sketch can still produce artifacts outside what
the geometry engine predicts. **The engine is trying to do too much.**

## The proposed direction

Stop treating the whole frame as one implicit line-set. Instead expose a
**toolbox of explicit, single-purpose joint tools** — the way Fusion's *Sheet
Metal* workspace has its own ribbon with Flange / Bend / Fold / Lofted Flange —
where each tool takes **explicit selections** rather than relying on detection:

| Tool | Explicit inputs (instead of auto-detect) |
| --- | --- |
| **Extrude / member** | one path line (or polyline) + profile + designation |
| **Bend** | a vertex (the corner) + the two legs + die (CLR) |
| **Miter** | a vertex + the two members meeting there |
| **Butt** | a vertex/face + the member to trim |
| **Cope** | a target **face** (the tube to sit on) + the coping member + depth |
| **Saddle / through** | a target face + member |

Key change: **select a vertex and the intersecting face(s)** rather than
"select all the lines and let the script guess the topology." The user states
intent; the code computes only that one joint. This collapses most of the
detection heuristics — there is no "is this tip on a bend arc?" question because
the user picked the face they want to cope onto, and the tool validates that
choice against the real solid.

## How the registry fits (this is what makes it tractable)

The registry (`lib/registry.py`, originally the Phase 4 deliverable) becomes the
**backbone** of this model, not just a re-run convenience:

1. **Persistent member/joint records.** Each member and each joint is stored as
   an explicit record (its path, profile, and — crucially — the *selections* that
   defined it: vertex ids, face refs, partner member). Re-running a tool edits a
   record; it does not re-detect the whole frame.
2. **Explicit references survive sketch edits.** Because a joint names the face /
   vertex it was built against (via entity paths / registry handles), editing the
   driving sketch re-applies *that* joint instead of guessing a new topology —
   directly fixing the "edit the sketch and get artifacts" failure above.
3. **User-facing "BOM with settings".** The registry is surfaced as a browser /
   palette panel listing every member and joint with its parameters (profile,
   die, cope depth, rotation, position). It doubles as a cut list / BOM and as
   the place to edit a joint's settings without reopening a builder dialog.

## Relationship to the existing engine

- `lib/joints.py` does **not** get thrown away. The per-joint *math* (miter plane,
  cope cutter box, bend arc/sweep, setback) is exactly what each toolbox tool
  needs. What we retire is the **frame-level auto-detection** layer
  (`detect_corners` / `detect_t_junctions` / arc-zone inference) — replaced by
  "the tool was told which vertex/face."
- Suggested refactor: split `joints.py` into
  - `lib/joint_math.py` — pure per-joint geometry (keep, well-tested),
  - `lib/registry.py` — member/joint records + read/write to attributes,
  - and move detection helpers to a `lib/_detect.py` kept only for the legacy
    one-shot "auto" command during transition.

## Command surface (UI placement)

Today the single `Weldment` command is promoted into the shared **Create** panel
(`commands/weldment/entry.py` `start()`: `panel.controls.addCommand(...)` beside
`PrimitivePipe`). That is the "one button does all" surface.

The toolbox needs its **own section**, the way Sheet Metal gets its own ribbon
tab. Concretely, `start()` should create a dedicated toolbar panel (and, if we
want a full tab, a workspace) instead of borrowing Create:

- `workspace.toolbarPanels.add('WeldmentsPanel', 'Weldments', '', 'SolidTools', ...)`
  creates the panel; each toolbox tool (`weldment`, `bend`, `cope`, `miter`, ...)
  is then `panel.controls.addCommand(cmd_def)` into it.
- A new **workspace** (ribbon tab) is `ui.workspaces.add(...)` with its own
  toolbar/panel; heavier, only if a top-level tab is wanted rather than a panel
  inside the existing Solid environment.
- `stop()` must delete the panel we added (and any workspace), not just the
  command control, so reloading the add-in leaves no orphan panel.

Decision: **dedicated `Weldments` panel** on the Solid workspace's Tools tab
(landed in Phase 4-UI, commit 636be15). Promote to a full ribbon tab later only
if the tool count grows. `start()`/`stop()` in `commands/weldment/entry.py`
create/reuse the panel and clean up the legacy Create-panel button.

## Phasing this into the roadmap

- **Phase 4a — registry core** (unchanged intent): `lib/registry.py`, write
  member/joint records to a design attribute, read them back for re-run pickup.
  Live harness for pickup.
- **Phase 4b — registry-as-BOM panel**: expose the records in a palette panel
  (read-only list + edit-in-place for a few params). This is the UI surface the
  toolbox tools write into.
- **Phase 4c — first toolbox tool**: pick **Bend** or **Cope** (the two that hurt
  most) and rebuild it as an explicit vertex/face-selection command that reads
  and writes registry records, bypassing frame detection. Prove the pattern.
- **Phase 4d — roll out the rest** (miter, butt, saddle, through) on the same
  pattern; then demote the current all-in-one `weldment` command to a thin
  "auto" convenience that just calls the tools in sequence.
- **Phase 5** (unchanged): delete dead detection code, full suite + harness
  green, tag.

## Open questions to resolve before building

1. Command surface: separate toolbar buttons per tool, or one docked "Joint
   Toolbox" palette with a tool picker? (Sheet Metal uses a ribbon tab; we likely
   want a toolbar + a palette for the BOM.)
2. Selection stability: what Fusion entity handle do we store for a face/vertex
   so it survives a sketch edit? (`Entity.getEntityByFullEntityName` /
   topology references vs. registry-assigned stable member ids.)
3. Back-compat: existing single-command frames have no registry records — do we
   migrate them on first open (detect once, then persist), or leave them
   "legacy" and only new frames use the toolbox?
4. Scope of Phase 4: how much of 4a–4d lands now vs. becomes its own milestone?
