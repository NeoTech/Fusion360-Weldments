"""Pure-Python weldment profile data + cross-section geometry.

Everything in this module is deliberately free of the ``adsk`` API so it can be
unit-tested outside of Fusion.  The command layer (``commands/weldment/entry.py``)
imports these helpers and is the only place that touches ``adsk.*``.

Geometry convention
-------------------
A cross-section is described in its own local 2D frame ``(u, v)`` in **millimetres**
centred on the section centroid:

* ``v`` runs along the section *height* ``h`` (the "up" axis of the profile).
* ``u`` runs along the section *width* ``b``.

The command maps ``(u, v)`` into model space using two in-plane basis vectors that
are derived from the picked line direction, then draws the loops on the profile
sketch.
"""

import json
import math
import os

# Fusion internal length unit is centimetres; profiles.json is in millimetres.
MM_TO_CM = 0.1

_DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "profiles.json")


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_profiles(path=_DATA_PATH):
    """Load the profile catalogue from ``data/profiles.json``.

    Returns a list of family dicts exactly as stored in the JSON file.
    """
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def family_labels(families):
    """Human-readable one-line label per family, e.g. ``'IPE - Parallel Flange I-Beams'``."""
    return [f"{f['abbreviation']} - {f['profile_family']}" for f in families]


def designations(family):
    """List of designation dicts for one family."""
    return family.get("standard_designations", [])


def designation_labels(family):
    """List of designation names (the value shown in the dropdown)."""
    return [d["designation"] for d in designations(family)]


def find_designation(family, label):
    """Return the designation dict whose ``designation`` equals ``label`` (or None)."""
    for d in designations(family):
        if d["designation"] == label:
            return d
    return None


# --------------------------------------------------------------------------- #
# Small pure vector helpers (tuples, no dependency on adsk)
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(a):
    m = math.sqrt(_dot(a, a))
    if m == 0.0:
        return (0.0, 0.0, 0.0)
    return (a[0] / m, a[1] / m, a[2] / m)


def compute_basis(direction, ref=None):
    """Return ``(axis_u, axis_v)`` unit vectors perpendicular to ``direction``.

    ``direction`` is the (unit) line direction and also the extrude/plane-normal
    axis.  ``axis_v`` is the profile "height" direction and ``axis_u`` the profile
    "width" direction.

    ``ref`` is an optional shared "up" reference for a whole selection (see
    :func:`selection_reference`).  When given (and not parallel to ``direction``)
    it is projected onto the plane perpendicular to the line, so every profile in
    the selection is rolled the same way and joined/mitred ends line up.  When it
    is omitted (or the line is parallel to it) a stable per-line world reference
    is used instead.
    """
    d = _norm(direction)
    if ref is not None:
        r = _norm(ref)
        if r != (0.0, 0.0, 0.0) and abs(_dot(r, d)) < 0.999:
            axis_v = _norm(_sub(r, _scale(d, _dot(r, d))))
            axis_u = _norm(_cross(axis_v, d))
            return axis_u, axis_v
    # Fallback reference "up" candidate: world Z, unless the line is near-vertical.
    ref2 = (0.0, 0.0, 1.0) if abs(d[2]) < 0.95 else (1.0, 0.0, 0.0)
    axis_v = _norm(_sub(ref2, _scale(d, _dot(ref2, d))))
    axis_u = _norm(_cross(axis_v, d))
    return axis_u, axis_v


def selection_reference(directions):
    """Return one shared "up" reference for a whole selection of line directions.

    The reference is the normal of the plane the lines span -- the cross product
    of the first two non-parallel directions.  Passing this to every
    :func:`compute_basis` call keeps all profiles rolled identically, so joined
    or mitred ends align.  This is what makes a 3D-sketch frame behave like a 2D
    one (where the shared reference is simply the sketch normal).  Returns None
    when every line is parallel (or there is only one), in which case the
    per-line fallback in :func:`compute_basis` is already consistent.
    """
    ds = [_norm(d) for d in directions]
    ds = [d for d in ds if d != (0.0, 0.0, 0.0)]
    for i in range(len(ds)):
        for j in range(i + 1, len(ds)):
            n = _cross(ds[i], ds[j])
            if _dot(n, n) > 1e-8:
                return _norm(n)
    return None


