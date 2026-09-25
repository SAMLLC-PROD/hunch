"""Command line: `hunch <command>`. Run `hunch --help` for the list."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import DEFAULT_HTTP_PORT, __version__
from .manifold import AUTHORITY_HELP, AUTHORITY_MODES
from .project import HunchError, Project
from .providers import ProviderError, get_provider

BOLD, DIM, GRN, YEL, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[33m", "\033[0m"
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    BOLD = DIM = GRN = YEL = RESET = ""


def _project(args) -> Project:
    p = Project(args.root)
    p.ensure_fresh(min_interval=0)
    return p


def _provider(args):
    return get_provider(getattr(args, "provider", None), getattr(args, "model", None))


def _print_areas(p: Project):
    if not p.domains:
        print("No documents found. Supported: .md .txt .rst .org .html .docx .pdf")
        return
    for d in p.domains:
        kind = "folder" if d.source == "folder" else "auto"
        print(f"{BOLD}{d.label}{RESET}  {DIM}id={d.id}  {kind}  {len(d.docs)} docs  [{d.authority}]{RESET}")
        if d.description:
            print(f"  {d.description}")
        print(f"  {DIM}words:{RESET} {', '.join(d.aliases[:10])}")
        if d.signals:
            print(f"  {DIM}signals:{RESET} {'; '.join(d.signals[:6])}" + (" ..." if len(d.signals) > 6 else ""))


# ------------------------------------------------------------------ commands

def cmd_init(args):
    root = Path(args.root).expanduser().resolve()
    p = Project(root)
    stats = p.build()
    print(f"{GRN}Indexed {stats['documents']} documents ({stats['chunks']} passages) "
          f"into {stats['domains']} areas in {stats['seconds']}s.{RESET}\n")
    _print_areas(p)
    print(f"""
{BOLD}Next steps{RESET}
  hunch resolve "a question"          see what Hunch would pull in (offline, no AI needed)
  hunch enrich                        let an AI name your areas and write signal phrases (recommended)
  hunch chat                          chat with Claude or Grok using your documents
  hunch connect                       hook Hunch into Claude Desktop, Claude Code, Hermes, or OpenClaw
