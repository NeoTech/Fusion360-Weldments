"""Pure bend-sequence planner: turn a tube's centerline chain into machine marks.

Like :mod:`lib.gussets` and :mod:`lib.joints`, this module is adsk-free --
everything operates on plain ``(x, y, z)`` cm tuples so the whole table can be
unit-tested without Fusion.  The glue command (``commands/weldmentBendTable``)
resolves registry members into ``segments`` and calls :func:`bend_table`.

What the table says
-------------------
A rotary-draw bender needs, for each bend in order from the datum end:

* **start_mark / end_mark** -- where to scribe the two tangent points on the
  straight (unfolded) tube.  The operator marks the tube, feeds the far mark
  to the die, and the arc lands between the marks.
* **feed_mm** -- distance from the datum end to the far tangent point: how far
  the tube travels through the die for this bend (== ``end_mark``).
* **angle_deg** -- the geometric turn.  Springback is the operator's problem.
* **clock_deg** -- how far to ROTATE the tube about its own axis before this
  bend, measured from the previous bend's plane.  Sign convention (agreed):
  as seen by the operator **standing behind the machine, looking down the
  tube toward the end being bent** (i.e. along the feed direction), positive
  is clockwise.  The clock also encodes the bend *side*: bends continuing in
  the same plane (a U/hairpin) clock 0 deg; bends that reverse the curve (a
  Z/staircase) clock 180 deg -- exactly how they are made on the machine.

Developed length
----------------
Vertex distances overcount: each bend replaces two setbacks
(``R*tan(theta/2)`` per leg) with one arc (``R*theta``), saving
``2*sb - arc``.  The near tangent of the bend at sharp vertex ``k`` sits at
``sharp_k - sb - saved_so_far`` along the unfolded tube.
"""
import math

from . import joints as jt

MM = 10.0   # cm -> mm


# --------------------------------------------------------------------------- #
# small vector helpers (tuples; joints._norm rejects non-tuples)
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _len(a):
    return math.sqrt(_dot(a, a))


