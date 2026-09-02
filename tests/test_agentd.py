"""a2a-agentd: the CLI adapters, the session state, the auth middleware.

No CLI is ever run: _invoke is replaced by a scripted fake, so the turn
logic — pinning, the two session-recovery retries, error reporting — is
exercised against exactly the process outcomes each case needs.
"""

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

# --- CliAdapter.build ------------------------------------------------------


def adapter(agentd, kind, extra=()):
    return agentd.CliAdapter(kind, kind, list(extra))


def test_claude_argv_new_and_resume(agentd):
    a = adapter(agentd, "claude", ["--model", "m"])
    argv, stdin = a.build("hello", "sid", resume=False, lastmsg="/lm")
    assert argv == ["claude", "-p", "--output-format", "json",
                    "--session-id", "sid", "--model", "m"]
    assert stdin == "hello"
    argv, _ = a.build("hello", "sid", resume=True, lastmsg="/lm")
    assert argv[4:6] == ["--resume", "sid"]


def test_gemini_argv_trust_and_empty_prompt(agentd):
    a = adapter(agentd, "gemini", ["-m", "pro"])
    argv, stdin = a.build("hello", "sid", resume=False, lastmsg="/lm",
                          trusted=True)
    assert argv == ["gemini", "-o", "json", "--skip-trust",
                    "--session-id", "sid", "-m", "pro", "-p", ""]
    assert stdin == "hello"
    argv, _ = a.build("hello", "sid", resume=True, lastmsg="/lm")
    assert "--skip-trust" not in argv
    assert argv[3:5] == ["--resume", "sid"]


def test_codex_extra_args_precede_the_subcommand(agentd):
    a = adapter(agentd, "codex", ["--approve-for-me"])
    argv, stdin = a.build("hello", None, resume=False, lastmsg="/lm")
    assert argv == ["codex", "--approve-for-me", "exec", "--json",
                    "--skip-git-repo-check", "--output-last-message", "/lm",
                    "-"]
    assert stdin == "hello"
    argv, _ = a.build("hello", "tid", resume=True, lastmsg="/lm")
    assert argv[:4] == ["codex", "--approve-for-me", "exec", "resume"]
    assert argv[-2:] == ["tid", "-"]


def test_kimi_prompt_travels_in_argv(agentd):
    a = adapter(agentd, "kimi")
    argv, stdin = a.build("hello", None, resume=False, lastmsg="/lm")
    assert argv == ["kimi", "--output-format", "stream-json", "-p", "hello"]
    assert stdin is None
    argv, _ = a.build("hello", "s1", resume=True, lastmsg="/lm")
    assert argv[1:3] == ["-r", "s1"]


def test_prompts_go_through_stdin_except_for_kimi(agentd):
    for kind in ("claude", "gemini", "codex"):
        argv, stdin = adapter(agentd, kind).build("secret", "s", False, "/lm")
        assert stdin == "secret" and "secret" not in argv


def test_unknown_kind_is_refused_at_construction(agentd):
    with pytest.raises(ValueError, match="unknown adapter kind 'llama'"):
        adapter(agentd, "llama")


def test_who_chooses_the_session_id(agentd):
    assert not adapter(agentd, "claude").chooses_own_id()
    assert not adapter(agentd, "gemini").chooses_own_id()
    assert adapter(agentd, "codex").chooses_own_id()
    assert adapter(agentd, "kimi").chooses_own_id()


# --- CliAdapter.parse ------------------------------------------------------


def test_parse_claude(agentd):
    out = json.dumps({"result": "hi", "session_id": "s1", "is_error": False})
    assert adapter(agentd, "claude").parse(out, "/lm") == ("hi", "s1")


def test_parse_gemini_both_shapes(agentd):
    a = adapter(agentd, "gemini")
    out = json.dumps({"response": "hi", "sessionId": "s1"})
    assert a.parse(out, "/lm") == ("hi", "s1")
    out = json.dumps({"result": "hi", "stats": {"sessionId": "s2"}})
    assert a.parse(out, "/lm") == ("hi", "s2")


