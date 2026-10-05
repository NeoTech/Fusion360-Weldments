"""Pure-Python member/joint registry for the weldment toolbox.

Like :mod:`lib.profiles`, :mod:`lib.joints` and :mod:`lib.bending_dies`, this
module never touches the ``adsk`` API, so it unit-tests outside Fusion.  It is
the data backbone of the Phase-4 UI rethink (see
``docs/phase4-ui-rethink.md``): instead of re-detecting a whole frame's topology
from bodies on every run, each member and each joint is stored as an explicit
record, and a tool edits a record rather than guessing.

Responsibilities (pure)
-----------------------
* Model a **member** (a straight or bent tube): its centreline, section geometry,
  placed basis, and the feature/body it was built into.
* Model a **joint**: the kind, the members/roles it touches, its vertex, its
  parameters, and (for the toolbox tools) the explicit face/vertex *selections*
  the user made -- the thing that lets a joint survive a sketch edit.
* Serialise to / from a JSON string (the payload the command layer parks on a
  design attribute) and look records up by geometry.

The command layer (``commands/weldment/entry.py``) owns the only non-pure part:
resolving a record's ``feature``/``body_index`` back to a live ``BRepBody`` and
turning a member record into the ``{'line','geom','basis','body'}`` context dict
:func:`lib.joints.joint_spec` consumes.  That glue is thin because everything
that can be decided without Fusion lives here.

Units
-----
Centrelines and vertices are in **centimetres** (Fusion's internal unit), the
same frame :mod:`lib.joints` reads from ``worldGeometry``.  ``geom`` is the
section descriptor in **millimetres** exactly as
:func:`lib.profiles.section_geometry` returns it (``{'kind':'circles','radii':...}``
or ``{'kind':'polygons','loops':...}``), so a record round-trips straight into
the joint engine.  ``clr_mm``/``depth_mm`` parameters are millimetres.

Schema (version 1)
------------------
``to_json`` emits::

    {
      "version": 1,
      "members": [
        {"mid": 1, "start": [x,y,z], "end": [x,y,z], "geom": {...},
         "basis": [[ux,uy,uz],[vx,vy,vz]] | null,
         "designation": "20x1", "family": "CHS",
         "feature": 12, "body_index": 0, "name": "Tube-1"}
      ],
      "joints": [
        {"jid": 1, "kind": "cope",
         "refs": [{"mid": 1, "role": "end"}, {"mid": 2, "role": null}],
         "vertex": [x,y,z],
         "params": {"clr_mm": 90.0, "depth_mm": 0.0},
         "selections": {"face": "Face:23", "vertex": "Vertex:7"}}
      ]
    }

``refs`` lists the members a joint touches; the FIRST is the member being cut
(the "subject"), the rest are partners/tools.  ``role`` is ``'start'``/``'end'``
for a corner end or ``None`` for a T-junction landing mid-run.  ``selections`` is
optional and only the explicit toolbox tools fill it; the legacy auto command
leaves it empty and detection still works from ``vertex``/``refs``.
"""

import json

# Fusion internal length unit is centimetres; the section descriptor is mm.
MM_TO_CM = 0.1

SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- #
# Small pure geometry helpers (cm tuples) -- no adsk, no numpy.
# --------------------------------------------------------------------------- #
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _scale(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a):
    n = _dot(a, a) ** 0.5
    return (a[0] / n, a[1] / n, a[2] / n) if n > 0 else (0.0, 0.0, 0.0)


def midpoint(a, b):
    """Centre of segment ``a``-``b`` (cm)."""
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0, (a[2] + b[2]) / 2.0)


def closest_point_on_segment(p, a, b):
    """The point on segment ``a``-``b`` nearest ``p``, plus its parameter ``t``.

    ``t`` runs 0 at ``a`` to 1 at ``b`` (clamped to the segment).  Used to match
    a stored centreline to a live body by proximity.
    """
    ab = _sub(b, a)
    len2 = _dot(ab, ab)
    if len2 <= 1e-18:
        return a, 0.0
    t = _dot(_sub(p, a), ab) / len2
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return _add(a, _scale(ab, t)), t