def rotate_basis(axis_u, axis_v, angle_rad):
    """Rotate a profile basis ``(axis_u, axis_v)`` about the line axis.

    ``axis_u``/``axis_v`` are the (orthonormal) width/height directions returned
    by :func:`compute_basis`, both perpendicular to the extrude (line) direction.
    Spinning them within their own plane is exactly a rotation of the placed
    profile *around the selected line* -- the profile turns about its centre
    point along the line axis and never about any world axis.  ``angle_rad`` is
    in radians (positive = counter-clockwise looking back along the line).
    """
    if not angle_rad:
        return axis_u, axis_v
    c, s = math.cos(angle_rad), math.sin(angle_rad)
    u = axis_u
    v = axis_v
    rot_u = (u[0] * c + v[0] * s, u[1] * c + v[1] * s, u[2] * c + v[2] * s)
    rot_v = (u[0] * -s + v[0] * c, u[1] * -s + v[1] * c, u[2] * -s + v[2] * c)
    return _norm(rot_u), _norm(rot_v)


def map_local_to_model(origin, axis_u, axis_v, u_mm, v_mm):
    """Map a local ``(u, v)`` millimetre point to a model-space point (cm)."""
    return (origin[0] + u_mm * MM_TO_CM * axis_u[0] + v_mm * MM_TO_CM * axis_v[0],
            origin[1] + u_mm * MM_TO_CM * axis_u[1] + v_mm * MM_TO_CM * axis_v[1],
            origin[2] + u_mm * MM_TO_CM * axis_u[2] + v_mm * MM_TO_CM * axis_v[2])


# --------------------------------------------------------------------------- #
# Cross-section outline builders
# --------------------------------------------------------------------------- #
# A loop is a plain list of ``(u, v)`` corner points in mm; the drawing layer
# connects consecutive points with straight lines and closes the loop.  Alongside
# each loop a parallel list of ``(corner_index, radius_mm)`` fillets is emitted;
# the drawing layer applies them with the sketch fillet tool
# (SketchArcs.addFillet), which trims the two edges and inserts the tangent arc.
# ``corner_index i`` is the vertex where edge (i-1 -> i) meets edge (i -> i+1).


def _ibeam_loops(h, b, tw, tf, r=0.0):
    """Parallel-flange I / H section -> one closed 12-point polygon.

    ``r`` is the web-to-flange fillet radius; the four *internal* corners are
    filleted (the outer tips are physically sharp on hot-rolled sections).
    """
    hb, ht, wt = b / 2.0, h / 2.0, tw / 2.0
    loop = [
        (-hb,  ht), ( hb,  ht), ( hb,  ht - tf), ( wt,  ht - tf),
        ( wt, -ht + tf), ( hb, -ht + tf), ( hb, -ht), (-hb, -ht),
        (-hb, -ht + tf), (-wt, -ht + tf), (-wt,  ht - tf), (-hb,  ht - tf),
    ]
    r = max(0.0, min(r, hb - wt, tf))
    fillets = [(i, r) for i in (3, 4, 9, 10)] if r > 0.0 else []
    return [loop], [fillets]


def _channel_loops(h, b, tw, tf, r=0.0):
    """Parallel/tapered-flange channel (C section) -> one closed 8-point polygon.

    The web sits on the -u side; flanges extend toward +u.  The 8% taper of UPN
    is ignored for this prototype (parallel flanges are drawn).  ``r`` fillets
    the two web-to-flange corners.
    """
    hb, ht = b / 2.0, h / 2.0
    loop = [
        (-hb,  ht), ( hb,  ht), ( hb,  ht - tf), (-hb + tw,  ht - tf),
        (-hb + tw, -ht + tf), ( hb, -ht + tf), ( hb, -ht), (-hb, -ht),
    ]
    # The fillet sits where the flange inner face meets the web; it must fit
    # along the flange (b - tw) and the clear web height (h - 2 tf).
    r = max(0.0, min(r, b - tw, h - 2 * tf))
    fillets = [(i, r) for i in (3, 4)] if r > 0.0 else []
    return [loop], [fillets]


