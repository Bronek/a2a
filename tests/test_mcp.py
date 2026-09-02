"""a2a-mcp: the protocol client, the CLI surfaces and the mailbox."""

import asyncio
import io
import json
import os
import sys
import threading
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest

# --- extract_text ----------------------------------------------------------


def test_extract_message(mcp):
    result = {"kind": "message", "metadata": {"agent": "kimi"},
              "parts": [{"kind": "text", "text": "a"}, {"kind": "file"},
                        {"kind": "text", "text": "b"}]}
    assert mcp.extract_text(result) == ("a\nb", {"agent": "kimi"})


def test_extract_task_artifacts_then_history(mcp):
    task = {"kind": "task",
            "artifacts": [{"parts": [{"kind": "text", "text": "art"}]}],
            "history": [
                {"role": "user", "parts": [{"kind": "text", "text": "q"}]},
                {"role": "agent", "metadata": {"m": 1},
                 "parts": [{"kind": "text", "text": "hist"}]}]}
    assert mcp.extract_text(task) == ("art", {"m": 1})
    task["artifacts"] = []
    assert mcp.extract_text(task) == ("hist", {"m": 1})


def test_extract_unknown_dumps_json(mcp):
    text, meta = mcp.extract_text({"kind": "weird", "x": 1})
    assert json.loads(text) == {"kind": "weird", "x": 1}
    assert meta == {}


# --- configuration and addresses ------------------------------------------


def test_load_conf_keeps_only_agent_stanzas(mcp, mesh):
    assert list(mcp.load_conf()) == ["claude", "kimi"]


def test_missing_or_broken_conf_is_a_usage_error(mcp, home):
    with pytest.raises(RuntimeError, match="not found — `make install`"):
        mcp.load_conf()
    with pytest.raises(SystemExit, match="not found — `make install`"):
        mcp.agent_cli(["-c", "kimi/ctx", "hi"], default_post=False)
    mcp.CONFIG_FILE.parent.mkdir(parents=True)
    mcp.CONFIG_FILE.write_text("[kimi\n")
    with pytest.raises(RuntimeError, match=r"agents\.toml: "):
        mcp.load_conf()


def test_parse_address(mcp, mesh):
    conf = mcp.load_conf()
    assert mcp.parse_address("kimi/review-1", conf) == ("kimi", "review-1")
    assert mcp.parse_address("kimi", conf) == ("kimi", None)
    assert mcp.parse_address("kimi/", conf) == ("kimi", None)
    with pytest.raises(SystemExit, match="unknown agent 'llama'"):
        mcp.parse_address("llama/x", conf)


def test_list_reports_each_daemon_without_sending_the_key(mcp, mesh,
                                                          monkeypatch):
    def fake_get(url, **kw):
        assert "headers" not in kw
        if "18004" in url:
            return httpx.Response(200, json={"description": "kimi worker"})
        if "18002" in url:
            raise httpx.ConnectError("refused")
        raise AssertionError(url)

    monkeypatch.setattr(mcp.httpx, "get", fake_get)
    assert mcp.do_list() == ("claude (port 18002): DOWN\n"
                             "kimi (port 18004): up — kimi worker")


# --- send_rpc, against a mock daemon --------------------------------------


@pytest.fixture
def daemon(mesh, monkeypatch):
    """A MockTransport behind httpx.Client: records every request and the
    client options, and answers with whatever the test sets as respond."""
    state = SimpleNamespace(requests=[], options=[], respond=None)

    def handler(request):
        state.requests.append(request)
        return state.respond(request)

    real_client = httpx.Client

    def client(**kw):
        state.options.append(kw)
        return real_client(transport=httpx.MockTransport(handler), **kw)

    monkeypatch.setattr(httpx, "Client", client)
    return state


def reply(text, meta=None):
    def respond(request):
        body = json.loads(request.content)
        msg = {"kind": "message", "role": "agent", "messageId": "m",
               "parts": [{"kind": "text", "text": text}],
               "metadata": meta or {}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                         "result": msg})
    return respond


