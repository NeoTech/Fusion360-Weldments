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


def _dist(a, b):
    """Distance between two cm points."""
    return math.sqrt(_dot(_sub(a, b), _sub(a, b)))


def line_direction_from(s, e):
    """Unit direction of the segment ``s``->``e`` (cm), or None if degenerate."""
    d = _sub(e, s)
    m = math.sqrt(_dot(d, d))
    return (d[0] / m, d[1] / m, d[2] / m) if m > 1e-12 else None


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


def _all_lines(lines, context):
    """Selected lines followed by context lines (a single indexed space)."""
    return list(lines) + list(context or [])


def _resolve_line(idx, lines, context):
    """The line object for a detection index.

    Indices ``0..len(lines)-1`` are selected lines; a negative index ``~k`` (from
    :func:`detect_corners`) or an index ``>= len(lines)`` (from
    :func:`detect_t_junctions`) refers to context member ``k``.
    """
    if idx >= 0:
        if idx < len(lines):
            return lines[idx]
        return (context or [])[idx - len(lines)]
    return (context or [])[~idx]


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


def detect_corners(lines, tol=_CORNER_TOL, context=None):
    """Auto-detect shared vertices among ``lines``.

    Returns a list of corners; each corner is a dict::

        {'point': (x, y, z),
         'members': [(line_index, 'start'|'end'), ...]}

    Only vertices touched by two or more lines are corners.  Coincident
    endpoints are clustered by proximity (``tol`` cm).

    ``context`` (optional) is a list of *existing* member lines (see
    :func:`detect_t_junctions`).  Their endpoints join the clustering so a new
    line's vertex coinciding with an existing member's END is recognised as a
    corner, but a corner made *only* of context members is not returned (the
    caller never edits those).
    """
    # Collect every endpoint with the line index and which end it is.
    ends = []
    for i, ln in enumerate(lines):
        s, e = line_endpoints(ln)
        ends.append((i, 'start', s))
        ends.append((i, 'end', e))
    n_sel = len(ends)
    if context:
        for k, ln in enumerate(context):
            s, e = line_endpoints(ln)
            ends.append((~k, 'start', s))   # negative index = context member
            ends.append((~k, 'end', e))

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
        # A corner needs >=2 distinct lines meeting there, and at least one of
        # them must be a *selected* line (index >= 0) -- a corner formed only by
        # existing (context) members is not something the caller edits.
        if len({mi for mi, _ in members}) >= 2 and any(mi >= 0 for mi, _ in members):
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


def detect_t_junctions(lines, tol=_CORNER_TOL, context=None):
    """Find endpoints that land on the *interior* of another line (T-junctions).

    Returns a list of dicts::

        {'point': P, 'member': (line_index, 'start'|'end'), 'tool': line_index}

    where ``member``'s endpoint coincides with a point strictly inside ``tool``'s
    run (not at either of ``tool``'s endpoints).  This is the configuration a
    cope saddle is meant for: one tube's end butts against the middle of another
    (a "T"), as opposed to two ends meeting at a shared vertex (a corner).

    ``context`` (optional) is a list of *existing* member lines already placed in
    the design.  Their runs join the set of candidate tools, so a new line's end
    landing on the middle of an existing member is detected as a T-junction and
    gets the same cope/butt treatment -- the tool index is negative (``~k`` for
    context member ``k``) so the caller can tell an existing member from a
    selected line.  Context members are never *members* of a junction (the
    caller does not edit them).
    """
    segs = [line_endpoints(ln) for ln in lines]
    if context:
        segs = segs + [line_endpoints(ln) for ln in context]
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
def _half_extent_cm(geom, basis, axis, anchor=None):
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

    ``anchor`` (optional, local ``(u, v)`` mm) is the section point that sits ON
    the shared reference line when the member is placed off-centre (the
    "Position" alignment grid; see :func:`profiles.grid_anchor`).  The member's
    body is then displaced from the reference line by ``-(au*axis_u + av*axis_v)``,
    so measuring the extent from the anchor (the reference line through the shared
    vertex) rather than the displaced centroid adds that shift's projection -- a
    section tangent to the line by its left edge spans its full width to the
    right, so a neighbour butting along that axis must clear more than the
    symmetric half.  ``anchor`` None / ``(0, 0)`` reproduces the centred
    (historical) extent exactly.

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
    au, av = anchor if anchor else (0.0, 0.0)
    kind = geom.get('kind')
    if kind == 'circles':
        radii = geom.get('radii') or [0.0]
        # A circle's centre is displaced from the anchor by the anchor offset;
        # its extent along ``axis`` is the radius term plus that shift.
        return (max(radii) * math.sqrt(cu * cu + cv * cv)
                + abs(au * cu + av * cv)) * MM_TO_CM
    if kind == 'polygons':
        best = 0.0
        for loop in geom.get('loops') or []:
            for u, v in loop:
                best = max(best, abs((u - au) * cu + (v - av) * cv))
        return best * MM_TO_CM
    return member_depth(geom) * 0.5 * MM_TO_CM


def _miter_setback_cm(geom, basis, own, neigh, anchor, phi):
    """Directional miter setback (cm) for a corner between outward dirs own/neigh.

    The miter plane's normal is the bisector ``n = normalize(own - neigh)``; the
    section is sliced along the in-plane direction ``mdir`` -- ``n`` projected
    perpendicular to the member's own run.  The setback that brings the section's
    far corner exactly onto the plane through the vertex is ``SB = h / tan(phi/2)``,
    where ``h`` is the section's half-extent along ``mdir`` measured from its
    Position anchor.  Using the *directional* extent (not the scalar
    ``member_depth``) is what makes a rotated or off-centre I-beam miter land on
    its flange (or its anchor edge) instead of its web height -- the max dimension
    is blind to both the Rotation column and the Position grid.  Falls back to
    ``member_depth/2`` when no basis is supplied (isotropic, the historical trim).
    """
    n = _norm(_sub(own, neigh))
    mdir = _sub(n, _scale(own, _dot(n, own)))
    if _dot(mdir, mdir) < 1e-12:
        return 0.0                       # n parallel to own: degenerate corner
    mdir = _norm(mdir)
    h = _half_extent_cm(geom, basis, mdir, anchor)
    half = phi / 2.0
    if half <= 1e-6 or half >= math.pi / 2.0 - 1e-6:
        # Collinear (phi ~ pi) or folded-back (phi ~ 0): no sensible setback.
        return 0.0
    return h / math.tan(half)


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


def _depth_cm(depth_by_line, idx):
    """Extra cope/saddle penetration (cm) for line ``idx`` from a mm list.

    ``cope_depth_by_line[idx]`` is the user's fishmouth depth in millimetres
    (how far past the default stopping face the saddled/cope end bites into the
    neighbour); it is converted to the cm the offsets work in.  Absent/None/
    out-of-range yields 0.0 (the default depth).
    """
    if not depth_by_line or idx >= len(depth_by_line):
        return 0.0
    d = depth_by_line[idx]
    return (d or 0.0) * MM_TO_CM


