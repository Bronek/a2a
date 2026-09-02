"""The scripts' inline metadata and the project file must agree."""

import os
import re
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