def test_send_rpc_success(mcp, daemon, home):
    daemon.respond = reply("hi", {"native_session_id": "n1"})
    proj = home / "proj"
    proj.mkdir()
    (home / "link").symlink_to(proj)
    text, meta = mcp.send_rpc("kimi", "ctx", "hello", workdir="~/link")
    assert (text, meta) == ("hi", {"native_session_id": "n1"})
    [req] = daemon.requests
    assert str(req.url) == "http://127.0.0.1:18004/"
    assert req.headers["x-api-key"] == "k" * 64
    body = json.loads(req.content)
    assert body["jsonrpc"] == "2.0" and body["method"] == "message/send"
    msg = body["params"]["message"]
    assert (msg["kind"], msg["role"], msg["contextId"]) == (
        "message", "user", "ctx")
    assert msg["parts"] == [{"kind": "text", "text": "hello"}]
    assert msg["metadata"] == {"workdir": str(proj.resolve())}
    uuid.UUID(msg["messageId"])


def test_send_rpc_without_workdir_sends_no_metadata(mcp, daemon):
    daemon.respond = reply("hi")
    mcp.send_rpc("kimi", "ctx", "hello")
    assert "metadata" not in json.loads(daemon.requests[0].content)[
        "params"]["message"]


def test_send_rpc_read_timeout_tracks_the_agent_timeout(mcp, daemon):
    daemon.respond = reply("hi")
    mcp.send_rpc("kimi", "ctx", "hello")
    mcp.send_rpc("claude", "ctx", "hello")
    mcp.send_rpc("kimi", "ctx", "hello", read_timeout=6)
    assert [o["timeout"].read for o in daemon.options] == [
        30 + 120, 1800 + 120, 6]


def test_send_rpc_errors(mcp, daemon):
    def rpc_error(_):
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": "1",
            "error": {"code": -32600, "message": "bad request"}})

    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    cases = [
        (rpc_error, RuntimeError, "A2A -32600: bad request"),
        (lambda _: httpx.Response(200, content=b"<html>"),
         RuntimeError, "non-JSON"),
        (lambda _: httpx.Response(200, json={"jsonrpc": "2.0", "id": "1"}),
         RuntimeError, "malformed JSON-RPC"),
        (lambda _: httpx.Response(401, text="missing or bad X-API-Key"),
         RuntimeError, "unreachable or failed"),
        (timeout, httpx.ReadTimeout, "slow"),
    ]
    for respond, exc, match in cases:
        daemon.respond = respond
        with pytest.raises(exc, match=match):
            mcp.send_rpc("kimi", "ctx", "hello")


def test_send_rpc_needs_a_known_agent_and_a_key(mcp, mesh):
    with pytest.raises(RuntimeError, match="unknown agent 'llama'"):
        mcp.send_rpc("llama", "ctx", "hello")
    mcp.KEY_FILE.unlink()
    with pytest.raises(RuntimeError, match="start a daemon once"):
        mcp.send_rpc("kimi", "ctx", "hello")


# --- do_send: the MCP tool surface -----------------------------------------


def test_do_send_tail_and_failures(mcp, monkeypatch):
    def answer(*a, **k):
        return "hi", {"native_session_id": "n1"}

    monkeypatch.setattr(mcp, "send_rpc", answer)
    assert mcp.do_send("kimi", "ctx", "m") == (
        "hi\n\n[kimi / context ctx / native session n1]", False)

    monkeypatch.setattr(mcp, "send_rpc", lambda *a, **k: ("boom",
                                                          {"error": True}))
    assert mcp.do_send("kimi", "ctx", "m") == (
        "ERROR (worker-side): boom\n\n[kimi / context ctx]", True)

    def unknown(*a, **k):
        raise RuntimeError("unknown agent 'kimi'")

    monkeypatch.setattr(mcp, "send_rpc", unknown)
    assert mcp.do_send("kimi", "ctx", "m") == (
        "ERROR: unknown agent 'kimi'", True)

    def slow(*a, **k):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(mcp, "send_rpc", slow)
    assert mcp.do_send("kimi", "ctx", "m") == (
        "ERROR: kimi did not answer: ReadTimeout", True)


