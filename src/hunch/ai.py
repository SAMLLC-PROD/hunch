"""Features that use a language model. Everything else in Hunch works offline."""
from __future__ import annotations

from dataclasses import dataclass

from .project import Project
from .providers import Provider, extract_json
from .resolver import ContextPack

# ------------------------------------------------------------------ enrich

ENRICH_SYSTEM = """You help organize a person's document collection.
You will see one area ("domain") of their documents. Return ONLY a JSON object:
{
  "label": "2-4 word plain-English name for this area",
  "description": "one sentence on what this area covers",
  "signals": ["..."]
}
"signals" are 12-20 short everyday phrases (2-5 words) that a person might say in
conversation when this area is relevant, WITHOUT naming the area or its technical terms.
Think about situations, symptoms, goals and worries, not topics.
Example for a folder of furnace manuals: "house feels cold", "weird smell from vents",
"getting ready for winter", "energy bill went up".
No markdown, no commentary, JSON only."""


def enrich_domain(project: Project, provider: Provider, domain_id: str) -> dict:
    d = project.domain(domain_id)
    idx = project.index
    samples = []
    for path in d.docs[:6]:
        meta = idx.docs.get(path, {})
        cids = meta.get("chunks", [])[:2]
        text = "\n".join(idx.chunks[c]["text"] for c in cids)[:900]
        samples.append(f"### {path} — {meta.get('title', '')}\n{text}")
    prompt = (
        f"Folder/area id: {d.id}\n"
        f"Frequent distinctive words: {', '.join(d.aliases[:15])}\n"
        f"Number of documents: {len(d.docs)}\n\n"
        "Samples:\n\n" + "\n\n".join(samples)
    )
    reply = provider.complete(ENRICH_SYSTEM, [{"role": "user", "content": prompt}], max_tokens=900)
    data = extract_json(reply)
    signals = [s.strip() for s in data.get("signals", []) if isinstance(s, str) and s.strip()][:25]
    label = str(data.get("label", "")).strip()[:60]
    desc = str(data.get("description", "")).strip()[:300]
    project.store_ai_enrichment(d.id, label, desc, signals)
    return {"id": d.id, "label": label, "description": desc, "signals": signals}


# -------------------------------------------------------- contradiction check

CHECK_SYSTEM = """You compare a DRAFT answer against numbered excerpts from the user's own documents.
Report only MATERIAL contradictions: the draft states something that an excerpt directly
contradicts (different number, opposite instruction, wrong name, wrong date, etc.).
Do NOT report: omissions, extra detail, rephrasings, or things the excerpts don't address.
Return ONLY JSON: {"conflicts": [{"draft_says": "...", "documents_say": "...", "passage": <n>}]}
Return {"conflicts": []} when there are none."""


@dataclass
class Conflict:
    draft_says: str
    documents_say: str
    passage: int | None
    doc: str = ""


def check_draft(provider: Provider, draft: str, pack: ContextPack) -> list[Conflict]:
    if not pack.passages or not draft.strip():
        return []
    excerpts = "\n\n".join(f"[{p.n}] ({p.doc}) {p.text}" for p in pack.passages)
    prompt = f"EXCERPTS:\n{excerpts}\n\nDRAFT:\n{draft}"
    reply = provider.complete(CHECK_SYSTEM, [{"role": "user", "content": prompt}], max_tokens=800,
                              temperature=0)
    try:
        data = extract_json(reply)
    except ValueError:
        return []
    by_n = {p.n: p.doc for p in pack.passages}
    out = []
    for c in data.get("conflicts", []) if isinstance(data, dict) else []:
        try:
            n = int(c.get("passage")) if c.get("passage") is not None else None
        except (TypeError, ValueError):
            n = None
        out.append(Conflict(str(c.get("draft_says", "")), str(c.get("documents_say", "")), n, by_n.get(n, "")))
    return out


# --------------------------------------------------- benchmark question maker

GEN_SYSTEM = """You write test questions for a document search tool.
Given one document, write questions a real person might ask where THIS document holds
the answer, but phrased the way someone would ask who hasn't looked at the document:
describe the situation or goal, and AVOID the document's distinctive vocabulary
(listed below). Return ONLY a JSON array of strings."""


def generate_questions(project: Project, provider: Provider, path: str, n: int = 2) -> list[str]:
    idx = project.index
    meta = idx.docs[path]
    text = "\n\n".join(idx.chunks[c]["text"] for c in meta["chunks"])[:3500]
    d = next((d for d in project.domains if path in d.docs), None)
    avoid = ", ".join((d.aliases[:12] if d else []) + [meta.get("title", "")])
    prompt = f"Write {n} questions.\nAvoid these words: {avoid}\n\nDOCUMENT ({path}):\n{text}"
    reply = provider.complete(GEN_SYSTEM, [{"role": "user", "content": prompt}], max_tokens=500)
    data = extract_json(reply)
    return [q for q in data if isinstance(q, str)][:n] if isinstance(data, list) else []