def _butt_through(members, joint_by_line, through_by_line, n=None):
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

    ``n`` (optional) is the count of selected lines; a member index ``>= n`` or
    negative is an *existing* (context) member, whose joint is always ``none``
    (it is never edited), so it always runs through -- the selected butt/cope end
    backs off it.
    """
    def is_ctx(idx):
        return idx < 0 or (n is not None and idx >= n)

    def jid(idx, role):
        return 'none' if is_ctx(idx) else joint_at(joint_by_line, idx, role)

    butt = [(idx, role) for idx, role in members if jid(idx, role) in ('butt', 'cope')]
    if not butt:
        return None
    if through_by_line:
        for idx, role in butt:
            if not is_ctx(idx) and flag_at(through_by_line, idx, role):
                return idx
    for idx, role in members:
        if jid(idx, role) not in ('butt', 'cope'):
            return idx
    return max(idx for idx, _ in butt)


def _corner_partner(members, idx, role, joint_by_line, through_by_line, n=None):
    """The leg that member ``(idx, role)`` actually joins with at a vertex.

    ``members`` is a corner's ``[(line_index, role), ...]``.  A vertex can carry
    more than two endpoints when a *bystander* member's END merely lands there
    too; a joint is really between THIS end and one partner, so pick the partner
    by the joint type and ignore the rest (this is what stops an adjacent tube
    from silently disabling a bend or a miter):

    * ``butt`` / ``cope``: the member that runs THROUGH the corner (see
      :func:`_butt_through`).
    * ``bend`` / ``miter``: another SELECTED leg requesting the same
      relationship; failing that, an existing (context) leg, which supplies the
      direction the arc/miter blends into.

    Returns ``(pidx, prole)`` or ``None`` when no partner applies (e.g. a lone
    bend leg with nothing to blend into).
    """
    def is_ctx(i):
        return i < 0 or (n is not None and i >= n)

    def jid(i, r):
        return 'none' if is_ctx(i) else joint_at(joint_by_line, i, r)

    j = jid(idx, role)
    others = [(i, r) for (i, r) in members if i != idx]
    if j in ('butt', 'cope'):
        t = _butt_through(members, joint_by_line, through_by_line, n=n)
        if t is None:
            return None
        return next(((i, r) for (i, r) in members if i == t), None)
    # bend / miter: prefer a selected leg sharing the relationship, then a
    # context leg that supplies the direction.
    for (i, r) in others:
        if not is_ctx(i) and jid(i, r) == j:
            return (i, r)
    for (i, r) in others:
        if is_ctx(i):
            return (i, r)
    return None


def corner_offsets(lines, geoms, joint_by_line, clr_by_line=None,
                   through_by_line=None, bases=None, saddle_by_line=None,
                   cope_depth_by_line=None, context=None, anchor_by_line=None,
                   bend_joints=None, bend_context=False):
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
    ``cope_depth_by_line[i]`` (optional) is a per-line extra depth (mm) that a
    saddled/cope end bites INTO the neighbour beyond its default stopping face,
    deepening the fishmouth; 0 (or absent) keeps the default.  ``bases[i]``
    (optional) is line ``i``'s placed ``(axis_u, axis_v)`` section basis (see
    :func:`profiles.compute_basis`); when supplied the butt/cope trim uses the
    neighbour's *directional* half-extent along the incoming axis (an I-beam's
    flange width, not its web depth).  When omitted it falls back to the
    isotropic :func:`member_depth`/2.

    ``context`` (optional) is a list of *existing* weldment members already in
    the design, each a dict ``{'line': <line-like>, 'geom': <section geom>,
    'basis': <(u, v) or None>}``.  They join corner/T-junction detection as
    neighbours a selected line can butt/cope/miter against, but are never
    trimmed themselves (their offsets stay ``(0, 0)``).  A selected end meeting
    an existing member's END is a corner (the existing member runs through);
    meeting its interior is a T-junction (cope/butt tool).

    ``bend_joints`` / ``bend_context`` (candidate #4): when ``bend_context`` is
    on and ``bend_joints`` carries the design's Joint records, a corner rounded
    by an EARLIER swept bend is classified with :func:`bend_context_state` and
    the new member's length is set to meet the ARC rather than a leg's flat end
    (S1 extends to the centerline + wall, S2 shortens to the centerline radius;
    S0/S3/S4 need no length change).  Off by default: behaviour is then exactly
    as before, since a context member carries no joint setting of its own and the
    leg-based trim below is blind to the arc.

    ``anchor_by_line[i]`` (optional) is line ``i``'s local ``(u, v)`` mm section
    anchor -- the point that sits ON the shared reference line for the "Position"
    alignment grid (see :func:`profiles.grid_anchor`).  An off-centre member's
    body is displaced from the reference line, so the butt/cope trim measures the
    neighbour's extent from that neighbour's anchor (the reference line through
    the shared vertex) rather than its displaced centroid.  ``None`` / ``(0, 0)``
    (the default, and every existing/context member) reproduces the centred trim.

    The result is a list of ``(offset_start, offset_end)`` tuples, one per line,
    to be *added* to the user's manual start/end offsets.  Lines whose joint is
    ``none`` (or a corner that cannot be resolved) get ``(0.0, 0.0)``.
    """
    n = len(lines)
    offs = [(0.0, 0.0) for _ in range(n)]
    ctx = context or []
    ctx_lines = [c['line'] if isinstance(c, dict) else c for c in ctx]
    # Combined index space: selected lines 0..n-1, then context members n..n+m-1.
    # A corner reports a context member as a negative index (~k); a T-junction
    # reports it as n+k.  ci() maps either encoding to the combined array index.
    L = list(lines) + ctx_lines
    G = list(geoms) + [c.get('geom') for c in ctx]
    B = (list(bases) + [c.get('basis') for c in ctx]) if bases else None
    # Anchors exist only for selected lines; an existing (context) member was
    # placed in an earlier run and its alignment is not recoverable, so it is
    # treated as centred (anchor None -> the historical extent).
    A = list(anchor_by_line) + [None] * len(ctx) if anchor_by_line else None
    all_lines = list(lines) + ctx_lines
    # Candidate #4: mid -> leg for every member, so a bend arc at a corner can
    # be rebuilt from its joint record (context members by registry mid, the
    # lines built this run by a synthetic ('sel', i) key).
    legs_by_mid = {}
    if bend_context and bend_joints:
        for c in ctx:
            mid = _ctx_mid(c)
            if mid is not None:
                legs_by_mid[mid] = c['line'] if isinstance(c, dict) else c
        for i, ln in enumerate(lines):
            legs_by_mid.setdefault(('sel', i), ln)

    def ci(i):
        return i if i >= 0 else n + (~i)

    def is_sel(i):
        return 0 <= i < n

    def dirv(i):
        return line_direction(L[ci(i)])

    def geomv(i):
        return G[ci(i)]

    def basisv(i):
        return B[ci(i)] if B else None

    def anchorv(i):
        return A[ci(i)] if A else None

    def jointv(i, role):
        return joint_at(joint_by_line, i, role) if is_sel(i) else 'none'

    def flagv(f, i, role):
        return flag_at(f, i, role) if is_sel(i) else False

    def bump(i, role, delta):
        if is_sel(i):
            cur = offs[i]
            offs[i] = ((cur[0], cur[1] + delta) if role == 'end'
                       else (cur[0] - delta, cur[1]))

    corners = detect_corners(lines, context=ctx_lines)

    for corner in corners:
        point = corner['point']
        members = corner['members']
        # Outward direction of every leg at the corner, keyed by (idx, role).
        dir_of = {}
        for idx, role in members:
            outward = dirv(idx)
            if role == 'end':
                outward = (-outward[0], -outward[1], -outward[2])
            dir_of[(idx, role)] = outward

        # Candidate #4: a corner an EARLIER swept bend rounded.  A new butt/
        # cope member's tip meets the curved ARC, not a leg's flat end, so the
        # leg-based trim below is wrong for it -- classify the tip and set the
        # length from the arc instead (S1 extends to centerline + wall, S2
        # shortens to the centerline radius; S3/S4 need no length change).
        # S0 (nearest material is a straight leg) stays with the normal path.
        arc_handled = set()
        if bend_context and bend_joints:
            for (idx, role) in members:
                if not is_sel(idx) or jointv(idx, role) not in ('butt', 'cope'):
                    continue
                own = dir_of[(idx, role)]
                r_cm = _perp_extent_cm(geomv(idx), basisv(idx), anchorv(idx),
                                       own)
                # The classifier wants the direction the TIP GROWS (from the
                # member's body toward and past the tip), which is the OPPOSITE
                # of the outward-from-vertex direction dir_of stores.  Passing
                # outward made every outside-approach cope read the arc as
                # BEHIND the tip (S4, delta 0) and silently skip the extension.
                grow = (-own[0], -own[1], -own[2])
                st = bend_context_classify(point, grow, point, bend_joints,
                                           legs_by_mid, r_cm)
                if st is None or st['state'] == 'S0':
                    continue
                arc_handled.add((idx, role))
                if st['delta_cm']:
                    bump(idx, role, -st['delta_cm'])

        # The one butt member that runs through this corner (others back off).
        through_idx = _butt_through(members, joint_by_line, through_by_line,
                                    n=n)

        # A butt corner is handled once here (not per member): exactly one
        # member runs THROUGH (extends past the vertex) and the other backs off.
        # A plain butt stops the backing-off member at the through member's NEAR
        # face (a flat square end, no boolean).  A SADDLED butt instead runs it to
        # the through member's FAR face so the boolean notch (see
        # :func:`corner_cuts`) has overlap to carve -- a near-face trim would
        # leave zero overlap and the saddle would remove nothing.  Handling it per
        # corner (rather than per member) is what stops two butt members from both
        # backing off and leaving a gap; the through member is chosen by
        # :func:`_butt_through`.  The backing-off leg is the butt/cope member that
        # is not the through one, so a bystander leg sharing the vertex is ignored.
        if through_idx is not None:
            t_k = next(k for k, (idx, _) in enumerate(members)
                       if idx == through_idx)
            o_k = next((k for k, (idx, role) in enumerate(members)
                        if k != t_k and jointv(idx, role) in ('butt', 'cope')),
                       None)
            if o_k is not None and members[o_k] not in arc_handled:
                T = members[t_k][0]
                O = members[o_k][0]
                sinp = _sin_between(dir_of[members[t_k]], dir_of[members[o_k]])
                if sinp > 1e-6:
                    # Directional half-extents (cm): the backing-off member stops
                    # at the through member's face measured ALONG the incoming
                    # axis, and the through member grows to the other's face along
                    # ITS axis.  Using the placed bases (when available) makes this
                    # correct for open sections (an I-beam's flange width, not its
                    # web depth).
                    g_t = geomv(T)
                    g_o = geomv(O)
                    b_t = basisv(T)
                    b_o = basisv(O)
                    a_t = anchorv(T)
                    a_o = anchorv(O)
                    orole = members[o_k][1]
                    # The through member's extent along the incoming member's axis.
                    trim = _half_extent_cm(g_t, b_t, dir_of[members[o_k]], a_t) / sinp
                    # The incoming member's extent along the through member's axis.
                    grow = _half_extent_cm(g_o, b_o, dir_of[members[t_k]], a_o) / sinp
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
                    # A cope at a corner is saddled by definition (see
                    # corner_cuts), so it reaches into the tool like a saddle.
                    saddled = (flagv(saddle_by_line, O, orole)
                               or jointv(O, orole) == 'cope')
                    if not saddled:
                        reach = -trim
                    elif _is_hollow(g_t):
                        reach = -trim + (_wall_cm(g_t) or 0.0)
                    else:
                        reach = trim
                    if saddled:
                        reach += _depth_cm(cope_depth_by_line, O)
                    bump(O, orole, reach)
                    bump(T, members[t_k][1], grow)

        for k, (idx, role) in enumerate(members):
            jid = jointv(idx, role)
            if jid == 'none':
                continue
            if (idx, role) in arc_handled:
                continue          # candidate #4: its length came from the arc
            # The partner leg this member joins with at the corner, chosen by the
            # joint type so a bystander leg merely sharing the vertex is ignored
            # (this is what stops an adjacent tube disabling a bend or miter).
            partner = _corner_partner(members, idx, role, joint_by_line,
                                      through_by_line, n=n)
            if partner is None:
                continue
            other_k = members.index(partner)
            phi = _angle_between(dir_of[members[k]], dir_of[members[other_k]])
            if jid == 'bend':
                # Trim each leg back to its arc tangent point (centerline setback).
                # The radius is the max over the SELECTED legs of this bend pair
                # (a context partner contributes no radius of its own), matching
                # the arc :func:`bend_plan` builds.
                pidx = partner[0]
                pair = [idx] + ([pidx] if is_sel(pidx) else [])
                bend_clr = max((clr_by_line[j] for j in pair
                                if clr_by_line and j < len(clr_by_line)),
                               default=0.0)
                if bend_clr <= 0.0:
                    continue
                theta = math.pi - phi
                sb = bend_setback(bend_clr, theta)
                bump(idx, role, -sb)
            elif jid == 'miter':
                # A miter face runs corner-to-corner, so the member must reach
                # PAST the centreline vertex by the setback; the bisector plane
                # through the vertex then trims the diagonal (see corner_cuts).
                # The setback is DIRECTIONAL (the section's extent in the miter
                # plane, from its anchor), so a rotated or off-centre I-beam
                # miters on its flange/anchor edge, not its web height.
                sb = _miter_setback_cm(geomv(idx), basisv(idx),
                                       dir_of[members[k]],
                                       dir_of[members[other_k]],
                                       anchorv(idx), phi)
                if sb <= 0.0:
                    continue
                bump(idx, role, sb)
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
    for jn in detect_t_junctions(lines, context=ctx_lines):
        idx, role = jn['member']
        tool = jn['tool']
        jid = joint_at(joint_by_line, idx, role)
        if jid not in ('butt', 'cope'):
            continue
        g_tool = geomv(tool)
        b_tool = basisv(tool)
        a_tool = anchorv(tool)
        # The tool's half-extent along the incoming member's axis (cm).
        trim = _half_extent_cm(g_tool, b_tool, line_direction(lines[idx]), a_tool)
        if trim <= 0.0:
            continue
        saddled = (jid == 'cope' or flag_at(saddle_by_line, idx, role))
        # A cope onto a BEND's curved arc cannot be cut by a straight box: if the
        # member's tip lands on the tool's arc zone, saddle it no further than a
        # plain butt (a flat square end) -- see _bend_arc_zones.
        if saddled and jid == 'cope':
            ts, te = line_endpoints(L[ci(tool)])
            if _point_in_arc_zones(jn['point'], ts, line_direction(L[ci(tool)]),
                                   _bend_arc_zones(L, tool, joint_by_line,
                                                   clr_by_line, all_lines,
                                                   ctx_lines, n, _CORNER_TOL),
                                   _CORNER_TOL):
                saddled = False
        if not saddled:
            reach = -trim
        elif _is_hollow(g_tool):
            reach = -trim + (_wall_cm(g_tool) or 0.0)
        else:
            reach = trim
        if saddled:
            reach += _depth_cm(cope_depth_by_line, idx)
        bump(idx, role, reach)
    return offs