# --- agent_cli: a2a-send / a2a-post ---------------------------------------


@pytest.fixture
def rpc(mcp, mesh, monkeypatch):
    """send_rpc replaced by a recorder that answers as the test says."""
    rec = SimpleNamespace(calls=[], reply=("hi", {"native_session_id": "n1"}),
                          raises=None)

    def fake(agent, context, message, workdir=None, read_timeout=None):
        rec.calls.append(SimpleNamespace(
            agent=agent, context=context, message=message, workdir=workdir,
            read_timeout=read_timeout))
        if rec.raises:
            raise rec.raises
        return rec.reply

    monkeypatch.setattr(mcp, "send_rpc", fake)
    return rec


def test_cli_sends_words_joined(mcp, rpc, capsys):
    mcp.agent_cli(["-c", "kimi/ctx", "hello", "there"], default_post=False)
    [call] = rpc.calls
    assert vars(call) == {"agent": "kimi", "context": "ctx",
                          "message": "hello there", "workdir": None,
                          "read_timeout": None}
    out, err = capsys.readouterr()
    assert out == "hi\n"
    assert err == "(context ctx -> native session n1)\n"


def test_cli_dash_message_needs_a_double_dash(mcp, rpc):
    mcp.agent_cli(["-c", "kimi/ctx", "--", "-v", "please"], default_post=False)
    assert rpc.calls[0].message == "-v please"
    with pytest.raises(SystemExit):
        mcp.agent_cli(["-c", "kimi/ctx", "-v"], default_post=False)


def test_cli_reads_stdin_verbatim(mcp, rpc, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("line one\n  line two\n"))
    mcp.agent_cli(["-", "-c", "kimi/ctx"], default_post=False)
    assert rpc.calls[0].message == "line one\n  line two\n"


@pytest.mark.parametrize("argv, message", [
    (["hello"], "-c AGENT./CONTEXT. is required"),
    (["-c", "kimi/ctx"], "nothing to send"),
    (["-c", "kimi/ctx", "-", "extra"], "must be the only"),
    (["-c", "kimi/ctx", "extra", "-"], "must be the only"),
    (["-c", "llama/ctx", "hi"], "unknown agent 'llama'"),
])
def test_cli_usage_errors(mcp, rpc, argv, message):
    with pytest.raises(SystemExit, match=message):
        mcp.agent_cli(argv, default_post=False)
    assert rpc.calls == []


def test_cli_empty_stdin_is_refused(mcp, rpc, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("  \n"))
    with pytest.raises(SystemExit, match="stdin was empty"):
        mcp.agent_cli(["-c", "kimi/ctx", "-"], default_post=False)
    assert rpc.calls == []


def test_cli_fresh_context_gets_a_generated_name(mcp, rpc, capsys):
    mcp.agent_cli(["-c", "kimi", "hi"], default_post=False)
    ctx = rpc.calls[0].context
    uuid.UUID(ctx)
    assert f"(context {ctx} -> native session n1)" in capsys.readouterr().err


def test_cli_resolves_the_workdir_where_the_caller_is(mcp, rpc, home,
                                                       monkeypatch):
    proj = home / "proj"
    proj.mkdir()
    (home / "link").symlink_to(proj)
    monkeypatch.chdir(proj)
    mcp.agent_cli(["-c", "kimi/a", "--here", "hi"], default_post=False)
    mcp.agent_cli(["-c", "kimi/b", "--workdir", "../link", "hi"],
                  default_post=False)
    mcp.agent_cli(["-c", "kimi/c", "--workdir", "~/link", "hi"],
                  default_post=False)
    assert [c.workdir for c in rpc.calls] == [str(proj.resolve())] * 3
    with pytest.raises(SystemExit):
        mcp.agent_cli(["-c", "kimi/d", "--here", "--workdir", ".", "hi"],
                      default_post=False)


