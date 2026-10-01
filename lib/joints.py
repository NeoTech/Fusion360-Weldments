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

# Labels for the two per-row butt controls (table checkboxes).
THROUGH_LABEL = 'Through'
SADDLE_LABEL = 'Saddle'

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


def _point_on_segment_interior(P, A, B, tol):
    """True if ``P`` lies strictly inside segment ``AB`` (not at either end).

    "Strictly inside" excludes the endpoints (within ``tol``), so a point that
    merely coincides with ``A`` or ``B`` -- a shared vertex, i.e. a corner -- is
    not a T-junction.
    """
    if _dot(_sub(P, A), _sub(P, A)) <= tol * tol:
        return False
    if _dot(_sub(P, B), _sub(P, B)) <= tol * tol:
        return False
    ab = _sub(B, A)
    L2 = _dot(ab, ab)
    if L2 <= 0.0:
        return False
    t = _dot(_sub(P, A), ab) / L2
    if t <= 0.0 or t >= 1.0:
        return False
    closest = _add(A, _scale(ab, t))
    d = _sub(P, closest)
    return _dot(d, d) <= tol * tol


def detect_t_junctions(lines, tol=_CORNER_TOL):
    """Find endpoints that land on the *interior* of another line (T-junctions).

    Returns a list of dicts::

        {'point': P, 'member': (line_index, 'start'|'end'), 'tool': line_index}

    where ``member``'s endpoint coincides with a point strictly inside ``tool``'s
    run (not at either of ``tool``'s endpoints).  This is the configuration a
    cope saddle is meant for: one tube's end butts against the middle of another
    (a "T"), as opposed to two ends meeting at a shared vertex (a corner).
    """
    segs = [line_endpoints(ln) for ln in lines]
    junctions = []
    for i, ln in enumerate(lines):
        s, e = segs[i]
        for role, P in (('start', s), ('end', e)):
            for j, (sj, ej) in enumerate(segs):
                if j == i:
                    continue
                if _point_on_segment_interior(P, sj, ej, tol):
                    junctions.append({'point': P, 'member': (i, role),
                                      'tool': j})
    return junctions


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
def _half_extent_cm(geom, basis, axis):
    """Half-extent (cm) of section ``geom`` measured along a world ``axis``.

    ``basis`` is the member's placed ``(axis_u, axis_v)`` world unit vectors
    (its section width/height directions, both perpendicular to its own run);
    ``axis`` is the world direction to measure along (usually a *neighbour's*
    run).  The section outline lives in local ``(u, v)`` millimetres, so a point
    sits at ``u*axis_u + v*axis_v`` and its projection on ``axis`` is
    ``u*(axis_u.axis) + v*(axis_v.axis)``; the half-extent is the largest such
    projection.  This is what a butt/cope member must stop short by so its end
    lands on the neighbour's *visible* face along the incoming direction -- for
    an I-beam that is the flange width (not the web depth), which a single
    scalar depth cannot express.

    When ``basis`` is None (a caller that has not computed the placed frame, e.g.
    a unit test) it falls back to the isotropic ``member_depth``/2, reproducing
    the historical scalar trim.
    """
    if geom is None:
        return 0.0
    if basis is None:
        return member_depth(geom) * 0.5 * MM_TO_CM
    axis_u, axis_v = basis
    cu, cv = _dot(axis_u, axis), _dot(axis_v, axis)
    kind = geom.get('kind')
    if kind == 'circles':
        radii = geom.get('radii') or [0.0]
        return max(radii) * math.sqrt(cu * cu + cv * cv) * MM_TO_CM
    if kind == 'polygons':
        best = 0.0
        for loop in geom.get('loops') or []:
            for u, v in loop:
                best = max(best, abs(u * cu + v * cv))
        return best * MM_TO_CM
    return member_depth(geom) * 0.5 * MM_TO_CM


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


def _ends(pair):
    """Normalise a per-line value to a ``(start, end)`` 2-tuple.

    A joint / Through / Saddle setting is really a property of a line *end*.  To
    keep the API simple, a caller may give either a scalar (applies to BOTH ends)
    or a ``(start, end)`` 2-sequence (the two ends differ).  This returns the
    2-tuple form either way.
    """
    if isinstance(pair, (tuple, list)) and len(pair) == 2:
        return (pair[0], pair[1])
    return (pair, pair)