def _angle_between(a, b):
    d = max(-1.0, min(1.0, _dot(_norm(a), _norm(b))))
    return math.acos(d)


# Tolerance (rad) on the perpendicularity test in :func:`cope_kind`.  Matches
# the auto path's classification in ``corner_cuts`` (0.02 rad ~ 1.15 deg).
_COPES_TOL = 0.02


def cope_kind(subject_dir, tool_dir):
    """The specific cope kind for a member coping against a tool member.

    A coped member meeting its tool at (very near) a right angle is a
    ``cope_t``; anything else (an angled T or a continuous-frame corner) is a
    ``cope_angle``.  This is the same rule the auto path applies when it
    classifies detected T-junctions, shared here so the toolbox Cope tool
    records the same vocabulary instead of a bare ``'cope'``.
    """
    angle = _angle_between(subject_dir, tool_dir)
    return 'cope_t' if abs(angle - math.pi / 2.0) <= _COPES_TOL else 'cope_angle'


def _sin_between(a, b):
    """|sin| of the angle between ``a`` and ``b`` (0 when collinear)."""
    na, nb = _norm(a), _norm(b)
    return math.sqrt(max(0.0, 1.0 - _dot(na, nb) ** 2))


# --------------------------------------------------------------------------- #
# Real corner cuts (Phase 4): angled miter faces and butt/cope saddles
# --------------------------------------------------------------------------- #
def corner_cuts(lines, joint_by_line, tol=_CORNER_TOL, saddle_by_line=None,
                through_by_line=None, context=None):
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
    section's interior.  A corner is cut when a member's joint is ``miter``, a
    saddled ``butt``, or a ``cope``; ``none``/``bend`` corners are skipped.

    A ``cope`` fires at BOTH a T-junction and a shared-vertex corner.  At a
    T-junction (one member's END lands on the *interior* of another's run, see
    :func:`detect_t_junctions`) it saddles over the *side* of the tool.  At a
    corner (two members' ENDs meet at one vertex) it saddles into the tool's
    open END face -- coping a tube so it fits over the end of another, which is
    the "cope to the end of the pipe" case.  Either way the backing-off member
    (not the through one) is cut.

    ``context`` (optional) is a list of *existing* member lines (see
    :func:`corner_offsets`).  A selected member can miter or saddle against one:
    the emitted ``tool`` index is then negative (``~k`` for a corner, or
    ``len(lines)+k`` for a T-junction) so the caller resolves it to the existing
    body.  Only the selected member is ever cut; the context member is the tool.
    """
    ctx = context or []
    ctx_lines = [c['line'] if isinstance(c, dict) else c for c in ctx]
    n = len(lines)

    def line_of(i):
        return _resolve_line(i, lines, ctx_lines)

    def jid_of(i, role):
        return 'none' if (i < 0 or i >= n) else joint_at(joint_by_line, i, role)

    def flag_of(f, i, role):
        return False if (i < 0 or i >= n) else flag_at(f, i, role)

    cuts = []
    for corner in detect_corners(lines, tol, context=ctx_lines):
        members = corner['members']
        V = corner['point']
        # Outward (vertex -> into member) direction of every leg, keyed by
        # (idx, role), so a member's partner can be looked up by reference.
        out_of = {}
        for idx, role in members:
            d = line_direction(line_of(idx))
            out_of[(idx, role)] = d if role == 'start' else _scale(d, -1)
        # For a butt, only the member that BACKS OFF (not the through one) is
        # saddled; the through member keeps its square extended end.
        butt_through = _butt_through(members, joint_by_line, through_by_line,
                                     n=n)
        for k, (idx, role) in enumerate(members):
            if idx < 0 or idx >= n:
                continue  # never cut an existing (context) member
            jid = joint_at(joint_by_line, idx, role)
            # A saddled butt end (not the through one) is notched against the
            # neighbour's body; only meaningful against a solid tool.
            saddled_butt = (jid == 'butt'
                            and flag_at(saddle_by_line, idx, role)
                            and idx != butt_through)
            # A cope at a CORNER (a shared vertex, not a mid-run T) saddles into
            # the neighbour's END face -- coping a tube so it fits over the open
            # end of another.  Like a saddled butt, only the backing-off member
            # (not the through one) is cut.  Cope at a T-junction is handled
            # below; here it degrades from a plain butt to a conformal saddle.
            cope_corner = (jid == 'cope' and idx != butt_through)
            if jid != 'miter' and not saddled_butt and not cope_corner:
                continue
            # The partner leg this member miters/notches against, chosen by the
            # joint type so a bystander leg merely sharing the vertex is ignored.
            partner = _corner_partner(members, idx, role, joint_by_line,
                                      through_by_line, n=n)
            if partner is None:
                continue
            other = partner[0]
            own = out_of[(idx, role)]
            neigh = out_of[partner]
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
    for jn in detect_t_junctions(lines, tol, context=ctx_lines):
        idx, role = jn['member']
        tool = jn['tool']
        if tool >= n:
            tool = ~(tool - n)   # normalise a context tool to the negative form
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


def _bend_arc_zones(L, tool_idx, joint_by_line, clr_by_line, all_lines,
                    ctx_lines, n, tol):
    """Every curved-arc interval [lo, hi] (cm) a bend tool occupies on its run.

    A swept bend replaces the corner between one of ``tool_idx``'s ends and its
    partner leg with a die-radius ARC.  Along the tool's own axis each such arc
    spans from its tangent point (``bend_setback`` from the vertex, toward the
    tool's interior) to the vertex.  A coping member whose tip lands INSIDE one
    of these intervals meets a CURVED surface, which a straight cutter cannot
    match -- the caller downgrades such a cope to a butt.  Returns ``[]`` when
    the tool has no bend (every joint is 'none'/butt/miter/cope), so the run is
    straight and a cope is legitimate.

    ``tool_idx`` may be negative (a context member, which never carries a joint
    setting of its own -> no bend -> ``[]``).  ``all_lines`` is the combined
    selected+context line list used for corner detection.
    """
    if not (0 <= tool_idx < n):
        return []
    if 'bend' not in _ends(joint_by_line[tool_idx]):
        return []
    ln = L[ci_index(tool_idx, n)]
    s, e = line_endpoints(ln)
    d = line_direction(ln)
    zones = []
    for role in ('start', 'end'):
        if joint_at(joint_by_line, tool_idx, role) != 'bend':
            continue
        V = s if role == 'start' else e
        own = _outward(ln, role)
        # The partner leg at this corner: the other member sharing vertex V.
        partner = None
        for corner in detect_corners(all_lines, tol, context=ctx_lines):
            if _dot(_sub(corner['point'], V), _sub(corner['point'], V)) > tol * tol:
                continue
            for (mi, mr) in corner['members']:
                if (mi, mr) != (tool_idx, role):
                    partner = (mi, mr)
                    break
            break
        if partner is None:
            continue
        pidx, prole = partner
        pln = L[ci_index(pidx, n)]
        neigh = _outward(pln, prole)
        theta = bend_turn_angle(own, neigh)
        if abs(theta) < 1e-6 or abs(theta) >= math.pi - 1e-6:
            continue
        pair = [tool_idx] + ([pidx] if 0 <= pidx < n else [])
        clr = max((clr_by_line[j] for j in pair
                   if clr_by_line and j < len(clr_by_line)), default=0.0)
        if clr <= 0.0:
            continue
        sb = bend_setback(clr, theta)          # cm, tangent point distance from V
        t_pt = _add(V, _scale(own, sb))        # toward the tool's interior
        lo = _dot(_sub(t_pt, s), d)
        hi = _dot(_sub(V, s), d)
        zones.append((min(lo, hi), max(lo, hi)))
    return zones


def _point_in_arc_zones(P, s, d, zones, tol):
    """True when the projection of ``P`` on the tool axis falls inside a zone."""
    for z in zones or []:
        t = _dot(_sub(P, s), d)
        if z[0] - tol <= t <= z[1] + tol:
            return True
    return False


def _outward(line, role):
    """Unit direction from the corner *into* a member (away from the vertex)."""
    d = line_direction(line)
    return (-d[0], -d[1], -d[2]) if role == 'end' else d


# --------------------------------------------------------------------------- #
# Bends as EXISTING context (candidate #4): joints against bent elements.
#
# A swept bend in the registry is a 'bend' Joint record (vertex V, two leg
# refs, params.clr_mm) plus an arc BODY that is deliberately not a member --
# it hangs off the joint so the BOM / Bend Table never see it.  To cope or
# butt a NEW member against such a corner, the straight-run zone math above
# is blind (a context member carries no joint setting of its own), so these
# helpers rebuild the arc from the joint record and classify where the new
# member's tip sits relative to it.  See plan/candidate4-bend-joints.md.
# --------------------------------------------------------------------------- #
def _xyz(p):
    """A 3-tuple for a point given either as a tuple/list or an adsk point."""
    if p is None:
        return None
    if isinstance(p, (tuple, list)):
        return (p[0], p[1], p[2])
    return (p.x, p.y, p.z)


def _leg_ends(leg):
    """``(start, end)`` 3-tuples for a leg given as a line, a ``{'line':...}``
    context dict, or a plain ``(start, end)`` endpoint pair."""
    if isinstance(leg, dict):
        return line_endpoints(leg['line'])
    if hasattr(leg, 'worldGeometry'):
        return line_endpoints(leg)
    s, e = leg                              # endpoint pair
    return _xyz(s), _xyz(e)


def _vertex_outward(leg, V):
    """Unit direction at the corner ``V`` pointing AWAY from it along ``leg``.

    The leg end nearest ``V`` is the corner end; the outward direction runs from
    ``V`` toward the far end.  Independent of any stored 'role' (a toolbox-bend
    joint records None, and a context leg's drawn centreline may run start->end
    either way), which is why :func:`bend_arc_geometry` uses this rather than
    :func:`_leg_outward`.
    """
    s, e = _leg_ends(leg)
    V = _xyz(V)
    ds, de = _dist(V, s), _dist(V, e)
    if min(ds, de) > 1e-4:
        return None                      # V is not on this leg's endpoints
    far = e if ds <= de else s
    return line_direction_from(V, far)


def _leg_outward(leg, role):
    """Outward unit direction at the vertex for any leg form (see _leg_ends)."""
    if isinstance(leg, dict) or hasattr(leg, 'worldGeometry'):
        return _outward(leg['line'] if isinstance(leg, dict) else leg, role)
    s, e = _leg_ends(leg)
    d = line_direction_from(s, e)
    if d is None:
        return None
    return _scale(d, -1) if role == 'end' else d


def bend_arc_geometry(V, leg_lines, clr_mm):
    """Centerline arc of the bend rounding the corner V between ``leg_lines``.

    ``V`` is the sharp vertex (cm) as recorded on the joint; ``leg_lines`` is
    ``[(leg, role), ...]`` for the two legs, where a leg is a sketch line, a
    ``{'line': ...}`` context dict, or a ``(start, end)`` endpoint pair -- the
    outward directions at V come from it exactly like :func:`bend_path`.
    Returns the :func:`bend_path` dict augmented with ``'legs'`` (the input
    pairs) and ``'setback_cm'``, or None when the corner is degenerate.
    """
    if clr_mm is None or clr_mm <= 0.0 or len(leg_lines) != 2:
        return None
    # Outward from the VERTEX, not the stored role: a toolbox-bend joint records
    # role=None and a context leg's drawn centreline may run start->end either
    # way, so the end nearest V is the corner end and the far end gives the
    # direction (see :func:`_vertex_outward`).
    dirs = [_vertex_outward(leg, V) for (leg, _role) in leg_lines]
    if any(d is None for d in dirs):
        return None
    g = bend_path(_xyz(V), dirs[0], dirs[1], clr_mm * MM_TO_CM)
    if g is None:
        return None
    g['legs'] = list(leg_lines)
    g['setback_cm'] = g['radius_cm'] * math.tan(g['theta'] / 2.0)
    return g


def bend_arc_zones_for_member(line, mid, joints, legs_by_mid=None):
    """Curved-arc intervals [lo, hi] (cm) a CONTEXT member occupies on its axis.

    The context-tool twin of :func:`_bend_arc_zones`: a member stored in the
    registry takes part in a swept corner through a ``'bend'`` Joint record
    rather than a joint setting of its own, so the zones come from every bend
    joint referencing ``mid``.  ``line`` is the member's centreline (a sketch
    line or a ``{'line': ...}`` context dict); ``joints`` an iterable of Joint
    records (or their ``to_dict`` form); ``legs_by_mid`` optionally maps
    ``mid -> (leg, role)`` so the setback is re-derived from live geometry --
    otherwise the joint's stored ``params['setback_cm']`` is used.  Each zone
    spans vertex -> tangent point along the member's own axis, the same
    interval :func:`_bend_arc_zones` produces for a selected tool.  Returns
    ``[]`` when no bend references ``mid``.
    """
    ln = line['line'] if isinstance(line, dict) else line
    s, e = line_endpoints(ln)
    d = line_direction(ln)
    zones = []
    for j in joints or []:
        kind = j.get('kind') if isinstance(j, dict) else j.kind
        if kind != 'bend':
            continue
        refs = j.get('refs') if isinstance(j, dict) else j.refs
        params = (j.get('params') if isinstance(j, dict) else j.params) or {}
        V = _xyz(j.get('vertex') if isinstance(j, dict) else j.vertex)
        if V is None or not refs:
            continue
        for r in refs:
            if r.get('mid') != mid:
                continue
            own = _outward(ln, r.get('role'))
            sb = params.get('setback_cm')
            if sb is None and legs_by_mid:
                pair = [(legs_by_mid[rr['mid']], rr.get('role'))
                        for rr in refs if rr.get('mid') in legs_by_mid]
                if len(pair) == 2:
                    g = bend_arc_geometry(V, pair, params.get('clr_mm'))
                    sb = g['setback_cm'] if g else None
            if sb is None or sb <= 0.0:
                continue
            t_pt = _add(V, _scale(own, sb))
            lo = _dot(_sub(t_pt, s), d)
            hi = _dot(_sub(V, s), d)
            zones.append((min(lo, hi), max(lo, hi)))
    return zones


def _ctx_mid(c):
    """The registry member id of a context entry (a dict with 'mid', else None)."""
    return c.get('mid') if isinstance(c, dict) else None


def bend_arc_at_vertex(V, joints, legs_by_mid, tol=0.05):
    """The centerline arc of the bend joint rounding vertex ``V``, or None.

    ``joints`` are Joint records (or their ``to_dict`` form) and ``legs_by_mid``
    maps each leg's ``mid -> (leg, role)`` for the members available live.  A
    ``'bend'`` joint whose vertex coincides with ``V`` (within ``tol`` cm) and
    whose two legs both resolve yields :func:`bend_arc_geometry` (tagged with the
    joint's ``jid``); otherwise None.  This is the single lookup the cutter layer
    and the length layer share, so a cope's cut and its built length never
    disagree about which arc (if any) the tip meets.
    """
    for j in joints or []:
        kind = j.get('kind') if isinstance(j, dict) else j.kind
        if kind != 'bend':
            continue
        jv = _xyz(j.get('vertex') if isinstance(j, dict) else j.vertex)
        if jv is None or _dist(jv, _xyz(V)) > tol:
            continue
        refs = j.get('refs') if isinstance(j, dict) else j.refs
        params = (j.get('params') if isinstance(j, dict) else j.params) or {}
        if not refs or len(refs) < 2:
            continue
        pair = [(legs_by_mid[r['mid']], r.get('role'))
                for r in refs if legs_by_mid and r.get('mid') in legs_by_mid]
        if len(pair) != 2:
            continue
        g = bend_arc_geometry(jv, pair, params.get('clr_mm'))
        if g is None:
            continue
        g['jid'] = j.get('jid') if isinstance(j, dict) else j.jid
        return g
    return None


def bend_context_classify(tip, own, V, joints, legs_by_mid, r_cm, tol=0.05):
    """Classify a new member's tip against a bend arc at corner ``V`` (or None).

    Convenience over :func:`bend_arc_at_vertex` + :func:`bend_context_state`:
    returns the state dict (``state``/``target``/``delta_cm``/``reason``) with
    the arc's ``jid`` and ``arc`` attached, or None when no bend rounds ``V``
    (the ordinary cope/butt path is then correct and must run untouched).  The
    tip is classified at the drawn corner ``V`` running along ``own``.
    """
    g = bend_arc_at_vertex(V, joints, legs_by_mid, tol=tol)
    if g is None:
        return None
    s = bend_context_state(_xyz(V), own, g, r_cm)
    s['jid'] = g['jid']
    s['arc'] = g
    return s


def bend_context_state(tip, d, arc_geom, r_cm, tol=1e-6):
    """Where a member tip ``tip`` (cm) running along unit ``d`` meets a bend.

    ``arc_geom`` is a :func:`bend_arc_geometry` result (centerline arc C/R,
    tangent points T1/T2, ``legs``) and ``r_cm`` the new member's tube
    half-extent (its section radius).  Returns::

        {'state': 'S0'|'S1'|'S2'|'S3'|'S4', 'target': P|None,
         'delta_cm': float, 'reason': str}

    States (plan/candidate4-bend-joints.md, user policy "Reach CL + Wall"):
      S0  tip lies on a leg's STRAIGHT run (outside every arc zone) -- the
          existing cope/butt-vs-leg-body path handles it; delta 0.
      S1  corner gap: the tip is outside the arc's tube envelope and the
          axis crosses the centerline circle beyond the tip -- extend to the
          first crossing of |l(t)-C| = R plus one wall (2r) of overlap.
      S2  overshoot: the tip is inside the centerline circle (R - r) on the
          concave side -- shorten to R from C (first crossing behind the tip).
      S3  on-arc: the tip is within the tube envelope -- no length change;
          cope/saddle against the arc body.
      S4  skew / out-of-plane: the axis never crosses the circle -- butt to
          the nearest point of the centerline arc pulled back by r (saddle
          cut vs the arc body is the execution layer's choice).

    ``delta_cm`` is the SIGNED axial move along ``d`` from tip to target
    (negative = extend, positive = shorten); it is recomputed from the joint
    geometry every call, never accumulated, so re-runs are idempotent.
    """
    tip = _xyz(tip)
    d = _norm(d)
    C = arc_geom['center']
    R = arc_geom['radius_cm']
    env = R + r_cm
    inner = R - r_cm
    dist = _dist(tip, C)

    def _crossings():
        w = _sub(C, tip)
        b = _dot(w, d)
        c = _dot(w, w) - R * R
        disc = b * b - c
        if disc < -1e-9:
            return None                      # skew: never meets the circle
        sq = math.sqrt(max(disc, 0.0))
        return (b - sq, b + sq)              # sorted (t1 <= t2)

    if dist > env + tol:
        # Outside the tube envelope.  Whichever centreline feature is NEAREST
        # to the tip decides: a leg's straight segment (S0 -- the existing
        # cope/butt-vs-leg-body path is correct) or the curved arc.  When the
        # arc is nearest, the axis either crosses the circle ahead (S1: the
        # tip floats in the corner void short of the arc -- extend) or misses
        # it entirely (S4: a skew/out-of-plane pass-by).
        V = arc_geom['point']
        sb = arc_geom.get('setback_cm')
        if sb is None:
            sb = R * math.tan(arc_geom['theta'] / 2.0)
        leg_d = float('inf')
        for (leg, role) in arc_geom.get('legs') or []:
            s, e = _leg_ends(leg)
            own = _leg_outward(leg, role)
            far, tang = e if role == 'start' else s, _add(V, _scale(own, sb))
            leg_d = min(leg_d, _seg_dist(tip, far, tang))
        arc_d = _dist(tip, _arc_nearest(tip, arc_geom))
        if leg_d <= arc_d + tol:
            return {'state': 'S0', 'target': tip, 'delta_cm': 0.0,
                    'reason': 'nearest bend material is a straight leg'}
        x = _crossings()
        if x is None or x[1] <= tol:
            return {'state': 'S4', 'target': _arc_nearest(tip, arc_geom),
                    'delta_cm': 0.0,
                    'reason': 'axis never crosses the centerline circle'}
        t_star = max(x[0], 0.0) + 2.0 * r_cm   # reach CL + one wall overlap
        return {'state': 'S1', 'target': _add(tip, _scale(d, t_star)),
                'delta_cm': -t_star,
                'reason': 'corner gap: extend to centerline + wall'}

    # S3: inside the tube envelope (R - r <= dist <= R + r) -> cope vs arc.
    if dist >= inner - tol:
        return {'state': 'S3', 'target': tip, 'delta_cm': 0.0,
                'reason': 'tip within the arc tube envelope'}

    # S2: tip inside the centerline circle on the concave side -> shorten.
    x = _crossings()
    if x is None:
        return {'state': 'S4', 'target': _arc_nearest(tip, arc_geom),
                'delta_cm': 0.0, 'reason': 'inside circle, no crossing'}
    t_back = -x[0]                            # crossing behind the tip
    return {'state': 'S2', 'target': _add(tip, _scale(d, t_back)),
            'delta_cm': t_back,
            'reason': 'overshoot: shorten to centerline radius from C'}


def _arc_nearest(P, arc_geom):
    """Nearest point of the centerline ARC (not its circle) to ``P``.

    Projects ``P`` onto the bend plane, radializes about the center, and
    clamps to the arc's angular span between the tangent points; outside the
    span the nearest point is the closer tangent point.
    """
    C, R, axis = arc_geom['center'], arc_geom['radius_cm'], arc_geom['axis']
    w = _sub(P, C)
    radial = _sub(w, _scale(axis, _dot(w, axis)))
    n = math.sqrt(_dot(radial, radial))
    v1 = _sub(arc_geom['t1'], C)
    v2 = _sub(arc_geom['t2'], C)
    if n <= 1e-9:
        return arc_geom['t1'] if _dist(P, arc_geom['t1']) <= \
            _dist(P, arc_geom['t2']) else arc_geom['t2']
    uq = _scale(radial, 1.0 / n)

    def _ang(u):
        return math.atan2(_dot(_cross(v1, u), axis), _dot(v1, u))

    a2, aq = _ang(_scale(v2, 1.0 / _dist((0, 0, 0), v2))), _ang(uq)
    lo, hi = (a2, 0.0) if a2 < 0 else (0.0, a2)   # the arc's angular span
    if aq < lo - 1e-9 or aq > hi + 1e-9:
        return arc_geom['t1'] if _dist(P, arc_geom['t1']) <= \
            _dist(P, arc_geom['t2']) else arc_geom['t2']
    return _add(C, _scale(uq, R))


def _seg_dist(P, A, B):
    """Distance from point ``P`` to the segment ``A``->``B`` (cm)."""
    ab = _sub(B, A)
    t = _dot(_sub(P, A), ab)
    ll = _dot(ab, ab)
    if ll <= 1e-12:
        return _dist(P, A)
    t = max(0.0, min(ll, t)) / ll
    return _dist(P, _add(A, _scale(ab, t)))


def bend_plan(lines, joint_by_line, clr_by_line, inverse_by_line=None,
              tol=_CORNER_TOL, context=None):
    """Plan every swept-bend corner among ``lines``.

    ``clr_by_line[i]`` is the die centerline radius (mm) to use at line ``i``'s
    end; a corner bends when its leg(s) request ``bend`` and a radius is
    available.  ``inverse_by_line[i]`` (optional, scalar or ``(start, end)``)
    flips the sweep direction of line ``i``'s bend.  Returns a list of corner
    dicts::

        {'point': V, 'center': C, 'axis': a, 'theta': turn_rad,
         'tangent': [(line_index, role, T), ...], 'arc_length': L}

    ``center`` sits on the inside of the turn at ``V + (R/cos(theta/2)) *
    normalize(u+v)``; ``axis`` is the bend-plane normal (revolve axis); each
    ``tangent`` entry gives the trimmed end point of one leg.

    The arc centre is symmetric in the two legs, but the sweep direction is the
    sign of the revolve angle, which Fusion derives from the ordered pair
    ``(u, v)`` -- so the same physical corner sweeps +90 or -90 purely by
    selection order.  When either leg requests ``inverse`` the plan's
    ``direction`` is set to -1, flipping the sweep to the other side (the
    builder multiplies the angle by it; negating the axis *line* would do
    nothing, since a line has no direction).

    ``context`` (optional) is a list of *existing* member lines (see
    :func:`corner_offsets`).  A selected ``bend`` leg that meets an existing
    member's END is planned as a bend against it: the arc blends the new leg into
    the existing member's direction.  ``tangent`` lists the selected leg first so
    the builder always revolves from a line it actually created.
    """
    ctx = context or []
    ctx_lines = [c['line'] if isinstance(c, dict) else c for c in ctx]
    n = len(lines)

    def line_of(i):
        return _resolve_line(i, lines, ctx_lines)

    def is_sel(i):
        return 0 <= i < n

    def jid_of(i, role):
        return 'none' if not is_sel(i) else joint_at(joint_by_line, i, role)

    plans = []
    for corner in detect_corners(lines, tol, context=ctx_lines):
        members = corner['members']
        # A swept bend rounds a leg that asks for 'bend' and its partner leg.
        # Pick the first SELECTED bend leg and resolve its partner by joint type,
        # so a bystander member merely sharing the vertex does not disable the
        # bend (the old "exactly two members" gate rejected such corners).
        sel = [(idx, role) for idx, role in members if is_sel(idx)]
        bend_leg = next(((i, r) for (i, r) in sel
                         if joint_at(joint_by_line, i, r) == 'bend'), None)
        if bend_leg is None:
            continue
        partner = _corner_partner(members, bend_leg[0], bend_leg[1],
                                  joint_by_line, None, n=n)
        if partner is None:
            continue
        # The partner must be a bend too (a selected leg) or an existing member
        # whose direction the arc blends into -- never a plain 'none' leg.
        if is_sel(partner[0]) and joint_at(joint_by_line, *partner) != 'bend':
            continue
        # Order the legs so the selected bend leg is first (builder uses tangent[0]).
        (i0, r0), (i1, r1) = bend_leg, partner
        # The radius is the max over the SELECTED legs of this pair, matching the
        # trim corner_offsets applies (which also uses the pair max).  Reading
        # only the first leg's radius silently dropped the arc whenever that leg
        # had no die assigned while its partner did -- the legs got trimmed but
        # no arc was built, leaving a gap at the corner.
        pair = [i0] + ([i1] if is_sel(i1) else [])
        clr = max((clr_by_line[j] for j in pair
                   if clr_by_line and j < len(clr_by_line)), default=0.0)
        if clr <= 0.0:
            continue
        V = corner['point']
        u = _outward(line_of(i0), r0)
        v = _outward(line_of(i1), r1)
        theta = bend_turn_angle(u, v)
        if abs(theta) < 1e-6 or abs(theta) >= math.pi - 1e-6:
            continue  # collinear or folded back -- not a bend
        sb = clr * math.tan(theta / 2.0) * MM_TO_CM   # cm
        bis = _norm(_add(u, v))
        dist = (clr * MM_TO_CM) / math.cos(theta / 2.0)
        center = _add(V, _scale(bis, dist))
        axis = _norm(_cross(u, v))
        # Inverse on either leg flips the sweep by negating the revolve angle's
        # sign (see the builder), so a corner built from reversed selection
        # order bends the same way as one built in the natural order.  The axis
        # vector itself is left as the geometric normal u x v.
        direction = -1.0 if (inverse_by_line and any(
            is_sel(idx) and flag_at(inverse_by_line, idx, role)
            for idx, role in (bend_leg, partner))
        ) else 1.0
        tangent = [(i0, r0, _add(V, _scale(u, sb))),
                   (i1, r1, _add(V, _scale(v, sb)))]
        plans.append({'point': V, 'center': center, 'axis': axis,
                      'theta': theta, 'direction': direction,
                      'radius_cm': clr * MM_TO_CM,
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
# Joint specs (refactor): ONE record per joint occurrence, carrying the axial
# setback AND a bounded cutter spec.  The execution layer builds members from
# ``offsets`` and trims them from ``occs`` -- no infinite-plane Split Body and
# no pointContainment guessing: every cut is a Combine(Cut) against a finite
# cutter solid confined to the joint box.  Material outside a joint box can
# never be touched, by construction.
# --------------------------------------------------------------------------- #

# Safety factor on the joint-box cross extents (ratio, not cm).
_BOX_SAFETY = 1.2


def _frame(d):
    """Right-handed orthonormal frame ``(d, e1, e2)`` for a unit direction ``d``.

    ``e1``/``e2`` span the plane perpendicular to ``d``; the choice of helper
    axis avoids the near-parallel case.  Used to express a joint box in the
    member's own frame (axial + two section directions).
    """
    helper = (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)
    e1 = _norm(_cross(d, helper))
    e2 = _cross(d, e1)
    return d, e1, e2


def _plug_reach(base, perp_both, angle):
    """Axial reach (cm) a cope/saddle box must cover along the coping member.

    A wall plug pushed into the neighbour's hollow is bounded by BOTH sections:
    its far corner sits ``perp_both`` (the tool's and the coping member's
    perpendicular extents added) away from the neighbour's axis, and the plug
    lies along the neighbour's bore, so projected onto the COPING member's axis
    that corner is ``perp_both / sin(angle)`` from the joint (``angle`` between
    the two runs).  On a perpendicular T (sin = 1) this is just ``perp_both``;
    on a shallow angle it grows, and the box must grow with it or the plug pokes
    out and is mis-classified as the run.  ``base`` (the tool's axial half-extent
    + cope depth) is the floor.
    """
    s = math.sin(max(min(angle, math.pi - angle), 1e-3))
    return max(base, perp_both / s)


def _region_box(V, d, reach, perp, leg_room):
    """A joint box: centred ON the vertex, faces normal to the member.

    ``V`` is the joint vertex, ``d`` the member's unit direction (vertex ->
    into member), ``reach`` the distance from V the cutting must cover along
    the axis, and ``perp`` the needed half-extent perpendicular to the axis.
    The box is symmetric about V (the waste of a miter pokes PAST the vertex
    toward the neighbour, and a cope plug sits inside the tool just past V, so
    the cutter must straddle the vertex on both sides).  Its axial half-extent
    is ``reach`` but CLAMPED to ``leg_room`` (half the shortest leg at the
    joint) so the box can never reach past a leg's midpoint: elements farther
    out along a member are untouchable by construction.

    Returns ``{'center': V, 'axes': (d, e1, e2), 'half': (ha, hb, hc)}`` in cm.
    """
    d, e1, e2 = _frame(d)
    axial = min(max(reach, 0.0), leg_room) * _BOX_SAFETY
    perp = perp * _BOX_SAFETY
    return {'center': V,
            'axes': (d, e1, e2),
            'half': (axial, perp, perp)}


def _leg_room(L, members, V, n):
    """Half the shortest leg's length from the vertex (cm) -- the box clamp.

    Every member at the joint bounds how far a joint box may extend along its
    run: never past the midpoint of the shortest one.
    """
    room = float('inf')
    for idx, _role in members:
        ln = L[ci_index(idx, n)]
        s, e = line_endpoints(ln)
        to_v = _dot(_sub(V, s), line_direction(ln))
        length = math.sqrt(_dot(_sub(e, s), _sub(e, s)))
        # Distance from V to the member's FARTHER endpoint.  When V is at an
        # end (a coping member's tip, or a corner) the whole run lies one way,
        # so the box may reach half of it; the old ``length - abs(to_v)`` read
        # 0 there and collapsed the box so its cutoffs were never classified.
        from_v_far = max(abs(to_v), abs(length - to_v))
        room = min(room, 0.5 * from_v_far)
    return room if room != float('inf') else 0.0


def _perp_extent_cm(geom, basis, anchor, d):
    """Max section half-extent (cm) perpendicular to run direction ``d``.

    Measured in the member's own placed ``basis`` (so an I-beam contributes its
    flange width / web height, not a bounding guess).  ``anchor`` is the local
    ``(u, v)`` mm point on the reference line (Position grid); None / ``(0, 0)``
    is the centred case.  ``basis`` None falls back to the isotropic
    ``member_depth``/2.  Lifted out of :func:`joint_spec` so the Phase-4 toolbox
    tools (which take an explicit member pair, no frame detection) reuse the
    exact same extent the auto path computes.
    """
    if basis is None:
        return member_depth(geom) * 0.5 * MM_TO_CM
    u, v = basis
    cu, cv = _dot(u, d), _dot(v, d)
    au, av = anchor if anchor else (0.0, 0.0)
    if geom and geom.get('kind') == 'circles':
        r = max(geom.get('radii') or [0.0])
        return (r * math.sqrt(max(0.0, 1.0 - cu * cu - cv * cv) + 0.0)
                + abs(au * cu + av * cv)) * MM_TO_CM or r * MM_TO_CM
    best = 0.0
    for loop in (geom or {}).get('loops') or [[(0, 0)]]:
        for p0, p1 in loop:
            pu, pv = p0 - au, p1 - av
            par = pu * cu + pv * cv
            best = max(best, math.sqrt(max(0.0, pu * pu + pv * pv - par * par)))
    return best * MM_TO_CM


def cope_cutter(subject, tool, landing, subject_geom=None, tool_geom=None,
                subject_basis=None, tool_basis=None, subject_anchor=None,
                tool_anchor=None, depth_mm=0.0):
    """The cutter for ONE cope, from an explicitly-picked pair (no detection).

    This is the Phase-4 toolbox entry point: instead of feeding the whole frame
    to :func:`joint_spec` and letting it *discover* that ``subject``'s end lands
    on ``tool``, the Cope tool hands us the two members and the landing point
    directly.  We compute exactly the ``{'type':'body','tool','region'}`` cutter
    the auto path would have produced for that pair, reusing the same box math
    (:func:`_region_box`, :func:`_plug_reach`, :func:`_perp_extent_cm`) so the
    result is identical -- but with no corner/T detection to get wrong.

    ``subject``/``tool`` are ``(start, end)`` centrelines in cm (the coping
    member and the member it sits on).  ``landing`` is the point (cm) on the
    tool where the subject's tip meets it -- the vertex the user picked.
    ``*_geom`` are mm section descriptors, ``*_basis`` the placed ``(u, v)``
    axes (None -> isotropic), ``*_anchor`` the Position-grid local ``(u, v)`` mm.
    ``depth_mm`` deepens the bite into the tool.  Returns the cutter dict (with
    ``tool`` set to the caller's own tool handle) or None if the geometry is
    degenerate (zero-length members).
    """
    ss, se = subject
    ts, te = tool
    sd = line_direction_from(ss, se)
    td = line_direction_from(ts, te)
    if sd is None or td is None:
        return None
    # The subject's tip is the endpoint nearest the landing; ``own`` points from
    # the vertex INTO the member (toward its far end) -- the same as joint_spec's
    # outward(idx, role) whichever end carries the coping tip.
    far = ss if _dist(landing, se) <= _dist(landing, ss) else se
    own = line_direction_from(landing, far)
    if own is None:
        return None
    sub_perp = _perp_extent_cm(subject_geom, subject_basis, subject_anchor, sd)
    tool_perp = _perp_extent_cm(tool_geom, tool_basis, tool_anchor, td)
    perp = max(sub_perp, tool_perp)
    trim = _half_extent_cm(tool_geom, tool_basis, sd, tool_anchor)
    reach = _plug_reach(trim + abs(depth_mm * MM_TO_CM),
                        sub_perp + tool_perp, _angle_between(sd, td))
    # Box clamp: half the distance from the vertex to the FARTHER endpoint of
    # either member (mirrors _leg_room, so a mid-run landing on the tool does not
    # let the box reach the tool's whole length).
    room = min(0.5 * max(_dist(landing, ss), _dist(landing, se)),
               0.5 * max(_dist(landing, ts), _dist(landing, te)))
    box = _region_box(landing, own, reach, perp, room)
    return {'type': 'body', 'tool': tool, 'region': box}


def _tip_and_own(subject, vertex):
    """``(own, tip)`` for a member whose coping/mitering end is at ``vertex``.

    ``own`` points from the vertex INTO the member (toward its far end) -- the
    same as joint_spec's ``outward(idx, role)`` whichever end carries the tip;
    ``tip`` is that far endpoint. Returns ``(None, None)`` if degenerate.
    """
    ss, se = subject
    far = ss if _dist(vertex, se) <= _dist(vertex, ss) else se
    return line_direction_from(vertex, far), far


def miter_cutter(member_a, member_b, vertex, a_geom=None, b_geom=None,
                 a_basis=None, b_basis=None, a_anchor=None, b_anchor=None):
    """The cutter for ONE miter, from an explicitly-picked pair (no detection).

    The Miter tool's Phase-4 entry point, mirroring :func:`cope_cutter`: the two
    members meeting at ``vertex`` are handed in directly, and we build exactly
    the ``{'type':'plane', ...}`` cutter :func:`joint_spec` produces for a corner
    miter -- the bisector plane through the vertex plus a joint box -- reusing the
    same setback/extent/box math so the result is identical, with no corner
    detection to get wrong.

    ``member_a``/``member_b`` are ``(start, end)`` centrelines in cm; ``vertex``
    is the shared corner (cm). ``*_geom`` are mm section descriptors, ``*_basis``
    the placed ``(u, v)`` axes, ``*_anchor`` the Position-grid local ``(u, v)`` mm.
    Returns the cutter dict (with ``setback`` for the caller's information) or
    None if the geometry is degenerate (collinear or folded-back members).
    """
    as_, ae = member_a
    bs, be = member_b
    ad = line_direction_from(as_, ae)
    bd = line_direction_from(bs, be)
    if ad is None or bd is None:
        return None
    own, _ta = _tip_and_own(member_a, vertex)
    neigh, _tb = _tip_and_own(member_b, vertex)
    if own is None or neigh is None:
        return None
    phi = _angle_between(own, neigh)
    if phi <= 1e-6 or phi >= math.pi - 1e-6:
        return None                       # folded-back or straight: no miter
    nrm = _norm(_sub(own, neigh))
    if _dot(nrm, nrm) < 1e-9:
        return None
    sb = _miter_setback_cm(a_geom, a_basis, own, neigh, a_anchor, phi)
    perp = max(_perp_extent_cm(a_geom, a_basis, a_anchor, ad),
               _perp_extent_cm(b_geom, b_basis, b_anchor, bd))
    # Box clamp: half the distance from the vertex to the FARTHER endpoint of
    # either member (mirrors _leg_room for the two legs at the corner).
    room = min(0.5 * max(_dist(vertex, as_), _dist(vertex, ae)),
               0.5 * max(_dist(vertex, bs), _dist(vertex, be)))
    box = _region_box(vertex, own, sb, perp, room)
    return {'type': 'plane', 'point': vertex, 'normal': nrm,
            'region': box, 'setback': sb}


def butt_trim(subject, tool, landing, subject_geom=None, tool_geom=None,
              subject_basis=None, tool_basis=None, subject_anchor=None,
              tool_anchor=None, saddle=False, depth_mm=0.0):
    """The axial trim for ONE butt, from an explicitly-picked pair (no detection).

    The Butt tool's Phase-4 entry point. ``subject`` backs off onto ``tool``
    (which runs through). Returns how far along the subject's own axis its tip
    must sit from ``landing`` (negative = short of the vertex, i.e. a flat end at
    the tool's near face; positive = into the tool, for a saddle), plus the
    cutter the execution layer applies. Unlike the auto path -- where a plain
    butt is a pure build-time axial trim -- the toolbox cuts EXISTING bodies, so
    every mode returns a ``{'type':'body', ...}`` cutter (the tool body bounded
    to a joint box):

    * butt        -> a NEAR-face box (reach = the tool's half-extent): the
      subject's tip is trimmed flush to the tool's surface, a flat square end.
    * saddle      -> the full plug box (see :func:`cope_cutter`): the tool's
      cross-section is carved out so the subject sits INTO it, run to the FAR
      face (solid) or just past the near wall (hollow), matching
      :func:`corner_offsets`' saddled-butt rule.

    ``depth_mm`` deepens a saddle's bite. Returns None if the members are
    collinear (no meaningful butt face).
    """
    ss, se = subject
    ts, te = tool
    sd = line_direction_from(ss, se)
    td = line_direction_from(ts, te)
    if sd is None or td is None:
        return None
    sinp = _sin_between(sd, td)
    if sinp <= 1e-6:
        return None                       # collinear: nothing to trim against
    own, _far = _tip_and_own(subject, landing)
    if own is None:
        return None
    trim = _half_extent_cm(tool_geom, tool_basis, sd, tool_anchor) / sinp
    sub_perp = _perp_extent_cm(subject_geom, subject_basis, subject_anchor, sd)
    tool_perp = _perp_extent_cm(tool_geom, tool_basis, tool_anchor, td)
    perp = max(sub_perp, tool_perp)
    room = min(0.5 * max(_dist(landing, ss), _dist(landing, se)),
               0.5 * max(_dist(landing, ts), _dist(landing, te)))
    if not saddle:
        # Flush butt: a shallow box at the tool's near face, no plug reach.
        box = _region_box(landing, own, trim, perp, room)
        return {'reach': -trim, 'cutter': {'type': 'body', 'tool': tool,
                                           'region': box}}
    depth = abs(depth_mm) * MM_TO_CM
    if _is_hollow(tool_geom):
        reach = -trim + (_wall_cm(tool_geom) or 0.0) + depth
    else:
        reach = trim + depth
    reach_box = _plug_reach(trim + depth, sub_perp + tool_perp,
                            _angle_between(sd, td))
    box = _region_box(landing, own, reach_box, perp, room)
    return {'reach': reach, 'cutter': {'type': 'body', 'tool': tool,
                                       'region': box}}


def cope_trim_box(subject, tool, landing, subject_geom=None, tool_geom=None,
                  subject_basis=None, tool_basis=None, subject_anchor=None,
                  tool_anchor=None, depth_mm=0.0):
    """The pull-back prism for coping an EXISTING member (toolbox Cope, C1).

    :func:`cope_cutter` builds the symmetric joint box the auto path uses while
    the coping member is still being BUILT: its tip is *placed* at the stopping
    face by an axial offset, and the tool boolean then carves the saddle.  The
    toolbox copes a member that already exists -- its tip was built to the
    vertex and now pokes into the tool's bore.  Before the saddle boolean can
    run, that protruding tip must be pulled back to the same stopping face the
    auto path builds to.  This is that pull-back: a finite prism spanning from
    the offset plane to past the vertex, for a Combine(Cut, keep_tool=False)
    against the member alone (the tool is not a target of this cut).

    ``subject``/``tool`` are ``(start, end)`` centrelines in cm and ``landing``
    the picked point (cm) on the tool where the subject's tip meets it; the
    section descriptors / bases / anchors are as in :func:`cope_cutter`.  The
    stopping face (``reach``, signed from the vertex along the subject's axis,
    negative = short of the vertex, the :func:`corner_offsets` convention):

    * hollow tool -> ``-trim + wall + depth``: just past the near wall's inner
      face, deepened by ``depth_mm``.  The prism removes the member's plug
      inside the bore; the following boolean then carves the wall it still
      overlaps.
    * solid tool  -> ``depth`` (the vertex plane by default).  An existing
      member's tip is already at the vertex, embedded in the tool's near half;
      the boolean (member minus tool) carves the conforming face by itself, so
      the prism only needs to swallow any overshoot *past* the vertex.  (The
      auto path instead builds the tip to the far face so the boolean spans the
      whole section -- with equal sections the two end shapes agree.)

    Returns ``{'reach': cm, 'region': box}`` -- ``region`` the asymmetric box
    (its member-side face EXACTLY on the offset plane; the safety factor
    extends only toward the tool) consumable by the builder's
    ``_box_cutter``.  None when degenerate (zero-length member, collinear
    pair, or a box clamped to nothing by the leg-room safety).
    """
    ss, se = subject
    ts, te = tool
    sd = line_direction_from(ss, se)
    td = line_direction_from(ts, te)
    if sd is None or td is None:
        return None
    if _sin_between(sd, td) <= 1e-6:
        return None                       # collinear: nothing to trim against
    own, _far = _tip_and_own(subject, landing)
    if own is None:
        return None
    trim = _half_extent_cm(tool_geom, tool_basis, sd, tool_anchor)
    depth = abs(depth_mm) * MM_TO_CM
    sub_perp = _perp_extent_cm(subject_geom, subject_basis, subject_anchor, sd)
    tool_perp = _perp_extent_cm(tool_geom, tool_basis, tool_anchor, td)
    perp = max(sub_perp, tool_perp) * _BOX_SAFETY
    if _is_hollow(tool_geom):
        reach = -trim + (_wall_cm(tool_geom) or 0.0) + depth
    else:
        reach = depth
    # Axial span along own (s = signed distance from the vertex, + into the
    # member): the offset plane sits at s = -reach (corner_offsets' sign
    # convention), and the prism extends from there PAST the vertex into the
    # tool (s = -f).  f is the plug reach so the prism swallows any tip
    # overshoot and the tool's whole cross-section, mirroring cope_cutter's
    # box coverage.
    f = _plug_reach(trim + depth, sub_perp + tool_perp, _angle_between(sd, td))
    hi = min(-reach, 0.5 * max(_dist(landing, ss), _dist(landing, se)))
    lo = max(-f, -0.5 * max(_dist(landing, ts), _dist(landing, te)))
    if hi <= lo:
        return None
    center = _add(landing, _scale(own, 0.5 * (hi + lo)))
    box = {'center': center, 'axes': _frame(own), 'half': (0.5 * (hi - lo),
                                                           perp, perp)}
    return {'reach': reach, 'region': box}


def ci_index(idx, n):
    """Detection index -> combined-array index (selected 0..n-1, then context).

    A corner reports a context member as ``~k``; a T-junction reports it as
    ``n+k``.  Both encodings land on combined slot ``n+k`` for context member
    ``k``.
    """
    return idx if idx >= 0 else n + (~idx)


def bend_path(V, u, v, radius_cm):
    """Die-radius centerline path rounding the corner between outward dirs u/v.

    ``V`` is the sharp vertex (cm), ``u``/``v`` the unit directions from V into
    each leg, ``radius_cm`` the bending die's centerline radius.  Returns::

        {'point': V, 'theta': rad, 'radius_cm': R, 'center': C, 'axis': a,
         't1': T1, 't2': T2, 'arc_start': A1, 'arc_end': A2,
         'segments': [('line', P, Q), ('arc', C, A1, A2), ('line', Q2, T2)]}

    The path runs leg0 -> leg1: straight to the first tangent point T1, a
    tangent arc of radius R, straight from T2.  Because the arc is tangent to
    both legs, its endpoints ARE the tangent points, so the arc alone is the
    corner's path; the builder extends it with straight runs to each member's
    far end when sweeping a whole chain (``entry``/``exit`` give the tangent
    point and its incoming/outgoing direction for that).  The arc's sweep
    direction is inherent in the ordered pair (start, end) plus the bend-plane
    normal ``axis = normalize(u x v)`` -- reversing the legs reverses the path,
    which is the only ambiguity left (the caller orders legs so the member it
    built comes first).  ``theta`` is the turn angle (0 straight, pi/2 for a
    square corner); degenerate (collinear/folded) corners return None.
    """
    theta = bend_turn_angle(u, v)
    if abs(theta) < 1e-6 or abs(theta) >= math.pi - 1e-6 or radius_cm <= 0.0:
        return None
    sb = radius_cm * math.tan(theta / 2.0)
    bis = _norm(_add(u, v))
    center = _add(V, _scale(bis, radius_cm / math.cos(theta / 2.0)))
    axis = _norm(_cross(u, v))
    t1 = _add(V, _scale(u, sb))
    t2 = _add(V, _scale(v, sb))
    return {'point': V, 'theta': theta, 'radius_cm': radius_cm,
            'center': center, 'axis': axis,
            't1': t1, 't2': t2,
            'entry': (t1, u), 'exit': (t2, v)}


def joint_spec(lines, geoms, joint_by_line, clr_by_line=None,
               through_by_line=None, saddle_by_line=None,
               cope_depth_by_line=None, bases=None, context=None,
               anchor_by_line=None, tol=_CORNER_TOL, bend_joints=None,
               bend_context=False):
    """Single source of truth for every joint among ``lines`` (+ ``context``).

    Same inputs as :func:`corner_offsets` (see its docstring for the index
    space, context dicts, bases, and anchors).  Returns::

        {'offsets': [(start, end), ...],   # cm, one per SELECTED line
         'occs':    [occurrence, ...]}

    ``offsets`` is exactly what :func:`corner_offsets` computes (the builder
    draws each member to the trimmed length).  Each occurrence describes one
    member END's joint::

        {'kind':   'miter' | 'butt' | 'butt_saddle' | 'cope_end' |
                   'cope_t' | 'cope_angle' | 'bend',
         'member': line_index, 'role': 'start'|'end', 'vertex': V,
         'partner': (pidx, prole) | None,
         'setback': cm,             # the axial offset already in ``offsets``
         'cutter':  cutter | None,  # what to remove, as a FINITE solid
         'legs':    [(idx, role), ...]}   # both legs for 'bend'

    ``cutter`` is one of:
      ``{'type': 'plane', 'point': P, 'normal': nrm, 'region': box}``
          -- remove the member's material on the +normal side of the plane
          through P, but only inside ``region`` (a joint box).  The execution
          layer builds the cutter solid as ``region`` ∩ half-space and
          Combine(Cut)s it: no Split Body, no pointContainment.
      ``{'type': 'body', 'tool': line_index, 'region': box}``
          -- remove the member's material inside ``region`` that is also
          inside the partner member's body (a bounded cope/saddle: the tool
          body ∩ region is the cutter).  ``tool`` may be negative (context).
      ``None`` -- nothing to cut (a plain butt is a pure axial trim).

    ``region`` boxes are clamped to half the shortest leg (see
    :func:`_region_box`), so a cut can never touch material far along a run.
    """
    n = len(lines)
    ctx = context or []
    ctx_lines = [c['line'] if isinstance(c, dict) else c for c in ctx]
    L = list(lines) + ctx_lines
    G = list(geoms) + [c.get('geom') for c in ctx]
    B = (list(bases) + [c.get('basis') for c in ctx]) if bases else None
    A = list(anchor_by_line) + [None] * len(ctx) if anchor_by_line else None
    all_lines = list(lines) + ctx_lines
    offs = corner_offsets(lines, geoms, joint_by_line, clr_by_line,
                          through_by_line, bases, saddle_by_line,
                          cope_depth_by_line, context, anchor_by_line)
    # Candidate #4: map every member (context by registry mid, selected by a
    # synthetic key) to its leg so a bend arc at a corner can be rebuilt from
    # its joint record.  Only consulted when bend_context is on.
    legs_by_mid = {}
    if bend_context and bend_joints:
        for c in ctx:
            mid = _ctx_mid(c)
            if mid is not None:
                legs_by_mid[mid] = c['line'] if isinstance(c, dict) else c
        for i in range(n):
            legs_by_mid.setdefault(('sel', i), L[i])

    def arc_case(V, idx, role):
        """The bend-arc state for selected member ``idx``'s tip at corner ``V``.

        None unless bend_context is on and a bend joint rounds ``V``.  The
        classifier wants the TIP-GROWTH direction (from the member's body
        toward/past the tip), the opposite of ``outward`` -- see the note in
        :func:`corner_offsets` (an outside approach read S4/0 through the
        outward vector and skipped the arc path entirely).
        """
        if not (bend_context and bend_joints):
            return None
        own = outward(idx, role)
        grow = (-own[0], -own[1], -own[2])
        r_cm = _perp_extent_cm(geomv(idx), basisv(idx), anchorv(idx), own)
        return bend_context_classify(V, grow, V, bend_joints, legs_by_mid, r_cm)


    def is_sel(i):
        return 0 <= i < n

    def dirv(i):
        return line_direction(L[ci_index(i, n)])

    def outward(i, role):
        d = dirv(i)
        return _scale(d, -1) if role == 'end' else d

    def geomv(i):
        return G[ci_index(i, n)]

    def basisv(i):
        return B[ci_index(i, n)] if B else None

    def anchorv(i):
        return A[ci_index(i, n)] if A else None

    def perp_extent(i, role, V):
        """Max section half-extent (cm) of member ``i`` perpendicular to its run."""
        return _perp_extent_cm(geomv(i), basisv(i), anchorv(i), dirv(i))

    occs = []

    # ---- corners ---------------------------------------------------------- #
    for corner in detect_corners(lines, tol, context=ctx_lines):
        V = corner['point']
        members = corner['members']
        room = _leg_room(L, members, V, n)
        through_idx = _butt_through(members, joint_by_line, through_by_line,
                                    n=n)
        for k, (idx, role) in enumerate(members):
            if not is_sel(idx):
                continue
            jid = joint_at(joint_by_line, idx, role)
            if jid in ('none',):
                continue
            partner = _corner_partner(members, idx, role, joint_by_line,
                                      through_by_line, n=n)
            if partner is None:
                continue
            pidx, prole = partner
            own = outward(idx, role)
            neigh = outward(pidx, prole)
            phi = _angle_between(own, neigh)
            if phi <= 1e-6 or phi >= math.pi - 1e-6:
                continue  # folded-back or straight run: no joint geometry
            if jid == 'miter':
                nrm = _norm(_sub(own, neigh))
                if _dot(nrm, nrm) < 1e-9:
                    continue
                sb = _miter_setback_cm(geomv(idx), basisv(idx), own, neigh,
                                       anchorv(idx), phi)
                perp = max(perp_extent(idx, role, V),
                           perp_extent(pidx, prole, V))
                # The builder grows the member by sb so its raw end pokes sb
                # PAST the vertex (toward the neighbour); the bisector plane
                # through V trims that poke.  The waste spans sb on the far side
                # of V, so the box's axial half-extent is sb (symmetric about V).
                reach = sb
                box = _region_box(V, own, reach, perp, room)
                occs.append({'kind': 'miter', 'member': idx, 'role': role,
                             'vertex': V, 'partner': partner, 'setback': sb,
                             'cutter': {'type': 'plane', 'point': V,
                                        'normal': nrm, 'region': box},
                             'legs': [(idx, role), partner]})
            elif jid == 'bend':
                pair = [idx] + ([pidx] if is_sel(pidx) else [])
                clr = max((clr_by_line[j] for j in pair
                           if clr_by_line and j < len(clr_by_line)),
                          default=0.0)
                path = (bend_path(V, own, neigh, clr * MM_TO_CM)
                        if clr > 0.0 else None)
                occs.append({'kind': 'bend', 'member': idx, 'role': role,
                             'vertex': V, 'partner': partner, 'setback': 0.0,
                             'cutter': None, 'clr_mm': clr,
                             'path': path,
                             'legs': [(idx, role), partner]})
            elif jid in ('butt', 'cope'):
                if idx == through_idx:
                    continue  # the through member is never cut
                # Candidate #4: if a swept bend rounds THIS corner, the tip
                # meets a curved arc, not a straight leg.  Classify the tip and
                # cut against the arc BODY (the length delta is applied by
                # corner_offsets, which runs the same classifier).  S0 (nearest
                # material is a straight leg) falls through to the cope/butt
                # path below unchanged.
                st = arc_case(V, idx, role)
                if st is not None and st['state'] != 'S0':
                    perp = max(perp_extent(idx, role, V),
                               perp_extent(pidx, prole, V))
                    reach = _plug_reach(
                        2.0 * perp,
                        perp_extent(idx, role, V) + perp_extent(pidx, prole, V),
                        _angle_between(own, neigh))
                    box = _region_box(V, own, reach, perp, room)
                    occs.append({'kind': 'cope_end', 'member': idx,
                                 'role': role, 'vertex': V,
                                 'partner': partner, 'setback': 0.0,
                                 'cutter': {'type': 'arc', 'jid': st['jid'],
                                            'region': box},
                                 'bend_state': st['state'],
                                 'legs': [(idx, role), partner]})
                    continue
                saddled = (jid == 'cope'
                           or flag_at(saddle_by_line, idx, role))
                # A cope onto a BEND partner's curved arc cannot be cut by a
                # straight box; the tip at V lands on the arc -> plain butt.
                on_arc = False
                if saddled and jid == 'cope':
                    ps, pe = line_endpoints(L[ci_index(pidx, n)])
                    if _point_in_arc_zones(
                            V, ps, line_direction(L[ci_index(pidx, n)]),
                            _bend_arc_zones(L, pidx, joint_by_line, clr_by_line,
                                            all_lines, ctx_lines, n, tol), tol):
                        saddled = False
                        on_arc = True
                if not saddled:
                    occs.append({'kind': 'butt', 'member': idx, 'role': role,
                                 'vertex': V, 'partner': partner,
                                 'setback': 0.0, 'cutter': None,
                                 'on_arc': on_arc,
                                 'legs': [(idx, role), partner]})
                    continue
                # Corner cope / saddled butt: the member's tip overlaps the
                # neighbour's end region; cut it against the neighbour's BODY,
                # bounded to a box covering both sections at the vertex.
                trim = _half_extent_cm(geomv(pidx), basisv(pidx), own,
                                       anchorv(pidx))
                depth = _depth_cm(cope_depth_by_line, idx)
                perp = max(perp_extent(idx, role, V),
                           perp_extent(pidx, prole, V))
                # A plug pushed into the neighbour's hollow reaches along ``own``
                # by BOTH sections' perpendicular extents / sin(angle between the
                # two runs); on a shallow corner that is far more than ``trim``,
                # so grow the reach to keep the whole plug inside the box.
                reach = _plug_reach(
                    trim + abs(depth),
                    perp_extent(idx, role, V) + perp_extent(pidx, prole, V),
                    _angle_between(own, neigh))
                box = _region_box(V, own, reach, perp, room)
                kind = 'cope_end' if jid == 'cope' else 'butt_saddle'
                occs.append({'kind': kind, 'member': idx, 'role': role,
                             'vertex': V, 'partner': partner, 'setback': 0.0,
                             'cutter': {'type': 'body', 'tool': pidx,
                                        'region': box},
                             'legs': [(idx, role), partner]})

    # ---- T-junctions ------------------------------------------------------- #
    for jn in detect_t_junctions(lines, tol, context=ctx_lines):
        idx, role = jn['member']
        if not is_sel(idx):
            continue
        P = jn['point']
        tool = jn['tool']
        pidx = tool if tool < n else ~(tool - n)
        jid = joint_at(joint_by_line, idx, role)
        if jid not in ('butt', 'cope'):
            continue
        saddled = (jid == 'cope' or flag_at(saddle_by_line, idx, role))
        own = outward(idx, role)
        # A cope onto a BEND tool's curved arc cannot be cut by a straight box;
        # if the tip at P lands on the tool's arc zone, fall back to a butt.
        # For a CONTEXT tool the selected-tool zone math is blind (a stored
        # member carries no joint setting), so with bend_context on the zones
        # come from the tool's bend joint records instead.
        on_arc = False
        if saddled and jid == 'cope':
            ts, te = line_endpoints(L[ci_index(pidx, n)])
            if pidx < 0 and bend_context and bend_joints:
                zones = bend_arc_zones_for_member(
                    L[ci_index(pidx, n)], _ctx_mid(ctx[~pidx]), bend_joints,
                    legs_by_mid)
            else:
                zones = _bend_arc_zones(L, pidx, joint_by_line, clr_by_line,
                                        all_lines, ctx_lines, n, tol)
            if _point_in_arc_zones(P, ts, line_direction(L[ci_index(pidx, n)]),
                                   zones, tol):
                saddled = False
                on_arc = True
        if not saddled:
            occs.append({'kind': 'butt', 'member': idx, 'role': role,
                         'vertex': P, 'partner': (pidx, None), 'setback': 0.0,
                         'cutter': None, 'on_arc': on_arc,
                         'legs': [(idx, role)]})
            continue
        trim = _half_extent_cm(geomv(pidx), basisv(pidx), dirv(idx),
                               anchorv(pidx))
        depth = _depth_cm(cope_depth_by_line, idx)
        perp = max(perp_extent(idx, role, P), perp_extent(pidx, None, P))
        room = _leg_room(L, [(idx, role), (pidx, None)], P, n)
        angle = _angle_between(dirv(idx), dirv(pidx))
        # See the corner note: a plug driven into the tool's bore along the
        # coping member reaches (both sections' perpendicular extents summed) /
        # sin(angle) from the joint, which on an angled T is far more than the
        # tool's axial half-extent alone.
        reach = _plug_reach(trim + abs(depth),
                            perp_extent(idx, role, P) + perp_extent(pidx, None, P),
                            angle)
        box = _region_box(P, own, reach, perp, room)
        kind = cope_kind(dirv(idx), dirv(pidx))
        occs.append({'kind': kind, 'member': idx, 'role': role, 'vertex': P,
                     'partner': (pidx, None), 'setback': 0.0,
                     'cutter': {'type': 'body', 'tool': pidx, 'region': box},
                     'legs': [(idx, role)]})

    return {'offsets': offs, 'occs': occs}


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