def test_cli_worker_error_exits_2(mcp, rpc, capsys):
    rpc.reply = ("it broke", {"error": True})
    with pytest.raises(SystemExit) as exc:
        mcp.agent_cli(["-c", "kimi/ctx", "hi"], default_post=False)
    assert exc.value.code == 2
    out, err = capsys.readouterr()
    assert out == "" and "it broke" in err


def test_cli_transport_failures_die_with_the_cause(mcp, rpc):
    rpc.raises = RuntimeError("~/.config/a2a-agents/api-key not found")
    with pytest.raises(SystemExit, match="api-key not found"):
        mcp.agent_cli(["-c", "kimi/ctx", "hi"], default_post=False)
    rpc.raises = httpx.ConnectError("refused")
    with pytest.raises(SystemExit, match="kimi daemon did not answer"):
        mcp.agent_cli(["-c", "kimi/ctx", "hi"], default_post=False)


def test_post_returns_once_the_turn_is_queued(mcp, rpc, capsys):
    rpc.raises = httpx.ReadTimeout("still working")
    mcp.agent_cli(["-c", "kimi/ctx", "go"], default_post=True)
    assert rpc.calls[0].read_timeout == mcp.POST_GRACE_SECONDS
    out, err = capsys.readouterr()
    assert out == "" and err == "queued to kimi (context ctx)\n"


def test_post_reports_a_fast_refusal(mcp, rpc, capsys):
    rpc.reply = ("context 'ctx' has no pinned workdir", {"error": True})
    with pytest.raises(SystemExit) as exc:
        mcp.agent_cli(["-c", "kimi/ctx", "go"], default_post=True)
    assert exc.value.code == 2
    assert "no pinned workdir" in capsys.readouterr().err


def test_post_prints_a_fast_reply_with_its_tail(mcp, rpc, capsys):
    mcp.agent_cli(["-c", "kimi", "quick"], default_post=True)
    out, err = capsys.readouterr()
    assert out == "hi\n" and "-> native session n1" in err


def test_mode_flags_override_the_name(mcp, rpc):
    mcp.agent_cli(["-c", "kimi/ctx", "--post", "x"], default_post=False)
    mcp.agent_cli(["-c", "kimi/ctx", "--send", "x"], default_post=True)
    assert [c.read_timeout for c in rpc.calls] == [mcp.POST_GRACE_SECONDS,
                                                   None]
    with pytest.raises(SystemExit):
        mcp.agent_cli(["-c", "kimi/ctx", "--send", "--post", "x"],
                      default_post=False)


# --- the mailbox -----------------------------------------------------------


def box_files(mcp, name):
    return sorted((mcp.MAILBOX_DIR / name).glob("*.json"))


def test_box_names(mcp, home):
    for bad in ("", ".", "..", "../x", "a/b", "a b", "x" * 65, "-", "._-"):
        with pytest.raises(ValueError, match="invalid recipient"):
            mcp._box(bad)
    box = mcp._box("lead")
    assert box == mcp.MAILBOX_DIR / "lead" and box.is_dir()
    assert box.stat().st_mode & 0o777 == 0o700
    assert mcp.MAILBOX_DIR.stat().st_mode & 0o777 == 0o700