def joint_at(joints, idx, role):
    """The joint id chosen for line ``idx``'s given ``role`` end ('start'/'end')."""
    if joints is None or idx >= len(joints):
        return 'none'
    s, e = _ends(joints[idx])
    return s if role == 'start' else e


def flag_at(flags, idx, role):
    """The boolean (Through / Saddle) chosen for line ``idx``'s given end."""
    if not flags or idx >= len(flags):
        return False
    s, e = _ends(flags[idx])
    return bool(s if role == 'start' else e)


def _is_hollow(geom):
    """True when ``geom`` encloses a void (a tube / hollow section).

    A hollow tool cannot be saddled by a plain body-cut: a member run to the far
    face leaves a plug floating in the void.  Such joints stop at the near face
    instead (a flush butt).  A single-loop polygon (I-beam, channel) or a solid
    circle is not hollow.
    """
    if geom is None:
        return False
    kind = geom.get('kind')
    if kind == 'circles':
        return len(geom.get('radii') or []) > 1
    if kind == 'polygons':
        return len(geom.get('loops') or []) > 1
    return False


def _wall_cm(geom):
    """Wall thickness (cm) of a hollow section, or None when not determinable.

    A hollow tool (round CHS or square SHS/RHS tube) is saddled by running the
    member to just PAST the near wall (``half + wall``) so the boolean carves a
    saddle that removes the wall it overlaps -- stopping at the near face would
    leave the tube's wall poking through the member (the "no saddle" symptom),
    and running to the far face would leave a plug floating in the void.  The
    wall is the gap between the outer and inner outlines: the second radius for
    a circle, the inner-loop inset for a polygon.
    """
    if geom is None:
        return None
    kind = geom.get('kind')
    if kind == 'circles':
        radii = geom.get('radii') or []
        if len(radii) > 1:
            return (radii[0] - radii[1]) * MM_TO_CM
        return None
    if kind == 'polygons':
        loops = geom.get('loops') or []
        if len(loops) > 1:
            outer, inner = loops[0], loops[1]
            if outer and inner:
                # Half the difference of the two bounding half-widths (the wall).
                wo = max(abs(p[0]) for p in outer)
                wi = max(abs(p[0]) for p in inner)
                ho = max(abs(p[1]) for p in outer)
                hi = max(abs(p[1]) for p in inner)
                wall = min(wo - wi, ho - hi)
                return max(wall, 0.0) * MM_TO_CM
        return None
    return None


def _butt_through(members, joint_by_line, through_by_line):
    """Index of the member that runs THROUGH at a butt corner (or None).

    ``members`` is a corner's ``[(line_index, role), ...]``.  Exactly one member
    at a butt corner should run through (extend past the vertex) while the
    others back off.  Resolution, in order of priority:

    1. a butt/cope END the user explicitly marked ``through``;
    2. otherwise a neighbour whose END at this corner is not butt/cope (it runs
       through, the butt end backs off -- the classic single-butt case);
    3. otherwise (all ends butt/cope, none marked) the highest-index member.

    Returns None when no member's END at the corner is a butt/cope.

    A ``cope`` END is treated as butt-like *at a corner*: coping is only a saddle
    at a T-junction (see :func:`corner_cuts`), so where a cope's end coincides
    with another member's END (a shared vertex) it degrades to a plain axial trim
    exactly like a butt -- one member runs through, the other backs off.
    """
    butt = [(idx, role) for idx, role in members
            if joint_at(joint_by_line, idx, role) in ('butt', 'cope')]
    if not butt:
        return None
    if through_by_line:
        for idx, role in butt:
            if flag_at(through_by_line, idx, role):
                return idx
    for idx, role in members:
        if joint_at(joint_by_line, idx, role) not in ('butt', 'cope'):
            return idx
    return max(idx for idx, _ in butt)


