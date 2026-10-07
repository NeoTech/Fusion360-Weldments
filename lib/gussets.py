"""Pure-Python gusset-plate geometry (no ``adsk``), so it unit-tests standalone.

Gussets are a *toolbox-only* feature: they never appear in the automatic
generator's joint vocabulary (``lib.joints.joint_spec``).  A gusset is an
additive flat plate welded onto (or inside) members to stiffen a joint, so the
registry records it as a ``gusset`` joint purely for the BOM listing -- it is
never a geometry cut, and :meth:`lib.registry.Registry.plan_rebuild` skips it.

Two flavours, matching the two toolbar commands:

* **Corner gusset** (:func:`corner_vertex`, :func:`plate_frame`,
  :func:`gusset_polygon`): a plate at the vertex where two members meet, sitting
  in the plane that contains both runs.  Shapes: a right ``triangle``, a
  ``rectangle``, or a ``triangle_extended`` (the triangle with a band of extra
  material at each leg's far end, running parallel to the members, so there is a
  lap to weld).  ``width``/``height`` are the legs along the two members;
  ``alignment`` (inside/outside) mirrors the plate into the opposite quadrant.

* **Profile gusset** (:func:`profile_inner_polygon`): a stiffener plate inside a
  construction profile (HEA / UPE / UPN -- an I-beam or channel, not a hollow
  tube).  Its height follows the *inner* clear faces of the flanges and its
  ``depth`` controls how far it reaches across the section; it is placed
  perpendicular to the run and extruded by ``thickness`` along it.

Local ``(u, v)`` polygon points are millimetres; the command layer maps them
into model space (cm) with :func:`lib.profiles.map_local_to_model`.
"""

import math

# Internal length unit is cm; polygons here are mm (matches lib.profiles).
MM_TO_CM = 0.1

# Corner-plate shapes offered in the dialog, mapped to the polygon builder.
SHAPES = ('triangle', 'triangle_extended', 'rectangle')

# Alignment: which quadrant of the corner the plate occupies.
ALIGNMENTS = ('inside', 'outside')


# --------------------------------------------------------------------------- #
# Small vector helpers (tuples; independent of the adsk API on purpose).
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _len(a):
    return math.sqrt(_dot(a, a))