def test_spool_writes_one_private_file_per_message(mcp, home, monkeypatch):
    monkeypatch.setenv("A2A_AGENT", "kimi")
    monkeypatch.setenv("A2A_CONTEXT", "slice-a")
    assert mcp.do_spool("lead", "finding one") == "spooled to lead"
    mcp.do_spool("lead", "finding two", sender="codex")
    first, second = (json.loads(f.read_text()) for f in box_files(mcp, "lead"))
    assert first == {"from": "kimi/slice-a", "to": "lead", "ts": first["ts"],
                     "text": "finding one"}
    assert second["from"] == "codex"
    assert all(f.stat().st_mode & 0o777 == 0o600
               for f in box_files(mcp, "lead"))
    assert list((mcp.MAILBOX_DIR / "lead").glob(".*")) == []


def test_spool_sender_falls_back(mcp, home, monkeypatch):
    mcp.do_spool("lead", "x")
    monkeypatch.setenv("A2A_AGENT", "kimi")
    mcp.do_spool("lead", "y")
    assert [json.loads(f.read_text())["from"]
            for f in box_files(mcp, "lead")] == ["unknown", "kimi"]


def test_spool_bounds(mcp, home, monkeypatch):
    with pytest.raises(ValueError, match=r"over the .*limit"):
        mcp.do_spool("lead", "x" * (mcp.MAX_MESSAGE_BYTES + 1))
    monkeypatch.setattr(mcp, "MAX_BOX_MESSAGES", 2)
    mcp.do_spool("lead", "1")
    mcp.do_spool("lead", "2")
    with pytest.raises(ValueError, match="already holds 2 undrained"):
        mcp.do_spool("lead", "3")


def test_spool_reaps_stale_leftovers(mcp, home):
    box = mcp._box("lead")
    old_tmp, old_bad, new_bad = (box / ".123.tmp", box / ".bad-old.json",
                                 box / ".bad-new.json")
    for p in (old_tmp, old_bad, new_bad):
        p.write_text("x")
    ancient = time.time() - 8 * 24 * 3600
    os.utime(old_tmp, (ancient, ancient))
    os.utime(old_bad, (ancient, ancient))
    mcp.do_spool("lead", "hello")
    assert not old_tmp.exists() and not old_bad.exists()
    assert new_bad.exists()


def test_check_empty_box(mcp, home):
    delivered = []
    assert mcp.do_check("lead", deliver=delivered.append) == "no mail for lead"
    assert delivered == ["no mail for lead"]


def test_check_drains_in_order(mcp, home):
    mcp.do_spool("lead", "first", sender="a")
    mcp.do_spool("lead", "second", sender="b")
    delivered = []
    text = mcp.do_check("lead", deliver=delivered.append)
    assert delivered == [text]
    assert "from a:\nfirst\n\n---\n\n[" in text and "from b:\nsecond" in text
    assert box_files(mcp, "lead") == []


def test_check_keeps_mail_when_delivery_fails(mcp, home):
    mcp.do_spool("lead", "precious")

    def broken(_):
        raise BrokenPipeError

    with pytest.raises(BrokenPipeError):
        mcp.do_check("lead", deliver=broken)
    assert len(box_files(mcp, "lead")) == 1


def test_check_quarantines_unreadable_entries(mcp, home):
    mcp.do_spool("lead", "good")
    box = mcp.MAILBOX_DIR / "lead"
    (box / "0-bad.json").write_text("{not json")
    (box / "1-list.json").write_text("[1]")
    text = mcp.do_check("lead")
    assert "good" in text
    assert "[unreadable entry 0-bad.json:" in text
    assert "[unreadable entry 1-list.json: not a JSON object]" in text
    assert sorted(p.name for p in box.iterdir()) == [".bad-0-bad.json",
                                                     ".bad-1-list.json"]
    assert mcp.do_check("lead") == "no mail for lead"


def test_watch_honours_a_zero_timeout(mcp, home):
    t0 = time.monotonic()
    assert mcp.do_check("lead", wait=True, timeout=0) == "no mail for lead"
    assert time.monotonic() - t0 < 1.5


