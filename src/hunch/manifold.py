"""The domain manifold: a small map of which knowledge areas exist in your documents.

A domain is either
  * a top-level folder (the natural way people already organize files), or
  * an automatic cluster of loose files that talk about the same things.

The manifold holds no knowledge itself. It holds a territory map: each domain's
name, the words that point at it (aliases), the everyday phrases that should make
the assistant suspect it applies (signals), and how much to trust it (authority).
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .index import Index
from .text import humanize, slugify, tokenize

AUTHORITY_MODES = ("project", "training", "ask", "watch")
AUTHORITY_HELP = {
    "project": "Your documents win over the AI's general knowledge (default). Conflicts are shown to you.",
    "training": "Ignore your documents for this area; let the AI use its general knowledge.",
    "ask": "High-stakes area: the AI asks you before relying on either source.",
    "watch": "Like 'project', plus Hunch checks a published source URL for updates.",
}

CLUSTER_THRESHOLD = 0.16
N_ALIASES = 15


@dataclass
class Domain:
    id: str
    label: str
    description: str
    source: str                       # "folder" or "cluster"
    docs: list[str]
    aliases: list[str]                # surface words shown to people
    alias_stems: dict[str, float]     # stem -> weight used for matching
    signals: list[str]                # everyday phrases that suggest this domain
    authority: str = "project"
    watch_url: str | None = None
    chunk_count: int = 0
    user_aliases: list[str] = field(default_factory=list)
    user_signals: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "authority": self.authority,
            "watch_url": self.watch_url,
            "documents": len(self.docs),
            "aliases": self.aliases[:12],
            "signals": self.signals[:12],
        }


# --------------------------------------------------------------- clustering

def _doc_vectors(index: Index) -> dict[str, dict[str, float]]:
    raw: dict[str, Counter] = {}
    for path, meta in index.docs.items():
        c: Counter = Counter()
        for cid in meta["chunks"]:
            c.update(index.tf[cid])
        raw[path] = c
    n_docs = max(len(raw), 1)
    ddf: Counter = Counter()
    for c in raw.values():
        ddf.update(c.keys())
    vecs = {}
    for path, c in raw.items():
        v = {t: (1 + math.log(f)) * math.log((n_docs + 1) / (ddf[t] + 0.5)) for t, f in c.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        vecs[path] = {t: x / norm for t, x in v.items() if x > 0}
    return vecs


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(t, 0.0) for t, x in a.items())


def _cluster(paths: list[str], vecs: dict[str, dict[str, float]]) -> list[list[str]]:
    """Greedy single-pass clustering against running centroids. Fast and good enough."""
    clusters: list[tuple[list[str], dict[str, float]]] = []
    for p in paths:
        v = vecs.get(p, {})
        best, best_sim = None, 0.0
        for i, (_, centroid) in enumerate(clusters):
            s = _cos(v, centroid)
            if s > best_sim:
                best, best_sim = i, s
        if best is not None and best_sim >= CLUSTER_THRESHOLD:
            members, centroid = clusters[best]
            members.append(p)
            k = len(members)
            merged = {t: centroid.get(t, 0) * (k - 1) / k + v.get(t, 0) / k for t in set(centroid) | set(v)}
            norm = math.sqrt(sum(x * x for x in merged.values())) or 1.0
            clusters[best] = (members, {t: x / norm for t, x in merged.items()})
        else:
            clusters.append(([p], dict(v)))
    return [m for m, _ in clusters]


def _distinctive_terms(index: Index, groups: dict[str, list[str]]) -> dict[str, list[tuple[str, float]]]:
    agg: dict[str, Counter] = {}
    for gid, paths in groups.items():
        c: Counter = Counter()
        for cid in index.chunk_ids_for_docs(paths):
            c.update(index.tf[cid])
        agg[gid] = c
    n_groups = max(len(agg), 1)
    gdf: Counter = Counter()
    for c in agg.values():
        gdf.update(c.keys())
    out = {}
    for gid, c in agg.items():
        total = sum(c.values()) or 1
        scored = []
        for t, f in c.items():
            if t.isdigit() or len(t) < 3:
                continue
            spread = math.log((n_groups + 1) / gdf[t]) + 0.1
            scored.append((t, (f / total) * index.idf(t) * spread))
        scored.sort(key=lambda x: -x[1])
        out[gid] = scored[:N_ALIASES]
    return out


# ------------------------------------------------------------------ building

def build_auto_domains(index: Index, previous: list[dict] | None = None) -> list[dict]:
    """Derive domains from the index. `previous` lets ids and AI-written fields survive rebuilds."""
    by_folder: dict[str, list[str]] = {}
    loose: list[str] = []
    for path, meta in index.docs.items():
        if meta.get("folder"):
            by_folder.setdefault(meta["folder"], []).append(path)
        else:
            loose.append(path)

    groups: dict[str, list[str]] = {}
    kinds: dict[str, str] = {}
    for folder, paths in sorted(by_folder.items()):
        gid = slugify(folder)
        groups[gid] = sorted(paths)
        kinds[gid] = "folder"
    if loose:
        vecs = _doc_vectors(index)
        for i, members in enumerate(_cluster(sorted(loose), vecs)):
            gid = f"_cluster{i}"
            groups[gid] = sorted(members)
            kinds[gid] = "cluster"

    terms = _distinctive_terms(index, groups)
    prev = previous or []
    used: set[str] = set()
    domains: list[dict] = []

    for gid, paths in groups.items():
        top = terms.get(gid, [])
        match = _best_previous(paths, prev) if kinds[gid] == "cluster" else next(
            (p for p in prev if p["id"] == gid), None)
        if kinds[gid] == "folder":
            did = gid
            label = humanize(index.docs[paths[0]]["folder"])
        else:
            did = match["id"] if match else "auto-" + "-".join(index.show(t) for t, _ in top[:2]) or "auto"
            title_terms = set()
            for path in paths:
                title_terms.update(tokenize(index.docs[path]["title"]))
            ranked = sorted(top, key=lambda tw: (tw[0] not in title_terms, -tw[1]))
            label = " & ".join(index.show(t) for t, _ in ranked[:2]).title() or "Misc"
        base, n = did, 2
        while did in used:
            did, n = f"{base}-{n}", n + 1
        used.add(did)
        domains.append({
            "id": did,
            "source": kinds[gid],
            "docs": paths,
            "label_auto": label,
            "description_auto": (match or {}).get("description_auto", ""),
            "signals_auto": (match or {}).get("signals_auto", []),
            "label_ai": (match or {}).get("label_ai", ""),
            "alias_stems": {t: round(w, 6) for t, w in top},
            "aliases": [index.show(t) for t, _ in top],
            "chunk_count": len(index.chunk_ids_for_docs(paths)),
        })
    return domains


def _best_previous(paths: list[str], previous: list[dict]) -> dict | None:
    s = set(paths)
    best, best_j = None, 0.0
    for p in previous:
        if p.get("source") != "cluster":
            continue
        o = set(p.get("docs", []))
        j = len(s & o) / max(len(s | o), 1)
        if j > best_j:
            best, best_j = p, j
    return best if best_j >= 0.5 else None


def merge(auto: list[dict], overrides: dict) -> list[Domain]:
    """Combine automatic domains with the user's hand edits from hunch.json."""
    user = overrides.get("domains", {})
    out = []
    for a in auto:
        u = user.get(a["id"], {})
        user_aliases = [x for x in u.get("aliases", []) if x.strip()]
        user_signals = [x for x in u.get("signals", []) if x.strip()]
        alias_stems = dict(a["alias_stems"])
        top_weight = max(alias_stems.values(), default=1.0)
        for phrase in user_aliases + [u.get("label", ""), a["label_auto"], a.get("label_ai", "")]:
            for t in tokenize(phrase):
                alias_stems[t] = max(alias_stems.get(t, 0.0), top_weight)
        if a["source"] == "folder":
            for t in tokenize(a["id"]):
                alias_stems[t] = max(alias_stems.get(t, 0.0), top_weight)
        authority = u.get("authority", "project")
        if authority not in AUTHORITY_MODES:
            authority = "project"
        out.append(Domain(
            id=a["id"],
            label=u.get("label") or a.get("label_ai") or a["label_auto"],
            description=u.get("description") or a.get("description_auto", ""),
            source=a["source"],
            docs=a["docs"],
            aliases=user_aliases + [x for x in a["aliases"] if x not in user_aliases],
            alias_stems=alias_stems,
            signals=user_signals + [s for s in a.get("signals_auto", []) if s not in user_signals],
            authority=authority,
            watch_url=u.get("watch_url"),
            chunk_count=a["chunk_count"],
            user_aliases=user_aliases,
            user_signals=user_signals,
        ))
    return out