def _rect_hollow_loops(h, b, t, r=0.0):
    """Square / rectangular hollow section -> outer + inner rectangle loops.

    ``r`` is the EN 10210 OUTER corner radius; the inner radius is ``r - t``
    (clamped at 0).  Falls back to the code default when ``r`` is 0.  All four
    corners of each loop are filleted.
    """
    if r <= 0.0:
        r = 3.0 * t if t <= 6.0 else 2.4 * t
    ho, bo = h / 2.0, b / 2.0
    r_out = max(0.0, min(r, bo, ho))
    r_in = max(0.0, min(r_out - t, bo - t, ho - t))
    outer = [(-bo, -ho), (bo, -ho), (bo, ho), (-bo, ho)]
    inner = [(-bo + t, -ho + t), (bo - t, -ho + t),
             (bo - t, ho - t), (-bo + t, ho - t)]
    fillets_o = [(i, r_out) for i in range(4)] if r_out > 0.0 else []
    fillets_i = [(i, r_in) for i in range(4)] if r_in > 0.0 else []
    return [outer, inner], [fillets_o, fillets_i]


def section_geometry(designation):
    """Return a geometry descriptor for a designation dict.

    Descriptor is a dict with a ``kind`` key:

    * ``{'kind': 'polygons', 'loops': [...], 'fillets': [...]}``
      (mm; ``fillets[i]`` = list of ``(corner_index, radius_mm)`` for ``loops[i]``)
    * ``{'kind': 'circles', 'radii': [r, ...]}``  (mm, concentric, outer first)
    """
    abbr = designation.get("_abbreviation")
    # Fall back to inferring from the designation string when not annotated.
    if abbr is None:
        abbr = _infer_abbreviation(designation)

    if abbr in ("IPE", "HEA", "HEB"):
        loops, fillets = _ibeam_loops(designation["h_mm"], designation["b_mm"],
                                      designation["tw_mm"], designation["tf_mm"],
                                      designation.get("r_mm", 0.0))
        return {"kind": "polygons", "loops": loops, "fillets": fillets}
    if abbr in ("UPE", "UPN"):
        loops, fillets = _channel_loops(designation["h_mm"], designation["b_mm"],
                                        designation["tw_mm"], designation["tf_mm"],
                                        designation.get("r_mm", 0.0))
        return {"kind": "polygons", "loops": loops, "fillets": fillets}
    if abbr in ("SHS", "RHS"):
        loops, fillets = _rect_hollow_loops(designation["h_mm"], designation["b_mm"],
                                           designation["t_mm"],
                                           designation.get("r_mm", 0.0))
        return {"kind": "polygons", "loops": loops, "fillets": fillets}
    if abbr == "CHS":
        od = designation["od_mm"]
        t = designation["t_mm"]
        return {"kind": "circles", "radii": [od / 2.0, od / 2.0 - t]}

    raise ValueError(f"Unknown profile abbreviation: {abbr!r}")


# --------------------------------------------------------------------------- #
# Section placement on the picked line (the "Position" alignment grid)
# --------------------------------------------------------------------------- #
# The picked sketch line is a *reference*: the Position dropdown chooses which
# point of the cross-section lies on it.  "center" puts the centroid on the line
# (the historical behaviour); the other eight slide the section so a face or a
# corner is tangent to the line -- e.g. two members whose OUTER faces must be
# flush with a shared reference plane.  The nine positions form a 3x3 grid over
# the section's bounding box in its local (u, v) frame (u = width, v = height):
#
#     top-left     top        top-right
#     left         CENTER     right
#     bottom-left  bottom     bottom-right
#
# The KEYS below are the grid_anchor() selector (local +v = "top" in the
# section's own frame).  Seen from the FRONT (looking down -Y, Z up), a
# horizontal member's placed axis_v points +Z, so grid_anchor's +v ("top")
# lands the section BELOW the line -- i.e. the key names are vertically
# inverted w.r.t. what the user sees.  The LABELS are paired to the key that
# renders in that visual cell, so picking "Top" puts the member at the top.
GRID_POSITIONS = [
    ("center", "Center"),
    ("bottom", "Top"),
    ("top", "Bottom"),
    ("left", "Left"),
    ("right", "Right"),
    ("bottom-left", "Top Left"),
    ("bottom-right", "Top Right"),
    ("top-left", "Bottom Left"),
    ("top-right", "Bottom Right"),
]


