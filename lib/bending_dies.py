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
      "dies": [ {die_id, profile_family, clr_mm}, ... ]
    }

Each die is one centerline radius (``clr_mm``) available for a profile family
(``profile_family``); ``die_id`` is a readable ``<FAMILY>-CLR-<value>`` tag.  The
CLR is the only value that shapes a swept bend, so the old per-die OD/size/wall
ranges (which the command ignored in practice) are gone: a family's dies are
filtered by family alone and the shop picks the radius it owns.

Families that share tooling are declared in ``_DIE_FAMILY_ALIASES`` (currently
``RHS -> SHS``): a rectangular tube is rotary-draw-bent on the same flat-face
dies as a square one, so RHS is served SHS's centreline radii rather than
duplicating every die row under its own family name.
"""

import json
import os

# Fusion internal length unit is centimetres; the catalogue is in millimetres.
MM_TO_CM = 0.1

_DATA_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "bendingdies.json")

# Families that share another family's bend tooling.  A rectangular hollow
# section is rotary-draw-bent on the same flat-face dies as a square one (the
# die bears on a face parallel to the bend plane), so RHS resolves to SHS's
# centreline radii instead of duplicating every die entry in the catalogue.
_DIE_FAMILY_ALIASES = {"RHS": "SHS"}


def _die_family(abbreviation):
    """The catalogue family whose dies ``abbreviation`` uses (alias-aware)."""
    return _DIE_FAMILY_ALIASES.get(abbreviation, abbreviation)


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
    """Human-readable one-line label per die, e.g. ``'CHS-CLR-114.3 (R114.3)'``."""
    return [f"{d['die_id']} (R{d['clr_mm']:g})" for d in dies(catalogue)]


def die_clr(die):
    """Centerline radius (mm) of a die entry (0.0 when absent)."""
    return die.get("clr_mm", 0.0) if die else 0.0


def find_die(catalogue, die_id):
    """Return the die dict whose ``die_id`` equals ``die_id`` (or None)."""
    for d in dies(catalogue):
        if d.get("die_id") == die_id:
            return d
    return None


def dies_for_family(catalogue, abbreviation):
    """All dies whose ``profile_family`` matches ``abbreviation``.

    Alias-aware: a family listed in ``_DIE_FAMILY_ALIASES`` (e.g. RHS) is served
    the dies of the family it shares tooling with (SHS), so the dropdown, the
    default die, and the resolved CLR all agree without duplicate catalogue rows.
    """
    family = _die_family(abbreviation)
    return [d for d in dies(catalogue) if d.get("profile_family") == family]


# --------------------------------------------------------------------------- #
# Family -> die matching
# --------------------------------------------------------------------------- #
def die_for_designation(catalogue, designation, abbreviation):
    """Pick the default (tightest) die for a profile family, or None.

    The catalogue is keyed by family alone now -- the centerline radius is the
    one value that shapes a bend -- so this returns the die with the smallest
    ``clr_mm`` for ``abbreviation``.  ``designation`` is still accepted for
    call-site compatibility but no longer filters anything.  None when the
    family has no dies (an open section cannot be swept-bent).
    """
    matches = dies_for_designation(catalogue, designation, abbreviation)
    return matches[0] if matches else None


def dies_for_designation(catalogue, designation, abbreviation):
    """Every centerline radius offered for a family, sorted ascending.

    Filtering is by family alone: a tube of a given family can be swept to any
    of the catalogue's CLRs for that family, and the shop picks the radius it
    owns (the tightest is the default, so the dropdown pre-selects item 0).
    ``designation`` is accepted for call-site compatibility but no longer gates
    the result.  Empty for a family with no dies (an open section).
    """
    out = list(dies_for_family(catalogue, abbreviation))
    out.sort(key=lambda d: d.get("clr_mm", float("inf")))
    return out


def clr_cm(die):
    """Centerline radius of a die in cm (Fusion's internal unit)."""
    return die_clr(die) * MM_TO_CM