def _norm(a):
    n = _len(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else None


# --------------------------------------------------------------------------- #
# Corner gusset: where two members meet
# --------------------------------------------------------------------------- #
def _clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _closest_on_segments(p1, q1, p2, q2):
    """The closest points ``(c1, c2)`` between segments ``p1q1`` and ``p2q2``.

    Standard clamped segment-segment closest-approach (Ericson, *Real-Time
    Collision Detection*).  Unlike comparing only the four endpoint pairs, this
    finds where two runs come nearest even when the near point sits in the
    middle of a member (a T or a face-to-face corner), which is exactly where a
    gusset belongs.
    """
    d1, d2, r = _sub(q1, p1), _sub(q2, p2), _sub(p1, p2)
    a, e, f = _dot(d1, d1), _dot(d2, d2), _dot(d2, r)
    if a <= 1e-12 and e <= 1e-12:                 # both degenerate points
        return p1, p1
    if a <= 1e-12:                               # seg1 is a point
        s, t = 0.0, _clamp01(f / e)
    else:
        c = _dot(d1, r)
        if e <= 1e-12:                           # seg2 is a point
            s, t = _clamp01(-c / a), 0.0
        else:
            b = _dot(d1, d2)
            denom = a * e - b * b
            s = _clamp01((b * f - c * e) / denom) if denom != 0.0 else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                s, t = _clamp01(-c / a), 0.0
            elif t > 1.0:
                s, t = _clamp01((b - c) / a), 1.0
    return _add(p1, _scale(d1, s)), _add(p2, _scale(d2, t))


def _section_radius_cm(geom):
    """How far a member's solid reaches from its centreline (cm).

    The outer radius of the section descriptor (``circles`` -> outer radius,
    ``polygons`` -> farthest outer-loop corner), used as a size-aware "do these
    two members touch?" reach.  A missing descriptor falls back to a generous
    default so hand-made members still register as meeting.
    """
    if not geom:
        return 8.0
    mm = 0.0
    if geom.get('kind') == 'circles':
        mm = max(geom.get('radii') or [0.0])
    elif geom.get('kind') == 'polygons':
        loops = geom.get('loops') or []
        if loops:
            mm = max(max(math.hypot(u, v) for u, v in loop) for loop in loops)
    return mm * MM_TO_CM if mm else 8.0


def _half_extent_cm(geom, basis, direction):
    """Half-size of a section along a world ``direction`` (cm), or None.

    Projects the section's outer loop (local mm ``(u, v)`` mapped to world by the
    placed ``basis`` = ``(axis_u, axis_v)``) onto ``direction`` and takes the
    largest absolute reach -- the member's half-width facing that way.  A round
    section is isotropic (its radius); a missing descriptor returns None so the
    caller can fall back to :func:`_section_radius_cm`.
    """
    if not geom:
        return None
    if geom.get('kind') == 'circles':
        radii = geom.get('radii') or [0.0]
        return max(radii) * MM_TO_CM if max(radii) else None
    if geom.get('kind') == 'polygons':
        loops = geom.get('loops') or []
        if not loops:
            return None
        au, av = basis if basis else ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        best = 0.0
        for loop in loops:
            for lu, lv in loop:
                wx = lu * au[0] + lv * av[0]
                wy = lu * au[1] + lv * av[1]
                wz = lu * au[2] + lv * av[2]
                best = max(best, abs(wx * direction[0] + wy * direction[1]
                                     + wz * direction[2]))
        return best * MM_TO_CM if best else None
    return None


def corner_vertex(a_cl, b_cl, gap_cm=1.0):
    """The corner where two members meet, from their centrelines ``a_cl``/``b_cl``.

    Each centreline is ``(start, end, geom, basis)`` (see
    ``weldment._member_centerline``).  Returns the midpoint of the closest
    points on the two centreline *segments* -- the coincident vertex for an
    end-to-end corner, and the contact point for a face-to-face or T corner
    where the centreline ends are offset by the members' own depths.  ``None``
    only when the two solids do not touch: the closest-centreline gap exceeds
    the sum of their section radii plus ``gap_cm``.
    """
    c1, c2 = _closest_on_segments(a_cl[0], a_cl[1], b_cl[0], b_cl[1])
    if _len(_sub(c1, c2)) > _section_radius_cm(a_cl[2]) + \
            _section_radius_cm(b_cl[2]) + gap_cm:
        return None
    return _scale(_add(c1, c2), 0.5)


def _into_member(cl, vertex):
    """Unit direction from ``vertex`` toward the member's far end (cm space)."""
    ss, se = cl[0], cl[1]
    far = ss if _len(_sub(vertex, se)) <= _len(_sub(vertex, ss)) else se
    return _norm(_sub(far, vertex))


def corner_placement(a_cl, b_cl, vertex):
    """The material corner a plate nests into, offset from the centreline ``vertex``.

    The centreline vertex sits where the two runs' *axes* meet -- buried in the
    material for a face-to-face or T joint.  The gusset's right-angle corner
    belongs on the reentrant corner where the two members' facing *surfaces*
    meet, so we push the vertex onto each member's inner face by that member's
    own half-width (the profile depth registered in the BOM), measured along the
    in-plane direction perpendicular to its run and pointing into the other
    member.  For an L this is ``vertex + hw_a * (perp to A toward B) + hw_b *
    (perp to B toward A)``; it collapses to the vertex itself when a section
    size is unknown.
    """
    d_a = _into_member(a_cl, vertex)
    d_b = _into_member(b_cl, vertex)
    if d_a is None or d_b is None:
        return vertex
    m_a = _norm(_sub(d_b, _scale(d_a, _dot(d_b, d_a))))   # perp to A, toward B
    m_b = _norm(_sub(d_a, _scale(d_b, _dot(d_a, d_b))))   # perp to B, toward A
    if m_a is None or m_b is None:                        # collinear: no corner
        return vertex
    ha = _half_extent_cm(a_cl[2], a_cl[3], m_a)
    hb = _half_extent_cm(b_cl[2], b_cl[3], m_b)
    # An unknown section (no descriptor) offsets by nothing rather than a guessed
    # radius -- better to leave the plate on the vertex than misplace it.
    ha = ha if ha is not None else 0.0
    hb = hb if hb is not None else 0.0
    return _add(vertex, _add(_scale(m_a, ha), _scale(m_b, hb)))


def plate_frame(vertex, a_cl, b_cl):
    """Orthonormal frame ``(normal, e1, e2)`` for a corner plate at ``vertex``.

    ``e1`` runs along member A (from the vertex into it), ``normal`` is
    perpendicular to the plane containing both runs, and ``e2`` completes the
    right-handed in-plane pair.  The plate polygon (local mm ``(u, v)``) is
    mapped with ``u`` along ``e1`` and ``v`` along ``e2``.  Returns ``None`` when
    the two members are (nearly) collinear -- there is no corner to gusset.
    """
    d_a = _into_member(a_cl, vertex)
    d_b = _into_member(b_cl, vertex)
    if d_a is None or d_b is None:
        return None
    n = _norm(_cross(d_a, d_b))
    if n is None:                          # collinear members: no plane
        return None
    e1 = d_a
    e2 = _cross(n, e1)                     # in-plane, perpendicular to e1
    return n, e1, e2


def gusset_polygon(shape, width_mm, height_mm, extension_mm=0.0,
                   alignment='inside'):
    """The plate outline in local ``(u, v)`` millimetres (corner at the origin).

    ``u`` runs along member A, ``v`` along the in-plane perpendicular toward
    member B.  ``width``/``height`` are the leg lengths; ``extension`` is the
    extra band on the ``triangle_extended`` shape; ``alignment`` mirrors the
    plate into the opposite quadrant for an outside gusset.

    Shapes:
      * ``triangle``          -- right triangle (0,0)-(w,0)-(0,h).
      * ``rectangle``         -- w x h rectangle from the corner.
      * ``triangle_extended`` -- the triangle plus a band of ``extension`` extra
        material at each leg's far end, running parallel to the other member
        (a lap to weld that follows the bodies, not the hypotenuse).

    Returns a closed-ready list of ``(u, v)`` points (not repeated at the end).
    """
    w, h, e = float(width_mm), float(height_mm), float(extension_mm)
    if shape == 'rectangle':
        pts = [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)]
    elif shape == 'triangle':
        pts = [(0.0, 0.0), (w, 0.0), (0.0, h)]
    elif shape == 'triangle_extended':
        # The two legs run along the members; the extension adds a band at each
        # leg's far end that runs PARALLEL to the other member (not perpendicular
        # to the hypotenuse), so the extra weldable material follows the bodies.
        # (w,0)->(w,e) is parallel to member B; (e,h)->(0,h) is parallel to A.
        pts = [(0.0, 0.0), (w, 0.0), (w, e), (e, h), (0.0, h)]
    else:
        raise ValueError(f'Unknown gusset shape: {shape!r}')
    if alignment == 'outside':
        pts = [(-u, -v) for u, v in pts]
    elif alignment != 'inside':
        raise ValueError(f'Unknown gusset alignment: {alignment!r}')
    return pts