def distance(a, b):
    return _dot(_sub(a, b), _sub(a, b)) ** 0.5


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #
class Member:
    """One weldment member (a straight or bent tube) as a stored record.

    ``start``/``end`` are the centreline endpoints in cm (the *drawn* line, so a
    bend member keeps its full virtual corner even though the built body stops at
    the tangent points).  ``geom`` is the mm section descriptor; ``basis`` is the
    PLACED ``(u, v)`` axes (after the reference and Rotation, exactly as
    :func:`commands.weldment.entry._build_weldment` draws the profile) or None
    for a round tube.  ``feature``/``body_index`` locate the built body for the
    command layer to re-resolve.

    ``angle_rad``/``ref``/``anchor``/``offset_start``/``offset_end`` record the
    per-row placement the builder used, so a member can be REBUILT from the
    record alone (the BOM-as-history goal) without re-reading the command dialog.
    """

    __slots__ = ('mid', 'start', 'end', 'geom', 'basis',
                 'designation', 'family', 'feature', 'body_index', 'name',
                 'angle_rad', 'ref', 'anchor', 'offset_start', 'offset_end')

    def __init__(self, mid, start, end, geom=None, basis=None,
                 designation='', family='', feature=None, body_index=None,
                 name='', angle_rad=0.0, ref=None, anchor=None,
                 offset_start=0.0, offset_end=0.0):
        self.mid = mid
        self.start = tuple(start)
        self.end = tuple(end)
        self.geom = geom
        self.basis = basis
        self.designation = designation
        self.family = family
        self.feature = feature
        self.body_index = body_index
        self.name = name
        self.angle_rad = angle_rad
        self.ref = list(ref) if ref else None
        self.anchor = list(anchor) if anchor else None
        self.offset_start = offset_start
        self.offset_end = offset_end

    @property
    def direction(self):
        return _norm(_sub(self.end, self.start))

    def midpoint(self):
        return midpoint(self.start, self.end)

    def to_dict(self):
        return {'mid': self.mid, 'start': list(self.start), 'end': list(self.end),
                'geom': self.geom, 'basis': self.basis,
                'designation': self.designation, 'family': self.family,
                'feature': self.feature, 'body_index': self.body_index,
                'name': self.name, 'angle_rad': self.angle_rad,
                'ref': self.ref, 'anchor': self.anchor,
                'offset_start': self.offset_start,
                'offset_end': self.offset_end}

    @staticmethod
    def from_dict(d):
        return Member(d['mid'], d['start'], d['end'], geom=d.get('geom'),
                      basis=d.get('basis'), designation=d.get('designation', ''),
                      family=d.get('family', ''), feature=d.get('feature'),
                      body_index=d.get('body_index'), name=d.get('name', ''),
                      angle_rad=d.get('angle_rad', 0.0), ref=d.get('ref'),
                      anchor=d.get('anchor'),
                      offset_start=d.get('offset_start', 0.0),
                      offset_end=d.get('offset_end', 0.0))


