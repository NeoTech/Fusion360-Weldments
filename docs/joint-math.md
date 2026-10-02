# Weldments — Joint Geometry & Math

Derivations, formulas, sign conventions, and worked examples for every joint
type, written for **debugging**: when a corner is wrong, this doc tells you which
equation to check and what sign/units to expect. Code lives in
[`lib/joints.py`](../lib/joints.py); module overview in
[architecture.md](architecture.md).

- [Notation and conventions](#notation-and-conventions)
- [Corner detection math](#corner-detection-math)
- [Section extent math](#section-extent-math)
- [Butt joint](#butt-joint)
- [Miter joint](#miter-joint)
- [Cope / saddle joint](#cope--saddle-joint)
- [Bend joint](#bend-joint)
- [T-junctions](#t-junctions)
- [Sign conventions cheat-sheet](#sign-conventions-cheat-sheet)
- [Debugging recipes](#debugging-recipes)

---

## Notation and conventions

| Symbol | Meaning | Unit |
|---|---|---|
| `V` | Corner vertex (shared endpoint) | cm |
| `u`, `v` | Member **outward** unit dirs from `V` (away from corner) | — |
| `φ` (phi) | Angle **between** the outward dirs: `φ = acos(u·v)` | rad |
| `θ` (theta) | **Turn/deflection** angle: `θ = π − φ` | rad |
| `d` | Section depth (`member_depth`) | mm |
| `R` | Bend centerline radius (CLR) | mm |
| `t` | Wall thickness | mm |
| `h_o` | Half-extent of a section along a given axis | cm |

**Two angles, don't confuse them:**
- `φ` is the geometric angle between the members as drawn (90° corner → `φ=π/2`).
- `θ` is how much the path *turns* (90° corner → `θ=π/2`; straight run → `θ=0`).
- They are complements: `θ = π − φ`. A **straight-through** run has `φ=π, θ=0`;
  a **folded-back** run has `φ=0, θ=π`. Both are degenerate (no joint).

**Outward direction** of a line at a corner (`_outward`): if the corner is the
line's `start`, outward = `+direction`; if it's the line's `end`, outward =
`−direction`.

**Units:** all offsets returned by `corner_offsets` are **cm** (Fusion internal).
Depths/radii come in as **mm** and are scaled by `MM_TO_CM = 0.1`.

---

## Corner detection math

Two endpoints coincide when their squared distance is within tolerance:

$$\lVert P_a - P_b \rVert^2 \le \text{tol}^2, \qquad \text{tol} = 10^{-4}\ \text{cm} = 1\ \mu\text{m}$$

A cluster of ≥2 distinct lines (with ≥1 selected) is a corner. Endpoints are
clustered transitively (single-pass greedy: each unused point joins the first
cluster it's within tol of).

**Debug:** a corner that "should" exist isn't detected → the endpoints differ by
more than 1 µm. Check the sketch: are the lines snapped to a shared point, or do
they only *look* coincident? Zoom-level snapping errors of a few microns are the
usual culprit.

---

## Section extent math

The trim distance for butt/cope/miter is how far a member's material reaches
**along a neighbour's axis**. That is the *directional* half-extent
(`_half_extent_cm`), not a single scalar depth.

Given a section's placed basis `(axis_u, axis_v)` (world unit vectors ⟂ to its
own run) and a target world `axis` (usually the neighbour's run):

$$c_u = axis_u \cdot axis, \quad c_v = axis_v \cdot axis$$

For a **polygon** with local points `(u_i, v_i)` in mm:

$$h_o = \max_i \lvert u_i c_u + v_i c_v \rvert \times 0.1 \quad \text{(cm)}$$

For a **circle** of outer radius `r`:

$$h_o = r\sqrt{c_u^2 + c_v^2} \times 0.1$$

The `√(c_u²+c_v²)` factor is the projection of the plane's unit circle onto
`axis` — for a tube seen end-on it's the radius; foreshortened it shrinks.

**Fallback:** when no basis is supplied (unit tests), `h_o = member_depth/2 ×
0.1` (isotropic). `member_depth` = `2·max|coordinate|` for polygons, `2·r_outer`
for circles.

**Debug:** an I-beam butt trims by the wrong amount → confirm the basis is being
passed. Without it you get the web depth instead of the flange width. The
directional extent is exactly what makes an I-beam corner land on the flange.

---

## Butt joint

**Idea:** one member runs *through* the corner; the other stops at the through
member's face. Pure axial trim — **no boolean** (a boolean would notch into an
open section's hollow, which is wrong).

At a two-member corner, pick the through member `T` (`_butt_through`: an
explicitly-marked Through end, else a neighbour whose end isn't butt/cope, else
highest index). The other member `O` backs off.

With `sinφ = √(1 − (u·v)²)` (0 when collinear):

$$\text{trim} = \frac{h_o(T \text{ along } v_O)}{\sin\varphi} \qquad
  \text{grow} = \frac{h_o(O \text{ along } v_T)}{\sin\varphi}$$

The `1/sinφ` blows the perpendicular section extent out along the *incoming*
axis — at a shallow corner the member must stop much further back to clear the
neighbour's face.

The backing-off member's tip **reach** (offset from the vertex, + = into tool):

| Case | Reach |
|---|---|
| plain butt | `−trim` (near face, flat square) |
| saddle, solid tool | `+trim` (far face — boolean carves whole section) |
| saddle, hollow tool | `−trim + wall` (just past near wall — carves a saddle) |

The through member gets `+grow`. `bump(O, role, reach)` adds `−reach` to O's
start offset or `+reach` to O's end offset (see sign cheat-sheet).

**Why hollow stops at the near wall:** running a saddled member to the far face
of a tube leaves a **plug** floating in the void; stopping at the near face leaves
the tube wall poking through. `−trim + wall` removes exactly the wall it overlaps.

**Debug:**
- *Gap at a butt corner* → both members backed off. Only one should run through;
  check `_butt_through` picked a `T` (usually a Through checkbox on the wrong end).
- *Overlap / poke-through* → `sinφ` tiny (near-collinear) making `trim` explode,
  or the wall term missing on a hollow tool.
- *Butt into an I-beam leaves a notch* → a body cut fired when it shouldn't; a
  plain butt must emit **no** cut (only Saddle does).

---

## Miter joint

**Idea:** both members are cut on the **bisector** so mating faces coincide
(45° each at a 90° corner). Realised as: extend each member *past* the vertex by
the setback, then trim with a bisector **plane** (`corner_cuts`, `kind='plane'`).

Symmetric miter setback (from `member_depth` `d`):

$$SB = \frac{d/2}{\tan(\varphi/2)} \times 0.1 \quad \text{(cm)}$$

Each member bumps `+SB` (past the vertex). The cut plane's normal is the
bisector:

$$n = \text{normalize}(u_{own} - u_{neigh})$$

and the kept half is the member's own outward side (`keep = u_own`).

**Why extend then cut:** a miter face runs **corner-to-corner**, so the member
must reach past the centreline vertex; the bisector plane through `V` then slices
the diagonal. Without the extension the plane only clips the square end's centre
— no visible miter.

**Degenerate guards:** `SB=0` when `φ/2 ≤ 0` or `≥ π/2` (collinear or folded
back). No cut when `n·n < 1e-9` (members parallel → no bisector).

**Debug:**
- *Miter only cuts one member* → miter is a **two-member relationship**;
  `_propagate_corner_joint` must set it on both. A lone miter leaves the
  neighbour's square end poking through.
- *Diagonal face in the wrong direction* → check `keep`/`normal` sign; the plane
  keeps the half toward `u_own`.
- *No miter at 90°* → confirm `φ≈π/2` (not `θ`); using `θ` in `tan(φ/2)` gives
  the wrong setback.

---

## Cope / saddle joint

**Idea:** one member's end is notched to fit **over the side** of another — a
boolean saddle. This is a **T-junction** operation (end on another's interior),
not a shared-vertex corner.

Detection (`_point_on_segment_interior`): point `P` strictly inside segment `AB`:

$$t = \frac{(P-A)\cdot(B-A)}{\lVert B-A\rVert^2}, \quad 0 < t < 1, \quad
  \lVert P - (A + t(B-A))\rVert^2 \le \text{tol}^2$$

The strict `0<t<1` (plus endpoint-distance guards) means a point at `A` or `B` is
a **corner**, not a T.

The cut is `kind='body'`: split the member with the **tool's body**
(`isKeepToolBodies=True`), keeping the half away from the tool (`keep = u_own`,
`normal = −u_own`). The axial reach is the same table as the butt (near/far/wall).

**Cope Depth** (`cope_depth_by_line`, mm) adds penetration past the default
stopping face: `reach += depth × 0.1`.

**A cope at a shared vertex degrades to a butt** (axial trim, no boolean) —
coping mid-run is the only physical case.

**Debug:**
- *Cope does nothing* → it's at a corner, not a T-junction. Verify the end lands
  strictly inside the tool's run (`0<t<1`).
- *Floating plug after a cope* → tip overshot a hollow tool's near wall.
  `_remove_combine_orphans` keeps the tool + largest body and `Remove`s the rest.
- *Cope too shallow/deep* → Cope Depth spinner is read in **cm** (database units)
  and ×10'd to mm; a 10× error here is the classic bug.
- *Cope against an existing SHS/RHS does nothing* → the recovered section was
  misread as **round**. A filleted square tube has four small corner
  *cylinders*; recovery must route by the dominant face kind (≥3 planar normal
  axes ⇒ prismatic), not "any cylinder ⇒ round", or the cope reach is computed
  against a tiny circle instead of the real section (`_member_centerline`).

---

## Bend joint

**Idea:** a swept centerline **arc** of radius `R` (the die CLR) replaces the
sharp corner. Two legs shorten to their tangent points; a revolved arc fills the
gap.

Turn angle:

$$\theta = \pi - \varphi = \pi - \arccos(u \cdot v)$$

Setback (vertex → tangent, along each leg):

$$SB = R \tan(\theta/2) \times 0.1 \quad \text{(cm)}$$

Arc length:

$$L = R\,\theta \times 0.1 \quad \text{(cm)}$$

Arc centre (inside of the turn):

$$C = V + \frac{R}{\cos(\theta/2)}\,\text{normalize}(u+v) \times 0.1$$

Revolve axis (bend-plane normal):

$$a = \text{normalize}(u \times v)$$

Tangent points: `T_u = V + u·SB`, `T_v = V + v·SB`.

**Section orientation — a square tube bends about a FLAT face, never a rolled
corner.** The die groove bears on a face parallel to the bend plane; bending a
corner first collapses the tube. So each bend leg's section basis is set so
`axis_v` is parallel to the bend axis `a = u×v` (the bend-plane normal), placing
a pair of flat faces in the bend plane: `v = a`, `u = a×d` (see
`_bend_bases`). For a **planar** bend this equals `compute_basis(d, ref)`
exactly (there `a` *is* the shared `ref`), so it's a no-op on flat frames; it
only overrides the global reference on **non-planar (3D)** bends, where the
reference would otherwise roll a corner into the plane and twist the leg out of
the arc.

**Sweep direction = SIGN of the revolve angle**, not the axis. Fusion derives
direction from the ordered pair `(u,v)`, so the same corner sweeps ±θ by
selection order. `bend_plan` sets `direction = −1` when either leg is Inverse;
the builder multiplies the angle by it. **Negating the axis line does nothing**
(a sketch line has no direction).

**Guards:** no bend when `θ≈0` (straight) or `θ≈π` (folded back); no bend when
`R≤0` (no die / open section).

**Debug:**
- *Bend bends the wrong way* → Inverse flips the **angle sign** (`direction`),
  not the axis. If flipping Inverse does nothing, the sign isn't reaching
  `setAngleExtent`.
- *Arc doesn't meet the legs* → tangent points use `SB = R·tan(θ/2)`; if the legs
  weren't shortened by the same `SB` in `corner_offsets`, there's a gap/overlap.
- *Bend appears at a straight run* → `θ≈0` guard failed; check `φ` vs `θ` swap.
- *Only one leg curves* → bend is a **two-member relationship**; both selected
  legs must request `bend` (a context member counts as the other leg).

---

## T-junctions

Covered under [cope](#cope--saddle-joint) for the cut. For **offsets**, a member
whose end lands mid-run on a tool gets the same near/far/wall reach logic,
measured with the **tool's** half-extent along the incoming member's axis:

$$\text{reach} = \begin{cases}
  -h_o(\text{tool along } v_{member}) & \text{plain butt}\\
  +h_o & \text{saddle, solid}\\
  -h_o + wall & \text{saddle, hollow}
\end{cases}$$

(No `1/sinφ` here — a T is by construction perpendicular-ish; the incoming member
is trimmed to the tool's face directly.)

---

## Sign conventions cheat-sheet

`bump(idx, role, delta)` applies an axial offset `delta` (cm, + = **into** the
material / past the vertex):

| End | Effect of `+delta` |
|---|---|
| `start` | offset_start `−= delta` (start moves back, away from corner) |
| `end`   | offset_end `+= delta` (end moves forward, past the corner) |

So a member **ending** at a corner that must stop short gets a **negative**
reach at its `end`; a member **starting** at a corner that must extend gets a
**negative** offset_start. The through member's `grow` is positive (extends past).

Manual user offsets (Offset Start/End columns) are **added on top** of the joint's
computed offsets — never replace them.

---

## Debugging recipes

| Symptom | First equation to check |
|---|---|
| Corner not detected | Endpoint distance vs `tol=1e-4` cm — are lines truly snapped? |
| Gap at butt | `_butt_through` chose wrong `T`; both backed off. |
| Overlap at butt | `sinφ≈0` (near-collinear) → `trim` explodes. |
| Notch in open section | A body cut fired for a plain butt (should be trim-only). |
| Miter on one member only | Relationship not propagated to the neighbour. |
| Miter face backwards | `normal`/`keep` sign in `corner_cuts`. |
| Cope does nothing | It's a corner, not a T (`0<t<1` fails). |
| Floating plug | Cope overshot hollow near wall → orphan removal. |
| Bend wrong way | Inverse must flip angle **sign**, not axis. |
| Bend gap vs legs | `SB` mismatch between `corner_offsets` and `bend_plan`. |
| Everything 10× off | mm↔cm: a `'mm'` spinner's `.value` is cm; ×10 before use. |
| I-beam trims by web | Basis not passed → isotropic `member_depth/2` fallback. |

**Isolate a formula headlessly** (no Fusion): import `lib.joints`, build stub
lines exposing `worldGeometry.startPoint/.endPoint` (`.x/.y/.z` cm) and call
`corner_offsets` / `corner_cuts` / `bend_plan` directly — they're pure. Compare
the returned cm offsets against the hand-computed `SB`/`trim`/`grow` above.
