# Hunch

**Give your AI a hunch about which of your documents matter, even when you don't mention them.**

Most "chat with your documents" tools only search when your question uses the same words
as your files. Ask *"we've got a cold snap coming this weekend, what should I do?"* and
they'll find nothing, because your plumbing notes say "hard freeze" and "hose bibs," not
"cold snap."

Hunch fixes that. It builds a small map of the areas your documents cover (your house,
your car, your insurance, your projects) along with the everyday situations that should
bring each area to mind. Before your AI answers, Hunch checks the conversation against
that map and pulls in the right notes on its own:

```
you> we're getting a cold snap this weekend, what should I do?
hunch: Car (sounds like "cold snap"), House (sounds like "cold snap"), Garden (sounds like "cold snap")

A few things from your notes are worth doing before Saturday:
1. Your outdoor spigots aren't frost-free. Close the "front" and "back" valves in the
   basement ceiling, open the spigots to drain, and bring the hoses in [4]. Your notes
   mention a 2022 burst pipe that cost $1,800 when this got skipped.
2. Cover the tomatoes or pick the green ones [5].
3. Tire pressure drops in the cold; yours should be 35 psi [1].

sources: [1] car/tires.md; [2] car/maintenance-log.md*; [3] house/furnace.md*; [4] house/plumbing.md*; [5] garden-plan.md*; [6] tomato-notes.md*
* surfaced by Hunch even though your question didn't mention it
```

(The areas and sources above are real output from the sample folder in `examples/`.
The answer text is illustrative. It depends on which AI you connect.)

It also lets you decide **who wins when your documents and the AI disagree**, and it tells
you when that happens instead of quietly picking one:

```
! Conflict with your documents
  answer says:    5W-30
  your docs say:  0W-20 full synthetic  [1] car/maintenance-log.md
```

Hunch works with **Claude** and **Grok**, in a terminal chat, or as a plug-in (MCP server)
for **Claude Desktop, Claude Code, Hermes Agent, OpenClaw,** and other MCP-capable apps.
Your documents stay on your machine.

---

## Install

You need Python 3.10 or newer.

```bash
pipx install git+https://github.com/SAMLLC-PROD/hunch
# or: uv tool install git+https://github.com/SAMLLC-PROD/hunch
# or: pip install git+https://github.com/SAMLLC-PROD/hunch
```

Install from that git URL. The command you run is still `hunch`.

Do not `pip install hunch-ai` or `pip install hunch`. Those names on PyPI are other projects.

For PDF support add the extra: `pipx install "samllc-hunch[pdf] @ git+https://github.com/SAMLLC-PROD/hunch"`.

## Five-minute start

```bash
hunch init ~/Documents/notes      # index a folder; each top-level subfolder becomes an "area"
cd ~/Documents/notes
hunch areas                       # see what it found
hunch resolve "leaving town for the holidays"   # see what it would pull in (no AI needed)
```

Then connect an AI. Set **one** of these:

```bash
export ANTHROPIC_API_KEY=...   # Claude  (get one at console.anthropic.com)
export XAI_API_KEY=...         # Grok    (get one at console.x.ai)
```

(On Windows PowerShell: `$env:ANTHROPIC_API_KEY="..."`)

```bash
hunch enrich     # recommended: the AI names your areas and writes "signal" phrases for each
hunch chat       # chat in the terminal
```

Want to try it first? This repo has a sample folder:

```bash
cd examples/home-notes && hunch init . && hunch resolve "a hail storm just rolled through"
```

## Use it inside your AI app (MCP)

`hunch connect` prints ready-to-paste setup for each app, with the right paths already
filled in. Run it from your documents folder.