Settings live in {p.config_path.name}. Generated files live in .hunch/ (safe to delete).""")


def cmd_build(args):
    p = Project(args.root)
    s = p.build()
    print(f"{s['documents']} documents, {s['chunks']} passages, {s['domains']} areas ({s['seconds']}s)")


def cmd_areas(args):
    p = _project(args)
    if args.json:
        print(json.dumps([d.summary() for d in p.domains], indent=2, ensure_ascii=False))
    else:
        _print_areas(p)


def cmd_area(args):
    p = _project(args)
    d = p.domain(args.area)
    print(json.dumps({**d.summary(), "aliases": d.aliases, "signals": d.signals, "documents": d.docs},
                     indent=2, ensure_ascii=False))


def cmd_rename(args):
    d = _project(args).rename(args.area, args.label, args.description)
    print(f"Renamed to {d.label}")


def cmd_trust(args):
    d = _project(args).set_authority(args.area, args.mode, args.url)
    print(f"{d.label}: {d.authority} — {AUTHORITY_HELP[d.authority]}")


def cmd_signal(args):
    p = _project(args)
    for phrase in args.phrases:
        d = p.add_signal(args.area, phrase)
    print(f"{d.label} signals: {'; '.join(d.user_signals)}")


def cmd_alias(args):
    p = _project(args)
    for w in args.words:
        d = p.add_alias(args.area, w)
    print(f"{d.label} words: {', '.join(d.user_aliases)}")


def cmd_enrich(args):
    from .ai import enrich_domain
    p = _project(args)
    prov = _provider(args)
    targets = [p.domain(args.area)] if args.area else p.domains
    print(f"Enriching {len(targets)} area(s) with {prov} ...")
    for d in targets:
        try:
            r = enrich_domain(p, prov, d.id)
        except (ProviderError, ValueError) as e:
            print(f"{YEL}  {d.id}: failed ({e}){RESET}")
            continue
        print(f"  {BOLD}{r['label'] or d.id}{RESET}: {r['description']}")
        print(f"    {DIM}{'; '.join(r['signals'][:8])}{RESET}")
    print("Done. Your own names and signals in hunch.json always take priority.")


def cmd_resolve(args):
    from .resolver import resolve
    p = _project(args)
    pack = resolve(p, args.text, max_passages=args.k)
    if args.json:
        print(json.dumps(pack.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(pack.render())


def cmd_ask(args):
    from .chat import system_prompt
    from .resolver import resolve
    p = _project(args)
    prov = _provider(args)
    pack = resolve(p, args.question, max_passages=args.k)
    reply = prov.complete(system_prompt(pack), [{"role": "user", "content": args.question}])
    print(reply)
    if pack.passages:
        print(DIM + "\nsources: " + "; ".join(f"[{x.n}] {x.doc}" for x in pack.passages) + RESET)


def cmd_chat(args):
    from .chat import run_chat
    p = _project(args)
    run_chat(p, _provider(args), check=not args.no_check, verbose=args.verbose)


def cmd_check(args):
    from .ai import check_draft
    from .resolver import resolve
    p = _project(args)
    pack = resolve(p, [t for t in (args.question or "", args.draft) if t])
    conflicts = check_draft(_provider(args), args.draft, pack)
    if not conflicts:
        print("No material contradictions with your documents.")
    for c in conflicts:
        print(f"{YEL}!{RESET} says: {c.draft_says}\n  your docs: {c.documents_say}  ({c.doc})")


def cmd_watch(args):
    from .extras import check_watches
    p = _project(args)
    results = check_watches(p, accept=args.accept)
    if not results:
        print("No areas are in 'watch' mode. Use: hunch trust <area> watch --url https://...")
    for r in results:
        print(f"{BOLD}{r['domain']}{RESET} {r['url']}: {r['status']}")
        if r["status"] in ("changed", "new"):
            print(f"  +{r['lines_added']} / -{r['lines_removed']} lines")
            for line in r.get("sample_removed", []):
                print(f"  {DIM}- {line[:120]}{RESET}")
            for line in r.get("sample_added", []):
                print(f"  {DIM}+ {line[:120]}{RESET}")
        if r.get("saved_to"):
            print(f"  saved to {r['saved_to']} (will be indexed)")
        elif r["status"] in ("changed", "new", "awaiting review"):
            print("  Review, then run `hunch watch --accept` to add it to your documents.")


def cmd_eval(args):
    from .extras import load_questions, run_eval
    p = _project(args)
    if args.generate:
        from .ai import generate_questions
        prov = _provider(args)
        paths = sorted(p.index.docs)[: args.limit]
        out = Path(args.generate)
        n = 0
        with out.open("w", encoding="utf-8") as f:
            for path in paths:
                try:
                    qs = generate_questions(p, prov, path, n=args.per_doc)
                except (ProviderError, ValueError) as e:
                    print(f"{YEL}  {path}: {e}{RESET}")
                    continue
                for q in qs:
                    f.write(json.dumps({"question": q, "expect": path}, ensure_ascii=False) + "\n")
                    n += 1
        print(f"Wrote {n} questions to {out}. Review them, then run: hunch eval {out}")
        return
    if not args.questions:
        sys.exit("Give a questions file (JSON lines with 'question' and 'expect'), or --generate FILE.")
    report = run_eval(p, load_questions(Path(args.questions)), k=args.k)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    b, h = report["baseline_recall"], report["hunch_recall"]
    print(f"{report['questions']} questions, {report['k']} passages each\n")
    print(f"  plain search found the right document:  {b:6.1%}")
    print(f"  Hunch found the right document:         {h:6.1%}   ({(h - b) * 100:+.1f} points)\n")
    for label, key in (("Rescued by Hunch", "rescued"), ("Lost vs. plain search", "lost"),
                       ("Missed by both", "both_missed")):
        rows = report[key]
        if rows:
            print(f"{BOLD}{label} ({len(rows)}){RESET}")
            for r in rows[:10]:
                print(f"  - {r['question']}  {DIM}-> {r['expect']}; areas: {', '.join(r['active']) or 'none'}{RESET}")


def cmd_mcp(args):
    from .mcp_server import serve
    root = str(Path(args.root).expanduser().resolve())
    if args.http:
        print(f"Hunch MCP (streamable HTTP) on http://{args.host}:{args.port}/mcp  root={root}", file=sys.stderr)
        if args.host not in ("127.0.0.1", "localhost"):
            print("WARNING: no authentication in this mode; anyone who can reach this port can read "
                  "your documents.", file=sys.stderr)
    serve(root, http=args.http, host=args.host, port=args.port)


def _command_parts(root: str) -> tuple[str, list[str]]:
    exe = shutil.which("hunch")
    if exe:
        return exe, ["mcp", "--root", root]
    return sys.executable, ["-m", "hunch", "mcp", "--root", root]


def cmd_connect(args):
    root = str(Path(args.root).expanduser().resolve())
    cmd, cargs = _command_parts(root)
    client = (args.client or "all").lower()
    blocks = {
        "claude-desktop": (
            "Claude Desktop — add to claude_desktop_config.json (Settings > Developer > Edit Config), "
            "then restart Claude Desktop",
            json.dumps({"mcpServers": {"hunch": {"command": cmd, "args": cargs}}}, indent=2),
        ),
        "claude-code": (
            "Claude Code — run in a terminal",
            "claude mcp add hunch -- " + " ".join([cmd, *cargs]),
        ),
        "hermes": (
            "Hermes Agent — add under mcp_servers in ~/.hermes/config.yaml, then /reload-mcp or restart",
            "mcp_servers:\n  hunch:\n    command: " + json.dumps(cmd) + "\n    args: " + json.dumps(cargs),
        ),
        "openclaw": (
            "OpenClaw — add under mcp.servers in ~/.openclaw/openclaw.json, then restart the gateway",
            json.dumps({"mcp": {"servers": {"hunch": {"command": cmd, "args": cargs}}}}, indent=2),
        ),
        "http": (
            "Any client that takes a URL (remote/HTTP) — start the server, then point the client at it",
            f"hunch mcp --http --root {json.dumps(root)}\n# URL: http://127.0.0.1:{DEFAULT_HTTP_PORT}/mcp",
        ),
    }
    chosen = blocks if client == "all" else {client: blocks.get(client)}
    if None in chosen.values():
        sys.exit(f"Unknown client. Choose from: {', '.join(blocks)}")
    for name, (title, body) in chosen.items():
        print(f"{BOLD}{title}{RESET}\n{body}\n")


# -------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="hunch",
        description="Give your AI a hunch about which of your documents matter, even when you don't mention them.",
    )
    ap.add_argument("--version", action="version", version=f"hunch {__version__}")
    ap.add_argument("--root", default=os.environ.get("HUNCH_ROOT", "."),
                    help="your documents folder (default: current folder or $HUNCH_ROOT)")
    sub = ap.add_subparsers(dest="cmd", metavar="<command>")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=argparse.SUPPRESS, help="your documents folder")

    def add(name, fn, help_, aliases=()):
        sp = sub.add_parser(name, help=help_, aliases=list(aliases), parents=[common])
        sp.set_defaults(fn=fn)
        return sp

    def ai_opts(sp):
        sp.add_argument("--provider", help="anthropic | xai | openai (default: whichever key is set)")
        sp.add_argument("--model", help="model name (defaults: claude-sonnet-5, grok-4.7)")

    sp = add("init", cmd_init, "index a folder and show its areas")
    sp.add_argument("folder", nargs="?", help="folder to index (default: --root)")
    add("build", cmd_build, "re-index (usually automatic)")
    sp = add("areas", cmd_areas, "list the areas Hunch found", aliases=["domains"])
    sp.add_argument("--json", action="store_true")
    sp = add("area", cmd_area, "show one area in detail")
    sp.add_argument("area")
    sp = add("rename", cmd_rename, "give an area a friendlier name")
    sp.add_argument("area")
    sp.add_argument("label")
    sp.add_argument("--description")
    sp = add("trust", cmd_trust, "set an area's authority: " + ", ".join(AUTHORITY_MODES))
    sp.add_argument("area")
    sp.add_argument("mode", choices=AUTHORITY_MODES)
    sp.add_argument("--url", help="published source to watch (mode 'watch')")
    sp = add("signal", cmd_signal, "add everyday phrases that should bring an area into play")
    sp.add_argument("area")
    sp.add_argument("phrases", nargs="+")
    sp = add("alias", cmd_alias, "add words that name an area")
    sp.add_argument("area")
    sp.add_argument("words", nargs="+")
    sp = add("enrich", cmd_enrich, "use an AI to name areas and write signal phrases")
    sp.add_argument("--area")
    ai_opts(sp)
    sp = add("resolve", cmd_resolve, "show what Hunch would pull in for some text (offline)")
    sp.add_argument("text")
    sp.add_argument("-k", type=int, default=None, help="max passages")
    sp.add_argument("--json", action="store_true")
    sp = add("ask", cmd_ask, "ask one question with your documents in play")
    sp.add_argument("question")
    sp.add_argument("-k", type=int, default=None)
    ai_opts(sp)
    sp = add("chat", cmd_chat, "chat in the terminal with Claude or Grok")
    sp.add_argument("--no-check", action="store_true", help="skip the contradiction check")
    sp.add_argument("--verbose", "-v", action="store_true", help="show the full context each turn")
    ai_opts(sp)
    sp = add("check", cmd_check, "check a draft answer against your documents")
    sp.add_argument("draft")
    sp.add_argument("--question")
    ai_opts(sp)
    sp = add("watch", cmd_watch, "check published sources for areas in 'watch' mode")
    sp.add_argument("--accept", action="store_true", help="save changed sources into your documents")
    sp = add("eval", cmd_eval, "measure Hunch vs. plain search on your own questions")
    sp.add_argument("questions", nargs="?")
    sp.add_argument("-k", type=int, default=6)
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--generate", metavar="FILE", help="have an AI write test questions into FILE")
    sp.add_argument("--per-doc", type=int, default=2)
    sp.add_argument("--limit", type=int, default=40, help="max documents to generate questions for")
    ai_opts(sp)
    sp = add("mcp", cmd_mcp, "run the MCP server (used by Claude, Hermes, OpenClaw...)")
    sp.add_argument("--http", action="store_true", help="serve over streamable HTTP instead of stdio")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=DEFAULT_HTTP_PORT)
    sp = add("connect", cmd_connect, "print setup snippets for AI apps")
    sp.add_argument("client", nargs="?", help="claude-desktop | claude-code | hermes | openclaw | http")
    return ap


def main(argv=None):
    if hasattr(__import__("signal"), "SIGPIPE"):
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    ap = build_parser()
    args = ap.parse_args(argv)
    if getattr(args, "cmd", None) == "init" and args.folder:
        args.root = args.folder
    if not getattr(args, "fn", None):
        ap.print_help()
        return
    try:
        args.fn(args)
    except (HunchError, ProviderError) as e:
        sys.exit(f"hunch: {e}")
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