# --------------------------------------------------------------------------- #
# Profile gusset: a stiffener inside an I-beam / channel
# --------------------------------------------------------------------------- #
def profile_inner_polygon(family, designation, depth_mm):
    """The inner stiffener outline (local ``(u, v)`` mm) for a section profile.

    ``family`` is a catalogue abbreviation (HEA/HIB.. I-beams, UPE/UPN channels);
    ``designation`` is its dict (``h_mm``, ``b_mm``, ``tw_mm``, ``tf_mm``).  The
    plate's height follows the *inner* faces of the flanges (clear web height
    ``h - 2 tf``); ``depth`` controls how far it reaches across the section:

      * I-beam (HEA/HEB/IPE): a web stiffener centred on the web, spanning
        ``+/-depth/2`` in width between the flanges.
      * Channel (UPE/UPN): a plate against the web's inner face, reaching
        ``depth`` toward the open flange tips.

    Returns ``None`` for a family this does not handle (a tube, say).
    """
    h = designation.get('h_mm')
    tf = designation.get('tf_mm')
    tw = designation.get('tw_mm')
    b = designation.get('b_mm')
    if h is None or tf is None or tw is None or b is None:
        return None
    v_half = h / 2.0 - tf                  # clear distance between flange faces
    if v_half <= 0.0:
        return None
    depth = float(depth_mm)
    if family in ('HEA', 'HEB', 'IPE'):
        half_w = min(depth / 2.0, b / 2.0 - tw / 2.0)
        u0, u1 = -half_w, half_w
    elif family in ('UPE', 'UPN'):
        u0 = -b / 2.0 + tw                 # web inner face
        u1 = min(u0 + depth, b / 2.0)      # toward the flange tips
        if u1 <= u0:
            return None
    else:
        return None
    return [(u0, -v_half), (u1, -v_half), (u1, v_half), (u0, v_half)]
