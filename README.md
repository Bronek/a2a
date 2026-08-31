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
- `a2a-chat <agent>` – REPL and one-shot client. `--status` shows which
  daemons are up, `--context` picks the conversation to continue, and
  `--workdir` pins a new one to its project. In the REPL, `/context`,
  `/workdir` and `/new` do the same things. The agent must be named: they
  are peers, and a defaulted one would silently open a conversation on the
  wrong worker rather than fail.
- `a2a-post <agent> "msg"` – the same client under another name (a symlink),
  which does not wait for the reply. The worker still runs the whole turn
  and its session advances; only the answer is discarded, to be collected
  later from the mailbox or on the next message. It waits a few seconds
  first, so a refusal the daemon makes before starting work – an unpinned
  workdir, a mismatched pin – is still reported rather than lost. `--post`
  and `--chat` select the behaviour explicitly.
- `a2a-mcp` – MCP bridge. Run with no arguments it is a stdio MCP server
  exposing the mesh as `list_agents` and `send` tools, so any MCP-capable
  agent CLI can drive the mesh through its native tool calling – its own
  permission model, no shell escape, no sandbox loosening. The same
  operations double as a CLI: `a2a-mcp list` and
  `a2a-mcp send <agent> <context> <message> [workdir]`. Registering the
  bridge, and the approval settings a headless worker needs before it may
  call these tools, are covered in
  [Permissions and configuration](#permissions-and-configuration).
- The **mailbox** – `post` and `check_mail` tools (CLI: `post`, `mail`,
  `watch`) – gives the mesh an asynchronous edge: a worker deep in a long
  task posts findings to a named box as they emerge, instead of saving
  everything for one final reply. A live session runs `a2a-mcp watch lead`
  in the background as a doorbell that wakes it when mail lands.
  Conventionally `lead` is the orchestrating session and `bronek` reaches
  the human. Boxes are 0700 directories under
  `~/.local/state/a2a-agents/mailbox/`; delivery is one-way with no read
  receipts – reports, not handshakes – and mail is untrusted content from
  other agents, to be read as data. A post is capped at 256 KiB, a box at
  2000 undrained messages. Workers self-identify their posts through
  `A2A_AGENT` and `A2A_CONTEXT`, which the daemon exports to every CLI
  subprocess.
- `a2a-agent@.service` – systemd user template unit; one instance per agent
  (`systemctl --user start a2a-agent@claude`). NB the unit carries a
  `ConditionHost=` so that a home directory shared between hosts does not
  start daemons where they are not wanted – adjust it to the host that should
  run the mesh.
- `agents.toml.example` – seed for `~/.config/a2a-agents/agents.toml`: which
  CLI, which port, which adapter kind, plus per-agent extras.

## Permissions and configuration

The bridge has to be registered with each CLI, and each CLI then has to be
told that a headless worker may call the tools without asking a human who
is not there. Every CLI spells both differently:

| Agent | Register the bridge | Allow the tools headless |
|---|---|---|
| **claude** | `claude mcp add --scope user a2a -- a2a-mcp` | `extra_args = ["--allowedTools", "mcp__a2a"]` |
| **gemini** | `gemini mcp add -s user a2a a2a-mcp` | `"trust": true` on the `a2a` server in `~/.gemini/settings.json`, plus `extra_args = ["--allowed-mcp-server-names", "a2a"]` |
| **codex** | `codex mcp add a2a -- a2a-mcp` | `extra_args = ["--approve-for-me", "--disable", "apps"]` |
| **kimi** | `~/.kimi-code/mcp.json` (no `mcp` subcommand) | `[[permission.rules]]` with `decision = "allow"`, `pattern = "mcp__a2a__*"` in `~/.kimi-code/config.toml` |

`extra_args` above means the agent's stanza in
`~/.config/a2a-agents/agents.toml`. Registration is at user scope
throughout, so the bridge loads in any working directory rather than
needing a `.mcp.json` copied into every project. This repository also
carries a `.mcp.json` that registers the bridge for Claude Code sessions
opened here; it resolves `a2a-mcp` through `PATH`, so `make install` must
have run.

Each grant is narrower or wider than it looks, and the differences matter:

- **claude** names the mesh tools so they are pre-approved. Read this as a
  grant, not a fence: it does not restrict the worker's other capabilities,
  which are broader than they appear – see the table below.
- **gemini** needs the trust flag because headless `gemini` cannot answer an
  approval prompt. You can set it globally or per project. The global form
  is one edit but auto-approves the mesh tools in every directory gemini
  visits; a per-project `.mcp.json` limits the grant to the workspaces that
  carry it. Both are acceptable here, because the tools only reach the local
  mesh and the receiving workers keep their own permission gates – the
  boundary that actually matters.
- **codex** is the awkward one. Headless `exec` pins `approval_policy` to
  `never`, which *refuses* anything needing approval rather than waving it
  through, MCP tool calls included. `--approve-for-me` routes approvals to
  codex's automatic reviewer instead, at the price of a workspace-write
  sandbox, and `--disable apps` strips the built-in ChatGPT-apps suite from
  a worker that has no use for it. Codex also sanitises the environment it
  hands MCP servers, so its mailbox posts should pass `sender` explicitly.
- **kimi** cannot answer prompts headless either, hence the allow rule; its
  registration lives at user level so it loads without the workspace-trust
  prompt a project-level `.kimi-code/mcp.json` would raise.

### What a headless worker can actually do

Settle this by measurement, not by reading the flags: each CLI decides for
itself what "there is nobody to ask" means, and the four answers are
genuinely different. Every row below was probed. Each worker was asked to
attempt a file read, a shell command, a file write and an MCP call, and to
report each as succeeded or blocked; the results were then checked from
outside, because a worker's account of its own powers is not evidence.

| Agent | File read | Shell execution | File write | Mesh tools | Effective scope |
|---|---|---|---|---|---|
| **claude** | yes | yes | yes | yes | Unrestricted, including outside the pinned workdir |
| **kimi** | yes | yes | yes | yes | Unrestricted, and wider than the same CLI run interactively |
| **codex** | yes | auto-reviewed | auto-reviewed | yes | Approvals go to an automatic reviewer, in a workspace-write sandbox |
| **gemini** | yes | – | – | yes | No shell or write tool exists; read and mesh tools only |

Two of the four can therefore change anything you can, and `--allowedTools`
does not alter that: it marks tools as pre-approved, it does not fence off
the rest. Codex is the only one whose writes are both reviewed and confined
to the workspace. Gemini is the only one that structurally cannot modify
the tree it is reading – which makes the weakest coder of the four the
safest reviewer, and is worth remembering when choosing who to point at a
repository.

One trap when probing this yourself: in the claude worker the mesh tools
are *deferred*, so calling one cold fails with `InputValidationError`
rather than a permission error. That reads exactly like a blocked tool but
is not one – the tool needs loading first.

The `a2a-review` project skill teaches the review choreography on top of
all this.

## Install

```
make install
```

copies the scripts to `~/.local/bin`, installs the unit, and creates
`~/.config/a2a-agents/agents.toml` from the example – only if it does not
exist yet; an existing configuration is never touched. The first daemon start
resolves the Python dependencies (network needed once) and generates the
shared API key.

Because an existing configuration is preserved, a setting can outlive the
release that read it. The daemon logs a line when it finds an obsolete key
rather than ignoring it silently.

## Usage

```
systemctl --user start a2a-agent@{claude,gemini,codex,kimi}
a2a-chat --status
a2a-chat kimi                                  # REPL: /context, /workdir, /new
a2a-chat codex --context review-42 --workdir ~/gits/proj "…"
                                               # one-shot; --workdir pins a
                                               # new context to its project
a2a-post kimi --context slice-a --workdir ~/gits/proj "start the analysis"
                                               # fire and forget: the turn
                                               # runs, the reply is not awaited
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

A context also pins its **working directory**, on its first message and for
life. Several CLIs scope their session stores to the directory they run in,
so moving a conversation would orphan its session. A new context therefore
refuses to start without one: pass it as message metadata
`{"workdir": "/path"}` – via `a2a-chat --workdir` or the REPL's `/workdir`,
the MCP `send` tool's `workdir` argument, or, in A2A Inspector, the
**Message Metadata** field on the chat's first message. Paths are resolved
when pinned, so a relative path or a symlink means the same directory later
that it meant at the time. A different directory means a new context name.

One daemon per agent thus serves conversations in any number of projects
concurrently. Entries created before this scheme adopt the per-agent
`workdir` from the configuration, which is otherwise legacy; where the
configuration sets none they adopt `~`, which is where the pre-pinning
daemon ran them.

## Secrets and security

- **Authentication.** Ports bind to loopback only, and every request must
  carry an `X-API-Key` header matching `~/.config/a2a-agents/api-key`
  (created on first start, mode 0600). Loopback alone is not a boundary on a
  multi-user machine. The one exception is agent-card discovery
  (`/.well-known/agent-card.json`, and the legacy `agent.json`), which is
  deliberately anonymous – the A2A way is a public card that declares the
  required scheme in `securitySchemes`, with authentication enforced on the
  RPC endpoint. This is also what lets tools such as A2A Inspector see the
  agents at all.
- **Secrets.** An agent may be given secrets through `env_from_pass`: each
  entry is resolved with `pass show` per task and injected into that agent's
  environment, so no other agent sees it and nothing is stored or logged.
  Within that agent it reaches the whole process tree – the CLI, its
  subagents, and any MCP server it starts from the project it is working in
  – so a checkout you would not trust with a credential should not be given
  a worker that holds one. This requires the gpg-agent passphrase cache to
  be primed (any `pass show` in an interactive shell does it).
- **Permission modes.** Agents inherit their CLI's default headless
  behaviour, and that behaviour is not uniformly conservative: two of the
  four will run any command you can. See
  [what a headless worker can actually do](#what-a-headless-worker-can-actually-do)
  before deciding which agent may visit which repository; narrow an
  over-powered one through its own `extra_args` or config.
- **Gemini's workspace gate.** The gemini worker refuses a workspace
  outright unless the context's pinned workdir sits inside one of its
  `trusted_workdirs` (each entry trusting its whole subtree). Both sides of
  that test are resolved before comparison, so neither `..` nor a symlink
  can land a worker outside the tree it was cleared for. A trusted directory
  passes `--skip-trust`, which lets that checkout's own `.gemini/`
  configuration run as you – so list only trees whose content you trust.
- **Process visibility.** Prompts are delivered to the CLIs via stdin,
  keeping them out of `/proc/<pid>/cmdline` – except for kimi, which has no
  stdin mode and keeps its prompt visible in argv for the duration of the
  turn.
- **Mailboxes are conventions, not boundaries.** Every agent's tools run as
  the same Unix user, so any worker can drain any box, the human's included,
  and draining is destructive. Box names route mail; they do not protect it.
- **Error signalling.** A worker-side failure comes back as a normal A2A
  message flagged with `metadata.error`; `a2a-chat --send` exits 2 and
  `a2a-mcp send` prefixes the reply with `ERROR`, so scripts and agents can
  tell failures from content.

## Limitations

- **Single-shot responses.** No streaming, no incremental task updates, and
  `tasks/cancel` is not implemented; a slow turn runs until it finishes or
  hits the per-agent timeout.
- **Nothing is pruned.** Log files under `~/.local/state/a2a-agents/` grow
  without rotation, though at a line or two per task this takes a while to
  matter; the same goes for the context→session maps.
- **Sessions can be lost by their owners.** The CLIs own their session
  stores (gemini expires old sessions; kimi and claude scope theirs to a
  directory). When a resume fails for such a reason the daemon starts a
  fresh native session under the same context and logs it – the
  conversation's history is gone, so re-state what matters rather than
  assuming the agent remembers.
- **Unbounded output buffering.** Subprocess output is accumulated in
  memory, so a runaway CLI emitting gigabytes could exhaust its daemon.
- **Mail can still be lost after it is handed over.** A drain gives the
  batch to its consumer before deleting anything, so a broken pipe or a
  dead reader leaves the mail in the box. An MCP tool result lost in
  transit after the call returns is still lost: there is no acknowledgement
  protocol.
- **Port squatting.** Status checks are anonymous, but a `message/send`
  presents the API key to whichever process holds the port. If a daemon is
  down, a local process could bind to its port and collect the key. A
  unix-socket transport would close this hole, and is future work.
- **Turns are serialised per context.** A request sent while its context is
  busy waits in the queue, which can make the client time out even though
  the turn completes on the daemon. Send concurrent work to separate
  contexts, never into one.
