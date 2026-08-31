# The A2A mesh — how to talk to the other agents

This machine runs agent CLIs from several labs (claude, gemini, codex, kimi)
wrapped as A2A daemons on loopback ports. You can converse with any of them
through the `a2a` MCP server — tools `list_agents` and `send` — or, given
shell access, the same operations as a CLI: `a2a-mcp list` and
`a2a-mcp send <agent> <context> <message> [workdir]`.

Rules that matter:

- `send` blocks until the remote agent finishes its turn — often minutes
  for real work. Plan accordingly; do not assume it hung.
- A **context** is a persistent conversation with one agent. Reuse the same
  context name to continue it; a fresh name starts a fresh conversation
  with no memory.
- A **new** context must include `workdir` — the directory the remote agent
  runs in, pinned for that context's life. A different directory requires a
  new context name; the daemon refuses mismatches rather than moving a
  conversation.
- A failed turn raises a tool error (MCP) or exits 2 (`a2a-mcp` CLI).
  Never treat error text as the remote agent's answer.
- Only send to contexts you created (or were explicitly given): some
  context names map to live sessions owned by someone else.
- Turns within one context are serialised server-side; parallel work goes
  to parallel contexts, never into one.

About this repository: README.md documents the design. The daemons run as
`systemd --user` units `a2a-agent@<name>`, and each agent's activity log is
`~/.local/state/a2a-agents/<name>.log`.
