# Security

Hunch reads documents on the machine where you run it, and can send selected passages
to the AI provider you configure. It does not contact SpaceAutomationMachines or Lattice.

## Report a vulnerability

Email spaceautomationmachinesLLC@gmail.com.

Please include what you ran, what you expected, and what happened. Do not open a public
GitHub issue for an unfixed security problem, and do not include exploit steps, payloads,
or proof-of-concept code.

We will reply when we have read it. This is a small project. There is no bounty and no
fixed response window.

## Use it safely

- Point Hunch at a notes folder, not your whole home directory and not a folder of secrets.
- Keep MCP on the local process (`hunch connect`). Do not expose `hunch mcp --http` beyond
  localhost. That mode has no password.
- `hunch watch` fetches only URLs you saved, and only when you run it. Do not point it at
  addresses on your private network.
- Your API key stays in your environment. Hunch sends it only to the provider you chose.