def _unit(a):
    n = _len(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else None


def _perp(v, axis):
    """Component of ``v`` perpendicular to the unit vector ``axis``."""
    d = _dot(v, axis)
    return _unit((v[0] - d * axis[0], v[1] - d * axis[1], v[2] - d * axis[2]))


# --------------------------------------------------------------------------- #
# the clock-angle convention lives in this ONE function
# --------------------------------------------------------------------------- #
def clock_deg(center_prev, center_next, feed):
    """Signed tube-rotation angle (deg) between two consecutive bends.

    ``center_prev``/``center_next`` are unit vectors pointing from each bend's
    vertex toward its arc center (the direction the die pushed); ``feed`` is
    the unit direction of the leg joining them, pointing AWAY from the datum,
    i.e. into the machine -- the operator's line of sight.  Each center is
    flattened onto the tube cross-section (perpendicular to ``feed``) and the
    angle between them is taken about that axis.

    A right-hand-positive rotation about ``feed`` points the thumb away from
    an operator looking along ``feed``, which curls CLOCKWISE in their view --
    the agreed convention -- so the raw signed angle is returned unchanged.
    """
    p = _perp(center_prev, feed)
    q = _perp(center_next, feed)
    if p is None or q is None:
        return 0.0
    ang = math.atan2(_dot(_cross(p, q), feed), _dot(p, q))
    return math.degrees(ang)


# --------------------------------------------------------------------------- #
# chain walking
# --------------------------------------------------------------------------- #
def orient(segments, first, start_vertex, tol=0.05):
    """Copy of ``segments`` with segment ``first`` starting at ``start_vertex``.

    The datum pick (start vertex) fixes which end of the first leg is the
    machine's zero; reversing the segment here makes the forward walk start
    there.  Raises ValueError when the vertex is not an endpoint of the
    segment.
    """
    segs = [dict(s) for s in segments]
    if not (0 <= first < len(segs)):
        raise ValueError('first segment out of range')
    s = segs[first]
    if _len(_sub(s['start'], start_vertex)) <= tol:
        return segs
    if _len(_sub(s['end'], start_vertex)) <= tol:
        s['start'], s['end'] = s['end'], s['start']
        s['role_start'], s['role_end'] = s.get('role_end'), s.get('role_start')
        return segs
    raise ValueError('start vertex is not an endpoint of the first segment')


def walk(segments, first, tol=0.05):
    """Indices of the chain starting at ``first`` and following shared vertices.

    A forward-only walk: the datum pick defines where reading starts, so
    segments *before* ``first`` are deliberately not prepended -- the table
    covers the tube from the chosen leg onward.  Stops at a free end or at a
    branch vertex (3+ members), reporting ``complete=False`` in the latter
    case so the UI can warn that the chain is ambiguous there.
    """
    order = [first]
    used = {first}
    v = segments[first]['end']
    complete = True
    while True:
        touching = [j for j, s in enumerate(segments)
                    if j not in used and (_len(_sub(s['start'], v)) <= tol
                                          or _len(_sub(s['end'], v)) <= tol)]
        if not touching:
            break
        if len(touching) > 1:
            complete = False
            break
        nxt = touching[0]
        used.add(nxt)
        order.append(nxt)
        s = segments[nxt]
        v = s['end'] if _len(_sub(s['start'], v)) <= tol else s['start']
    if len(used) < len(segments):
        complete = False
    return order, complete


def free_ends(segments, tol=0.05):
    """``[(index, point), ...]`` -- chain ends no other segment touches.

    A rotary bender feeds from a FREE end of the tube, so these are the only
    legal datums.  The glue picks the one nearest the user's click, which lets
    a pick anywhere along the tube still read from the correct end.
    """
    ends = []
    for i, s in enumerate(segments):
        for p in (s['start'], s['end']):
            if not any(j != i and (_len(_sub(t['start'], p)) <= tol
                                   or _len(_sub(t['end'], p)) <= tol)
                       for j, t in enumerate(segments)):
                ends.append((i, p))
    return ends


# --------------------------------------------------------------------------- #
# the table
# --------------------------------------------------------------------------- #
def _bend_at(bends, vertex, tol):
    """The bend record whose vertex coincides with ``vertex`` (within ``tol``).

    ``bends`` is a list of ``{'vertex': (x,y,z), 'clr': mm, 'die_id': str}`` --
    the shape the glue builds straight from the registry's ``bend`` joints
    (vertex + ``params['clr_mm']``).  Returns None for a butt/miter/free vertex.
    """
    for b in bends or []:
        if _len(_sub(b.get('vertex'), vertex)) <= tol:
            return b
    return None


def bend_table(segments, bends, first=0, start_vertex=None, tol=0.05):
    """Plan the whole tube: one row per bend, in machine order from the datum.

    ``segments``: ``[{'start': (x,y,z), 'end': (x,y,z), 'mid': int}, ...]`` in
    cm -- the tube's legs in any order/orientation (from the registry; see the
    command's glue).  ``bends`` is a list of vertex-keyed records
    ``{'vertex': (x,y,z), 'clr': mm, 'die_id': str}`` (see :func:`_bend_at`);
    a chain vertex with no matching bend -- or a bend whose ``clr`` is 0/None --
    is a butt/miter/free corner: the walk continues through it and no row is
    emitted.  ``first`` + ``start_vertex`` fix the datum (see :func:`orient`).

    Returns ``{'rows': [...], 'developed_mm': float, 'complete': bool}``;
    each row: ``{'step', 'after_mid', 'before_mid', 'start_mark_mm',
    'end_mark_mm', 'feed_mm', 'angle_deg', 'clock_deg', 'die_id'}``.
    """
    segs = orient(segments, first, start_vertex, tol) if start_vertex \
        else [dict(s) for s in segments]
    chain, complete = walk(segs, first, tol)

    # Unit travel direction of each leg along the walk.
    tans = []
    for k, idx in enumerate(chain):
        s = segs[idx]
        if k == 0:
            t = _unit(_sub(s['end'], s['start']))
        else:
            prev_end = segs[chain[k - 1]]['end']
            t = (_unit(_sub(s['end'], s['start']))
                 if _len(_sub(s['start'], prev_end)) <= tol
                 else _unit(_sub(s['start'], s['end'])))
        if t is None:
            raise ValueError('zero-length segment in chain')
        tans.append(t)

    rows = []
    sharp = 0.0        # straight centerline distance from datum to vertex
    saved = 0.0        # length the earlier bends have already removed
    last_c = None      # center direction of the previous bend
    for k in range(len(chain) - 1):
        sharp += _len(_sub(segs[chain[k]]['end'], segs[chain[k]]['start']))
        a, b = tans[k], tans[k + 1]
        vertex = segs[chain[k]]['end']
        rec = _bend_at(bends, vertex, tol)
        clr = (rec or {}).get('clr') or 0.0
        theta = jt.bend_turn_angle((-a[0], -a[1], -a[2]), b)   # turn angle
        center = _unit(_sub(b, a))     # vertex -> arc center (die push dir)
        if clr <= 0.0 or center is None or theta <= 1e-9 \
                or theta >= math.pi - 1e-9:
            continue                   # straight run or butt/miter vertex
        sb = jt.bend_setback(clr, theta)          # cm
        arc = jt.bend_arc_length(clr, theta)      # cm
        start_mark = sharp - sb - saved
        end_mark = start_mark + arc
        # Rotation between consecutive bends happens about the leg they SHARE
        # (this bend's incoming direction ``a``), the axis the tube is clamped
        # along as it feeds into the machine.
        clock = clock_deg(last_c, center, a) if last_c else 0.0
        rows.append({
            'step': len(rows) + 1,
            'after_mid': segs[chain[k]].get('mid'),
            'before_mid': segs[chain[k + 1]].get('mid'),
            'start_mark_mm': start_mark * MM,
            'end_mark_mm': end_mark * MM,
            'feed_mm': end_mark * MM,
            'angle_deg': math.degrees(theta),
            'clock_deg': clock,
            'die_id': (rec or {}).get('die_id'),
        })
        saved += 2 * sb - arc
        last_c = center

    dev = sum(_len(_sub(segs[i]['end'], segs[i]['start'])) for i in chain) \
        - saved
    return {'rows': rows, 'developed_mm': dev * MM, 'complete': complete}


# --------------------------------------------------------------------------- #
# registry -> table (the pure half of the glue; the command resolves bodies to
# members, which is the only step that needs the adsk API)
# --------------------------------------------------------------------------- #
def tube_members(members, joints, start_mid):
    """The member records of the tube containing ``start_mid``.

    A rotary-bent tube is a chain of legs glued together by ``bend`` joints,
    each of which names BOTH legs it joins.  So one leg is enough to find the
    whole tube: take every bend joint touching a member already in the set and
    add its partners, repeating until nothing new appears.  Members joined only
    by a butt/miter/cope are NOT followed -- those are frame corners between
    different tubes, not bends in this one.

    Returns the matching Member records (in the order given in ``members``), so
    the caller can hand them straight to :func:`plan`.
    """
    by_mid = {m.mid: m for m in members}
    if start_mid not in by_mid:
        return []
    found = {start_mid}
    frontier = [start_mid]
    while frontier:
        mid = frontier.pop()
        for j in joints:
            if getattr(j, 'kind', None) != 'bend':
                continue
            ids = j.member_ids()
            if mid not in ids:
                continue
            for other in ids:
                if other not in found and other in by_mid:
                    found.add(other)
                    frontier.append(other)
    return [m for m in members if m.mid in found]


def plan(members, joints, first=0, start_vertex=None, die_id=None, tol=0.05):
    """Bend table for a tube given its member records and the design's joints.

    ``members`` are :class:`lib.registry.Member` records -- the legs of ONE tube
    (their ``start``/``end`` are the *drawn* centreline in cm, so a swept bend
    keeps its virtual corner, exactly what the planner wants).  ``joints`` are
    :class:`lib.registry.Joint` records; the ``bend`` ones whose members touch
    this tube become the vertex-keyed ``bends`` list :func:`bend_table` reads.
    ``die_id`` is an optional ``joint -> label`` callback (the command passes a
    catalogue lookup; the pure default reads ``params['die_id']``).

    Returns ``{'rows', 'developed_mm', 'complete'}`` (see :func:`bend_table`),
    or ``{'error': str}`` when fewer than two legs are given.
    """
    if len(members) < 2:
        return {'error': 'No bend joints found on this tube.'}
    segments = sorted(({'start': tuple(m.start), 'end': tuple(m.end),
                        'mid': m.mid} for m in members),
                      key=lambda s: s['mid'])
    mids = {s['mid'] for s in segments}
    bends = []
    for j in joints:
        if getattr(j, 'kind', None) != 'bend' or j.vertex is None:
            continue
        if not set(j.member_ids()) & mids:
            continue
        params = j.params or {}
        bends.append({'vertex': tuple(j.vertex),
                      'clr': params.get('clr_mm') or 0.0,
                      'die_id': die_id(j) if die_id else params.get('die_id')})
    return bend_table(segments, bends, first=first,
                      start_vertex=start_vertex, tol=tol)


def plan_from_pick(members, joints, start_mid, click=None, reverse=False,
                   die_id=None, tol=0.05):
    """Bend table from ONE leg pick -- the whole UX the command needs.

    The chain is discovered through the bend joints (see :func:`tube_members`),
    so the user selects a single leg instead of every one.  The datum is the
    FREE end of the tube nearest ``click`` (the 3D point they picked at), since
    a bender always feeds from an open end; ``reverse`` swaps to the far free
    end when they clicked the end they did not mean.  Returns what :func:`plan`
    returns, or ``{'error': str}`` when the pick is not a bent tube.
    """
    tube = tube_members(members, joints, start_mid)
    if len(tube) < 2:
        return {'error': 'No bend joints found on this tube.'}
    # plan() re-derives its segment list sorted by mid, so the index returned by
    # free_ends must refer to that same order -- sort once, here.
    tube = sorted(tube, key=lambda m: m.mid)
    segments = [{'start': tuple(m.start), 'end': tuple(m.end), 'mid': m.mid}
                for m in tube]
    ends = free_ends(segments, tol)
    if not ends:
        return {'error': 'This tube has no free end to feed from.'}
    if click is not None:
        ends = sorted(ends, key=lambda e: _len(_sub(e[1], click)))
    if reverse:
        ends = list(reversed(ends))
    first_idx, start_vertex = ends[0]
    return plan(tube, joints, first=first_idx, start_vertex=start_vertex,
                die_id=die_id, tol=tol)
