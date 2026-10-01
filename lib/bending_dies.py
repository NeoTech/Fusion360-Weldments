"""Pure-Python bending-die catalogue for swept tube bends.

Like :mod:`lib.profiles` and :mod:`lib.joints`, this module never touches the
``adsk`` API so it can be unit-tested outside Fusion.  It loads
``data/bendingdies.json`` and answers, for a given profile designation, which
die can form it and what centerline radius that die produces -- the inputs the
command layer needs to place a swept-bend arc (see :mod:`lib.joints`).

Data shape
----------
``data/bendingdies.json`` is a single object::

    {
      "standard": "...", "units": "mm",
      "dies": [ {die_id, profile_family, groove_profile_type,
                 nominal_CLR_mm, compatible_OD_mm | compatible_width_mm/
                 compatible_height_mm, wall_thickness_range_mm,
                 maximum_bend_angle_deg, min_adjacent_straight_mm,
                 neutral_axis_shift_mm, ...}, ... ]
    }

A die matches a designation when the designation's family equals the die's
``profile_family`` and its outside size falls inside the die's compatible
range.  When several dies match, the smallest CLR wins (tightest feasible bend).
"""

import json
import os

# Fusion internal length unit is centimetres; the catalogue is in millimetres.
MM_TO_CM = 0.1

_DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "bendingdies.json")


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_bending_dies(path=_DATA_PATH):
    """Load the die catalogue from ``data/bendingdies.json``.

    Returns the raw catalogue dict (with a ``dies`` list).  A missing file
    yields an empty catalogue rather than raising, so the add-in still loads
    when no tooling data is present.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {"units": "mm", "dies": []}


def dies(catalogue):
    """The list of die dicts in a catalogue."""
    return catalogue.get("dies", []) if catalogue else []


def die_labels(catalogue):
    """Human-readable one-line label per die, e.g. ``'CHS-D40-R120 (R120)'``."""
    return [f"{d['die_id']} (R{d['nominal_CLR_mm']:g})" for d in dies(catalogue)]


def find_die(catalogue, die_id):
    """Return the die dict whose ``die_id`` equals ``die_id`` (or None)."""
    for d in dies(catalogue):
        if d.get("die_id") == die_id:
            return d
    return None


def dies_for_family(catalogue, abbreviation):
    """All dies whose ``profile_family`` matches ``abbreviation``."""
    return [d for d in dies(catalogue) if d.get("profile_family") == abbreviation]


# --------------------------------------------------------------------------- #
# Designation -> die matching
# --------------------------------------------------------------------------- #
def _designation_size(designation):
    """Return ``(od_mm | None, width_mm, height_mm)`` for a designation dict.

    Round sections (CHS) carry ``od_mm``; hollow rectangular sections (SHS/RHS)
    carry ``h_mm``/``b_mm``.  Open sections have neither and cannot be bent.
    """
    od = designation.get("od_mm")
    if od is not None:
        return od, od, od
    h = designation.get("h_mm")
    b = designation.get("b_mm")
    if h is not None and b is not None:
        return None, b, h
    return None, None, None


def _in_range(value, rng):
    """True when ``value`` lies inside a ``[lo, hi]`` range (or rng is absent)."""
    if not rng:
        return True
    lo, hi = (rng + [None, None])[:2]
    if lo is not None and value < lo:
        return False
    if hi is not None and value > hi:
        return False
    return True


def die_for_designation(catalogue, designation, abbreviation):
    """Pick the best die for a designation, or None if it cannot be bent.

    Matches on family, then on the outside size falling inside the die's
    compatible range and the wall thickness inside the die's qualified range.
    Among the matches the smallest ``nominal_CLR_mm`` is returned (the tightest
    bend the tooling can make).
    """
    matches = dies_for_designation(catalogue, designation, abbreviation)
    if not matches:
        return None
    return min(matches, key=lambda d: d.get("nominal_CLR_mm", float("inf")))


def dies_for_designation(catalogue, designation, abbreviation):
    """Every die that can form ``designation``, sorted by ascending CLR.

    Same size/wall match as :func:`die_for_designation` but returns *all* the
    compatible dies rather than only the tightest.  A tube size is typically
    formable on several dies (different centerline radii), and a shop may own
    a different one than the default, so the command layer offers this list as
    a dropdown.  Empty when the designation cannot be bent (open section, or
    outside every die's range).
    """
    od, w, h = _designation_size(designation)
    if od is None and w is None:
        return []  # open section -- no swept bend possible
    wall = designation.get("t_mm")
    matches = []
    for d in dies_for_family(catalogue, abbreviation):
        groove = d.get("groove_profile_type")
        if groove == "round":
            if not _in_range(od, d.get("compatible_OD_mm")):
                continue
        else:  # square / rectangular groove
            if not (_in_range(w, d.get("compatible_width_mm")) and
                    _in_range(h, d.get("compatible_height_mm"))):
                continue
        if not _in_range(wall, d.get("wall_thickness_range_mm")):
            continue
        matches.append(d)
    matches.sort(key=lambda d: d.get("nominal_CLR_mm", float("inf")))
    return matches


def clr_cm(die):
    """Centerline radius of a die in cm (Fusion's internal unit)."""
    return (die.get("nominal_CLR_mm", 0.0) if die else 0.0) * MM_TO_CM
