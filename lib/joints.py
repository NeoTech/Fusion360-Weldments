"""Pure-Python corner-joint geometry for weldment frames.

Like :mod:`lib.profiles`, this module never touches the ``adsk`` API so it can be
unit-tested outside Fusion.  It answers one question for the command layer:

    given the selected sketch lines and each line's cross-section, what signed
    start/end offset (cm, along the line) should be applied to every line so the
    corners join cleanly for a chosen joint type?

Joint types
-----------
``none``  Full length to the vertex (the historical behaviour; the default).
``butt``  One member runs *through* the corner and the other stops short by the
          through-member's section depth.  The member that starts at the corner
          runs through; the member that ends there is trimmed.  This is exactly
          the "make one beam shorter by the thickness and the other one longer"
          overlap the user described, and it is fully expressible as a square-end
          offset.
``miter`` Both members are pulled back from the sharp vertex by the symmetric
          miter setback ``(d/2)/tan(phi/2)`` so the overlap disappears and a
          proper corner groove is left for the weld.  (A true angled cut face is
          a later refinement; the setback already removes the interference.)
``cope``  Round/hollow equivalent of the butt: the incoming member is saddled to
          the other's outer face.  Geometrically the same trim as ``butt``.
``bend``  A swept arc replaces the corner (Phase 3); not resolved here.

Geometry convention
-------------------
A "line" is anything exposing ``worldGeometry.startPoint`` / ``.endPoint`` (each
with ``.x/.y/.z`` in cm) and a ``.length`` -- real Fusion sketch lines and the
test stub both qualify.  Directions are unit tuples; depths are in millimetres
and are converted to cm (Fusion's internal unit) via ``MM_TO_CM``.
"""

import math

MM_TO_CM = 0.1

# Canonical joint vocabulary.  ``labels`` maps an id to the text shown in the
# per-line joint dropdown; ``all_ids`` is the ordered master list.
LABELS = {
    'none': 'None',
    'butt': 'Butt',
    'miter': 'Miter',
    'cope': 'Cope',
    'bend': 'Bend',
}
ALL_IDS = ['none', 'butt', 'miter', 'cope', 'bend']

# Endpoint coincidence tolerance (cm) for auto-detecting a shared corner.
_CORNER_TOL = 1e-4


# --------------------------------------------------------------------------- #
# Small vector helpers (tuples; independent of lib.profiles on purpose).
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    m = math.sqrt(_dot(a, a))
    return (a[0] / m, a[1] / m, a[2] / m) if m else (0.0, 0.0, 0.0)


def _pt(p):
    return (p.x, p.y, p.z)


# --------------------------------------------------------------------------- #
# Line accessors
# --------------------------------------------------------------------------- #
def line_endpoints(line):
    """Return ``(start, end)`` as 3-tuples in cm for a sketch line."""
    wg = line.worldGeometry
    return _pt(wg.startPoint), _pt(wg.endPoint)


def line_direction(line):
    """Unit direction (start -> end) of a sketch line."""
    s, e = line_endpoints(line)
    return _norm(_sub(e, s))


# --------------------------------------------------------------------------- #
# Corner detection
# --------------------------------------------------------------------------- #
def _end_role(line, corner, tol):
    """How ``line`` meets ``corner``: 'start', 'end', or None.

    A line is incident to a corner when one of its endpoints coincides with it.
    """
    s, e = line_endpoints(line)
    if _dot(_sub(s, corner), _sub(s, corner)) <= tol * tol:
        return 'start'
    if _dot(_sub(e, corner), _sub(e, corner)) <= tol * tol:
        return 'end'
    return None


def detect_corners(lines, tol=_CORNER_TOL):
    """Auto-detect shared vertices among ``lines``.

    Returns a list of corners; each corner is a dict::

        {'point': (x, y, z),
         'members': [(line_index, 'start'|'end'), ...]}

    Only vertices touched by two or more lines are corners.  Coincident
    endpoints are clustered by proximity (``tol`` cm).
    """
    # Collect every endpoint with the line index and which end it is.
    ends = []
    for i, ln in enumerate(lines):
        s, e = line_endpoints(ln)
        ends.append((i, 'start', s))
        ends.append((i, 'end', e))

    corners = []
    used = [False] * len(ends)
    for a in range(len(ends)):
        if used[a]:
            continue
        cluster = [a]
        used[a] = True
        pa = ends[a][2]
        for b in range(a + 1, len(ends)):
            if used[b]:
                continue
            pb = ends[b][2]
            if _dot(_sub(pa, pb), _sub(pa, pb)) <= tol * tol:
                used[b] = True
                cluster.append(b)
        members = [(ends[k][0], ends[k][1]) for k in cluster]
        # A corner needs >=2 distinct lines meeting there.
        if len({mi for mi, _ in members}) >= 2:
            corners.append({'point': pa, 'members': members})
    return corners


