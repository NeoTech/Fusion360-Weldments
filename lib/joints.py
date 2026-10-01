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


def corner_offsets(lines, geoms, joint_by_line):
    """Compute ``(offset_start, offset_end)`` in cm for every line.

    ``geoms[i]`` is the section geometry of line ``i`` (from
    ``profiles.section_geometry``); ``joint_by_line[i]`` is the joint id chosen
    for line ``i`` (``none``/``butt``/``miter``/``cope``/``bend``).

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

        for k, (idx, role) in enumerate(members):
            jid = joint_by_line[idx] if idx < len(joint_by_line) else 'none'
            if jid in ('none', 'bend'):
                continue
            # The neighbour this member joins to at the corner (first other one).
            other_k = next((m for m in range(len(members)) if m != k), None)
            if other_k is None:
                continue
            other = members[other_k][0]
            phi = _angle_between(dirs[k], dirs[other_k])
            if jid in ('butt', 'cope'):
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
