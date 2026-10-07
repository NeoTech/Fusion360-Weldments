# Weldments — an Autodesk Fusion add-in

Create parametric **weldment frames** (structural steel) by sweeping real
cross-sections along 3D sketch lines, with automatic corner treatment — butt,
miter, cope, and swept **tube bends** — and detection of members you have
*already* placed so new parts join them cleanly. A companion **toolbox** applies
single joints to existing members, lists the frame as a **BOM**, adds **gussets**,
and turns a bent tube into a **bend table** of shop-floor machine instructions.

> A weldment generator from simple lines. Pick the profile, pick the lines, and
> the add-in extrudes the correct section along each run and resolves every
> corner for you.

- **Author:** Andreas Pettersson
- **License:** MIT
- **Platform:** Autodesk Fusion (Windows / macOS), Python 3 API

---

## Table of contents

- [Why](#why)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Commands](#commands)
- [Usage](#usage)
- [Joint types](#joint-types)
- [Profile families](#profile-families)
- [Bend dies](#bend-dies)
- [Bend table](#bend-table)
- [Position alignment grid](#position-alignment-grid)
- [Detecting existing members](#detecting-existing-members)
- [Project structure](#project-structure)
- [Architecture](#architecture)
- [Data files](#data-files)
- [Testing](#testing)
- [Development notes](#development-notes)
- [License](#license)

---

## Why

Building a tubular frame or welded structure in Fusion by hand means extruding a
profile along every line, then manually trimming each corner so the members
don't overlap, don't leave gaps, and actually look like a fabricated joint. Coping
a tube so it saddles over another, or mitring two members at 45°, is fiddly and
breaks the parametric history the moment you add a second part to a frame you
already built.

This add-in automates all of that:

- Extrude a real EN-standard section along any number of 3D sketch lines.
- Choose a joint per **line end** and let the geometry resolve the trim, setback,
  or boolean automatically.
- Sweep a tube **bend** (a centerline arc from your shop's bend die) instead of a
  sharp corner.
- **Add to an existing frame** without drawing shadow parts or deleting
  duplicates — the design's already-placed members are detected and used as the
  tool to bend / cope / miter / butt against.

---

## Features

- **Multi-line extrusion** of parametric profiles along 3D sketch lines, one row
  per line with per-line rotation and start/end offsets.
- **Per-end corner joints** — each line has a *Joint Start* and *Joint End*
  dropdown, so the two ends of one member can differ (e.g. miter one end, butt
  the other).
- **Butt / Miter / Cope / Bend** joint vocabulary, filtered to what each profile
  family can physically be fabricated as.
- **Position alignment grid** — place each section on the layout line by its
  centre, a face, or a corner (a global 3×3 dropdown), so members can be flush
  on their outer faces instead of centre-aligned.
- **Swept tube bends** driven by a bend-die catalogue (centerline radius), built
  as a revolved arc that blends the two legs.
- **T-junction coping** — a member's end landing mid-run on another is saddled to
  its outer surface (with a fish-mouth depth control).
- **Existing-member detection** — new parts join already-placed weldments
  directly, keeping the parametric history clean.
- **Live preview** — the whole frame ghosts in the viewport as you edit, and
  corner cuts preview before you commit.
- **A joint toolbox** — standalone **Weld Bend / Cope / Miter / Butt** commands
  apply one joint to two already-placed members without rebuilding the frame.
- **Bill of materials** — a docked **Weldment BOM** palette lists every member
  with its profile, length, and mass, live from the design's member registry.
- **Bend table** — a **Bend Table** command reads one bent tube and prints the
  rotary-draw instructions: tangent marks, feed distance, bend angle, clock
  rotation, and die, per bend, from a chosen end.
- **Gussets** — **Corner Gusset** and **Profile Gusset** add reinforcing plates
  at frame corners.
- **Member registry** — every member and joint is stored as an explicit record on
  the design, so tools edit records instead of re-detecting topology each run.
- **Data-driven** — profiles and bend dies are plain JSON you can extend without
  touching code.

---

## Requirements

- **Autodesk Fusion** with the Python 3 API (the add-in ships with the standard
  Fusion desktop install).
- **Python 3** — the add-in runs inside Fusion's bundled interpreter. The pure
  geometry libraries and the test suite run on any CPython 3.11+ (developed on
  3.13).
- No third-party dependencies. Everything is the Fusion `adsk` API plus the
  standard library.

---

## Installation

1. Close Fusion (or be ready to reload the add-in).
2. Place this `Weldments` folder in the Fusion add-ins directory:

   - **Windows:** `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\Weldments`
   - **macOS:** `~/Library/Application Support/Autodesk/Autodesk Fusion 360/API/AddIns/Weldments`

   The folder must contain `Weldments.py` and `Weldments.manifest` at its root.
3. Start Fusion. The add-in loads on startup and adds a **Weldments** tab to the
   ribbon, with the commands pinned to its **Weldments** and **Data** panels
   (see [Commands](#commands)).

To reload after editing code without restarting Fusion, use
**Add-Ins → Weldments → Reload** (or restart Fusion).

> The auxiliary commands from the add-in template — *Command Dialog Sample*,
> *Show My Palette*, *Send to Palette* — are scaffold/demo commands and are not
> part of the weldment workflow.

---

## Commands

| Command | Panel | What it does |
|---------|-------|--------------|
| **Weldment** | Weldments | The main builder: extrude profiles along sketch lines and resolve every corner. |
| **Weld Bend** | Weldments | Sweep a bend between two existing tube legs (die-driven centerline arc). |
| **Weld Cope** | Weldments | Saddle/notch one member's end over another at a T-junction. |
| **Weld Miter** | Weldments | Cut a 45° (bisector) miter between two existing members. |
| **Weld Butt** | Weldments | Trim one existing member to butt flush against another. |
| **Corner Gusset** | Weldments | Add a reinforcing gusset plate at a frame corner. |
| **Profile Gusset** | Weldments | Add a gusset shaped to the members' profile faces. |
| **Bend Table** | Weldments | Output rotary-draw bending instructions (marks, feed, angle, clock, die) for one bent tube. |
| **Weldment BOM** | Data | A docked bill-of-materials palette listing every member, live from the registry. |

The eight **Weldments**-panel commands share one design: pick the member(s),
set the joint parameters, preview, and commit. **Bend Table** and **Weldment
BOM** are read-only outputs — they describe what is already built rather than
changing it.

---

## Usage

1. Draw the frame path as **3D sketch lines** (one line per member).
2. Run **Create → Weldment**.
3. **Lines** — select the 3D sketch line(s). A row appears per line in the table.
4. **Profile** — pick the family (IPE, HEA, HEB, UPE, UPN, SHS, RHS, CHS).
5. **Designation** — pick the size (e.g. `CHS 48.3 x 2.0`).
6. **Position** — choose which point of the section sits on the layout line
   (centre, a face, or a corner — a global 3×3 grid; see
   [Position alignment grid](#position-alignment-grid)).
7. **Per-Line table** — for each line set:
   - **Joint Start / Joint End** — the corner treatment at each end.
   - **Through / Saddle** — checkboxes that refine a *butt* (Through = this
     member runs past the corner; Saddle = notch the butt end to the neighbour's
     surface).
   - **Rotation** — spin the section about its own axis.
   - **Offset Start / Offset End** — manual length adjustments (added on top of
     the joint's computed trim).
   - **Inverse** — flip a swept bend's direction (concave vs. convex).
   - **Bend Die** — for a `bend` end, the die (centerline radius) to sweep.
   - **Cope Depth** — extra fish-mouth penetration for a saddled/cope end.
8. Watch the **live preview**, then **OK** to commit.

**Sync all** (table toolbar, on by default) propagates an edit in any row to
every row; turn it off to give each line its own values and manipulator arrows.

---

## Joint types

| id | Meaning | Realised by |
|----|---------|-------------|
| `none` | Beam runs full length to the vertex (default). | No trim. |
| `butt` | One member stops at the through neighbour's near face; the neighbour extends past the vertex so the corner reads flush. | Pure axial trim (`corner_offsets`) — no boolean. |
| `miter` | Both members cut on the bisector so mating faces coincide (45° each at a 90° corner). | Split Body by the bisector plane + `Remove` of the waste (`corner_cuts`, `kind='plane'`). |
| `cope` | A member's end notched/saddled to fit over the other's outer face. | Combine-cut against the neighbour's body (`kind='body'`), at a T-junction. |
| `bend` | A continuous swept centerline arc of radius CLR replaces the sharp corner. | Revolved arc about the bend axis (`bend_plan`). |

Notes:

- A **butt** never booleans into an open section's hollow interior — it is a
  length trim plus an extension, so it stays clean and parametric.
- A **cope** fires at a **T-junction** (one member's end on another's mid-run);
  where a cope's end coincides with a shared-vertex corner it degrades to a butt
  trim.
- **Miter** and **Bend** are two-member *relationships*: they resolve only when
  the geometry at the vertex is well-formed. Setting one on a line propagates the
  relationship to the other member at that corner.
- Which joints a family offers is declared in `data/profiles.json` (see below) —
  e.g. round tube (CHS) offers `cope` instead of a planar `miter`, and open
  sections (IPE/HEA/…) can't be swept-bent.

---

## Profile families

The shipped catalogue (`data/profiles.json`) covers EN 10365 hot-rolled and
hollow sections:

| Family | Description | Sizes | Joints |
|--------|-------------|:-----:|--------|
| **IPE** | Parallel Flange I-Beams | 18 | none, butt, miter |
| **HEA** | Wide Flange Beams (Light) | 19 | none, butt, miter |
| **HEB** | Wide Flange Beams (Medium) | 19 | none, butt, miter |
| **UPE** | Parallel Flange Channels | 14 | none, butt, miter |
| **UPN** | Taper Flange Channels | 12 | none, butt, miter |
| **SHS** | Square Hollow Sections | 54 | none, butt, miter, cope, bend |
| **RHS** | Rectangular Hollow Sections | 71 | none, butt, miter, cope, bend |
| **CHS** | Circular Hollow Sections | 109 | none, butt, cope, bend |

Each designation carries its section dimensions (`h_mm`, `b_mm`, wall/flange
thicknesses, root radius, mass) from which the cross-section loops are generated.

---

## Bend dies

Swept bends are driven by `data/bendingdies.json`, reduced to the one value that
shapes a bend: the **centerline radius** (`clr_mm`). One entry per
(family, CLR); the family filters the dropdown and the CLR sets the arc radius.

- **CHS:** 23 dies (rotary-draw CLRs from 57.15 mm up).
- **SHS:** 6 dies.
- **RHS:** shares SHS's 6 dies — a rectangular tube is bent on the same
  flat-face tooling as a square one (see `_DIE_FAMILY_ALIASES`).

The setback that trims each leg to its tangent point is `SB = R · tan(θ/2)` and
the arc length is `L = R · θ` (θ = corner turn angle, R = CLR).

---

## Bend table

A rotary-draw bender needs more than the geometry — it needs a **sequence**:
where to scribe the marks on the straight tube, how far to feed, and how far to
rotate the tube between bends. The **Bend Table** command produces exactly that
from a bent tube already in the design.

Pick **one leg** of the tube. The rest of the tube is found by following the
`bend` joints (each records both legs it joins), so there is nothing to select in
order. The machine's zero point is the **free end nearest where you clicked** —
a bender always feeds from an open end — and a **Read from the other end**
checkbox flips to the far end if you clicked the wrong one.

The table lists, per bend in feed order:

| Column | Meaning |
|--------|---------|
| **Start mark / End mark** | Where to scribe the two tangent points on the unfolded tube. |
| **Feed** | Distance from the datum end to the far mark — how far the tube travels for this bend. |
| **Angle** | The geometric turn (springback is the operator's problem, deliberately out of scope). |
| **Clock** | How far to rotate the tube about its own axis before this bend, from the previous bend's plane. |
| **Die** | The bend die (centerline radius) recorded on that joint. |

**Clock convention:** positive is **clockwise as seen by the operator standing
behind the machine, looking down the tube along the feed direction**. Two bends
in the same plane (a U / hairpin) clock 0°; a bend that reverses the curve (a Z /
staircase) clocks 180°; a rolled (out-of-plane) bend clocks ±90° — matching how
they are physically made.

**Developed length** is reported alongside: the sum of the straight legs minus
what each bend saves (`2·SB − L`), i.e. how much flat tube the part consumes.
Pressing **OK** also writes the table to a CSV next to the design for the shop
floor.

The math is pure and unit-tested in [`lib/bend_sequence.py`](lib/bend_sequence.py).

---

## Position alignment grid

By default every section is placed with its **centroid on the picked sketch
line**. The **Position** dropdown (global — one value for the whole selection,
since members of a frame must share a reference plane) instead chooses *which
point of the cross-section lies on the line*, as a 3×3 grid over the section's
bounding box:

```
top-left     top        top-right
left         CENTER     right
bottom-left  bottom     bottom-right
```

This is how you build a frame whose members are flush on their **outer faces**
(e.g. a table top where every tube's top skin is coplanar) rather than
centre-aligned on the layout sketch.

**How it works (the anchor model).** The picked line stays a *reference*: the
chosen grid point — the section's **anchor** — is placed on it, and the whole
section is rigidly translated by `-anchor` along its placed basis. Corner
detection still runs on the original (undisplaced) lines, so shared vertices are
never broken; the butt/cope/T trims simply measure each neighbour's extent
**from its anchor** (the reference line through the vertex) instead of its
displaced centroid — see `_half_extent_cm(..., anchor=)` in `lib/joints.py`.

**Correctness by direction:**

- **Out-of-plane** positions (`top`/`bottom` and the four corners' vertical
  half) are a pure rigid translation of the member — correct for **every**
  joint type.
- **In-plane** positions (`left`/`right`) keep the ends on the reference line:
  butt/cope/T trims are exact (anchor-based extent), miter is exact (its cut
  plane normal is in-plane), and a swept bend shifts its arc by the same leg
  displacement so the bend still tracks the displaced member.

`center` (the default) reproduces the historical placement exactly — no offset
arithmetic runs at all.

---

## Detecting existing members

When you add to a frame you already built, the add-in **automatically scans the
design** and recovers each existing member's centerline and section from its body
geometry (the dominant cylindrical face gives the run axis and radii). Those
recovered members become *context* for corner and T-junction detection, so a new
line's end can:

- **butt / miter / cope** against an existing member (its real body is the boolean
  tool — no shadow part, no duplicate to delete), or
- **bend** into an existing member's end.

Existing members are never edited (they have no table row); the joint is set only
on the new line's side. This keeps the parametric history clean, which is the
whole point of the feature.

---

## Project structure

```
Weldments/
├── Weldments.py              # add-in entry point (run / stop)
├── Weldments.manifest        # Fusion add-in manifest
├── config.py                 # shared globals (DEBUG, ids, icon folder)
├── AddInIcon.svg             # command icon
├── commands/
│   ├── __init__.py           # registers all commands
│   ├── weldment/             # the Weldment command (the main builder)
│   │   ├── entry.py          # dialog + Fusion API glue (the adsk.fusion touchpoint)
│   │   └── resources/        # command icons
│   ├── weldmentBend/         # Weld Bend  (bend two existing legs)
│   ├── weldmentCope/         # Weld Cope   (saddle one end over another)
│   ├── weldmentMiter/        # Weld Miter  (bisector cut between members)
│   ├── weldmentButt/         # Weld Butt   (trim one member flush)
│   ├── weldmentGusset/       # Corner Gusset
│   ├── weldmentGussetProfile/# Profile Gusset
│   ├── weldmentBendTable/    # Bend Table  (rotary-draw instructions + CSV)
│   ├── weldmentBom/          # Weldment BOM (docked palette)
│   ├── commandDialog/        # template demo command
│   ├── paletteShow/          # template demo command
│   └── paletteSend/          # template demo command
├── lib/
│   ├── profiles.py           # pure: profile data + cross-section geometry
│   ├── joints.py             # pure: corner/T-junction detection + joint geometry
│   ├── bending_dies.py       # pure: bend-die catalogue loader
│   ├── registry.py           # pure: member/joint records + JSON (de)serialisation
│   ├── gussets.py            # pure: gusset plate geometry
│   ├── bend_sequence.py      # pure: bend chain-walk, marks, feed, clock angle
│   └── fusionAddInUtils/     # shared add-in helpers (event/error utils)
├── data/
│   ├── profiles.json         # EN section catalogue
│   └── bendingdies.json      # bend-die (CLR) catalogue
├── tests/
│   ├── adsk_stub.py          # fake adsk API for headless tests
│   ├── test_profiles.py
│   ├── test_joints.py
│   ├── test_joint_spec.py
│   ├── test_bending_dies.py
│   ├── test_registry.py
│   ├── test_gussets.py
│   ├── test_bend_sequence.py
│   └── test_command.py       # command-layer tests against the stub
├── tools/
│   └── make_icons.py         # generates each command's unique PNG icons
├── docs/
│   ├── architecture.md       # in-depth technical reference for the codebase
│   ├── joint-math.md         # derivations + debugging recipes per joint type
│   └── phase4-ui-rethink.md  # the registry-backed toolbox design
└── README.md
```

---

## Architecture

> Full module-by-module reference: [`docs/architecture.md`](docs/architecture.md).
> The math behind each joint (for debugging): [`docs/joint-math.md`](docs/joint-math.md).

The code is split so the hard geometry is testable without Fusion:

- **`lib/` is pure Python** — no `adsk` imports. `profiles`, `joints`,
  `bending_dies`, `registry`, `gussets`, and `bend_sequence` do all the math
  (section loops, corner detection, offsets, setbacks, bend plans, member/joint
  records, gusset plates, bend sequences) and are unit-tested headlessly.
- **`commands/*/entry.py` is the only place that touches `adsk.fusion`** — it
  builds the dialog, reads inputs, calls into `lib/`, and turns the returned
  plans into real features (extrudes, revolves, combines, removes).

Key contracts:

- A **"line"** is anything exposing `worldGeometry.startPoint` / `.endPoint`
  (each `.x/.y/.z` in cm) and `.length` — real sketch lines and the test stub
  both qualify.
- **Units:** Fusion's internal length unit is centimetres; the JSON catalogues are
  in millimetres. `MM_TO_CM = 0.1` bridges them at the boundaries.
- **Detection** (`joints.detect_corners` / `detect_t_junctions`) finds shared
  vertices and mid-run landings; `corner_offsets` turns joints into length trims;
  `corner_cuts` plans the real boolean/plane cuts; `bend_plan` lays out swept arcs.
- **Existing members** are recovered in `entry.py` (`_recover_existing_members`)
  and passed to the `lib/` functions as a `context` list, so detection treats them
  as neighbours without ever editing them.
- **The registry** (`lib/registry.py`) is the toolbox's data backbone: every
  member and joint is stored as an explicit record on a design attribute (JSON),
  so a tool edits a record instead of re-detecting the whole frame's topology from
  bodies each run. The BOM and Bend Table read straight from it.

---

## Data files

Both catalogues are plain JSON and safe to extend.

**`data/profiles.json`** — a list of families, each with metadata, a `joints`
array (which corner treatments the family supports), and
`standard_designations` (sizes with their dimensions in mm).

**`data/bendingdies.json`** — an object with a `dies` list; each die is
`{die_id, profile_family, clr_mm}`.

To add a size, append a designation with its `h_mm` / `b_mm` / wall fields. To add
a die, append `{die_id, profile_family, clr_mm}`. Reload the add-in to pick up
changes.

---

## Testing

The suite runs on plain CPython — no Fusion needed (an `adsk` stub stands in for
the API).

```bash
python -m unittest discover -s tests
```

**387 tests** cover:

- `test_profiles.py` — section geometry, designations, basis vectors, and the
  Position grid helpers (extents, anchors, displacement).
- `test_joints.py` — corner/T-junction detection, butt/miter/cope/bend offsets,
  bend plans, **existing-member context** (a new member joining a placed one),
  and the Position anchor trims.
- `test_joint_spec.py` — the joint-spec layer that turns per-end joint choices
  into the concrete cut/trim operations.
- `test_bending_dies.py` — catalogue loading and die selection (incl. the
  RHS→SHS die alias).
- `test_registry.py` — member/joint records, JSON round-tripping, and geometry
  lookups (the toolbox data backbone).
- `test_gussets.py` — gusset plate geometry.
- `test_bend_sequence.py` — the Bend Table planner: chain walk, developed-length
  marks, feed, the clock-angle convention, and tube discovery from one leg.
- `test_command.py` — the command layer against the stub: weldment building,
  per-line table, joint propagation, corner-cut construction, cope-orphan removal,
  existing-member recovery, and the Position dropdown → anchor → build wiring.

---

## Development notes

A few non-obvious things that bit during development, kept here so future
changes don't regress them:

- **`Command` events are limited.** Valid ones include `activate`, `destroy`,
  `execute`, `executePreview`, `inputChanged`, `validateInputs`. There is **no**
  `preExecute`/`commandExecuting`/`select` — touching a non-existent event raises
  `AttributeError` *inside* `command_created`, aborting the rest of it so the
  dialog renders but is inert. Populate dropdowns in `command_created`, rebuild
  on `inputChanged`, and use `activate` to read a selection inherited from the
  canvas (which does **not** fire `inputChanged`).
- **Origin planes** live on the `Component` (`root.yZConstructionPlane`, capital
  Z), not `root.origin`.
- **Linear `ValueCommandInput.value` is in cm** (internal units) — multiply by 10
  for mm; dimensionless inputs use an empty unit string.
- **Bend direction is the SIGN of the revolve angle**, not the axis vector (a
  sketch line has no direction, so negating the axis is a no-op). The `Inverse`
  control flips the angle's sign via `bend_plan`'s `direction`.
- **Cope depth** read from a `'mm'` spinner is actually in cm (database units) —
  convert ×10 before feeding the mm-based joint layer.
- **Cope survivor:** a cope tip overshooting a hollow tool's near wall shaves a
  plug that floats in the void. `_survivor_after_cut` keeps the tool + the
  member's main run (the largest body in the joint region) so the spine
  re-stamps the right body.
- **Miter = wedge prism + Combine(Cut).** `_miter_cutter` builds a finite prism
  on the bisector plane whose `+nrm` face lies exactly on the miter face, then
  `Combine(Cut, keep_tool=False)` trims the member to that diagonal. Both members
  are cut by prisms on the identical bisector plane, so their faces coincide for
  any Rotation or Position — no Split Body and no `pointContainment` guess. (An
  earlier Split Body + `Remove` approach was retired; deleting the combine cleanly
  restores the body for preview teardown.)
- **Joint/flag lookups must never receive a negative index** — Python's
  `list[-1]` is the last element. Context members are encoded as negative indices
  (`~k`) and guarded so they're treated as "never edited".
- **To live-test edited `lib/` code without restarting Fusion**, `importlib.reload`
  the `joints` module first, then `entry` (so `entry` rebinds to the reloaded
  `joints`). Reload only redefines functions; command registration happens in
  `run()`, so it's safe.

---

## License

MIT © [Andreas Pettersson](LICENSE)