| App | What to do |
|---|---|
| **Claude Desktop** | `hunch connect claude-desktop`, paste into your config file, restart Claude Desktop |
| **Claude Code** | `hunch connect claude-code`, run the printed `claude mcp add ...` command |
| **Hermes Agent** | `hunch connect hermes`, paste under `mcp_servers:` in `~/.hermes/config.yaml`. Works with any model Hermes runs, including Grok |
| **OpenClaw** | `hunch connect openclaw`, paste under `mcp.servers` in `~/.openclaw/openclaw.json`. Works with any model OpenClaw runs, including Grok |
| **Anything that takes a URL** | `hunch mcp --http` serves `http://127.0.0.1:8741/mcp` |

Once connected, the app's AI gets these tools:

| Tool | What it does |
|---|---|
| `hunch_resolve` | Which of your areas matter right now, the rules for each, and cited passages |
| `hunch_check_draft` | Checks an answer against your documents before it's sent |
| `hunch_search`, `hunch_read_document` | Plain search and full-document reading |
| `hunch_list_areas` | Your map |
| `hunch_add_signal` | Lets the AI learn "when I say X, I mean my Y notes" |
| `hunch_set_authority` | Change how much an area is trusted (only when you ask) |

For agent frameworks that support skills (Hermes, OpenClaw, Claude), copy
`integrations/skill/hunch/` into your skills folder. It teaches the agent when to call Hunch.

**About claude.ai on the web and phone apps:** these can only reach MCP servers on the
public internet, not ones running on your computer. `hunch mcp --http` has **no password
protection** in this version, so only expose it through a tunnel or private network you
control, and never for sensitive documents. Claude Desktop and Claude Code don't have this
problem because they run Hunch locally.

**About the Grok app (grok.com / X):** Hunch plugs in wherever you can add an MCP server.
For Grok today, that means `hunch chat --provider xai`, or Hermes/OpenClaw with Grok
selected as the model.

## Areas, signals, and trust

**Areas** come from your folders. `car/`, `house/`, `taxes/` each become an area. Loose files
in the top folder get grouped automatically by topic. Hunch re-indexes by itself when files
change.

**Signals** are everyday phrases that should bring an area to mind even when nobody names
it: "cold snap" → House, "road trip" → Car, "hail storm" → Insurance. `hunch enrich` writes
them with AI; you can add your own:

```bash
hunch signal house "leaving town for the holidays" "power went out"
```

**Trust (authority)** decides what happens when your documents and the AI's general
knowledge disagree:

| Mode | Meaning |
|---|---|
| `project` (default) | Your documents are the authority. Conflicts are shown to you, never silently resolved. |
| `training` | Leave your documents out for this area and use the AI's general knowledge. Handy for old or scratch folders. |
| `ask` | High-stakes area (medical, legal, money): the AI asks you before relying on either source. |
| `watch` | Like `project`, plus Hunch tracks a published source (a manual, a policy page, a spec) and tells you when it changes. |

```bash
hunch trust insurance ask
hunch trust tax-rules watch --url https://example.gov/current-rates.html
hunch watch            # checks for updates; nothing changes until you run: hunch watch --accept
```

All of your choices live in `hunch.json` in your folder. It's plain JSON, safe to edit by
hand, and your edits always override the AI's suggestions. The `.hunch/` folder is generated
and safe to delete.

## Does it actually help? Measure it on your own files

Hunch includes a benchmark so you don't have to take its word for it:

```bash
hunch eval --generate questions.jsonl   # AI writes indirect questions for your documents
hunch eval questions.jsonl              # plain search vs. Hunch, same number of passages
```

```
  plain search found the right document:   80.0%
  Hunch found the right document:         100.0%   (+20.0 points)
```

That sample result is from the tiny example folder, with signals and questions written by
hand, so treat it as a demo, not proof. The number that matters is the one you get on
your own documents with generated questions. The report also lists which questions Hunch
rescued and which it lost, so you can see where it helps and where it doesn't.

## Other AI providers

Any OpenAI-compatible server works, including local models through Ollama or LM Studio:

```bash
export HUNCH_BASE_URL=http://localhost:11434/v1
export HUNCH_MODEL=llama3.1
hunch chat --provider openai
```

