# Weldments — an Autodesk Fusion add-in

Create parametric **weldment frames** (structural steel) by sweeping real
cross-sections along 3D sketch lines, with automatic corner treatment — butt,
miter, cope, and swept **tube bends** — and detection of members you have
*already* placed so new parts join them cleanly.

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
- [Usage](#usage)
- [Joint types](#joint-types)
- [Profile families](#profile-families)
- [Bend dies](#bend-dies)
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
- **Swept tube bends** driven by a bend-die catalogue (centerline radius), built
  as a revolved arc that blends the two legs.
- **T-junction coping** — a member's end landing mid-run on another is saddled to
  its outer surface (with a fish-mouth depth control).
- **Existing-member detection** — new parts join already-placed weldments
  directly, keeping the parametric history clean.
- **Live preview** — the whole frame ghosts in the viewport as you edit, and
  corner cuts preview before you commit.
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
3. Start Fusion. The add-in loads on startup and adds a **Weldment** command to
   the **Create** panel (promoted, next to the Pipe tool).

To reload after editing code without restarting Fusion, use
**Add-Ins → Weldments → Reload** (or restart Fusion).

> The auxiliary commands from the add-in template — *Command Dialog Sample*,
> *Show My Palette*, *Send to Palette* — are scaffold/demo commands and are not
> part of the weldment workflow.

---

## Usage

1. Draw the frame path as **3D sketch lines** (one line per member).
2. Run **Create → Weldment**.
3. **Lines** — select the 3D sketch line(s). A row appears per line in the table.
4. **Profile** — pick the family (IPE, HEA, HEB, UPE, UPN, SHS, RHS, CHS).
5. **Designation** — pick the size (e.g. `CHS 48.3 x 2.0`).
6. **Per-Line table** — for each line set:
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
7. Watch the **live preview**, then **OK** to commit.

**Sync all** (table toolbar, on by default) propagates an edit in any row to
every row; turn it off to give each line its own values and manipulator arrows.

---

## Joint types

| id | Meaning | Realised by |
|----|---------|-------------|
| `none` | Beam runs full length to the vertex (default). | No trim. |
| `butt` | One member stops at the through neighbour's near face; the neighbour extends past the vertex so the corner reads flush. | Pure axial trim (`corner_offsets`) — no boolean. |
| `miter` | Both members cut on the bisector so mating faces coincide (45° each at a 90° corner). | Combine-cut against a waste prism (`corner_cuts`, `kind='plane'`). |
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

The setback that trims each leg to its tangent point is `SB = R · tan(θ/2)` and
the arc length is `L = R · θ` (θ = corner turn angle, R = CLR).

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
├── config.py                 # shared globals (DEBUG, ids)
├── AddInIcon.svg             # command icon
├── commands/
│   ├── __init__.py           # registers all commands
│   ├── weldment/             # the Weldment command (the real feature)
│   │   ├── entry.py          # dialog + Fusion API glue (only adsk.fusion touchpoint)
│   │   └── resources/        # command icon
│   ├── commandDialog/        # template demo command
│   ├── paletteShow/          # template demo command
│   └── paletteSend/          # template demo command
├── lib/
│   ├── profiles.py           # pure: profile data + cross-section geometry
│   ├── joints.py             # pure: corner/T-junction detection + joint geometry
│   ├── bending_dies.py       # pure: bend-die catalogue loader
│   └── fusionAddInUtils/     # shared add-in helpers (event/error utils)
├── data/
│   ├── profiles.json         # EN section catalogue
│   └── bendingdies.json      # bend-die (CLR) catalogue
├── tests/
│   ├── adsk_stub.py          # fake adsk API for headless tests
│   ├── test_profiles.py
│   ├── test_joints.py
│   ├── test_bending_dies.py
│   └── test_command.py       # command-layer tests against the stub
├── docs/
│   └── corner-joints-plan.md # the phased design plan
└── README.md
```

---

## Architecture

The code is split so the hard geometry is testable without Fusion:

- **`lib/` is pure Python** — no `adsk` imports. `profiles`, `joints`, and
  `bending_dies` do all the math (section loops, corner detection, offsets,
  setbacks, bend plans) and are unit-tested headlessly.
- **`commands/weldment/entry.py` is the only place that touches `adsk.fusion`** —
  it builds the dialog, reads inputs, calls into `lib/`, and turns the returned
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

**164 tests** cover:

- `test_profiles.py` — section geometry, designations, basis vectors.
- `test_joints.py` — corner/T-junction detection, butt/miter/cope/bend offsets,
  bend plans, and **existing-member context** (a new member joining a placed one).
- `test_bending_dies.py` — catalogue loading and die selection.
- `test_command.py` — the command layer against the stub: weldment building,
  per-line table, joint propagation, corner-cut construction, cope-orphan removal,
  and existing-member recovery.

---

## Development notes

A few non-obvious things that bit during development, kept here so future
changes don't regress them:

- **`Command` has no `preExecute` event.** Populate dropdowns in
  `command_created` and rebuild on `inputChanged`.
- **Origin planes** live on the `Component` (`root.yZConstructionPlane`, capital
  Z), not `root.origin`.
- **Linear `ValueCommandInput.value` is in cm** (internal units) — multiply by 10
  for mm; dimensionless inputs use an empty unit string.
- **Bend direction is the SIGN of the revolve angle**, not the axis vector (a
  sketch line has no direction, so negating the axis is a no-op). The `Inverse`
  control flips the angle's sign via `bend_plan`'s `direction`.
- **Cope depth** read from a `'mm'` spinner is actually in cm (database units) —
  convert ×10 before feeding the mm-based joint layer.
- **Cope orphans:** a cope tip overshooting a hollow tool's near wall shaves a
  plug that floats in the void. Keep the tool + the largest remaining body and
  issue a `Remove` feature on the rest (Remove keeps the parametric flow intact).
- **`SplitBodyFeature` is unusable in parametric designs** (deleting the waste
  half cascade-deletes the kept half), so a miter is a combine against a hidden
  waste prism instead.
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
