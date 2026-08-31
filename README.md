# a2a – a local mesh of agent CLIs

Agent CLI tools from different labs, wrapped as [A2A](https://a2a-protocol.org)
servers on loopback ports, so that they can be driven uniformly – by a human at
a REPL, or by one another. The point of mixing labs is divergence: models with
different training do not share blind spots, which makes their agreement worth
more and their disagreement informative. Claude, Gemini, Codex and Kimi are
supported; each runs as its own daemon, launched and observed separately.

## Components

- `a2a-agentd <agent>` – daemon wrapping one CLI. A self-contained
  [uv](https://docs.astral.sh/uv/) script (PEP 723 inline dependencies, no
  virtualenv to manage) serving the A2A protocol – JSON-RPC `message/send` –
  on `127.0.0.1:<port>`, with the real work delegated to the CLI in
  non-interactive mode.
- `a2a-chat [agent]` – REPL and one-shot client. `--status` shows which
  daemons are up, `--send` makes it scriptable, `--context` picks the
  conversation to continue.
- `a2a-mcp` – MCP bridge: run with no arguments it is a stdio MCP server
  exposing the mesh as `list_agents` / `send` tools, so any MCP-capable
  agent CLI can drive the mesh through its native tool calling – its own
  permission model, no shell escape, no sandbox loosening. The repo's
  `.mcp.json` registers it for Claude Code sessions in this directory
  (it resolves via `PATH`, so run `make install` first), and the
  `a2a-review` project skill teaches the review choreography on top.
  For kimi the registration lives in `~/.kimi-code/mcp.json` instead –
  user level, so it loads in any workdir without the workspace-trust
  prompt a project-level `.kimi-code/mcp.json` would require – plus an
  `allow` rule for `mcp__a2a__*` in `~/.kimi-code/config.toml`, because
  headless `kimi -p` (as run by `a2a-agent@kimi`) cannot answer
  approval prompts.
  The same operations double as a CLI: `a2a-mcp list`,
  `a2a-mcp send <agent> <context> <message>`.
- The **mailbox** – `post` / `check_mail` tools (CLI: `post`, `mail`,
  `watch`) – gives the mesh an asynchronous edge: a worker deep in a long
  task posts findings to a named box (conventionally `lead`) as they
  emerge, instead of saving everything for one final reply; a live session
  runs `a2a-mcp watch lead` in the background as a doorbell that wakes it
  when mail lands. Boxes are 0700 directories under
  `~/.local/state/a2a-agents/mailbox/`; delivery is one-way,
  at-least-once, no read receipts – reports, not handshakes – and mail is
  untrusted content from other agents, to be read as data. Workers
  self-identify their posts via `A2A_AGENT`/`A2A_CONTEXT`, which the
  daemon exports to every CLI subprocess.
- `a2a-agent@.service` – systemd user template unit; one instance per agent
  (`systemctl --user start a2a-agent@claude`). NB the unit carries a
  `ConditionHost=` so that a home directory shared between hosts does not
  start daemons where they are not wanted – adjust it to the host that should
  run the mesh.
- `agents.toml.example` – seed for `~/.config/a2a-agents/agents.toml`: which
  CLI, which port, which adapter kind, plus per-agent extras.

## Install

```
make install
```

copies the scripts to `~/.local/bin`, installs the unit, and creates
`~/.config/a2a-agents/agents.toml` from the example – only if it does not
exist yet; an existing configuration is never touched. The first daemon start
resolves the Python dependencies (network needed once) and generates the
shared API key.

## Usage

```
systemctl --user start a2a-agent@{claude,gemini,codex,kimi}
a2a-chat --status
a2a-chat kimi                                  # REPL: /context, /new, /exit
a2a-chat codex --context review-42 --workdir ~/gits/proj --send "…"
                                               # one-shot; --workdir pins a
                                               # new context to its project
tail -f ~/.local/state/a2a-agents/claude.log   # progress of one agent
```

## Sessions

Every A2A `contextId` maps to a native CLI session, so a conversation survives
daemon restarts and can also be opened directly in the CLI that owns it
(`claude --resume <id>`, `kimi -r <id>`, and so on – the client prints the
native id). Claude and Gemini accept a caller-chosen session UUID, derived
deterministically from the context id; Codex and Kimi mint their own, which
the daemon captures from their JSON output. The mapping is persisted in
`~/.local/state/a2a-agents/<agent>.json`, and turns within one context are
serialised – concurrent requests to the same conversation queue rather than
race.

## Secrets and security

- Ports bind to loopback only, and every request must carry an `X-API-Key`
  header matching `~/.config/a2a-agents/api-key` (created on first start,
  mode 0600). Loopback alone is not a boundary on a multi-user machine.
  The one exception is agent-card discovery (`/.well-known/agent-card.json`,
  and the legacy `agent.json`), which is deliberately anonymous – the A2A
  way is a public card that declares the required scheme in
  `securitySchemes`, with authentication enforced on the RPC endpoint. This
  is also what lets tools such as A2A Inspector see the agents at all.
- An agent may be given secrets through `env_from_pass`: each entry is
  resolved with `pass show` per task and injected into that agent's
  subprocess only – no other agent sees it, nothing is stored or logged.
  This requires the gpg-agent passphrase cache to be primed (any `pass show`
  in an interactive shell does it).
- Agents run in their CLIs' default permission modes, which is deliberately
  conservative; grant more through `extra_args` per agent, and only as much
  as the workload needs. The gemini worker additionally refuses untrusted
  workspaces unless the context's pinned workdir sits inside one of its
  `trusted_workdirs` (each entry trusts its whole subtree) – that passes
  `--skip-trust`, letting the checkout's own `.gemini/` configuration run
  as you, so list only trees whose content you trust.
- Prompts are delivered to the CLIs via stdin, keeping them out of
  `/proc/<pid>/cmdline` – except for kimi, which has no stdin mode and
  keeps its prompt visible in argv for the duration of the turn.
- A worker-side failure comes back as a normal A2A message flagged with
  `metadata.error`; `a2a-chat --send` exits 2 and `a2a-mcp send` prefixes
  the reply with `ERROR`, so scripts and agents can tell failures from
  content.

## Limitations

Responses are single-shot – no streaming, no incremental task updates – and
`tasks/cancel` is not implemented; a slow turn simply runs until it finishes
or hits the per-agent timeout. Log files under `~/.local/state/a2a-agents/`
grow without rotation, though at a line or two per task this takes a while
to matter; the same goes for the context→session maps, which are never
pruned. The CLIs own their session stores and can lose them (gemini expires
old sessions, kimi and claude scope theirs to a directory): when a resume
fails for such a reason, the daemon starts a fresh native session under the
same context and logs it — the conversation's history is gone, so re-state
what matters rather than assuming the agent remembers.

A context also pins its **working directory** on its first message, for
life — several CLIs scope their session stores to the directory they run
in, so moving a conversation would orphan its session. A new context
refuses to start without one: pass it as message metadata
`{"workdir": "/path"}` — via `a2a-chat --workdir`, the MCP `send` tool's
`workdir` argument, or, in A2A Inspector, the **Message Metadata** field on
the chat's first message. A different directory means a new context name.
One daemon per agent thus serves conversations in any number of projects
concurrently; entries created before this scheme adopt the per-agent
`workdir` from the configuration, which is otherwise legacy. Subprocess output is buffered unbounded in memory, so a runaway CLI
emitting gigabytes could OOM its daemon. And while status checks are
anonymous by design, a `message/send` necessarily presents the key to
whatever holds the port – if a daemon is down, a local squatter on its port
could harvest it; a unix-socket transport would close that hole and is
future work. Finally, turns within one context are serialised server-side:
a second send into a busy context waits invisibly for the first to finish,
so its client may time out while the turn still runs – and completes – on
the daemon; send concurrent work to different contexts, not into one.
