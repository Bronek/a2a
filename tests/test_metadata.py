"""The scripts' inline metadata and the project file must agree."""

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ["a2a-agentd", "a2a-mcp"]


def inline_metadata(path: Path) -> dict:
    text = path.read_text()
    block = re.search(r"^# /// script\n(.*?)^# ///$", text, re.S | re.M)
    assert block, f"{path.name} has no inline script metadata"
    toml = "".join(line[2:] + "\n" for line in block.group(1).splitlines())
    return tomllib.loads(toml)


def test_scripts_are_executable_uv_scripts():
    for name in SCRIPTS:
        script = ROOT / name
        assert os.access(script, os.X_OK), f"{name} is not executable"
        first = script.read_text().splitlines()[0]
        assert first == "#!/usr/bin/env -S uv run --script", name


def test_pyproject_mirrors_the_inline_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    inline = {name: inline_metadata(ROOT / name) for name in SCRIPTS}
    for name, meta in inline.items():
        assert meta["requires-python"] == project["requires-python"], name
    union = set().union(*(meta["dependencies"] for meta in inline.values()))
    assert union == set(project["dependencies"])


def test_each_script_runs_in_its_own_environment():
    """The project environment holds both scripts' dependencies, so a
    dependency moved from one header to the other would still import under
    pytest. Only the environment `uv run --script` resolves from a header
    proves that header. Each script is run once, to its first exit that
    needs every import: the daemon's usage message (or its missing-config
    message), the MCP server's answer to initialize."""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}

    daemon = subprocess.run(["uv", "run", "--script", str(ROOT / "a2a-agentd")],
                            capture_output=True, text=True, env=env,
                            timeout=300)
    assert daemon.returncode == 1, daemon.stderr
    assert ("usage: a2a-agentd <agent>" in daemon.stderr
            or "agents.toml not found" in daemon.stderr), daemon.stderr

    hello = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18",
                                   "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "0"}}})
    server = subprocess.run(["uv", "run", "--script", str(ROOT / "a2a-mcp")],
                            input=hello + "\n", capture_output=True, text=True,
                            env=env, timeout=300)
    reply = json.loads(server.stdout.splitlines()[0])
    assert reply["result"]["serverInfo"]["name"] == "a2a", server.stderr
