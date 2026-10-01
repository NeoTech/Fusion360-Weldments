# Corner Joints & Tube Bends — Phased Plan

Feature: handle corners where two (or more) weldment lines meet, and prepare for
swept tube **bends** driven by bend-die data.

## Decisions (confirmed with user)

- Corners are **auto-detected** from shared line endpoints, but the default
  joint type is **`none`** (identical to today's behavior: each beam runs to the
  vertex).
- Joint type is exposed as a **new column in the existing per-line table**.
- Implement **all phases** in one session, with unit tests per phase and a
  live-verification + user acceptance test at the end of each phase.
- **Profile families declare which joints they support** in
  `data/profiles.json` via a new `joints` array. The joint-type dropdown is
  filtered to the selected family's supported joints.

## Joint vocabulary

| id      | Meaning (geometry) |
|---------|--------------------|
| `none`  | Beam runs full length to the vertex (current default). |
| `butt`  | **Pure axial trim, no boolean.** The member marked `butt` stops short at the through neighbour's near face; the neighbour extends past the vertex so the corner reads flush. Because the end stops at the near face it never enters an open section's interior, so no cut is ever needed. |
| `miter` | Both members cut at the bisector so mating faces coincide (45 deg each at a 90 deg corner). |
| `cope`  | One member's end notched/saddled to fit over the other's outer face (the boolean saddle — kept for when a real notch is wanted). |
| `bend`  | Continuous swept centerline arc of radius CLR replaces the sharp corner (needs bend-die data). |

> **Butt redesign (post-Phase 4b):** butt was initially a neighbour-body
> combine-cut, but that boolean-notched an incoming member into an open
> section's hollow C (wrong). A butt is now realised entirely by
> :func:`lib.joints.corner_offsets` (trim + extend); :func:`corner_cuts` emits
> a cut only for `miter` (plane) and `cope` (body).

## Which joints per family (physical reality)

- **IPE / HEA / HEB / UPE / UPN** (open/hot-rolled sections): `none, butt, miter`
  (cannot be swept-bent; coping an I-section is not a standard corner).
- **SHS / RHS** (hollow structural sections): `none, butt, miter, cope, bend`
  (all four fabrication routes exist; bending needs a die).
- **CHS** (round hollow tube): `none, butt, cope, bend`
  (a 45 deg planar miter on round tube is not standard — coping/fish-mouth is
  the round-tube equivalent; round tube bends readily).

## Bend geometry reference (from fabrication research)

All in the bend plane; theta = corner turn angle, R = centerline radius (CLR):

- Centerline arc length: `L_arc = R * phi` (phi = turn angle in radians)
- Centerline setback (leg trim from virtual apex to tangent point): `SB_c = R * tan(theta/2)`
- Bend allowance (neutral-axis arc): `BA = R_n * phi`, `R_n = R - delta` (delta = 0 nominal)
- Outside setback: `OSSB = (R + h) * tan(theta/2)`, h = half-depth in bend plane
- Miter cut for an N-piece turn of Theta: `alpha_cut = Theta / (2*(N-1))`

For a swept bend each adjoining straight is shortened by `SB_c` from the virtual
apex and a torus/arc segment of radius R fills the gap.

---

## Phase 1 — Corner detection + butt/miter (no bends)

**Todos**
1. [data] Add `joints` array to every family in `data/profiles.json`.
2. [lib] `lib/joints.py`: pure geometry —
   - `JOINT_TYPES` vocabulary + labels.
   - `detect_corners(lines)` -> list of corners (vertex, incident line indices,
     incident directions) using an endpoint proximity tolerance.
   - `member_depth(geom)` -> section depth (mm) along the beam for butt/cope trim.
   - `corner_offsets(lines, geom_by_line, joint_by_line)` -> per-line
     `(offset_start, offset_end)` in cm for `none|butt|miter`.
