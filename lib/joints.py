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
                   cope_depth_by_line=None, context=None, anchor_by_line=None):
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
            if o_k is not None:
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


def _outward(line, role):
    """Unit direction from the corner *into* a member (away from the vertex)."""
    d = line_direction(line)
    return (-d[0], -d[1], -d[2]) if role == 'end' else d


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
