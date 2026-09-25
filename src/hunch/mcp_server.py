"""Hunch as an MCP server, so any MCP-capable assistant can use it.

Works with Claude Desktop, Claude Code, Hermes Agent, OpenClaw, and other MCP clients,
whatever model they run (Claude, Grok, a local model...). Run `hunch connect` to print
ready-to-paste config for each.
"""
from __future__ import annotations

import json
import os

from mcp.types import ToolAnnotations

try:  # mcp >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server
    _V2 = True
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]
    _V2 = False

from . import DEFAULT_HTTP_PORT
from .project import HunchError, Project
from .providers import ProviderError, get_provider
from .resolver import resolve

INSTRUCTIONS = """Hunch gives you a map of the user's own documents, organized into areas.
Call hunch_resolve with the recent conversation BEFORE answering any message where the
user's own notes, files, projects, household, work or history might matter, even if they
did not mention their documents. It returns the relevant areas, authority rules for each,
and cited passages. Follow the authority rules: when the user's documents and your general
knowledge disagree, show both and let the user decide rather than silently picking one.
For important answers, call hunch_check_draft with your draft before sending it.
If the user tells you a phrase should point to an area, record it with hunch_add_signal."""

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def create_server(root: str) -> _Server:
    project = Project(root)
    server = _Server("hunch", instructions=INSTRUCTIONS)

    def fresh() -> Project:
        project.ensure_fresh()
        return project

    @server.tool(name="hunch_resolve", annotations=READ)
    def hunch_resolve(conversation: str, max_passages: int = 8) -> str:
        """Find which areas of the user's documents are relevant to the conversation, including
        ones not mentioned by name, and return authority rules plus cited passages.

        conversation: the latest user message, ideally with the previous few turns
            (plain text, oldest first, one turn per paragraph).
        max_passages: how many passages to return (1-20).
        """
        turns = [t for t in conversation.split("\n\n") if t.strip()] or [conversation]
        pack = resolve(fresh(), turns, max_passages=max(1, min(20, max_passages)))
        return pack.render()

    @server.tool(name="hunch_list_areas", annotations=READ)
    def hunch_list_areas() -> str:
        """List every area (domain) in the user's documents with its authority mode,
        document count, key words and signal phrases."""
        p = fresh()
        return json.dumps([d.summary() for d in p.domains], indent=1, ensure_ascii=False)

    @server.tool(name="hunch_search", annotations=READ)
    def hunch_search(query: str, area: str = "", limit: int = 8) -> str:
        """Plain keyword search across the user's documents, optionally within one area
        (use an id from hunch_list_areas)."""
        p = fresh()
        idx = p.index
        from .resolver import _query_weights
        restrict = None
        if area:
            try:
                restrict = idx.chunk_ids_for_docs(p.domain(area).docs)
            except HunchError as e:
                return str(e)
        hits = idx.search(dict(_query_weights(query)), k=max(1, min(25, limit)), restrict=restrict)
        if not hits:
            return "No matches. Try different words, or hunch_resolve for a broader look."
        out = []
        for i, h in enumerate(hits, 1):
            ch = idx.chunks[h.chunk]
            out.append(f"[{i}] {ch['doc']}" + (f" > {ch['section']}" if ch["section"] else "") + f"\n{ch['text']}")
        return "\n\n".join(out)

    @server.tool(name="hunch_read_document", annotations=READ)
    def hunch_read_document(path: str, max_chars: int = 20000) -> str:
        """Read a whole document by its path as shown in passages (e.g. 'house/furnace.md')."""
        try:
            return fresh().read_document(path, max_chars=max(500, min(100000, max_chars)))
        except HunchError as e:
            return f"Error: {e}"

    @server.tool(name="hunch_check_draft", annotations=READ)
    def hunch_check_draft(draft: str, conversation: str = "") -> str:
        """Check a draft answer against the user's documents before sending it.
        If Hunch has its own AI key configured it lists material contradictions itself;
        otherwise it returns the relevant passages for you to compare against."""
        p = fresh()
        pack = resolve(p, [t for t in (conversation, draft) if t.strip()])
        if not pack.passages:
            return "No relevant passages in the user's documents; nothing to check against."
        try:
            provider = get_provider(required=False)
        except ProviderError:
            provider = None
        if provider is not None:
            from .ai import check_draft
            try:
                conflicts = check_draft(provider, draft, pack)
            except ProviderError as e:
                conflicts, provider = [], None
                note = f"(automatic check unavailable: {e})\n\n"
            else:
                if not conflicts:
                    return "No material contradictions with the user's documents."
                lines = ["Material contradictions with the user's documents. Show these to the user:"]
                for c in conflicts:
                    lines.append(f"- Draft: {c.draft_says}\n  Documents ({c.doc or 'passage ' + str(c.passage)}): "
                                 f"{c.documents_say}")
                return "\n".join(lines)
        else:
            note = ""
        return (note + "Compare the draft against these passages. If the draft materially contradicts "
                "any of them, tell the user what their documents say and what you said, and let them "
                "decide.\n\n" + pack.render())

    @server.tool(name="hunch_add_signal", annotations=WRITE)
    def hunch_add_signal(area: str, phrase: str) -> str:
        """Teach Hunch that an everyday phrase should bring an area into play
        (e.g. area='house', phrase='getting ready for winter'). Saved to hunch.json."""
        try:
            d = fresh().add_signal(area, phrase)
        except HunchError as e:
            return f"Error: {e}"
        return f"Saved. '{phrase}' now points to {d.label}."

    @server.tool(name="hunch_set_authority", annotations=WRITE)
    def hunch_set_authority(area: str, mode: str, watch_url: str = "") -> str:
        """Set how much to trust an area of the user's documents. Only do this when the user asks.
        mode: 'project' (documents win, conflicts shown), 'training' (ignore documents for this area),
        'ask' (ask the user each time), or 'watch' (like project, plus track watch_url for updates)."""
        try:
            d = fresh().set_authority(area, mode, watch_url or None)
        except HunchError as e:
            return f"Error: {e}"
        return f"{d.label} is now '{d.authority}'."

    return server


def serve(root: str, http: bool = False, host: str = "127.0.0.1", port: int = DEFAULT_HTTP_PORT):
    server = create_server(root)
    if not http:
        server.run()
        return
    if _V2:
        server.run(transport="streamable-http", host=host, port=port)
    else:
        server.settings.host = host  # type: ignore[attr-defined]
        server.settings.port = port  # type: ignore[attr-defined]
        server.run(transport="streamable-http")


def main():  # entry point: hunch-mcp
    root = os.environ.get("HUNCH_ROOT") or os.getcwd()
    serve(root)