def section_extents(geom):
    """Bounding box ``(umin, umax, vmin, vmax)`` (mm) of a section in local coords.

    Every loop builder centres the section on ``(0, 0)``, so the extents are
    symmetric for the built-in families, but this reads them from the actual
    outline so an asymmetric section (a channel, whose web sits on -u) is
    handled correctly.  A circle's box is the outer radius square.
    """
    kind = geom.get("kind")
    if kind == "circles":
        r = max(geom.get("radii") or [0.0])
        return -r, r, -r, r
    if kind == "polygons":
        us = [u for loop in geom.get("loops") or [] for u, _v in loop]
        vs = [v for loop in geom.get("loops") or [] for _u, v in loop]
        if not us:
            return 0.0, 0.0, 0.0, 0.0
        return min(us), max(us), min(vs), max(vs)
    return 0.0, 0.0, 0.0, 0.0


def grid_anchor(geom, position):
    """Local ``(u, v)`` mm point of ``geom`` that should sit ON the reference line.

    ``position`` is a key from :data:`GRID_POSITIONS` (``"center"`` -> the
    centroid ``(0, 0)``, reproducing the historical centred placement).  The
    drawing layer offsets the section by ``-anchor`` along its basis so the
    returned point lands on the picked line; joint trims measure the neighbour's
    extent from this same point (see :func:`lib.joints._half_extent_cm`).
    """
    umin, umax, vmin, vmax = section_extents(geom)
    umid = (umin + umax) / 2.0
    vmid = (vmin + vmax) / 2.0
    hsel = {"left": umin, "center": umid, "right": umax}
    vsel = {"bottom": vmin, "center": vmid, "top": vmax}
    if position in ("center", "top", "bottom", "left", "right"):
        return hsel.get(position, umid), vsel.get(position, vmid)
    if "-" in position:                       # corner: "<v>-<h>" e.g. "top-left"
        vpart, hpart = position.split("-", 1)
        return hsel.get(hpart, umid), vsel.get(vpart, vmid)
    return umid, vmid


def displace_origin(origin, axis_u, axis_v, anchor):
    """Model-space (cm) placement origin so ``anchor`` sits on ``origin``.

    ``origin`` is the point on the picked line (cm) that the section would be
    centred on, ``axis_u``/``axis_v`` the section's placed (already-rotated) width
    and height unit vectors, and ``anchor`` the local ``(u, v)`` mm point of the
    section that must land on the line (see :func:`grid_anchor`).  The section's
    centroid therefore moves to ``origin - (au*axis_u + av*axis_v)``, putting the
    chosen grid point exactly on the reference line.  A ``(0, 0)`` / None anchor
    returns ``origin`` unchanged (the centred, historical placement).
    """
    if not anchor or (anchor[0] == 0.0 and anchor[1] == 0.0):
        return origin
    au, av = anchor
    k = MM_TO_CM
    return (origin[0] - k * (au * axis_u[0] + av * axis_v[0]),
            origin[1] - k * (au * axis_u[1] + av * axis_v[1]),
            origin[2] - k * (au * axis_u[2] + av * axis_v[2]))


def _infer_abbreviation(designation):
    name = designation.get("designation", "")
    for prefix in ("IPE", "HEA", "HEB", "UPE", "UPN", "SHS", "RHS", "CHS"):
        if name.startswith(prefix):
            return prefix
    if "od_mm" in designation:
        return "CHS"
    if "t_mm" in designation:
        return "RHS" if designation.get("h_mm") != designation.get("b_mm") else "SHS"
    return "IPE"


def annotate_families(families):
    """Attach ``_abbreviation`` to every designation so geometry can be resolved.

    Mutates and returns the same list for convenience.
    """
    for fam in families:
        abbr = fam["abbreviation"]
        for d in designations(fam):
            d["_abbreviation"] = abbr
    return families
