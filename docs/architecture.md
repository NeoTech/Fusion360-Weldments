# Weldments — Technical Reference

In-depth documentation of the add-in's architecture, geometry engine, and
Fusion-API command layer. For a feature overview and usage, see the top-level
[README](../README.md). This file is the codebase reference for future
maintainers. For the derivations and formulas behind each joint, see
[joint-math.md](joint-math.md).

- [Design principles](#design-principles)
- [Layered architecture](#layered-architecture)
- [Units and conventions](#units-and-conventions)
- [`lib/profiles.py` — sections](#libprofilespy--cross-section-geometry)
- [`lib/joints.py` — corner engine](#libjointspy--corner-joint-engine)
- [`lib/bending_dies.py` — die catalogue](#libbending_diespy--bend-die-catalogue)
- [`commands/weldment/entry.py` — command layer](#commandsweldmententrypy--command-layer)
- [Data flow: selection → features](#data-flow-selection--features)
- [Position alignment grid](#position-alignment-grid-the-anchor-model)
- [Joint semantics (authoritative)](#joint-semantics-authoritative)
- [Detection algorithms](#detection-algorithms)
- [Swept-bend geometry](#swept-bend-geometry) → see also [joint-math.md](joint-math.md)
- [Existing-member recovery](#existing-member-recovery-phase-j)
- [Data file schemas](#data-file-schemas)
- [Testing](#testing)
- [Gotchas and invariants](#gotchas-and-invariants)

---

## Design principles

1. **Pure geometry is separated from the Fusion API.** Everything in `lib/` is
   plain Python with **no `adsk` imports**, so it unit-tests headlessly on
   CPython. `commands/weldment/entry.py` is the *only* module that touches
   `adsk.fusion`.
2. **Data-driven.** Profiles and bend dies are JSON catalogues in `data/`;
   adding a size or a die needs no code change.
3. **Parametric-history friendly.** Features are built so the timeline stays
   clean (e.g. a butt is a length trim, not a boolean; a miter is a
   `SplitBodyFeature` by the bisector plane plus a reversible `Remove` of the
   waste sliver, never a giant boolean prism).
4. **A joint belongs to a line END.** Every member has a *start* and an *end*
   vertex; the two can carry different joints.

---

## Layered architecture

```
Weldments.py                 run()/stop() -> commands.start()/stop()
config.py                    DEBUG, ADDIN_NAME, COMPANY_NAME, palette id
commands/
  __init__.py                command registry (start/stop each)
  weldment/entry.py          THE feature: dialog + all adsk.fusion glue
  commandDialog/, paletteShow/, paletteSend/   template scaffold (unused)
lib/
  profiles.py                pure: section outlines + placement basis
  joints.py                  pure: corner/T detection, offsets, cuts, bends
  bending_dies.py            pure: die catalogue loader/lookup
  fusionAddInUtils/          shared add-in helpers (events, logging)
data/
  profiles.json              EN section catalogue
  bendingdies.json           bend-die (CLR) catalogue
tests/
  adsk_stub.py               fake adsk API for headless command tests
  test_profiles.py / test_joints.py / test_bending_dies.py / test_command.py
```

The `lib/` modules are the product; `entry.py` translates between Fusion's
selections/features and the pure functions.

---

## Units and conventions

- **Fusion internal length unit is centimetres.** All geometry handed to/from
  the API is cm.
- **JSON catalogues are millimetres.** `MM_TO_CM = 0.1` bridges them at the
  boundaries (defined identically in `profiles.py`, `joints.py`,
  `bending_dies.py`).
- **A "line"** is any object exposing `worldGeometry.startPoint` / `.endPoint`
  (each with `.x/.y/.z` in cm) and `.length`. Real `SketchLine`s and the test
  stub both satisfy this. `joints.line_endpoints` / `line_direction` are the
  only accessors.
- **Section local frame** `(u, v)` in mm, centred on the centroid: `v` along
  height `h`, `u` along width `b`. `profiles.compute_basis` maps it into model
  space from the line direction.
- **Endpoints/indices:** selected lines are `0..n-1`. A **context** (existing)
  member is reported as a **negative index `~k`** by `detect_corners` and as
  **`n+k`** by `detect_t_junctions`; `_resolve_line` decodes both.

---

## `lib/profiles.py` — cross-section geometry

Loads `data/profiles.json` and turns a designation into drawable loops.

| Function | Purpose |
|---|---|
| `load_profiles(path)` | Read the catalogue (list of family dicts). |
| `annotate_families(families)` | Attach `_abbreviation` to each designation (mutates + returns). |
| `family_labels(families)` | `'IPE - Parallel Flange I-Beams'` strings for the dropdown. |
| `designations(family)` / `designation_labels(family)` | Sizes of a family. |
| `find_designation(family, label)` | Look up one designation dict. |
| `section_geometry(designation)` | **The core:** a geometry descriptor (below). |
| `compute_basis(direction, ref)` | `(axis_u, axis_v)` unit vectors ⟂ to the line. |
| `selection_reference(directions)` | One shared "up" for a whole selection (plane normal). |
| `rotate_basis(u, v, angle)` | Spin a basis about the line (the Rotation column). |
| `map_local_to_model(origin, u, v, um, vm)` | mm local point → cm model point. |
| `GRID_POSITIONS` | The 9 `(key, label)` Position-grid entries (`center` first). |
| `section_extents(geom)` | Bounding box `(umin, umax, vmin, vmax)` (mm) of a section. |
| `grid_anchor(geom, position)` | Local `(u, v)` mm point of a section that sits on the line. |
| `displace_origin(origin, u, v, anchor)` | cm placement origin so `anchor` lands on `origin`. |

**`section_geometry` descriptor:**

- `{'kind': 'polygons', 'loops': [...], 'fillets': [...]}` — mm corner points;
  `fillets[i]` is `[(corner_index, radius_mm), ...]` for `loops[i]`.
- `{'kind': 'circles', 'radii': [r_outer, r_inner]}` — mm, concentric, outer first.

Builders: `_ibeam_loops` (IPE/HEA/HEB, 12-pt, 4 internal fillets),
`_channel_loops` (UPE/UPN, 8-pt; UPN taper ignored), `_rect_hollow_loops`
(SHS/RHS, outer+inner rects, EN 10210 corner radius `r`, inner `r-t`), and CHS
as two concentric circles. `_infer_abbreviation` recovers the family from the
designation string when not annotated.

`compute_basis` uses a shared `ref` (from `selection_reference`) when given and
not parallel to the line, so every profile in a frame rolls identically and
joined/mitred ends align — this makes a 3D-sketch frame behave like a 2D one.

---

## `lib/joints.py` — corner joint engine

The heart of the add-in. Answers: *given lines + sections + chosen joints, what
per-line offsets, cuts, and bend arcs make the corners join cleanly?*

**Vocabulary:** `LABELS` (`none/butt/miter/cope/bend` → display text),
`ALL_IDS` (ordered master list), `THROUGH_LABEL`, `SADDLE_LABEL`,
`_CORNER_TOL = 1e-4` cm.

| Function | Purpose |
|---|---|
| `line_endpoints` / `line_direction` | cm endpoints / unit direction of a line. |
| `detect_corners(lines, tol, context)` | Shared vertices: `{'point', 'members':[(idx,'start'\|'end')]}`. |
| `detect_t_junctions(lines, tol, context)` | End-on-interior landings: `{'point','member','tool'}`. |
| `member_depth(geom)` | Characteristic section depth (mm) for trims. |
| `_half_extent_cm(geom, basis, axis, anchor)` | **Directional** half-extent (cm) along a neighbour's axis, measured from an optional Position anchor. |
| `corner_offsets(...)` | Per-line `(offset_start, offset_end)` in cm. |
| `corner_cuts(...)` | Real boolean/plane cut plans. |
| `bend_plan(...)` | Swept-arc plans per bend corner. |
| `bend_setback` / `bend_arc_length` / `bend_turn_angle` | Bend math helpers. |
| `joints_for_family` / `joint_labels` / `joint_id_from_label` | Family filtering + label↔id. |

**`corner_offsets(lines, geoms, joint_by_line, clr_by_line, through_by_line,
bases, saddle_by_line, cope_depth_by_line, context, anchor_by_line)`** — the main
entry. Builds a combined index space (`L`, `G`, `B`, `A`) over selected + context
lines, then:

- **Butt corners** are handled **once per corner** via `_butt_through` (which
  member runs through). The backing-off member stops at the through member's
  face measured *along the incoming axis* (`_half_extent_cm`/sin φ); the through
  member grows to the other's face. Plain butt → near face (flat square, no
  boolean); saddled solid → far face; saddled hollow → just past the near wall
  (`-half + wall`) so the boolean carves a saddle without leaving a plug.
- **Miter** bumps each member *past* the vertex by `h/tan(φ/2)`, where `h` is the
  section's half-extent **in the miter plane** (from its anchor), so the bisector
  plane (from `corner_cuts`) produces a real corner-to-corner face even for a
  rotated or off-centre open section (`_miter_setback_cm`).
- **Bend** trims each leg to its tangent point (`bend_setback`).
- **T-junctions**: a butt/cope end landing mid-run gets the same near/far/wall
  reach logic against the tool.

`corner_cuts` emits `{'member','role','kind','point','normal','keep','tool'}`:
`kind='plane'` for miter (bisector), `kind='body'` for cope-at-T or saddled-butt.
A plain butt emits **no** cut. A context tool is encoded negative (`~k`) / `n+k`.

`bend_plan` returns `{'point','center','axis','theta','direction','radius_cm',
'tangent','arc_length'}`. `direction` is `±1` (the **sign of the revolve angle**,
since a sketch line has no direction to negate); `tangent` lists a selected leg
first so the builder revolves from a line it created.

**Position anchor (`anchor_by_line`).** When the global Position grid places a
member off-centre, its body is translated away from the reference line, so the
corner trims must measure a neighbour's extent **from that neighbour's anchor**
(the reference line through the shared vertex), not its displaced centroid —
that is the `anchor` argument to `_half_extent_cm`. Corner detection itself runs
on the **original** (undisplaced) lines, so translating sections never breaks
shared-vertex clustering (`_CORNER_TOL`). Context (existing) members always
carry `anchor=None` — their alignment was set in an earlier run and is not
recoverable, so they trim as centred.

---

## `lib/bending_dies.py` — bend-die catalogue

Reduced to the one value that shapes a bend: **centerline radius**.

| Function | Purpose |
|---|---|
| `load_bending_dies(path)` | Read catalogue; missing file → empty (never raises). |
| `dies` / `die_labels` | List / `'CHS-CLR-114.3 (R114.3)'` labels. |
| `die_clr(die)` / `clr_cm(die)` | CLR in mm / cm. |
| `find_die(cat, die_id)` | Exact id lookup. |
| `dies_for_family(cat, abbr)` | All dies of a family. |
| `dies_for_designation(cat, desig, abbr)` | Family's CLRs sorted ascending (`designation` arg is vestigial). |
| `die_for_designation(...)` | Tightest (default) die, or None for open sections. |

Filtering is by **family alone** — a tube can be swept to any CLR the shop owns.
A family in `_DIE_FAMILY_ALIASES` (currently `RHS → SHS`) is served the aliased
family's dies: a rectangular tube is rotary-draw-bent on the same flat-face dies
as a square one, so RHS shares SHS's tooling instead of duplicating every entry.

---

## `commands/weldment/entry.py` — command layer

The only `adsk.fusion` module. Registers the **Weldment** command on the Create
panel and drives the dialog.

**Registration constants:**
`CMD_ID = f'{COMPANY_NAME}_{ADDIN_NAME}_weldment'`, `WORKSPACE_ID =
'FusionSolidEnvironment'`, `PANEL_IDS = ['SolidCreatePanel',
'PlasticPartsCreatePanel']` (first that exists), `COMMAND_BESIDE_ID =
'PrimitivePipe'`.

**Lifecycle:**
- `start()` — loads `_FAMILIES` (annotated) and `_DIES`, adds the command
  definition, hooks `command_created`, promotes the control.
- `stop()` — removes the control + definition.
- `command_created` — builds inputs (below), hooks `execute`, `executePreview`,
  `inputChanged`, `select`, `destroy`. **There is no `preExecute` event.**

**Dialog inputs:**
- `path` — Selection input, filtered to `SketchLines`, min 1.
- `family` / `designation` — dropdowns (designation rebuilt on family change).
- `position` — a **global** 3×3 alignment-grid dropdown (which point of the
  section sits on the line; `center` default). See *Position anchor* below.
- `params` — an **11-column table**, one data row per line (row 0 is a read-only
  header). Columns:
  `# | Joint Start | Joint End | Through | Saddle | Rotation | Offset Start |
  Offset End | Inverse | Bend Die | Cope Depth`.
- `sync_all` — table-toolbar checkbox; when on (default), editing any row
  propagates to all rows and manipulators anchor to the first line.

**Key helpers (grouped):**

- *Row/param readers:* `_row_params`, `_row_joints`, `_row_joint_at`,
  `_row_flags`, `_row_through`, `_row_saddle`, `_row_inverses`,
  `_row_cope_depths`, `_row_die_clr`, `_bend_radii`.
- *Dropdown rebuilds:* `_rebuild_designations`, `_rebuild_joints`,
  `_populate_joint_dropdown`, `_rebuild_dies`, `_populate_die_dropdown`,
  `_update_bend_columns` (enable Inverse/Bend Die for bends, Cope Depth for copes).
- *Position grid:* `_position_key` (dropdown → grid key), `_section_anchor`
  (grid key → local `(u, v)` mm anchor, `None` for `center`).
- *Interaction:* `command_input_changed` (handles family/designation/joint/saddle
  edits, calls `_propagate_corner_joint`), `command_select`, `_sync_table_rows`,
  `_update_manipulators`, `_apply_row_manipulators`.
- *Geometry bridge:* `_resolve`, `_joint_offsets`, `_build_bend_arcs`,
  `_build_weldment`, `_draw_section`, `_draw_model_line`.
- *Cutting:* `_apply_corner_cuts`, `_miter_plane`, `_split_miter`,
  `_side`, `_find_body_near`, `_all_bodies`, `_body_centroid`, `_bbox_of`,
  `_remove_combine_orphans`.
- *Existing members:* `_CtxPoint`, `_CtxGeometry`, `_ContextLine`,
  `_member_centerline`, `_recover_existing_members`.
- *Preview/commit:* `command_execute_preview`, `command_execute`,
  `command_destroy`, `_clear_preview`, `_snapshot_selection`,
  `_restore_selection`, `_selected_lines`.

`_propagate_corner_joint`: **miter and bend are two-member relationships** —
setting one on a line sets the matching dropdown on the *other* member at that
vertex (guarded by `_syncing`). Butt/cope/none are per-member.

---

## Data flow: selection → features

```
Sketch lines selected
   └─ _selected_lines -> line objects
   └─ _recover_existing_members(root) -> context (Phase J)
   └─ profiles.section_geometry(designation) -> geom per line
   └─ profiles.compute_basis / selection_reference -> bases
   └─ profiles.grid_anchor(position) -> section anchor (Position grid)
   └─ joints.corner_offsets(..., anchor_by_line) -> per-line (start,end) offsets (cm)
   └─ joints.bend_plan(...) -> swept arcs -> _build_bend_arc (revolve)
   └─ joints.corner_cuts(...) -> cut plans -> _apply_corner_cuts (split/plane)
   └─ _build_weldment(line, geom, offsets) -> extrude/revolve per member
preview: build ghosted, record every object in _preview_objs/_preview_cuts
commit:  keep; destroy: _clear_preview deletes in reverse (cuts before members)
```

`_apply_corner_cuts` returns every created object in **delete order** (miter:
Remove feature, Split feature, plane-helper sketch, plane; cope/butt: combine
feature) so `_clear_preview` removes cutting features before member features.

---

## Position alignment grid (the anchor model)

The picked sketch line is a **reference**, not the member's centroid axis. The
global `position` dropdown chooses which point of the cross-section lies on it,
as a 3×3 grid over the section's local `(u, v)` bounding box (`center`, `top`,
`bottom`, `left`, `right`, and the four corners).

Flow: `_position_key` → `profiles.grid_anchor(geom, key)` → a local `(u, v)` mm
**anchor**. Two consumers:

- **Placement** (`_build_weldment` / `_build_bend_arc`): the centroid is
  displaced by `profiles.displace_origin(origin, axis_u, axis_v, anchor)`
  = `origin − MM_TO_CM·(au·axis_u + av·axis_v)`, so the anchor lands exactly on
  the line. The build uses the **rotated** basis with the plain local anchor.
- **Joint trims** (`_joint_offsets` → `corner_offsets(bases=…, anchor_by_line=…)`):
  the trims are handed the **placed** basis (`rotate_basis(compute_basis(dir,ref),
  row angle)`) and the **plain** local anchor — the *same* orientation and offset
  `_build_weldment` uses — so a rotated or off-centre member's butt/cope/T/miter
  trim measures its extent in its real orientation. (Rotating the anchor against
  an un-rotated basis is *not* equivalent for an extent measurement, only for the
  centroid displacement, so the placed basis is passed directly.)

Why the anchor model and not shifted reference lines: `detect_corners` clusters
by endpoint **proximity** (`_CORNER_TOL`); translating each centroid line would
break shared-vertex detection. Keeping the lines original and displacing only at
build/trim time preserves corner detection.

**Correctness by offset direction:** out-of-plane (`top`/`bottom`) is a pure
rigid translation → correct for every joint. In-plane (`left`/`right`, corner
horizontals): butt/cope/T exact (anchor extent), miter exact (the plane is the
centerline bisector through the vertex — rotation/anchor-independent — and the
setback is directional, `h/tan(φ/2)` with `h` the extent in the miter plane from
the anchor), bend arc shifted by the leg displacement. `center` short-circuits
to `None` and reproduces the historical placement with no offset arithmetic.

---

## Joint semantics (authoritative)

| Joint | Geometry | Realised by | Boolean? |
|---|---|---|---|
| `none` | Full length to vertex (default). | No offset. | No |
| `butt` | One member runs through, the other stops at its near face. | `corner_offsets` axial trim + extend. | **No** (unless Saddle). |
| `miter` | Both cut on the bisector (45° at 90°). | Offset past vertex + `corner_cuts` plane. | No (Split Body + Remove). |
| `cope` | End saddled over another's outer face. | `corner_cuts` body cut **at a T-junction**. | Yes. |
| `bend` | Swept centerline arc (radius CLR). | `bend_plan` + revolved arc. | No. |

- A **butt never booleans into an open section's hollow** — it is a length trim.
- A **cope at a shared-vertex corner degrades to a butt** (coping is a mid-run
  operation).
- **Saddle** turns a butt into a body cut; **Through** marks which member runs
  past; **Cope Depth** deepens the fishmouth past the default stopping face.
- Family support is declared in `data/profiles.json` `joints` (open sections:
  none/butt/miter; SHS/RHS: all five; CHS: none/butt/cope/bend — no planar miter).

---

## Detection algorithms

**Corners** (`detect_corners`): collect every endpoint with `(index, role)`,
cluster by proximity (`_CORNER_TOL`), keep clusters with ≥2 distinct lines and at
least one *selected* line. Context endpoints join the clustering (as `~k`) but a
context-only corner is dropped (never edited).

**T-junctions** (`detect_t_junctions`): for each selected endpoint, test whether
it lies **strictly inside** another line's segment (`_point_on_segment_interior`
— excludes both endpoints, so a shared vertex is a corner, not a T). Context
members are candidate tools (index `n+k`) but never members.

---

## Swept-bend geometry

In the bend plane; θ = turn angle, R = CLR:

- Turn angle: `θ = π − angle(u, v)` (u, v point away from the vertex).
- Setback (leg trim, vertex→tangent): `SB = R·tan(θ/2)`.
- Arc length: `L = R·θ`.
- Arc centre: `V + (R/cos(θ/2))·normalize(u+v)`; axis = `normalize(u×v)`.
- Sweep direction = **sign of the revolve angle** (`direction = ±1`), flipped by
  the Inverse column — negating the axis *line* is a no-op.
- **Section orientation:** a square tube bends about a **flat face**, never a
  rolled corner (the die bears on a face parallel to the bend plane). Each bend
  leg's basis is set so `axis_v` ∥ the bend axis `a` (`_bend_bases`); planar-safe
  (equals `compute_basis` when the bend is planar), overrides the global reference
  only on 3D bends.

The two straight legs are shortened by `SB` and a revolved arc fills the gap.

---

## Existing-member recovery (Phase J)

When adding to a frame, `entry.py` scans the design so new parts join placed
members directly — no shadow parts, no duplicate-delete, clean history.

- `_recover_existing_members(root)` → `[{'line': _ContextLine, 'geom': {...},
  'basis': (u, v) or None, 'body': body}]`.
- `_member_centerline(body)` recovers a member's run + section from its faces,
  routing by the **dominant face kind**: a round tube via its dominant
  cylindrical face (`_centerline_round`, `basis=None`); a square/rectangular tube
  via its planar faces clustered into three normal axes (`_centerline_prismatic`,
  whose two side-face pairs give the real in-plane `basis` and whose corner
  cylinders give the fillet radius). A filleted SHS/RHS has corner cylinders, so
  it must NOT be misrouted to the round path. Torus faces (bends) are skipped.
- `_ContextLine` is a `worldGeometry` shim (`_CtxPoint`/`_CtxGeometry`) so the
  pure `lib/` functions treat existing bodies exactly like selected lines.
- The recovered list is passed as `context` to `corner_offsets`, `corner_cuts`,
  and `bend_plan`. Existing members are **never edited** (no table row); the
  joint is set only on the new line's side. In `_apply_corner_cuts`, a negative
  tool index resolves to the context member's real **body** as the boolean tool.

---

## Data file schemas

**`data/profiles.json`** — list of families:

```jsonc
{
  "standard": "EN 10365", "abbreviation": "CHS",
  "profile_family": "Circular Hollow Sections",
  "joints": ["none","butt","cope","bend"],
  "standard_designations": [
    { "designation": "CHS 48.3 x 2.0", "od_mm": 48.3, "t_mm": 2.0, ... }
  ]
}
```

Dimension keys by family: I-beams/channels `h_mm,b_mm,tw_mm,tf_mm,r_mm`;
SHS/RHS `h_mm,b_mm,t_mm,r_mm`; CHS `od_mm,t_mm`.

**`data/bendingdies.json`** — `{ "units": "mm", "dies": [ {die_id,
profile_family, clr_mm}, ... ] }`. `die_id` is `<FAMILY>-CLR-<value>`.

Shipped: 8 families (IPE 18, HEA 19, HEB 19, UPE 14, UPN 12, SHS 54, RHS 71,
CHS 109 sizes); dies CHS 23, SHS 6 (RHS shares SHS's via `_DIE_FAMILY_ALIASES`).

---

## Testing

```bash
python -m unittest discover -s tests
```

196 tests, no Fusion needed. `tests/adsk_stub.py` fakes the `adsk` API (recording
created features, e.g. `CombineFeatures.add` logs tool bodies) so
`test_command.py` exercises the command layer headlessly.

- `test_profiles.py` — section geometry, designations, basis vectors, and the
  Position grid helpers (`TestGridPosition`: extents, anchors, displacement).
- `test_joints.py` — corner/T detection, butt/miter/cope/bend offsets, bend
  plans, **existing-member context** (`TestExistingMemberContext`), and the
  Position anchor trims (`TestPositionAnchor`).
- `test_bending_dies.py` — loading and die selection (incl. the RHS→SHS alias).
- `test_command.py` — weldment building, per-line table, joint propagation,
  corner-cut construction, cope-orphan removal, existing-member recovery, and
  the Position dropdown → anchor → build wiring (`TestPositionGrid`).

---

## Gotchas and invariants

Non-obvious rules that must not regress:

- **No `preExecute` event** on `Command`. Populate dropdowns in
  `command_created`; rebuild on `inputChanged`. New catalogue data needs an
  add-in reload (registration happens in `run()`, not import).
- **Origin planes** live on `Component` (`root.yZConstructionPlane`, capital Z),
  not `root.origin`.
- **Linear `ValueCommandInput.value` is cm** (×10 for mm); a `'mm'`
  `FloatSpinner`'s `.value` is also **database units (cm)** — convert ×10 before
  feeding the mm-based joint layer (this was the cope-depth 10× bug).
- **Bend direction is the angle SIGN**, not the axis vector.
- **Miter = Split Body + Remove.** A `SplitBodyFeature` by the bisector plane
  *is* usable in a parametric design (verified live): it returns a feature whose
  `bodies` are the two halves, and the waste half is dropped with a reversible
  `Remove` (deleting the Remove restores the body). The kept half is whichever
  piece still `pointContainment`-contains the member's own far end, so no normal
  sign is needed and both members' faces land on the identical plane. The old
  "Split is unusable, use a hidden waste prism + combine" approach is retired.
- **Cope orphans:** a cope tip overshooting a hollow tool's near wall shaves a
  floating plug. `_remove_combine_orphans` keeps the tool + largest remaining
  body and issues a `Remove` on the rest (Remove preserves the parametric flow).
- **Never pass a negative index to a joint/flag list** — Python `list[-1]` is the
  last element. Context members are `~k`/`n+k` and guarded so they read as joint
  `'none'` (never edited).
- **Rebuild a patterned component:** delete the `CircularPatternFeature` first,
  then the base occurrence (pattern instances can't be `deleteMe`'d individually).
- **Preview/execute flag mutation:** any handler that flips a module-level flag
  (e.g. `_preview_active`) must declare `global`, else the committed feature is
  deleted on OK because `command_destroy` sees a stale flag.
- **Live-reload edited `lib/` code without restarting Fusion:** `importlib.reload`
  `joints` **first**, then `entry` (so `entry` rebinds `jt`). Reload only
  redefines functions; it's safe because registration is in `run()`.
