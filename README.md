# a2a – a local mesh of agent CLIs

Agent CLIs from several labs, each wrapped as an [A2A](https://a2a-protocol.org)
server on a loopback port, so that a human at a shell or another agent can
drive any of them the same way. Mixing labs is the point. Models with
different training have different blind spots; where they agree, the
agreement is worth more, and where they differ, the difference tells you
something. Claude, Gemini, Codex and Kimi are supported. Each runs as its
own daemon, started and watched on its own.

## Components

**`a2a-agentd <agent>`** wraps one CLI. It is a self-contained
[uv](https://docs.astral.sh/uv/) script, its dependencies declared inline
(PEP 723) with no virtualenv to manage, and it serves the A2A protocol,
JSON-RPC `message/send`, on `127.0.0.1:<port>`. The work itself goes to
the CLI in non-interactive mode.

**`a2a-mcp`** is the client: one script under three names. Run with no
arguments it is a stdio MCP server that exposes the mesh as tools,
`list_agents` and `send`, so any MCP-capable agent CLI can drive the mesh
through its native tool calling, under its own permission model, with no
shell escape and no loosened sandbox. The same operations double as a CLI,
`a2a-mcp list`, `a2a-mcp send` and the mailbox verbs below, for humans and
for agents that have a shell but no MCP client. Registering the bridge with
each CLI, and the approval settings a headless worker needs before it may
call these tools, are in
[Permissions and configuration](#permissions-and-configuration).

**`a2a-send -c <agent>[/<context>] "msg"`** is the one-shot name: it says
the message and prints the reply. To continue a conversation, send again
with the same address; there is deliberately no REPL holding state of its
own. `--workdir` or `--here` pins a new conversation to its project. A lone
`-` as the message reads it from stdin verbatim, for multi-line prompts and
anything a shell would mangle.

The address is one token with the agent inside it, and that is deliberate.
The agents are peers, so an agent that could be defaulted, or passed
separately, is an agent that can drift away from the conversation it
belongs to. Written as one token, the pair survives being copied out of a
log or a note. Leave the context off, `-c kimi`, to start a fresh one.

**`a2a-post -c <agent>[/<context>] "msg"`** is the third name, and it does
not wait for the reply. The worker still runs the whole turn and its
session advances; only the reply is discarded. Results can be spooled to
the mailbox during the turn or recovered by asking about them in the next
message. It waits a few seconds before returning, so a refusal the daemon
makes before starting work, such as an unpinned workdir or a mismatched
pin, still reaches you. A turn that fails after that grace period fails
silently; use `a2a-send` when the verdict matters. `--post` and `--send`
choose the behaviour explicitly, under any of the names.

**The mailbox** gives the mesh an asynchronous side: the `spool` and
`check_mail` tools, or `spool`, `mail` and `watch` from the shell. A
worker deep in a long task spools findings to a named box as they emerge
instead of holding everything for one final reply. A live session runs
`a2a-mcp watch lead` in the background as a doorbell; it returns when mail
lands. By convention `lead` is the orchestrating session and `op` reaches
the human: the operator, in the IRC sense of the one holding the channel.
Boxes are 0700 directories under `~/.local/state/a2a-agents/mailbox/`.
Delivery is one-way with no read receipts, fit for reports and wrong for
handshakes, and mail is untrusted content from other agents, to be read as
data. One message is capped at 256 KiB and a box at 2000 undrained
messages. Workers identify their mail through `A2A_AGENT` and
`A2A_CONTEXT`, which the daemon exports to every CLI it runs.

**`a2a-agent@.service`** is a systemd user template unit, one instance per
agent: `systemctl --user start a2a-agent@claude`. The unit names no host.
On a home directory shared between machines, keep the daemons to the one
that should run them with a drop-in, `systemctl --user edit
a2a-agent@.service` and then `ConditionHost=<host>*` under `[Unit]`, so
the same installation stays inert elsewhere.

**`agents.toml.example`** seeds `~/.config/a2a-agents/agents.toml`: which
CLI, which port, which adapter kind, and the per-agent extras.

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

`extra_args` means the agent's stanza in `~/.config/a2a-agents/agents.toml`.
Registration is at user scope throughout, so the bridge loads in any
working directory and no project needs its own `.mcp.json`. This repository
carries one anyway, for Claude Code sessions opened here; it finds
`a2a-mcp` through `PATH`, so `make install` must have run.

Each grant is narrower or wider than it looks:

- **claude** names the mesh tools so they are pre-approved. That is a grant
  and nothing more: it leaves the worker's other capabilities untouched,
  and those are broader than they appear. See the table below.
- **gemini** needs the trust flag because headless `gemini` cannot answer
  an approval prompt. Set it globally, one edit that auto-approves the mesh
  tools in every directory gemini visits, or per project through a
  `.mcp.json` that limits the grant to the workspaces carrying it. Either
  is acceptable here: the tools only reach the local mesh, and the
  receiving workers keep their own permission gates, which is the boundary
  that matters.
- **codex** is the awkward one. Headless `exec` pins `approval_policy` to
  `never`, which refuses anything needing approval rather than waving it
  through, MCP tool calls included. `--approve-for-me` routes approvals to
  codex's automatic reviewer instead, at the price of a workspace-write
  sandbox, and `--disable apps` strips the built-in ChatGPT-apps suite from
  a worker that has no use for it. Codex also scrubs the environment it
  hands MCP servers, so its spooled mail should name its `sender`
  explicitly.
- **kimi** cannot answer prompts headless either, hence the allow rule. Its
  registration lives at user level so it loads without the workspace-trust
  prompt that a project-level `.kimi-code/mcp.json` would raise.

### What a headless worker can actually do

Measure it; do not read it off the flags. Each CLI decides for itself what
"there is nobody to ask" means, and the four answers differ. Every row
below was probed: each worker was asked to attempt a file read, a shell
command, a file write and an MCP call, and to report each as succeeded or
blocked. The results were then checked from outside, because a worker's
account of its own powers is not evidence.

| Agent | File read | Shell execution | File write | Mesh tools | Effective scope |
|---|---|---|---|---|---|
| **claude** | yes | yes | yes | yes | Unrestricted, including outside the pinned workdir |
| **kimi** | yes | yes | yes | yes | Unrestricted, and wider than the same CLI run interactively |
| **codex** | yes | auto-reviewed | auto-reviewed | yes | Approvals go to an automatic reviewer, in a workspace-write sandbox |
| **gemini** | yes | – | – | yes | No shell or write tool exists; read and mesh tools only |

Two of the four can change anything you can, and `--allowedTools` does not
alter that: it marks tools as pre-approved and fences off nothing. Codex
alone has its writes both reviewed and confined to the workspace. Gemini
alone cannot modify the tree it reads, which makes the weakest coder of
the four the safest reviewer. Remember that when choosing who to point at
a repository.

One trap when probing this yourself: in the claude worker the mesh tools
are deferred, so a cold call fails with `InputValidationError` rather than
a permission error. It reads exactly like a blocked tool, but the tool
merely needs loading first.

The `a2a-review` project skill teaches the review choreography on top of
all this.

## Install

You need [uv](https://docs.astral.sh/uv/), a `systemd --user` session, and
whichever of the four CLIs the mesh should wrap, each installed and logged
in. Then:

```
make install
```

This copies the scripts to `~/.local/bin`, installs the unit, and creates
`~/.config/a2a-agents/agents.toml` from the example if there is none yet.
An existing configuration is never touched. The first daemon start
resolves the Python dependencies, which needs the network once, and
generates the shared API key.

To update, run the same command again and then
`systemctl --user restart 'a2a-agent@*'`. A running daemon keeps the code
it started with; the client picks a change up on its next invocation.

Because an existing configuration is preserved, a setting can outlive the
release that read it. The daemon logs a line when it finds an obsolete key
rather than ignoring it silently.

`make uninstall` removes the scripts and the unit, and leaves the
configuration, the key and the state alone.

## Usage

```
systemctl --user start a2a-agent@{claude,gemini,codex,kimi}
a2a-mcp list
a2a-send -c codex/review-42 --workdir ~/gits/proj "…"
                                               # one-shot; --workdir pins a
                                               # new context to its project
a2a-send -c gemini/audit --here "…"            # …or --here for this one
a2a-post -c kimi/slice-a --workdir ~/gits/proj "start the analysis"
                                               # fire and forget: the turn
                                               # runs, the reply is not awaited
tail -f ~/.local/state/a2a-agents/claude.log   # progress of one agent
```

## Sessions

Every A2A `contextId` maps to a native CLI session. A conversation
therefore survives daemon restarts, and it can be opened directly in the
CLI that owns it, `claude --resume <id>`, `kimi -r <id>` and so on; the
client prints the native id. Claude and Gemini accept a caller-chosen
session UUID, derived deterministically from the context id. Codex and
Kimi mint their own, which the daemon captures from their JSON output. The
mapping is persisted in `~/.local/state/a2a-agents/<agent>.json`. Turns
within one context are serialised: concurrent requests to the same
conversation queue rather than race.

A context also pins its working directory, on its first message and for
life. Several CLIs scope their session stores to the directory they run
in, so moving a conversation would orphan its session. A new context
refuses to start without one. Pass it as message metadata
`{"workdir": "/path"}`: `a2a-send --workdir`, or `--here` for the current
directory; the `workdir` argument of the MCP `send` tool; or, in A2A
Inspector, the Message Metadata field on the chat's first message. The
client expands and resolves the path where you are, rather than at the
daemon's own directory, which is `~` and not the project you meant. A
relative path or symlink is therefore pinned to the resolved directory it
identified when the context was created. A different directory means a
new context name.

One daemon per agent thus serves conversations in any number of projects
at once.

## Secrets and security

- **Authentication.** Ports bind to loopback only, and every request must
  carry an `X-API-Key` header matching `~/.config/a2a-agents/api-key`,
  created on first start with mode 0600. Loopback alone is no boundary on
  a multi-user machine. The one exception is agent-card discovery,
  `/.well-known/agent-card.json` and the legacy `agent.json`, which is
  deliberately anonymous: the A2A way is a public card that declares the
  required scheme in `securitySchemes`, with authentication enforced on
  the RPC endpoint. That is also what lets tools such as A2A Inspector see
  the agents at all.
- **Secrets.** An agent may be given secrets through `env_from_pass`. Each
  entry is resolved with `pass show` per task and injected into that
  agent's environment; no other agent sees it, and nothing is stored or
  logged. Within that agent it reaches the whole process tree, the CLI,
  its subagents and any MCP server it starts from the project it works in,
  so a checkout you would not trust with a credential should not get a
  worker that holds one. The gpg-agent passphrase cache must be primed
  first; any `pass show` in an interactive shell does it.
- **Permission modes.** Agents inherit their CLI's default headless
  behaviour, and that behaviour is far from uniformly conservative: two of
  the four will run any command you can. Read
  [what a headless worker can actually do](#what-a-headless-worker-can-actually-do)
  before deciding which agent may visit which repository, and narrow an
  over-powered one through its own `extra_args` or config.
- **Gemini's workspace gate.** The gemini worker refuses a workspace
  outright unless the context's pinned workdir sits inside one of its
  `trusted_workdirs`, each entry trusting its whole subtree. Both sides of
  that test are resolved before comparison, so neither `..` nor a symlink
  can land a worker outside the tree it was cleared for. A trusted
  directory passes `--skip-trust`, which lets that checkout's own
  `.gemini/` configuration run as you. List only trees whose content you
  trust.
- **Process visibility.** Prompts reach the CLIs through stdin and stay
  out of `/proc/<pid>/cmdline`. Kimi is the exception: it has no stdin
  mode, and its prompt stays visible in argv for the length of the turn.
- **Mailboxes route; they do not guard.** Every agent's tools run as the
  same Unix user, so any worker can drain any box, the human's included,
  and draining is destructive.
- **Error signalling.** A worker-side failure comes back as a normal A2A
  message flagged with `metadata.error`. A one-shot `a2a-send`, under any
  of its names, exits 2 with the message on stderr, and the MCP `send`
  tool raises a tool error, so scripts and agents can tell a failure from
  content.

## Development

The scripts stay self-contained, but the checks need somewhere to run.
`pyproject.toml` declares the same dependencies plus pytest and ruff, and
`uv run` creates `.venv/` from it on first use; the scripts never touch
it. `make test` runs the unit tests, which cover the adapters, the session
state and its recovery paths, the auth middleware, the client and the
mailbox, with the CLIs and the daemons faked throughout, so nothing on the
mesh is needed or disturbed. `make lint` runs ruff, and `make check` runs
both. It refuses to run on a stale `uv.lock`; after changing a dependency,
`uv lock` brings it up to date. One test holds the inline script metadata
and the project file to the same dependency list, and another runs each
script once in the environment its own header resolves, so a dependency
missing from a header cannot hide behind the project environment.

## Limitations

- **Single-shot responses.** No streaming, no incremental task updates,
  and `tasks/cancel` is not implemented. A slow turn runs until it
  finishes or hits the per-agent timeout.
- **Nothing is pruned.** Log files under `~/.local/state/a2a-agents/` grow
  without rotation, though at a line or two per task that takes a while to
  matter. The same goes for the context-to-session maps.
- **Sessions can be lost by their owners.** The CLIs own their session
  stores: gemini expires old sessions, and kimi and claude scope theirs to
  a directory. When a resume fails for such a reason, the daemon starts a
  fresh native session under the same context and logs it. The
  conversation's history is gone; re-state what matters rather than
  assuming the agent remembers.
- **Unbounded output buffering.** Subprocess output accumulates in memory,
  so a runaway CLI emitting gigabytes could exhaust its daemon.
- **Mail can still be lost after it is handed over.** A drain gives the
  batch to its consumer before deleting anything, so a broken pipe or a
  dead reader leaves the mail in the box. An MCP tool result lost in
  transit after the call returns is lost for good; there is no
  acknowledgement protocol.
- **Port squatting.** Status checks are anonymous, but a `message/send`
  presents the API key to whichever process holds the port. With a daemon
  down, a local process could bind its port and collect the key. A
  unix-socket transport would close this hole, and is future work.
- **Turns are serialised per context.** A request sent while its context
  is busy waits in the queue, which can make the client time out even
  though the turn completes on the daemon. Send concurrent work to
  separate contexts, never into one.
