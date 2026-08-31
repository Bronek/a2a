---
name: a2a-review
description: Run a cross-lab review using the local A2A mesh agents (codex, kimi, gemini) as independent reviewers. Use when asked to have the mesh review a repo, branch, or piece of work, or to "ask the other agents" for their opinion on changes.
---

# Cross-lab review over the A2A mesh

Agents from different labs do not share blind spots; their agreement is
strong signal and their disagreement is worth reading. This skill runs a
review round using the mesh daemons as independent reviewers.

## Tools

Prefer the MCP tools if the `a2a` server is loaded: `list_agents()` and
`send(agent, context, message)`. Otherwise the same operations exist as CLI:
`a2a-mcp list` and `a2a-mcp send <agent> <context> <message>`.

A `send` blocks until the agent finishes — often several minutes (codex at
high reasoning effort can take 10+). From the Bash tool, run long sends in
the background and collect results as they complete; never let a foreground
timeout kill a send mid-flight.

## Procedure

1. **Preflight.** `list_agents` — every reviewer you plan to use must be
   "up" (`systemctl --user start a2a-agent@<name>` if not).
2. **Dispatch in parallel.** One fresh context per reviewer, named
   `review-<topic>`; contexts are per-agent, so the same name on both is
   fine. The first message must pin the context's working directory to the
   code under review — `send(..., workdir=...)` / `a2a-chat --workdir`; a
   different directory later needs a new context name. Brief template:

   > Review the repository in your current working directory: README first,
   > then the code. Look for correctness bugs, security weaknesses, and
   > robustness gaps. Report findings as a numbered list — each with
   > file:line, severity (high/medium/low), and a concrete failure
   > scenario. The README's Limitations section lists accepted gaps; flag
   > one only if you disagree with its acceptance. Do not modify anything.

3. **Cross-examine.** Send each reviewer the other's findings (same
   context): "An independent reviewer reported the following on the same
   code. For each item: CONFIRM or REFUTE, one sentence why. Note anything
   it found that you missed."
4. **Triage.** Verify every surviving claim against the code yourself
   before accepting it — reviewers hallucinate line numbers and APIs.
   Confirmed-by-both ≻ confirmed-by-one ≻ disputed. Reject with a written
   reason rather than silently dropping.
5. **Report — and fix only when asked.** Present the triage: accepted,
   rejected, each with a reason. When the user's request included fixing
   or improving, apply the accepted fixes and commit, ending with exactly
   `Assisted-by: Claude:<model-id>` and no other AI trailer — the user
   signs and pushes themselves. When the request was review-only, stop at
   the report. Either way, offer a re-review of the resulting diff to the
   reviewer that raised each finding.

## Caveats

- Never `send` to the **claude** worker on a context whose mapping in
  `~/.local/state/a2a-agents/claude.json` points at a live interactive
  session — two writers, one transcript.
- Reviewers run in their CLIs' default (read-only-ish) permission modes;
  that is correct for review. Do not loosen modes just to make a reviewer
  able to "fix things itself".
- A context is a persistent conversation: reuse it for follow-ups, burn it
  (fresh name) when history would bias a second opinion.