# --------------------------------------------------------------------------- #
# Section depth (how far a member's material extends perpendicular to its axis)
# --------------------------------------------------------------------------- #
def member_depth(geom):
    """Characteristic section depth (mm) used to trim a butt/cope joint.

    For a polygon section this is the larger of the bounding-box width/height
    (the through-member's visible size at the corner); for a circular section it
    is the outer diameter.  This is the distance the incoming member must stop
    short so its end lands on the through-member's far face.
    """
    if geom is None:
        return 0.0
    kind = geom.get('kind')
    if kind == 'circles':
        return 2.0 * max(geom.get('radii') or [0.0])
    if kind == 'polygons':
        best = 0.0
        for loop in geom.get('loops') or []:
            for pt in loop:
                best = max(best, abs(pt[0]), abs(pt[1]))
        # ``best`` is the half-extent; the full depth across the section is 2x.
        return 2.0 * best
    return 0.0


# --------------------------------------------------------------------------- #
# Per-line offsets for a chosen joint type
# --------------------------------------------------------------------------- #
def _miter_setback(depth_mm, phi):
    """Symmetric miter setback (cm) for a corner turn of ``phi`` radians.

    Both members pull back from the sharp vertex by ``(d/2)/tan(phi/2)`` so the
    square ends no longer overlap.  ``phi`` is the angle between the two member
    directions pointing *away* from the corner.
    """
    half = phi / 2.0
    if half <= 1e-6 or half >= math.pi / 2.0 - 1e-6:
        # Collinear (phi ~ pi) or folded-back (phi ~ 0): no sensible setback.
        return 0.0
    return (depth_mm * 0.5) / math.tan(half) * MM_TO_CM


def corner_offsets(lines, geoms, joint_by_line, clr_by_line=None):
    """Compute ``(offset_start, offset_end)`` in cm for every line.

    ``geoms[i]`` is the section geometry of line ``i`` (from
    ``profiles.section_geometry``); ``joint_by_line[i]`` is the joint id chosen
    for line ``i`` (``none``/``butt``/``miter``/``cope``/``bend``).
    ``clr_by_line[i]`` (optional) is the die centerline radius (mm) for a
    ``bend`` leg; without it a bend corner contributes no trim (the arc itself
    is built separately by :func:`bend_plan`).

    The result is a list of ``(offset_start, offset_end)`` tuples, one per line,
    to be *added* to the user's manual start/end offsets.  Lines whose joint is
    ``none`` (or a corner that cannot be resolved) get ``(0.0, 0.0)``.
    """
    n = len(lines)
    offs = [(0.0, 0.0) for _ in range(n)]
    corners = detect_corners(lines)

    for corner in corners:
        point = corner['point']
        members = corner['members']
        # Turn angle between the members, measured from the corner outward.
        dirs = []
        for idx, role in members:
            outward = line_direction(lines[idx])
            if role == 'end':
                outward = (-outward[0], -outward[1], -outward[2])
            dirs.append(outward)

        # A swept bend rounds exactly two legs; both must request 'bend'.
        bend_clr = 0.0
        if (len(members) == 2 and clr_by_line
                and all(joint_by_line[idx] == 'bend' for idx, _ in members)):
            bend_clr = max((clr_by_line[idx] for idx, _ in members
                            if idx < len(clr_by_line)), default=0.0)

        for k, (idx, role) in enumerate(members):
            jid = joint_by_line[idx] if idx < len(joint_by_line) else 'none'
            if jid == 'none':
                continue
            # The neighbour this member joins to at the corner (first other one).
            other_k = next((m for m in range(len(members)) if m != k), None)
            if other_k is None:
                continue
            other = members[other_k][0]
            phi = _angle_between(dirs[k], dirs[other_k])
            if jid == 'bend':
                # Trim each leg back to its arc tangent point (centerline setback).
                if bend_clr <= 0.0:
                    continue
                theta = math.pi - phi
                sb = bend_setback(bend_clr, theta)
                if role == 'end':
                    offs[idx] = (offs[idx][0], offs[idx][1] - sb)
                else:
                    offs[idx] = (offs[idx][0] + sb, offs[idx][1])
            elif jid in ('butt', 'cope'):
                # Trim this member by the neighbour's depth at the end that
                # touches the corner; the neighbour runs through (untouched).
                depth = member_depth(geoms[other]) if other < len(geoms) else 0.0
                trim = depth * MM_TO_CM
                if role == 'end':
                    offs[idx] = (offs[idx][0], offs[idx][1] - trim)
                else:
                    offs[idx] = (offs[idx][0] + trim, offs[idx][1])
            elif jid == 'miter':
                depth = member_depth(geoms[idx]) if idx < len(geoms) else 0.0
                sb = _miter_setback(depth, phi)
                if role == 'end':
                    offs[idx] = (offs[idx][0], offs[idx][1] - sb)
                else:
                    offs[idx] = (offs[idx][0] + sb, offs[idx][1])
    return offs