def test_parse_codex_prefers_the_last_message_file(agentd, tmp_path):
    a = adapter(agentd, "codex")
    out = "\n".join([
        "not json at all",
        json.dumps({"type": "thread.started", "thread_id": "t1"}),
        json.dumps({"item": {"type": "agent_message", "text": "first"}}),
        json.dumps({"item": {"type": "reasoning", "text": "hidden"}}),
        json.dumps({"item": {"type": "agent_message", "text": "second"}}),
    ])
    assert a.parse(out, str(tmp_path / "absent")) == ("first\nsecond", "t1")
    lm = tmp_path / "last.txt"
    lm.write_text("the final answer\n")
    assert a.parse(out, str(lm)) == ("the final answer", "t1")


def test_parse_kimi(agentd):
    out = "\n".join([
        json.dumps({"role": "assistant", "content": "part one"}),
        json.dumps({"role": "assistant", "content": [{"type": "tool_use"}]}),
        json.dumps({"role": "meta", "type": "session.resume_hint",
                    "session_id": "session_1"}),
        json.dumps({"role": "assistant", "content": "part two"}),
    ])
    assert adapter(agentd, "kimi").parse(out, "/lm") == (
        "part one\npart two", "session_1")


# --- CliAdapter.error_hint -------------------------------------------------


def test_error_hint_mines_structured_output(agentd):
    a = adapter(agentd, "codex")
    out = "\n".join([
        "plain text",
        json.dumps({"type": "error", "message": "first"}),
        json.dumps({"error": {"message": "nested"}}),
        json.dumps({"error": "usage limit hit"}),
        json.dumps([1, 2]),
    ])
    assert a.error_hint(out) == "usage limit hit"
    assert a.error_hint("") == ""


def test_error_hint_cli_only_ignores_model_text(agentd):
    a = adapter(agentd, "claude")
    out = json.dumps({"is_error": True, "result": "no conversation found"})
    assert a.error_hint(out) == "no conversation found"
    assert a.error_hint(out, cli_only=True) == ""


# --- CliAgentExecutor: state -----------------------------------------------

CFG = {"kind": "claude", "port": 1}


def executor(agentd, cfg=None):
    return agentd.CliAgentExecutor("claude", CFG | (cfg or {}))


def test_fresh_state(agentd, home):
    ex = executor(agentd)
    assert ex.sessions == {}
    assert not ex.state_file.exists()


def test_wrong_shape_state_is_set_aside(agentd, home, capsys):
    state = agentd.STATE_DIR / "claude.json"
    state.parent.mkdir(parents=True)
    state.write_text("[1, 2]")
    ex = executor(agentd)
    assert ex.sessions == {}
    [aside] = agentd.STATE_DIR.glob("claude.corrupt-*")
    assert aside.read_text() == "[1, 2]"
    assert not state.exists()
    assert "unusable" in capsys.readouterr().out


def test_malformed_entries_are_dropped_with_evidence(agentd, home, capsys):
    state = agentd.STATE_DIR / "claude.json"
    state.parent.mkdir(parents=True)
    kept = {"good": {"session": "s", "workdir": "/w"},
            "fresh": {"session": None, "workdir": "/w"}}
    state.write_text(json.dumps(kept | {
        "bare": "legacy-session-id",
        "wedge": {"session": 42, "workdir": "/w"},
        "nosession": {"workdir": "/w"},
    }))
    ex = executor(agentd)
    assert ex.sessions == kept
    assert json.loads(state.read_text()) == kept  # flushed eagerly
    [aside] = agentd.STATE_DIR.glob("claude.dropped-*")
    assert set(json.loads(aside.read_text())) == {"bare", "wedge", "nosession"}
    assert aside.stat().st_mode & 0o777 == 0o600
    assert "dropped 3 malformed state entries" in capsys.readouterr().out


def test_obsolete_config_keys_are_named(agentd, home, capsys):
    executor(agentd, {"trust_workspace": True, "workdir": "~"})
    out = capsys.readouterr().out
    assert "trust_workspace is obsolete" in out
    assert "workdir is obsolete" in out


def test_save_state_is_private_and_atomic(agentd, home):
    ex = executor(agentd)
    ex.sessions["c"] = {"session": "s", "workdir": "/w"}
    ex.save_state()
    assert json.loads(ex.state_file.read_text()) == ex.sessions
    assert ex.state_file.stat().st_mode & 0o777 == 0o600
    assert agentd.STATE_DIR.stat().st_mode & 0o777 == 0o700
    assert not ex.state_file.with_suffix(".tmp").exists()


