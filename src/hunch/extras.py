"""Source watching (authority mode 'watch') and the evaluation benchmark."""
from __future__ import annotations

import difflib
import hashlib
import json
import time
import urllib.request
from pathlib import Path

from .ingest import html_to_text
from .project import Project
from .resolver import baseline, resolve
from .text import slugify

# ------------------------------------------------------------------- watch


def _fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"user-agent": "hunch/0.1 (+source watch)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read(10 * 1024 * 1024).decode("utf-8", "ignore")
        ctype = r.headers.get("content-type", "")
    return html_to_text(raw) if ("html" in ctype or "<html" in raw[:500].lower()) else raw


def check_watches(project: Project, accept: bool = False) -> list[dict]:
    """For every domain in 'watch' mode, fetch its URL and report whether it changed.

    Nothing in your documents is ever changed unless you pass accept=True, which saves the
    new version as a file inside that domain's folder so it gets indexed like any other doc.
    """
    wdir = project.state / "watch"
    wdir.mkdir(parents=True, exist_ok=True)
    results = []
    for d in project.domains:
        if d.authority != "watch" or not d.watch_url:
            continue
        slot = wdir / slugify(d.id)
        slot.mkdir(exist_ok=True)
        latest, meta_p = slot / "latest.txt", slot / "meta.json"
        try:
            text = _fetch_text(d.watch_url)
        except Exception as e:  # network trouble shouldn't crash the whole run
            results.append({"domain": d.label, "url": d.watch_url, "status": "error", "detail": str(e)})
            continue
        digest = hashlib.sha256(text.encode()).hexdigest()
        meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
        row = {"domain": d.label, "url": d.watch_url}
        if meta.get("sha256") == digest:
            row["status"] = "unchanged" if meta.get("accepted_sha") == digest else "awaiting review"
        else:
            old = latest.read_text(encoding="utf-8") if latest.exists() else ""
            diff = list(difflib.unified_diff(old.splitlines(), text.splitlines(), lineterm="", n=0))
            added = [l[1:] for l in diff if l.startswith("+") and not l.startswith("+++")]
            removed = [l[1:] for l in diff if l.startswith("-") and not l.startswith("---")]
            latest.write_text(text, encoding="utf-8")
            row.update(status="new" if not old else "changed",
                       lines_added=len(added), lines_removed=len(removed),
                       sample_added=[l for l in added if l.strip()][:5],
                       sample_removed=[l for l in removed if l.strip()][:5])
        meta.update(sha256=digest, checked=time.time(), url=d.watch_url)
        if accept and meta.get("accepted_sha") != digest:
            folder = None
            if d.source == "folder" and d.docs:
                folder = project.root / Path(d.docs[0]).parts[0]
            target = (folder or project.root) / f"_watched_{slugify(d.id)}.md"
            target.write_text(f"# {d.label} (published source)\n\nSource: {d.watch_url}\n\n{text}\n",
                              encoding="utf-8")
            meta["accepted_sha"] = digest
            row["saved_to"] = str(target.relative_to(project.root))
        meta_p.write_text(json.dumps(meta))
        results.append(row)
    return results


# -------------------------------------------------------------------- eval


def load_questions(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        if "question" in obj and "expect" in obj:
            items.append(obj)
    return items


def run_eval(project: Project, questions: list[dict], k: int = 6) -> dict:
    """Compare plain search against Hunch at the same passage budget.

    A question is a hit if any returned passage comes from a document whose path
    contains the `expect` string.
    """
    rows = []
    for item in questions:
        q, expect = item["question"], item["expect"]
        base_docs = [p.doc for p in baseline(project, q, k=k)]
        pack = resolve(project, q, max_passages=k)
        hunch_docs = [p.doc for p in pack.passages]
        rows.append({
            "question": q,
            "expect": expect,
            "baseline_hit": any(expect in d for d in base_docs),
            "hunch_hit": any(expect in d for d in hunch_docs),
            "active": [a.domain.label for a in pack.active],
        })
    n = len(rows) or 1
    return {
        "questions": len(rows),
        "k": k,
        "baseline_recall": sum(r["baseline_hit"] for r in rows) / n,
        "hunch_recall": sum(r["hunch_hit"] for r in rows) / n,
        "rescued": [r for r in rows if r["hunch_hit"] and not r["baseline_hit"]],
        "lost": [r for r in rows if r["baseline_hit"] and not r["hunch_hit"]],
        "both_missed": [r for r in rows if not r["baseline_hit"] and not r["hunch_hit"]],
        "rows": rows,
    }