def _angle_between(a, b):
    d = max(-1.0, min(1.0, _dot(_norm(a), _norm(b))))
    return math.acos(d)


# --------------------------------------------------------------------------- #
# Swept-bend geometry (Phase 3)
# --------------------------------------------------------------------------- #
def bend_turn_angle(u, v):
    """Turn (deflection) angle in radians for a corner whose members point along
    unit directions ``u`` and ``v`` *away* from the vertex.

    A straight-through run (u opposite v) turns 0; a right-angle corner turns
    pi/2.  ``theta = pi - angle(u, v)``.
    """
    return math.pi - _angle_between(u, v)


def bend_setback(clr_mm, theta):
    """Centerline setback (cm): distance along each leg from the sharp vertex to
    its arc tangent point.  ``SB = R * tan(theta/2)``.
    """
    if abs(theta) < 1e-9:
        return 0.0
    return clr_mm * math.tan(theta / 2.0) * MM_TO_CM


def bend_arc_length(clr_mm, theta):
    """Centerline arc length (cm) of the swept bend.  ``L = R * theta``."""
    return clr_mm * abs(theta) * MM_TO_CM


def _outward(line, role):
    """Unit direction from the corner *into* a member (away from the vertex)."""
    d = line_direction(line)
    return (-d[0], -d[1], -d[2]) if role == 'end' else d


def bend_plan(lines, joint_by_line, clr_by_line, tol=_CORNER_TOL):
    """Plan every swept-bend corner among ``lines``.

    ``clr_by_line[i]`` is the die centerline radius (mm) to use at line ``i``'s
    end; a corner bends only when both members request ``bend`` and a radius is
    available.  Returns a list of corner dicts::

        {'point': V, 'center': C, 'axis': a, 'theta': turn_rad,
         'tangent': [(line_index, role, T), ...], 'arc_length': L}

    ``center`` sits on the inside of the turn at ``V + (R/cos(theta/2)) *
    normalize(u+v)``; ``axis`` is the bend-plane normal (revolve axis); each
    ``tangent`` entry gives the trimmed end point of one leg.
    """
    plans = []
    for corner in detect_corners(lines, tol):
        members = corner['members']
        if len(members) != 2:
            continue  # a swept bend rounds exactly two legs
        if any(joint_by_line[idx] != 'bend' for idx, _ in members):
            continue
        (i0, r0), (i1, r1) = members
        clr = clr_by_line[i0] if i0 < len(clr_by_line) else 0.0
        if clr <= 0.0:
            continue
        V = corner['point']
        u = _outward(lines[i0], r0)
        v = _outward(lines[i1], r1)
        theta = bend_turn_angle(u, v)
        if abs(theta) < 1e-6 or abs(theta) >= math.pi - 1e-6:
            continue  # collinear or folded back -- not a bend
        sb = clr * math.tan(theta / 2.0) * MM_TO_CM   # cm
        bis = _norm(_add(u, v))
        dist = (clr * MM_TO_CM) / math.cos(theta / 2.0)
        center = _add(V, _scale(bis, dist))
        axis = _norm(_cross(u, v))
        tangent = [(i0, r0, _add(V, _scale(u, sb))),
                   (i1, r1, _add(V, _scale(v, sb)))]
        plans.append({'point': V, 'center': center, 'axis': axis,
                      'theta': theta, 'radius_cm': clr * MM_TO_CM,
                      'tangent': tangent,
                      'arc_length': clr * abs(theta) * MM_TO_CM})
    return plans


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


# --------------------------------------------------------------------------- #
# Family joint filtering
# --------------------------------------------------------------------------- #
def joints_for_family(family):
    """Supported joint ids for a profile family (from its ``joints`` field).

    Falls back to ``['none']`` when the family declares nothing, so the dropdown
    always has at least the no-op option.
    """
    if not family:
        return ['none']
    js = family.get('joints')
    if not js:
        return ['none']
    return [j for j in ALL_IDS if j in js]


def joint_labels(ids):
    """Display labels for a list of joint ids, in the master order."""
    return [LABELS[j] for j in ids]


def joint_id_from_label(label):
    """Reverse of :func:`joint_labels` -- id for a display label."""
    for jid, lab in LABELS.items():
        if lab == label:
            return jid
    return 'none'