def test_watch_wakes_on_mail(mcp, home):
    threading.Timer(0.3, mcp.do_spool, ("lead", "ding")).start()
    t0 = time.monotonic()
    assert "ding" in mcp.do_check("lead", wait=True, timeout=10)
    assert time.monotonic() - t0 < 5


def test_nan_timeout_is_refused(mcp, home):
    with pytest.raises(ValueError, match="NaN"):
        mcp.do_check("lead", wait=True, timeout=float("nan"))


# --- main: the dispatch ----------------------------------------------------


def run_main(mcp, monkeypatch, *argv, name="a2a-mcp"):
    monkeypatch.setattr(sys, "argv", [name, *argv])
    mcp.main()


def test_main_dispatches_by_name_and_verb(mcp, monkeypatch):
    seen = []
    monkeypatch.setattr(mcp, "agent_cli", lambda argv, default_post, prog=None:
                        seen.append((argv, default_post, prog)))
    run_main(mcp, monkeypatch, "-c", "kimi/x", "hi", name="a2a-send")
    run_main(mcp, monkeypatch, "-c", "kimi/x", "hi", name="a2a-post")
    run_main(mcp, monkeypatch, "send", "-c", "kimi/x", "hi")
    run_main(mcp, monkeypatch, "post", "-c", "kimi/x", "hi")
    argv = ["-c", "kimi/x", "hi"]
    assert seen == [(argv, False, None), (argv, True, None),
                    (argv, False, "a2a-mcp send"), (argv, True, "a2a-mcp post")]


def test_main_list(mcp, monkeypatch, capsys):
    monkeypatch.setattr(mcp, "do_list", lambda: "kimi: up")
    run_main(mcp, monkeypatch, "list")
    assert capsys.readouterr().out == "kimi: up\n"


def test_main_spool_from_stdin(mcp, home, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("multi\nline\n"))
    run_main(mcp, monkeypatch, "spool", "lead", "-", "codex")
    [entry] = box_files(mcp, "lead")
    mail = json.loads(entry.read_text())
    assert (mail["text"], mail["from"]) == ("multi\nline\n", "codex")
    assert capsys.readouterr().out == "spooled to lead\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(SystemExit, match="stdin was empty"):
        run_main(mcp, monkeypatch, "spool", "lead", "-")
    assert len(box_files(mcp, "lead")) == 1


def test_main_mail_and_watch(mcp, home, monkeypatch, capsys):
    mcp.do_spool("lead", "hello", sender="x")
    run_main(mcp, monkeypatch, "mail", "lead")
    assert "from x:\nhello" in capsys.readouterr().out
    run_main(mcp, monkeypatch, "watch", "lead", "0")
    assert capsys.readouterr().out == "no mail for lead\n"


@pytest.mark.parametrize("argv", [
    ["bogus"], ["list", "extra"], ["spool", "lead"], ["mail"], ["watch"],
    ["watch", "lead", "1", "2"]])
def test_main_rejects_bad_arity(mcp, monkeypatch, argv):
    with pytest.raises(SystemExit, match="usage: a2a-mcp"):
        run_main(mcp, monkeypatch, *argv)


# --- the MCP surface -------------------------------------------------------


def test_mcp_tools_and_their_hints(mcp):
    server = mcp.build_server()
    tools = {t.name: t for t in asyncio.run(server.list_tools())}
    assert set(tools) == {"list_agents", "send", "spool", "check_mail"}
    hints = {name: (t.annotations.read_only_hint, t.annotations.destructive_hint,
                    t.annotations.idempotent_hint, t.annotations.open_world_hint)
             for name, t in tools.items()}
    assert hints == {
        "list_agents": (True, False, True, False),
        "send": (False, True, False, True),
        "spool": (False, False, False, False),
        "check_mail": (False, True, True, False),
    }
    schema = tools["send"].input_schema
    assert schema["required"] == ["agent", "context", "message"]
    assert "workdir" in schema["properties"]
    assert "untrusted" in tools["check_mail"].description
