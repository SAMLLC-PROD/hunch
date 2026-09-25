"""`hunch chat` — a terminal chat with Claude or Grok that knows when your documents matter."""
from __future__ import annotations

from .ai import check_draft
from .project import Project
from .providers import Provider, ProviderError
from .resolver import ContextPack, resolve

BASE_SYSTEM = """You are a helpful, plain-spoken assistant.
The user keeps their own documents (notes, manuals, policies, project files). Before each
reply, a tool called Hunch looks at the conversation and pulls in passages from any area of
those documents that looks relevant, including areas the user did not mention by name.
Use the passages when they help, cite them as [n], and ignore ones that turn out irrelevant.
When the documents and your general knowledge disagree, say so plainly instead of silently
choosing one."""

DIM, BOLD, YEL, CYAN, RESET = "\033[2m", "\033[1m", "\033[33m", "\033[36m", "\033[0m"


def system_prompt(pack: ContextPack) -> str:
    return BASE_SYSTEM + "\n\n" + pack.render()


def _show_pack(pack: ContextPack):
    if pack.active:
        names = ", ".join(f"{a.domain.label}" + (f" ({'; '.join(a.reasons)})" if a.reasons else "")
                          for a in pack.active)
        print(f"{DIM}hunch: {names}{RESET}")
    if pack.parked:
        print(f"{DIM}hunch: left out {', '.join(a.domain.label for a in pack.parked)} (set to general knowledge){RESET}")


def run_chat(project: Project, provider: Provider, check: bool = True, verbose: bool = False):
    print(f"{BOLD}Hunch chat{RESET} — {provider} — {len(project.domains)} areas in {project.root}")
    print(f"{DIM}Commands: /areas  /context  /check on|off  /clear  /quit{RESET}\n")
    history: list[dict] = []
    last_pack: ContextPack | None = None
    while True:
        try:
            user = input(f"{CYAN}you>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not user:
            continue
        if user in ("/quit", "/exit", "/q"):
            return
        if user == "/areas":
            for d in project.domains:
                print(f"  {d.id:<24} {d.label}  [{d.authority}]  {len(d.docs)} docs")
            continue
        if user == "/context":
            print(last_pack.render() if last_pack else "(nothing yet)")
            continue
        if user.startswith("/check"):
            check = user.endswith("on")
            print(f"contradiction check {'on' if check else 'off'}")
            continue
        if user == "/clear":
            history, last_pack = [], None
            print("(conversation cleared)")
            continue

        project.ensure_fresh()
        turns = [m["content"] for m in history if m["role"] == "user"] + [user]
        pack = resolve(project, turns)
        last_pack = pack
        _show_pack(pack)
        if verbose:
            print(DIM + pack.render() + RESET)

        for d in pack.ask_domains:
            ans = input(f"{YEL}Your notes cover '{d.label}', which you marked as ask-first. "
                        f"Use them for this answer? [Y/n] {RESET}").strip().lower()
            if ans.startswith("n"):
                pack.passages = [p for p in pack.passages if p.domain_id != d.id]
                pack.active = [a for a in pack.active if a.domain.id != d.id]

        history.append({"role": "user", "content": user})
        try:
            reply = provider.complete(system_prompt(pack), history)
        except ProviderError as e:
            print(f"{YEL}error: {e}{RESET}")
            history.pop()
            continue
        history.append({"role": "assistant", "content": reply})
        print(f"\n{reply}\n")

        if pack.passages:
            print(DIM + "sources: " + "; ".join(
                f"[{p.n}] {p.doc}" + ("*" if p.via == "hunch" else "") for p in pack.passages) + RESET)
            if any(p.via == "hunch" for p in pack.passages):
                print(DIM + "* surfaced by Hunch even though your question didn't mention it" + RESET)

        needs_check = check and any(a.domain.authority in ("project", "watch") for a in pack.active)
        if needs_check:
            try:
                conflicts = check_draft(provider, reply, pack)
            except ProviderError:
                conflicts = []
            for c in conflicts:
                print(f"{YEL}! Conflict with your documents{RESET}")
                print(f"  answer says:    {c.draft_says}")
                print(f"  your docs say:  {c.documents_say}" + (f"  [{c.passage}] {c.doc}" if c.passage else ""))
        print()