def corner_offsets(lines, geoms, joint_by_line, clr_by_line=None,
                   through_by_line=None, bases=None, saddle_by_line=None):
    """Compute ``(offset_start, offset_end)`` in cm for every line.

    ``geoms[i]`` is the section geometry of line ``i`` (from
    ``profiles.section_geometry``); ``joint_by_line[i]`` is the joint id chosen
    for line ``i`` -- either a single id (both ends) or a ``(start, end)`` pair
    (the two ends differ; see :func:`_ends`).
    ``clr_by_line[i]`` (optional) is the die centerline radius (mm) for a
    ``bend`` leg; without it a bend corner contributes no trim (the arc itself
    is built separately by :func:`bend_plan`).
    ``through_by_line[i]`` / ``saddle_by_line[i]`` (optional) are per-end
    booleans (scalar or ``(start, end)``) refining a butt: Through marks the end
    that runs through the corner; Saddle makes the other end's face conform to
    the neighbour (a boolean notch) instead of a flat square.
    ``bases[i]`` (optional) is line ``i``'s placed ``(axis_u, axis_v)`` section
    basis (see :func:`profiles.compute_basis`); when supplied the butt/cope trim
    uses the neighbour's *directional* half-extent along the incoming axis (an
    I-beam's flange width, not its web depth).  When omitted it falls back to the
    isotropic :func:`member_depth`/2.

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

        # The one butt member that runs through this corner (others back off).
        through_idx = _butt_through(members, joint_by_line, through_by_line)

        # A swept bend rounds exactly two legs; both must request 'bend'.
        bend_clr = 0.0
        if (len(members) == 2 and clr_by_line
                and all(joint_at(joint_by_line, idx, role) == 'bend'
                        for idx, role in members)):
            bend_clr = max((clr_by_line[idx] for idx, _ in members
                            if idx < len(clr_by_line)), default=0.0)

        # A butt corner is handled once here (not per member): exactly one
        # member runs THROUGH (extends past the vertex) and the other backs off.
        # A plain butt stops the backing-off member at the through member's NEAR
        # face (a flat square end, no boolean).  A SADDLED butt instead runs it to
        # the through member's FAR face so the boolean notch (see
        # :func:`corner_cuts`) has overlap to carve -- a near-face trim would
        # leave zero overlap and the saddle would remove nothing.  Handling it per
        # corner (rather than per member) is what stops two butt members from both
        # backing off and leaving a gap; the through member is chosen by
        # :func:`_butt_through`.
        if through_idx is not None and len(members) == 2:
            t_k = next(k for k, (idx, _) in enumerate(members)
                       if idx == through_idx)
            o_k = 1 - t_k
            T = members[t_k][0]
            O = members[o_k][0]
            sinp = _sin_between(dirs[t_k], dirs[o_k])
            if sinp > 1e-6:
                # Directional half-extents (cm): the backing-off member stops at
                # the through member's face measured ALONG the incoming axis, and
                # the through member grows to the other's face along ITS axis.
                # Using the placed bases (when available) makes this correct for
                # open sections (an I-beam's flange width, not its web depth).
                g_t = geoms[T] if T < len(geoms) else None
                g_o = geoms[O] if O < len(geoms) else None
                b_t = bases[T] if (bases and T < len(bases)) else None
                b_o = bases[O] if (bases and O < len(bases)) else None
                orole = members[o_k][1]
                # The through member's extent along the incoming member's axis.
                trim = _half_extent_cm(g_t, b_t, dirs[o_k]) / sinp
                # The incoming member's extent along the through member's axis.
                grow = _half_extent_cm(g_o, b_o, dirs[t_k]) / sinp
                # How far the backing-off member's tip reaches along its axis
                # (positive = past the vertex into the tool):
                #   plain butt        -> near face  (-half): a flat square end.
                #   saddle, solid     -> far face   (+half): the boolean carves
                #                                        the whole cross-section.
                #   saddle, hollow    -> just past the near wall (-half + wall):
                #                        the boolean carves a saddle through the
                #                        wall.  Stopping at the near face leaves
                #                        the wall poking through (the "no saddle"
                #                        symptom); the far face leaves a plug.
                saddled = flag_at(saddle_by_line, O, orole)
                if not saddled:
                    reach = -trim
                elif _is_hollow(g_t):
                    reach = -trim + (_wall_cm(g_t) or 0.0)
                else:
                    reach = trim
                if orole == 'end':
                    offs[O] = (offs[O][0], offs[O][1] + reach)
                else:
                    offs[O] = (offs[O][0] - reach, offs[O][1])
                trole = members[t_k][1]
                if trole == 'end':
                    offs[T] = (offs[T][0], offs[T][1] + grow)
                else:
                    offs[T] = (offs[T][0] - grow, offs[T][1])

        for k, (idx, role) in enumerate(members):
            jid = joint_at(joint_by_line, idx, role)
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
            elif jid == 'miter':
                # A miter face runs corner-to-corner, so the member must reach
                # PAST the centreline vertex by the setback ``(d/2)/tan(phi/2)``;
                # the bisector plane through the vertex then trims the diagonal
                # (see :func:`corner_cuts`).  Without the extension the plane
                # only clips the square end's centre -- no visible miter.
                depth = member_depth(geoms[idx]) if idx < len(geoms) else 0.0
                sb = _miter_setback(depth, phi)
                if sb <= 0.0:
                    continue
                if role == 'end':
                    offs[idx] = (offs[idx][0], offs[idx][1] + sb)
                else:
                    offs[idx] = (offs[idx][0] - sb, offs[idx][1])
            # ``butt`` is handled once per corner above (see the corner-level
            # block); ``cope`` is realised by a neighbour-body saddle cut (see
            # :func:`corner_cuts`), so neither contributes an offset here.

    # T-junctions: a member whose END lands on the interior of another member's
    # run (a "T", not a shared-vertex corner).  Its tip sits on the tool's
    # CENTRELINE, so half of it is buried inside the tube.  The axial trim places
    # the tip (offset from the centreline, positive = deeper into the tool):
    #   plain butt        -> near face  (-half): a flat square end, no overlap.
    #   saddle/cope, solid-> far face   (+half): the boolean carves the section.
    #   saddle/cope, hollow -> just past the near wall (-half + wall): the boolean
    #                        carves a saddle through the wall.  Reaching the far
    #                        face pokes straight through (the reported bug); the
    #                        near face leaves the wall poking through the member.
    for jn in detect_t_junctions(lines):
        idx, role = jn['member']
        tool = jn['tool']
        jid = joint_at(joint_by_line, idx, role)
        if jid not in ('butt', 'cope'):
            continue
        g_tool = geoms[tool] if tool < len(geoms) else None
        b_tool = bases[tool] if (bases and tool < len(bases)) else None
        # The tool's half-extent along the incoming member's axis (cm).
        trim = _half_extent_cm(g_tool, b_tool, line_direction(lines[idx]))
        if trim <= 0.0:
            continue
        saddled = (jid == 'cope' or flag_at(saddle_by_line, idx, role))
        if not saddled:
            reach = -trim
        elif _is_hollow(g_tool):
            reach = -trim + (_wall_cm(g_tool) or 0.0)
        else:
            reach = trim
        if role == 'end':
            offs[idx] = (offs[idx][0], offs[idx][1] + reach)
        else:
            offs[idx] = (offs[idx][0] - reach, offs[idx][1])
    return offs


def _angle_between(a, b):
    d = max(-1.0, min(1.0, _dot(_norm(a), _norm(b))))
    return math.acos(d)


def _sin_between(a, b):
    """|sin| of the angle between ``a`` and ``b`` (0 when collinear)."""
    na, nb = _norm(a), _norm(b)
    return math.sqrt(max(0.0, 1.0 - _dot(na, nb) ** 2))


# --------------------------------------------------------------------------- #
# Real corner cuts (Phase 4): angled miter faces and butt/cope saddles
# --------------------------------------------------------------------------- #
def corner_cuts(lines, joint_by_line, tol=_CORNER_TOL, saddle_by_line=None,
                through_by_line=None):
    """Plan the geometric cuts that realise ``miter``/``butt``/``cope`` corners.

    Unlike :func:`corner_offsets` (which only shortens a member along its axis),
    these cuts produce genuinely new end faces.  Returns a list of cut dicts::

        {'member': line_index, 'role': 'start'|'end', 'kind': 'plane'|'body',
         'point': V, 'normal': n, 'keep': d, 'tool': other_line_index}

    ``kind='plane'`` (miter): split ``member``'s body with a plane through the
    corner ``point`` whose ``normal`` is the bisector between the two members;
    keep the half on the ``keep`` side (the member's own outward direction).

    ``kind='body'`` (cope at a T-junction, or a saddled butt at a corner): split
    ``member``'s body with the *neighbour's* body (``tool``) so the incoming
    member is saddled to the through-member's surface; keep the half away from
    the neighbour (``keep``).

    A ``butt`` corner produces NO cut by default: it is a pure axial trim
    handled entirely by :func:`corner_offsets` (the incoming member stops at the
    through member's near face, so its flat end never enters the interior).
    When the user checks ``Saddle`` on the butt member (``saddle_by_line``), a
    body cut is emitted instead so the end is notched to clear an open
    section's interior.  A corner is cut when a member's joint is ``miter`` (or a
    saddled ``butt``) and exactly two members meet there; ``none``/``bend``
    corners are skipped.

    A ``cope`` is emitted only at a T-junction -- where one member's END lands on
    the *interior* of another's run (see :func:`detect_t_junctions`) -- never at
    a shared-vertex corner.  This matches the physical operation: coping is
    notching a tube so it fits over the *side* of another tube, which only
    happens mid-run.  A cope whose end coincides with a corner is treated as a
    plain butt (axial trim, no boolean).
    """
    cuts = []
    for corner in detect_corners(lines, tol):
        members = corner['members']
        if len(members) != 2:
            continue  # a clean two-member corner only
        V = corner['point']
        (i0, r0), (i1, r1) = members
        d0 = line_direction(lines[i0])
        d1 = line_direction(lines[i1])
        out0 = d0 if r0 == 'start' else _scale(d0, -1)
        out1 = d1 if r1 == 'start' else _scale(d1, -1)
        # For a butt, only the member that BACKS OFF (not the through one) is
        # saddled; the through member keeps its square extended end.
        butt_through = _butt_through(members, joint_by_line, through_by_line)
        for k, (idx, role) in enumerate(members):
            jid = joint_at(joint_by_line, idx, role)
            # A saddled butt end (not the through one) is notched against the
            # neighbour's body; only meaningful against a solid tool.
            saddled_butt = (jid == 'butt'
                            and flag_at(saddle_by_line, idx, role)
                            and idx != butt_through)
            # cope is NOT handled here -- it only fires at a T-junction below.
            if jid != 'miter' and not saddled_butt:
                continue
            other = members[1 - k][0]
            own = (out0, out1)[k]
            neigh = (out1, out0)[k]
            # A cut only makes sense at a genuine corner: neither a straight
            # run (outward dirs opposite, phi ~ pi) nor a fold-back (phi ~ 0).
            phi = _angle_between(own, neigh)
            if phi <= 1e-6 or phi >= math.pi - 1e-6:
                continue
            if jid == 'miter':
                # Bisector plane; normal points from the neighbour toward this
                # member's own axis, so the kept half is the member's body.
                nrm = _norm(_sub(own, neigh))
                if _dot(nrm, nrm) < 1e-9:
                    continue  # degenerate: no miter face
                cuts.append({'member': idx, 'role': role, 'kind': 'plane',
                             'point': V, 'normal': nrm, 'keep': own,
                             'tool': other})
            else:
                # saddled butt: notch against the neighbour's body at a corner.
                cuts.append({'member': idx, 'role': role, 'kind': 'body',
                             'point': V, 'normal': _scale(own, -1),
                             'keep': own, 'tool': other})

    # Cope (or a saddled butt) at T-junctions: a member whose END lands on the
    # interior of another member's run is saddled to that member's body.  A plain
    # butt at a T-junction gets only the axial trim (above) and no cut; checking
    # Saddle makes its end conform to the tool's outer surface instead of a flat
    # square face.  Cope always saddles at a T-junction.
    for jn in detect_t_junctions(lines, tol):
        idx, role = jn['member']
        tool = jn['tool']
        jid = joint_at(joint_by_line, idx, role)
        saddled = (jid == 'cope'
                   or (jid == 'butt' and flag_at(saddle_by_line, idx, role)))
        if not saddled:
            continue
        own = _outward(lines[idx], role)   # from the vertex into the member
        cuts.append({'member': idx, 'role': role, 'kind': 'body',
                     'point': jn['point'], 'normal': _scale(own, -1),
                     'keep': own, 'tool': tool})
    return cuts


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
        if any(joint_at(joint_by_line, idx, role) != 'bend'
               for idx, role in members):
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
