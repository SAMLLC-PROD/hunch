"""Tokenizing and light stemming. Pure Python, no dependencies."""
from __future__ import annotations

import re
from collections import Counter

_WORD = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset(
    """
    a about above after again against all also am an and any are aren as at be because been before being
    below between both but by can cannot could couldn did didn do does doesn doing don down during each
    else etc even ever every few for from further get gets getting got had hadn has hasn have haven having
    he her here hers herself him himself his how however i if in into is isn it its itself just let lets
    like ll m may me might more most much must my myself need needs no nor not now of off on once one only
    or other our ours ourselves out over own per re really s same shall she should shouldn so some such t
    than that the their theirs them themselves then there these they this those through thus to too under
    until up upon us use used using ve very via was wasn way we well were weren what when where whether
    which while who whom whose why will with within without won would wouldn yes yet you your yours
    yourself yourselves thing things want wants make makes made know see seem seems say says said go going
    anything something everything nothing someone anyone always never often maybe ok okay please thanks
    hi hello hey tell ask asked question questions help take takes took put puts give gives
    """.split()
)


def stem(word: str) -> str:
    """Very light suffix stripping so 'filters', 'filter', 'filtering' line up."""
    w = word
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
        if len(w) > 3 and w[-1] == w[-2]:
            w = w[:-1]
        return w
    if len(w) > 4 and w.endswith("ed") and not w.endswith("eed"):
        w = w[:-2]
        if len(w) > 3 and w[-1] == w[-2]:
            w = w[:-1]
        return w
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _keep(word: str) -> bool:
    if word in STOPWORDS:
        return False
    if len(word) < 2:
        return False
    if word.isdigit() and len(word) < 3:
        return False
    return True


def tokenize(text: str) -> list[str]:
    """Lowercase, split, drop stopwords, stem."""
    return [stem(w) for w in _WORD.findall(text.lower()) if _keep(w)]


def tokenize_with_surface(text: str) -> tuple[list[str], Counter]:
    """Tokenize and also count which surface word produced each stem.

    Returns (stems, Counter of (stem, surface) pairs) so we can show humans
    'filters' instead of 'filter' when that is how the documents spell it.
    """
    stems: list[str] = []
    pairs: Counter = Counter()
    for w in _WORD.findall(text.lower()):
        if not _keep(w):
            continue
        s = stem(w)
        stems.append(s)
        pairs[(s, w)] += 1
    return stems, pairs


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "domain"


def humanize(name: str) -> str:
    return re.sub(r"[-_]+", " ", name).strip().title() or name
