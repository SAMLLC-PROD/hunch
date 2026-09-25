"""The resolver: before the AI answers, decide which of your knowledge areas are in play.

Plain search asks "which chunks contain the words in this question?"
Hunch also asks "given what we're talking about, which of this person's areas
*should* be in the room, even if nobody named them?" That second question is
answered from the manifold: aliases, signal phrases, and domain-restricted search.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .manifold import Domain
from .project import Project
from .text import tokenize

ACTIVATION_THRESHOLD = 0.40
MAX_ACTIVE = 4
PASSAGE_CHARS = 1200


@dataclass
class Activation:
    domain: Domain
    score: float
    reasons: list[str]
    signal_terms: list[str] = field(default_factory=list)


@dataclass
class Passage:
    n: int
    doc: str
    section: str
    text: str
    domain_id: str
    via: str  # "question" (plain search found it) or "hunch" (surfaced by domain activation)


@dataclass
class ContextPack:
    active: list[Activation]
    passages: list[Passage]
    parked: list[Activation]  # domains that matched but are set to 'training'

    @property
    def ask_domains(self) -> list[Domain]:
        return [a.domain for a in self.active if a.domain.authority == "ask"]

    def to_dict(self) -> dict:
        return {
            "active_domains": [
                {"id": a.domain.id, "label": a.domain.label, "authority": a.domain.authority,
                 "score": round(a.score, 3), "why": a.reasons}
                for a in self.active
            ],
            "parked_domains": [{"id": a.domain.id, "label": a.domain.label} for a in self.parked],
            "passages": [
                {"n": p.n, "doc": p.doc, "section": p.section, "domain": p.domain_id, "via": p.via, "text": p.text}
                for p in self.passages
            ],
        }

    # ----------------------------------------------------------- rendering
    def authority_rules(self) -> str:
        lines = []
        for a in self.active:
            d = a.domain
            if d.authority in ("project", "watch"):
                lines.append(
                    f"- {d.label}: the user's documents are the authority. If your answer would contradict "
                    f"them, do not silently pick a side. Say what the documents say, say what you believe, "
                    f"and let the user decide."
                )
            elif d.authority == "ask":
                lines.append(
                    f"- {d.label}: HIGH-STAKES AREA. Before relying on the documents or on general knowledge, "
                    f"ask the user which they want you to use for this question."
                )
        for a in self.parked:
            lines.append(
                f"- {a.domain.label}: the user has chosen general knowledge over their documents here; "
                f"their documents for this area were deliberately left out."
            )
        return "\n".join(lines)

    def render(self) -> str:
        if not self.active and not self.passages:
            return ("Hunch: none of the user's document areas look relevant to this conversation. "
                    "Answer from general knowledge.")
        out = ["# Hunch context", ""]
        if self.active:
            out.append("Areas of the user's documents that look relevant (some may not have been mentioned):")
            for a in self.active:
                why = "; ".join(a.reasons) or "related content"
                out.append(f"- **{a.domain.label}** [{a.domain.authority}] (why: {why})")
            out.append("")
        rules = self.authority_rules()
        if rules:
            out += ["## How to treat these areas", rules, ""]
        if self.passages:
            out.append("## Passages from the user's documents")
            for p in self.passages:
                where = p.doc + (f" > {p.section}" if p.section else "")
                tag = "" if p.via == "question" else " (surfaced by Hunch, not by the question's wording)"
                out += [f"[{p.n}] {where}{tag}", p.text, ""]
        out.append("Cite passages as [n] when you use them. Ignore any passage that turns out to be irrelevant. "
                   "If an area above looks relevant but you did not use it, briefly mention that the user's "
                   "notes on it might be worth a look.")
        return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------ scoring

def _query_weights(conversation: str | list[str]) -> Counter:
    turns = [conversation] if isinstance(conversation, str) else [t for t in conversation if t]
    q: Counter = Counter()
    for i, turn in enumerate(turns[-6:]):
        is_last = i == len(turns[-6:]) - 1
        for t in tokenize(turn):
            q[t] += 1.0 if is_last else 0.4
    return q


def _signal_score(domain: Domain, qset: set[str]) -> tuple[float, list[str], list[str]]:
    best, hits, terms = 0.0, [], []
    for phrase in domain.signals:
        toks = set(tokenize(phrase))
        if not toks:
            continue
        overlap = len(toks & qset) / len(toks)
        if overlap == 1.0:
            s = 1.0 if len(toks) >= 2 else 0.75
        elif overlap >= 0.6 and len(toks) >= 3:
            s = 0.6 * overlap
        else:
            continue
        hits.append(phrase)
        terms.extend(toks)
        best = max(best, s)
    return best, hits, terms


def _alias_score(domain: Domain, q: Counter) -> tuple[float, list[str]]:
    if not domain.alias_stems:
        return 0.0, []
    top = max(domain.alias_stems.values()) or 1.0
    hits = [t for t in q if t in domain.alias_stems]
    raw = sum(domain.alias_stems[t] / top for t in hits)
    return min(1.0, raw / 1.5), hits


def resolve(project: Project, conversation: str | list[str], max_passages: int | None = None,
            char_budget: int = 9000) -> ContextPack:
    index = project.index
    max_passages = max_passages or int(project.setting("max_passages", 8) or 8)
    q = _query_weights(conversation)
    qset = set(q)
    if not q:
        return ContextPack([], [], [])

    # plain search across everything: the baseline any RAG tool would do
    global_hits = index.search(dict(q), k=max_passages)
    global_best = global_hits[0].score if global_hits else 0.0
    chunk_domain: dict[int, str] = {}
    for d in project.domains:
        for cid in index.chunk_ids_for_docs(d.docs):
            chunk_domain[cid] = d.id

    scored: list[Activation] = []
    for d in project.domains:
        sig, sig_hits, sig_terms = _signal_score(d, qset)
        ali, ali_hits = _alias_score(d, q)
        restrict = index.chunk_ids_for_docs(d.docs)
        local = index.search(dict(q), k=1, restrict=restrict)
        rel = (local[0].score / global_best) if (local and global_best > 0) else 0.0
        score = 1 - (1 - sig) * (1 - 0.8 * ali) * (1 - 0.7 * rel)
        reasons = []
        if sig_hits:
            reasons.append("sounds like " + ", ".join(f'"{s}"' for s in sig_hits[:2]))
        if ali_hits:
            reasons.append("mentions " + ", ".join(index.show(t) for t in ali_hits[:4]))
        if rel >= 0.5 and not reasons:
            reasons.append("strong text match")
        if score >= ACTIVATION_THRESHOLD:
            scored.append(Activation(d, score, reasons, sig_terms))

    scored.sort(key=lambda a: -a.score)
    active = [a for a in scored if a.domain.authority != "training"][:MAX_ACTIVE]
    parked = [a for a in scored if a.domain.authority == "training"]
    parked_ids = {a.domain.id for a in parked}

    # gather passages: plain-search hits first, then per-domain hunches
    passages: list[Passage] = []
    seen: set[int] = set()
    used = 0

    def add(cid: int, via: str) -> bool:
        nonlocal used
        if cid in seen or len(passages) >= max_passages:
            return False
        dom = chunk_domain.get(cid, "")
        if dom in parked_ids:
            return False
        ch = index.chunks[cid]
        text = ch["text"] if len(ch["text"]) <= PASSAGE_CHARS else ch["text"][:PASSAGE_CHARS] + " ..."
        if used + len(text) > char_budget and passages:
            return False
        seen.add(cid)
        used += len(text)
        passages.append(Passage(len(passages) + 1, ch["doc"], ch["section"], text, dom, via))
        return True

    for h in global_hits[: max(2, max_passages // 3)]:
        add(h.chunk, "question")

    if active:
        total = sum(a.score for a in active)
        for a in active:
            quota = max(1, math.ceil((max_passages - len(passages)) * a.score / total)) if total else 1
            restrict = index.chunk_ids_for_docs(a.domain.docs)
            focused = dict(q)
            for t in a.signal_terms:
                focused[t] = focused.get(t, 0) + 1.0
            if a.signal_terms:
                # the area's other signals describe the situations it covers; use them lightly
                for phrase in a.domain.signals:
                    for t in tokenize(phrase):
                        focused[t] = focused.get(t, 0) + 0.3
            hits = index.search(focused, k=quota + 4, restrict=restrict)
            # Activated by a signal or a name but nothing in the area shares words with the
            # conversation: fall back to the area's most representative passages.
            if not hits and (a.signal_terms or a.reasons):
                top = sorted(a.domain.alias_stems.items(), key=lambda kv: -kv[1])[:6]
                hits = index.search({t: 1.0 for t, _ in top}, k=min(2, quota), restrict=restrict)
            taken = 0
            for h in hits:
                via = "question" if any(h.chunk == g.chunk for g in global_hits) else "hunch"
                if add(h.chunk, via):
                    taken += 1
                if taken >= quota:
                    break

    for h in global_hits:  # fill any remaining room with reasonably strong plain hits
        if h.score >= 0.35 * global_best:
            add(h.chunk, "question")

    return ContextPack(active, passages, parked)


def baseline(project: Project, question: str, k: int = 8) -> list[Passage]:
    """Plain keyword search with no manifold. Used by `hunch eval` for comparison."""
    index = project.index
    out = []
    for i, h in enumerate(index.search(dict(_query_weights(question)), k=k), 1):
        ch = index.chunks[h.chunk]
        out.append(Passage(i, ch["doc"], ch["section"], ch["text"], "", "question"))
    return out