# --- CliAgentExecutor: the workdir pin -------------------------------------


@pytest.fixture
def dirs(home):
    real = home / "proj"
    real.mkdir()
    (home / "link").symlink_to(real)
    other = home / "other"
    other.mkdir()
    return SimpleNamespace(real=real.resolve(), link=home / "link",
                           other=other.resolve())


def test_pin_requires_a_workdir_on_first_use(agentd, home):
    with pytest.raises(RuntimeError, match="no pinned workdir"):
        executor(agentd)._pin_workdir("c", None, None)


@pytest.mark.parametrize("bad", ["", "   ", 42, ["x"]])
def test_pin_rejects_a_non_string_workdir(agentd, home, bad):
    with pytest.raises(RuntimeError, match="non-empty string"):
        executor(agentd)._pin_workdir("c", None, bad)


def test_pin_rejects_a_missing_directory(agentd, home):
    with pytest.raises(RuntimeError, match="not an existing directory"):
        executor(agentd)._pin_workdir("c", None, str(home / "nope"))


def test_pin_resolves_symlinks_tilde_and_relative(agentd, dirs, monkeypatch):
    ex = executor(agentd)
    assert ex._pin_workdir("c", None, str(dirs.link)) == str(dirs.real)
    assert ex._pin_workdir("c", None, "~/proj") == str(dirs.real)
    monkeypatch.chdir(dirs.real)
    assert ex._pin_workdir("c", None, ".") == str(dirs.real)


def test_pinned_context_keeps_its_directory(agentd, dirs):
    ex = executor(agentd)
    entry = {"session": "s", "workdir": str(dirs.real)}
    assert ex._pin_workdir("c", entry, None) == str(dirs.real)
    assert ex._pin_workdir("c", entry, str(dirs.link)) == str(dirs.real)
    with pytest.raises(RuntimeError, match="is pinned to workdir"):
        ex._pin_workdir("c", entry, str(dirs.other))


# --- CliAgentExecutor: turns ----------------------------------------------