Choose models with `--model` or `HUNCH_MODEL`. Defaults are `claude-sonnet-5` and `grok-4.7`.

Claude Sonnet 5 rejects a custom `temperature`. Hunch does not send one on the Anthropic path. Grok and other OpenAI-compatible servers still get `temperature=0` on the contradiction check.

## Privacy

- Indexing, the map, and `hunch resolve` run entirely on your machine with no network access.
- When you chat, enrich, or check, only the selected passages (plus your message) go to
  the AI provider you configured. `enrich` sends short samples from each area.
- When used through an app via MCP, the app's own AI sees the passages Hunch returns.
- `watch` downloads only the URLs you give it, and only when you run `hunch watch`.
- Hunch does not contact SpaceAutomationMachines, Lattice, or any other server of ours.
- The default MCP mode is a local process. Do not point `hunch mcp --http` at a public
  address. That mode has no password.

## How it works

1. **Index.** Documents are split into passages and indexed with BM25 keyword search.
   It's pure Python with no database and no embeddings to download.
2. **Map.** Each area gets its distinctive words (from your text), its signal phrases
   (from you or `enrich`), and a trust mode. The map is a few kilobytes.
3. **Resolve.** Before each answer, Hunch scores every area against the recent
   conversation: signal matches, area words, and how well the area's own passages match.
   Areas above the bar are "live," and Hunch pulls passages from each one, not just from
   whichever file shares the most words with the question.
4. **Surface, don't silently decide.** The AI gets rules per area. An optional second pass
   compares the answer with your documents and flags material contradictions.

Plain search answers "which passages contain these words?" Hunch also asks "which parts of
this person's world should be in the room?"

## Commands

| Command | Does |
|---|---|
| `hunch init [folder]` | Index a folder and show its areas |
| `hunch areas` / `hunch area <id>` | List areas / show one |
| `hunch rename <area> "Name"` | Friendlier name |
| `hunch signal <area> "phrase"...` | Add signal phrases |
| `hunch alias <area> word...` | Add words that name the area |
| `hunch trust <area> <mode>` | Set authority |
| `hunch enrich` | AI names areas and writes signals |
| `hunch resolve "text"` | Show what would be pulled in (offline) |
| `hunch ask "question"` | One-shot answer |
| `hunch chat` | Terminal chat (`/areas`, `/context`, `/check on/off`) |
| `hunch check "draft"` | Contradiction check |
| `hunch watch [--accept]` | Check watched sources |
| `hunch eval` | Benchmark |
| `hunch mcp [--http]` | Run the MCP server |
| `hunch connect [app]` | Print setup for an app |

Every command accepts `--root <folder>`, or run it from inside the folder.

## Current limitations (v0.1)

- Keyword search, not embeddings. Signals and `enrich` close most of the gap for everyday
  phrasing, but synonyms that appear nowhere in the map will still be missed.
- Word stemming is tuned for English.
- Scanned PDFs need OCR first. Only PDFs with a text layer are read.
- HTTP mode has no authentication yet.
- Very large collections (tens of thousands of files) will index slowly. Hunch is aimed at
  personal and team-sized folders.

## Support

GitHub is the support desk. There is no separate help line and no same-day promise.
One person reads these.

- Something broke, or you cannot get it working: [open a bug](https://github.com/SAMLLC-PROD/hunch/issues/new?template=bug.yml).
- You tried it and have a question or an idea: [open feedback](https://github.com/SAMLLC-PROD/hunch/issues/new?template=feedback.yml), or start a [Discussion](https://github.com/SAMLLC-PROD/hunch/discussions).
- A security problem: email spaceautomationmachinesLLC@gmail.com. Do not put exploit detail in a public issue.

Include what you ran, the error text, your OS, and whether you used the terminal or Hermes.
Do not paste API keys, and do not paste private documents.

## Contributing

```bash
git clone https://github.com/SAMLLC-PROD/hunch && cd hunch
pip install -e ".[dev,pdf]" && pytest
```

MIT licensed.