3. [ui] New `joint` column in the per-line table (dropdown filtered to the
   selected family's `joints`); default `none`.
4. [ui] Wire computed offsets into `_build_weldment` (combine with the manual
   start/end offset columns).
5. [test] `tests/test_joints.py`: corner detection, butt trim, miter bisector,
   family filtering. Extend `adsk_stub` if needed.
6. [verify] Reload add-in, live-verify a 2-line L-frame butt + miter.
7. [accept] User acceptance test.

## Phase 2 — Bend-die data + loader

**Todos**
1. [data] `data/bendingdies.json`: catalogue keyed to profile family with
   `die_id`, `units`, `nominal_CLR_mm`, `groove_profile_type`,
   compatible `width_mm`/`height_mm`/`OD_mm`, `wall_thickness_range_mm`,
   `maximum_bend_angle_deg`, `min_adjacent_straight_mm`.
2. [lib] `lib/bending_dies.py`: `load_bending_dies`, `die_labels`,
   `find_die`, `dies_for_family` (mirror `profiles.py` patterns, no `adsk`).
3. [test] Loader + lookup unit tests.
4. [accept] User reviews the die data model / sample data.

## Phase 3 — Swept-bend execution

**Todos**
1. [lib] `lib/joints.py`: setback + arc geometry —
   `bend_setback(R_mm, theta)`, `bend_arc_length(R_mm, phi)`, and a
   `bend_plan(lines, corner, die)` returning trimmed legs + arc parameters.
2. [ui] `bend` joint type exposes die selection (per corner / per line).
3. [build] `_build_bend` in `entry.py`: sweep the section along a
   tangent-arc-tangent centerline (revolve of the profile about the bend axis),
   plus the two shortened straight legs.
4. [test] Geometry unit tests (setback, arc length, leg trim) — pure python.
5. [verify] Live-verify a swept 90 deg CHS/SHS bend.
6. [accept] User acceptance test.

## Phase 4 — Real corner geometry (miter / butt / cope cuts)

Phase 1 only *shortens* members along their axis. Phase 4 gives the corners
genuinely new end faces.

**Todos**
1. [lib] `lib/joints.py`: `corner_cuts(lines, joint_by_line)` -> cut plan dicts
   `{'member', 'role', 'kind': 'plane'|'body', 'point', 'normal', 'keep',
   'tool'}`. `kind='plane'` for miter (bisector), `kind='body'` for butt/cope
   (saddle against the neighbour).
2. [build] `entry.py`:
   - miter = **waste-prism combine-cut**: a construction plane through the
     vertex, a big square sketch on it, extruded one-sided to the waste side,
     then `combineFeatures` Cut with `isKeepToolBodies=False` (the prism is
     consumed). SplitBodyFeature is NOT usable in parametric designs — its
     waste half stays shared with the member's extrude, so deleting it
     cascade-deletes the kept half too.
   - butt/cope = combine-cut against the neighbour's body
     (`isKeepToolBodies=True`).
   - Bodies are located *geometrically* (`_find_body_near`: nearest
     bbox-centre to the member's line midpoint), because cuts prune/reparent
     features and shift indices.
   - `_apply_corner_cuts` returns every created object in delete order
     (combine feature, prism extrude, prism sketch, plane-helper sketch,
     plane) for `_clear_preview`; cutting features must go before member
     features.
3. [test] `TestCornerCutBuild`: miter -> 2 combines (tool consumed, no direct
   body deletes, 2 one-sided prism extrudes); butt -> 1 combine keeping the
   neighbour; cleanup deletes everything with zero leaks.
4. [verify] Live: IPE 80 3-line L-frame; every mitered member ends at the same
   volume (382.2 cm^3) and both corners read corner-to-corner in screenshots;
   butt/cope cut correctly; `_clear_preview` leaves features=0, sketches=1.

## Conventions to keep

- Pure geometry lives in `lib/` (no `adsk`), tested with stdlib `unittest`.
- `entry.py` is the only place touching `adsk.fusion`.
- Distances in Fusion are cm; profiles/dies in mm (`MM_TO_CM = 0.1`).
- After each phase: remind user to reload the add-in
  (Tools -> Add-Ins -> Weldments -> Reload).