class Joint:
    """One joint as a stored record.

    ``refs`` is an ordered list of ``{'mid', 'role'}`` dicts; the first is the
    subject member being cut, the rest are partners/tools.  ``vertex`` (cm) is
    the corner or T-landing point.  ``params`` carries the numeric settings
    (``clr_mm``, ``depth_mm``, ...).  ``selections`` is the optional explicit
    face/vertex handles a toolbox tool recorded -- the anchor that lets the joint
    be re-applied to the same geometry after a sketch edit.
    """

    __slots__ = ('jid', 'kind', 'refs', 'vertex', 'params', 'selections')

    def __init__(self, jid, kind, refs, vertex=None, params=None,
                 selections=None):
        self.jid = jid
        self.kind = kind
        self.refs = [dict(r) for r in refs]
        self.vertex = tuple(vertex) if vertex is not None else None
        self.params = dict(params or {})
        self.selections = dict(selections or {})

    def member_ids(self):
        return [r['mid'] for r in self.refs]

    def touches(self, mid):
        return any(r['mid'] == mid for r in self.refs)

    def subject(self):
        """The member being cut (first ref), or None."""
        return self.refs[0]['mid'] if self.refs else None

    def to_dict(self):
        return {'jid': self.jid, 'kind': self.kind,
                'refs': [dict(r) for r in self.refs],
                'vertex': list(self.vertex) if self.vertex is not None else None,
                'params': dict(self.params), 'selections': dict(self.selections)}

    @staticmethod
    def from_dict(d):
        return Joint(d['jid'], d['kind'], d.get('refs', []),
                     vertex=d.get('vertex'), params=d.get('params'),
                     selections=d.get('selections'))


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #
class Registry:
    """An ordered set of member and joint records with geometry lookup.

    The command layer loads one per design (from a design attribute), mutates it
    as tools run, and saves it back.  ``add_member``/``add_joint`` assign ids;
    ``upsert_member`` matches an existing member by centreline proximity so a
    re-run edits rather than duplicates.
    """

    def __init__(self, members=None, joints=None):
        self.members = list(members or [])
        self.joints = list(joints or [])

    # -- ids ------------------------------------------------------------- #
    def _next_mid(self):
        return (max((m.mid for m in self.members), default=0)) + 1

    def _next_jid(self):
        return (max((j.jid for j in self.joints), default=0)) + 1

    # -- members ------------------------------------------------------- #
    def add_member(self, start, end, geom=None, basis=None, designation='',
                   family='', feature=None, body_index=None, name='',
                   angle_rad=0.0, ref=None, anchor=None,
                   offset_start=0.0, offset_end=0.0):
        m = Member(self._next_mid(), start, end, geom=geom, basis=basis,
                   designation=designation, family=family, feature=feature,
                   body_index=body_index, name=name, angle_rad=angle_rad,
                   ref=ref, anchor=anchor, offset_start=offset_start,
                   offset_end=offset_end)
        self.members.append(m)
        return m

    def member(self, mid):
        for m in self.members:
            if m.mid == mid:
                return m
        return None

    def upsert_member(self, start, end, tol=0.05, **fields):
        """Return the member whose centreline matches ``start``-``end``.

        If an existing member's midpoint is within ``tol`` cm of the given
        segment's midpoint AND its direction is parallel (or anti-parallel), it
        is updated in place (missing ``fields`` left as-is) and returned;
        otherwise a new member is added.  This is the re-run pickup: rebuilding
        the same tube edits its record instead of adding a duplicate.
        """
        pm = midpoint(start, end)
        pd = _norm(_sub(end, start))
        for m in self.members:
            if distance(m.midpoint(), pm) <= tol:
                md = m.direction
                par = abs(_dot(pd, md))
                if par >= 1.0 - 1e-3:          # parallel or anti-parallel
                    m.start = tuple(start)
                    m.end = tuple(end)
                    for k, v in fields.items():
                        if hasattr(m, k) and v is not None:
                            setattr(m, k, v)
                    return m
        return self.add_member(start, end, **fields)

    def member_near_point(self, p, tol=0.05):
        """The member whose centreline passes within ``tol`` cm of ``p``."""
        best, best_d = None, tol
        for m in self.members:
            cp, _t = closest_point_on_segment(p, m.start, m.end)
            d = distance(cp, p)
            if d <= best_d:
                best, best_d = m, d
        return best

    def member_by_feature(self, feature, body_index=None):
        for m in self.members:
            if m.feature == feature and (
                    body_index is None or m.body_index == body_index):
                return m
        return None

    # -- joints --------------------------------------------------------- #
    def add_joint(self, kind, refs, vertex=None, params=None, selections=None):
        j = Joint(self._next_jid(), kind, refs, vertex=vertex, params=params,
                  selections=selections)
        self.joints.append(j)
        return j

    def joint(self, jid):
        for j in self.joints:
            if j.jid == jid:
                return j
        return None

    def joints_for_member(self, mid):
        return [j for j in self.joints if j.touches(mid)]

    # -- edit-in-place (the BOM panel's write path) ---------------------- #
    def set_member(self, mid, **fields):
        """Update scalar fields on member ``mid`` (designation, name, ...).

        Unknown fields or an unknown ``mid`` are ignored (returns False) so a
        stale panel edit cannot corrupt the registry.
        """
        m = self.member(mid)
        if m is None:
            return False
        for k, v in fields.items():
            if hasattr(m, k) and k in ('designation', 'family', 'name'):
                setattr(m, k, v)
        return True

    def set_joint_kind(self, jid, kind):
        j = self.joint(jid)
        if j is None:
            return False
        j.kind = kind
        return True

    def set_joint_param(self, jid, key, value):
        j = self.joint(jid)
        if j is None:
            return False
        j.params[key] = value
        return True

    def remove_member(self, mid):
        self.members = [m for m in self.members if m.mid != mid]
        for j in self.joints:
            j.refs = [r for r in j.refs if r['mid'] != mid]
        self.joints = [j for j in self.joints if j.refs]

    # -- serialisation ------------------------------------------------- #
    def summary(self):
        """A JSON-friendly BOM view model for the palette panel.

        Members carry a computed ``length_mm`` (cut length) and joints a
        human-readable ``label``; ids are included so the panel can send an
        edit back keyed by ``mid``/``jid``.
        """
        members = []
        for m in self.members:
            length_mm = distance(m.start, m.end) / MM_TO_CM
            members.append({'mid': m.mid, 'name': m.name,
                            'designation': m.designation, 'family': m.family,
                            'length_mm': round(length_mm, 2),
                            'start': list(m.start), 'end': list(m.end)})
        joints = []
        for j in self.joints:
            joints.append({'jid': j.jid, 'kind': j.kind,
                           'refs': [r['mid'] for r in j.refs],
                           'params': dict(j.params)})
        return {'members': members, 'joints': joints}

    def to_dict(self):
        return {'version': SCHEMA_VERSION,
                'members': [m.to_dict() for m in self.members],
                'joints': [j.to_dict() for j in self.joints]}

    def to_json(self):
        return json.dumps(self.to_dict(), sort_keys=True)

    @staticmethod
    def from_dict(d):
        if not d:
            return Registry()
        ver = d.get('version', SCHEMA_VERSION)
        if ver > SCHEMA_VERSION:
            raise ValueError(
                f'registry schema version {ver} is newer than {SCHEMA_VERSION}')
        return Registry([Member.from_dict(m) for m in d.get('members', [])],
                        [Joint.from_dict(j) for j in d.get('joints', [])])

    @staticmethod
    def from_json(s):
        if not s:
            return Registry()
        return Registry.from_dict(json.loads(s))
