---
name: hunch
description: Use the Hunch MCP tools to bring the user's own documents into a conversation, including areas the user did not mention by name. Use at the start of any message where their notes, files, household, work, or projects might matter, and before sending answers that state facts their documents might cover.
---

# Using Hunch

Hunch keeps a map of the user's documents organized into areas (a folder, or a group of
related loose files). Each area has an authority mode set by the user.

## Every relevant turn
1. Call `hunch_resolve` with the latest user message plus the previous few turns
   (oldest first, blank line between turns).
2. Read the "Areas" list. Areas can appear even when the user never named them; that is
   the point. If one looks relevant, use its passages.
3. Follow the authority rules in the result:
   - `project` / `watch`: the user's documents are the authority. If your general
     knowledge disagrees, show both and let the user decide. Never silently pick one.
   - `ask`: ask the user whether to rely on their documents or general knowledge first.
   - Areas parked as general knowledge were deliberately left out; don't go looking for them.
4. Cite passages as [n] with the document path.

## Before important answers
Call `hunch_check_draft` with your draft. If it reports contradictions, show them to the user.

## Teaching Hunch
If the user says something like "when I say X, I mean my Y notes", call
`hunch_add_signal(area, phrase)`. Only call `hunch_set_authority` when the user asks
to change how much an area is trusted.

## Don't
- Don't call `hunch_resolve` for small talk or questions clearly unrelated to the user's life or work.
- Don't dump whole passages back at the user; summarize and cite.