class FakeCli:
    """Scripted stand-in for CliAgentExecutor._invoke: each call pops the
    next (rc, stdout, stderr) — or a callable producing one from the call —
    and records what it was asked to run."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    async def __call__(self, cmd, stdin_text, env, timeout, cwd):
        call = SimpleNamespace(cmd=cmd, stdin=stdin_text, env=env,
                               timeout=timeout, cwd=cwd)
        self.calls.append(call)
        result = self.results.pop(0)
        return result(call) if callable(result) else result


def claude_ok(text, sid):
    return 0, json.dumps({"result": text, "session_id": sid}), ""


def run(ex, ctx, prompt, workdir=None):
    return asyncio.run(ex.run_turn(ctx, prompt, workdir))


DETERMINISTIC = str(uuid.uuid5(uuid.NAMESPACE_URL, "a2a://claude/ctx"))


def test_first_turn_pins_and_records(agentd, dirs):
    ex = executor(agentd, {"extra_args": ["--x"]})
    ex._invoke = FakeCli(claude_ok("hi", DETERMINISTIC))
    assert run(ex, "ctx", "hello", str(dirs.link)) == ("hi", DETERMINISTIC)
    [call] = ex._invoke.calls
    assert call.cmd == ["claude", "-p", "--output-format", "json",
                        "--session-id", DETERMINISTIC, "--x"]
    assert call.stdin == "hello" and call.cwd == str(dirs.real)
    assert call.env["A2A_AGENT"] == "claude"
    assert call.env["A2A_CONTEXT"] == "ctx"
    assert ex.sessions["ctx"] == {"session": DETERMINISTIC,
                                  "workdir": str(dirs.real)}
    assert json.loads(ex.state_file.read_text()) == ex.sessions


def test_second_turn_resumes(agentd, dirs):
    ex = executor(agentd)
    ex.sessions["ctx"] = {"session": "s1", "workdir": str(dirs.real)}
    ex._invoke = FakeCli(claude_ok("again", "s1"))
    assert run(ex, "ctx", "more") == ("again", "s1")
    assert ex._invoke.calls[0].cmd[4:6] == ["--resume", "s1"]


def test_create_collision_retries_as_resume(agentd, dirs):
    ex = executor(agentd)
    ex._invoke = FakeCli((1, "", "Error: Session ID x is already in use"),
                         claude_ok("hi", DETERMINISTIC))
    assert run(ex, "ctx", "hello", str(dirs.real)) == ("hi", DETERMINISTIC)
    first, second = ex._invoke.calls
    assert first.cmd[4] == "--session-id"
    assert second.cmd[4:6] == ["--resume", DETERMINISTIC]


def test_lost_session_starts_a_fresh_one(agentd, dirs, capsys):
    ex = executor(agentd)
    ex.sessions["ctx"] = {"session": "old", "workdir": str(dirs.real)}
    ex._invoke = FakeCli((1, "", "No conversation found with session ID: old"),
                         lambda call: claude_ok("fresh", call.cmd[5]))
    text, native = run(ex, "ctx", "hello")
    assert text == "fresh"
    second = ex._invoke.calls[1]
    assert second.cmd[4:6] == ["--session-id", native]
    uuid.UUID(native)
    assert native not in ("old", DETERMINISTIC)  # genuinely fresh
    assert ex.sessions["ctx"]["session"] == native
    assert "starting a fresh native session" in capsys.readouterr().out


def test_model_text_cannot_orphan_a_session(agentd, dirs):
    """The retry matcher reads the CLI's own error fields only: a failed
    turn whose reply quotes the magic phrase must not repoint the context."""
    ex = executor(agentd)
    ex.sessions["ctx"] = {"session": "old", "workdir": str(dirs.real)}
    quoted = json.dumps({"is_error": True,
                         "result": "No conversation found with session ID"})
    ex._invoke = FakeCli((1, quoted, ""))
    with pytest.raises(RuntimeError, match=r"exited 1 .*No conversation"):
        run(ex, "ctx", "hello")
    assert len(ex._invoke.calls) == 1
    assert ex.sessions["ctx"]["session"] == "old"


def test_a_bare_not_found_does_not_repoint(agentd, dirs):
    ex = executor(agentd)
    ex.sessions["ctx"] = {"session": "old", "workdir": str(dirs.real)}
    ex._invoke = FakeCli((1, "", "file not found: foo.txt"))
    with pytest.raises(RuntimeError, match="file not found"):
        run(ex, "ctx", "hello")
    assert len(ex._invoke.calls) == 1
    assert ex.sessions["ctx"]["session"] == "old"


def test_failure_reports_the_structured_reason(agentd, dirs):
    """codex reports a usage limit as a JSON event on stdout, with an empty
    stderr; the daemon used to report that as exited 1: ''."""
    ex = executor(agentd, {"kind": "codex"})
    ex._invoke = FakeCli((1, json.dumps({"type": "error",
                                         "message": "usage limit hit"}), ""))
    with pytest.raises(RuntimeError, match=r"exited 1 .*usage limit hit"):
        run(ex, "ctx", "hello", str(dirs.real))


def test_unparseable_output_is_reported_and_the_pin_kept(agentd, dirs):
    ex = executor(agentd)
    ex._invoke = FakeCli((0, "not json", ""))
    with pytest.raises(RuntimeError, match="cannot parse claude output"):
        run(ex, "ctx", "hello", str(dirs.real))
    # Recorded before the turn ran, so the context is not left unpinned.
    assert ex.sessions["ctx"]["workdir"] == str(dirs.real)


def test_pin_mismatch_is_refused_before_the_cli_runs(agentd, dirs):
    ex = executor(agentd)
    ex.sessions["ctx"] = {"session": "s", "workdir": str(dirs.real)}
    ex._invoke = FakeCli()
    with pytest.raises(RuntimeError, match="is pinned to workdir"):
        run(ex, "ctx", "hello", str(dirs.other))
    assert ex._invoke.calls == []


def test_gemini_is_trusted_only_inside_trusted_workdirs(agentd, dirs):
    ex = agentd.CliAgentExecutor("gemini", {
        "kind": "gemini", "port": 1, "trusted_workdirs": [str(dirs.link)]})
    ex._invoke = FakeCli(
        (0, json.dumps({"response": "a", "sessionId": "s"}), ""),
        (0, json.dumps({"response": "b", "sessionId": "s"}), ""))
    run(ex, "inside", "hello", str(dirs.real / "."))
    run(ex, "outside", "hello", str(dirs.other))
    inside, outside = ex._invoke.calls
    assert "--skip-trust" in inside.cmd
    assert "--skip-trust" not in outside.cmd


# --- CliAgentExecutor._invoke, for real -----------------------------------


def alive(pid: int) -> bool:
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except FileNotFoundError:
        return False
    return "State:\tZ" not in status


def test_invoke_captures_both_streams(agentd, home):
    rc, out, err = asyncio.run(executor(agentd)._invoke(
        ["sh", "-c", "cat; echo oops >&2; exit 3"], "fed in",
        os.environ.copy(), 10, str(home)))
    assert (rc, out, err) == (3, "fed in", "oops\n")


def test_invoke_timeout_kills_the_whole_process_group(agentd, home):
    pidfile = home / "pid"
    cmd = ["sh", "-c", f"sleep 30 & echo $! > {pidfile}; wait"]
    with pytest.raises(RuntimeError, match="timed out after 0s"):
        asyncio.run(executor(agentd)._invoke(cmd, None, os.environ.copy(),
                                             0.5, str(home)))
    grandchild = int(pidfile.read_text())
    for _ in range(40):
        if not alive(grandchild):
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"grandchild {grandchild} survived the timeout")


# --- CliAgentExecutor.execute: the A2A envelope ---------------------------


def a2a_call(ex, metadata=None):
    context = SimpleNamespace(
        context_id="ctx", task_id="t1",
        message=SimpleNamespace(metadata=metadata),
        get_user_input=lambda: "hello")
    events = []

    async def enqueue(event):
        events.append(event)

    asyncio.run(ex.execute(context, SimpleNamespace(enqueue_event=enqueue)))
    [msg] = events
    assert (msg.context_id, msg.task_id) == ("ctx", "t1")
    return msg.parts[0].root.text, msg.metadata


def test_execute_reports_a_refusal_in_band(agentd, home, capsys):
    text, meta = a2a_call(executor(agentd))
    assert meta == {"agent": "claude", "error": True}
    assert text.startswith("ERROR from claude worker: context 'ctx' has no "
                           "pinned workdir")
    assert "FAILED" in capsys.readouterr().out


def test_execute_carries_the_native_session(agentd, dirs):
    ex = executor(agentd)

    async def turn(ctx_id, prompt, req_workdir):
        return f"re: {prompt}", "n1"

    ex.run_turn = turn
    text, meta = a2a_call(ex, {"workdir": str(dirs.real)})
    assert (text, meta) == ("re: hello",
                            {"agent": "claude", "native_session_id": "n1"})


# --- KeyAuth ---------------------------------------------------------------


class Inner:
    def __init__(self):
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        await send({"type": "http.response.start", "status": 200,
                    "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def asgi(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def http(method, path, key=None, header=b"x-api-key"):
    headers = [(b"host", b"127.0.0.1")]
    if key is not None:
        headers.append((header, key))
    return {"type": "http", "method": method, "path": path,
            "headers": headers}


def test_rpc_requires_the_key(agentd):
    inner = Inner()
    app = agentd.KeyAuth(inner, "sekrit")
    for scope in (http("POST", "/"), http("POST", "/", b"wrong"),
                  http("POST", "/", b""), http("GET", "/")):
        [start, body] = asgi(app, scope)
        assert start["status"] == 401
        assert b"X-API-Key" in body["body"]
    assert inner.scopes == []
    [start, _] = asgi(app, http("POST", "/", b"sekrit", b"X-API-Key"))
    assert start["status"] == 200
    assert len(inner.scopes) == 1


def test_agent_card_is_anonymous(agentd):
    inner = Inner()
    app = agentd.KeyAuth(inner, "sekrit")
    for path in agentd.KeyAuth.OPEN_PATHS:
        assert asgi(app, http("GET", path))[0]["status"] == 200
    assert len(inner.scopes) == 2
    assert asgi(app, http("POST", path))[0]["status"] == 401


def test_non_http_is_denied_and_lifespan_passes(agentd):
    inner = Inner()
    app = agentd.KeyAuth(inner, "sekrit")
    assert asgi(app, {"type": "websocket", "path": "/"}) == [
        {"type": "websocket.close", "code": 1008}]
    assert inner.scopes == []
    asgi(app, {"type": "lifespan"})
    assert inner.scopes == [{"type": "lifespan"}]


# --- the API key and pass(1) ----------------------------------------------


def test_key_is_created_private_and_reused(agentd, home):
    key = agentd.load_or_create_key()
    assert len(key) == 64 and int(key, 16)
    assert agentd.KEY_FILE.stat().st_mode & 0o777 == 0o600
    assert agentd.load_or_create_key() == key
    assert list(agentd.KEY_FILE.parent.glob(".api-key.*")) == []


def test_short_key_is_refused(agentd, home):
    agentd.KEY_FILE.parent.mkdir(parents=True)
    agentd.KEY_FILE.write_text("\n")
    with pytest.raises(SystemExit, match="empty or too short"):
        agentd.load_or_create_key()


def test_pass_env(agentd, monkeypatch):
    def fake_pass(argv, **kw):
        entry = argv[2]
        if entry == "bad":
            return subprocess.CompletedProcess(argv, 1, "", "not in store\n")
        secret = "" if entry == "empty" else f"secret-for-{entry}\nextra\n"
        return subprocess.CompletedProcess(argv, 0, secret, "")

    monkeypatch.setattr(agentd.subprocess, "run", fake_pass)
    assert agentd.resolve_pass_env({"TOKEN": "gh"}) == {
        "TOKEN": "secret-for-gh"}
    with pytest.raises(RuntimeError, match="not in store"):
        agentd.resolve_pass_env({"TOKEN": "bad"})
    with pytest.raises(RuntimeError, match="is empty"):
        agentd.resolve_pass_env({"TOKEN": "empty"})


# --- main ------------------------------------------------------------------


def test_main_usage(agentd, mesh, monkeypatch):
    for argv in (["a2a-agentd"], ["a2a-agentd", "llama"],
                 ["a2a-agentd", "kimi", "extra"]):
        monkeypatch.setattr(sys, "argv", argv)
        with pytest.raises(SystemExit, match="usage: a2a-agentd <agent>"):
            agentd.main()


def test_main_refuses_a_bad_configuration(agentd, home, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["a2a-agentd", "kimi"])
    with pytest.raises(SystemExit, match="not found — `make install`"):
        agentd.main()
    agentd.CONFIG_FILE.parent.mkdir(parents=True)
    for text, message in [
        ("[kimi]\nkind = \n", "agents.toml: "),
        ("[kimi]\nkind = \"kimi\"\n", r"\[kimi\] has no port"),
        ("[kimi]\nport = 1\n", r"\[kimi\] has no kind"),
        ("[kimi]\nkind = \"llama\"\nport = 1\n",
         r"\[kimi\] unknown adapter kind 'llama'"),
    ]:
        agentd.CONFIG_FILE.write_text(text)
        with pytest.raises(SystemExit, match=message):
            agentd.main()


def test_execute_logs_a_traceback_for_a_bug(agentd, dirs, capsys):
    ex = executor(agentd)

    async def turn(ctx_id, prompt, req_workdir):
        raise KeyError("session")

    ex.run_turn = turn
    text, meta = a2a_call(ex, {"workdir": str(dirs.real)})
    assert meta["error"] and text == "ERROR from claude worker: 'session'"
    out = capsys.readouterr().out
    assert "FAILED: 'session'" in out and "Traceback" in out
    assert "KeyError" in out


def test_agent_card_declares_the_contract(agentd):
    card = agentd.agent_card("kimi", {"kind": "kimi", "port": 18004})
    assert card.name == "kimi-worker"
    assert card.url == "http://127.0.0.1:18004"
    assert card.security == [{"apiKey": []}]
    scheme = card.security_schemes["apiKey"].root
    assert (scheme.type, scheme.name, scheme.in_.value) == (
        "apiKey", "X-API-Key", "header")
    assert not card.capabilities.streaming
    [skill] = card.skills
    assert '{"workdir": "/absolute/path"}' in skill.description
    assert "kimi" in skill.tags
