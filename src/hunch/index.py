"""A small BM25 search index over document chunks, stored as JSON."""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .ingest import Document
from .text import tokenize_with_surface

INDEX_VERSION = 1
K1 = 1.4
B = 0.75


@dataclass
class Hit:
    score: float
    chunk: int  # chunk index


class Index:
    def __init__(self):
        self.docs: dict[str, dict] = {}        # path -> {title, folder, mtime, size, chunks:[ids]}
        self.chunks: list[dict] = []           # {doc, section, text}
        self.tf: list[dict[str, int]] = []     # per chunk term counts
        self.lengths: list[int] = []
        self.df: Counter = Counter()
        self.surface: dict[str, str] = {}      # stem -> most common surface form
        self.postings: dict[str, list[int]] = {}

    # ------------------------------------------------------------ building
    @classmethod
    def build(cls, documents: list[Document]) -> "Index":
        idx = cls()
        surface_counts: Counter = Counter()
        for d in documents:
            ids = []
            for c in d.chunks:
                cid = len(idx.chunks)
                ids.append(cid)
                idx.chunks.append({"doc": c.doc, "section": c.section, "text": c.text})
                # the section heading and title count toward the chunk's terms
                stems, pairs = tokenize_with_surface(f"{d.title}\n{c.section}\n{c.text}")
                surface_counts.update(pairs)
                tf = Counter(stems)
                idx.tf.append(dict(tf))
                idx.lengths.append(sum(tf.values()))
                idx.df.update(tf.keys())
            idx.docs[d.path] = {
                "title": d.title, "folder": d.folder, "mtime": d.mtime, "size": d.size, "chunks": ids,
            }
        best: dict[str, tuple[int, str]] = {}
        for (s, w), n in surface_counts.items():
            if s not in best or n > best[s][0]:
                best[s] = (n, w)
        idx.surface = {s: w for s, (n, w) in best.items()}
        idx._finish()
        return idx

    def _finish(self):
        post = defaultdict(list)
        for cid, tf in enumerate(self.tf):
            for t in tf:
                post[t].append(cid)
        self.postings = dict(post)
        self.avgdl = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0

    # ------------------------------------------------------------ querying
    @property
    def n(self) -> int:
        return len(self.chunks)

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def search(self, query: dict[str, float], k: int = 8, restrict: set[int] | None = None) -> list[Hit]:
        """query: {stem: weight}. restrict: optional set of chunk ids to consider."""
        scores: dict[int, float] = defaultdict(float)
        for term, qw in query.items():
            if qw <= 0 or term not in self.postings:
                continue
            idf = self.idf(term)
            for cid in self.postings[term]:
                if restrict is not None and cid not in restrict:
                    continue
                f = self.tf[cid][term]
                dl = self.lengths[cid]
                scores[cid] += qw * idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / self.avgdl))
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
        return [Hit(score=s, chunk=c) for c, s in ranked]

    def chunk_ids_for_docs(self, paths) -> set[int]:
        out: set[int] = set()
        for p in paths:
            out.update(self.docs.get(p, {}).get("chunks", []))
        return out

    def show(self, stem: str) -> str:
        return self.surface.get(stem, stem)

    # --------------------------------------------------------- persistence
    def save(self, path: Path):
        data = {
            "version": INDEX_VERSION,
            "docs": self.docs,
            "chunks": self.chunks,
            "tf": self.tf,
            "surface": self.surface,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> "Index":
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != INDEX_VERSION:
            raise ValueError("index version mismatch; rebuild with `hunch build`")
        idx = cls()
        idx.docs = data["docs"]
        idx.chunks = data["chunks"]
        idx.tf = data["tf"]
        idx.surface = data["surface"]
        idx.lengths = [sum(tf.values()) for tf in idx.tf]
        for tf in idx.tf:
            idx.df.update(tf.keys())
        idx._finish()
        return idx
