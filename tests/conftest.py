"""Shared fixtures: the two scripts loaded as modules, and a throwaway
home so that no test can touch the real ~/.config or ~/.local/state."""

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = {"a2a_agentd": ROOT / "a2a-agentd", "a2a_mcp": ROOT / "a2a-mcp"}


def load_script(module_name: str):
    """The scripts have no .py suffix, so the import system cannot find
    them by name; SourceFileLoader takes any path it is given."""
    if module_name in sys.modules:
        return sys.modules[module_name]
    loader = importlib.machinery.SourceFileLoader(
        module_name, str(SCRIPTS[module_name]))
    spec = importlib.util.spec_from_loader(module_name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def agentd():
    return load_script("a2a_agentd")


@pytest.fixture(scope="session")
def mcp():
    return load_script("a2a_mcp")


@pytest.fixture
def home(tmp_path, monkeypatch, agentd, mcp):
    """A throwaway $HOME with both modules' path constants pointed into it,
    and no mesh identity leaking in from the environment."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("A2A_AGENT", raising=False)
    monkeypatch.delenv("A2A_CONTEXT", raising=False)
    conf = tmp_path / ".config/a2a-agents"
    state = tmp_path / ".local/state/a2a-agents"
    for mod in (agentd, mcp):
        monkeypatch.setattr(mod, "CONFIG_FILE", conf / "agents.toml")
        monkeypatch.setattr(mod, "KEY_FILE", conf / "api-key")
    monkeypatch.setattr(agentd, "STATE_DIR", state)
    monkeypatch.setattr(mcp, "MAILBOX_DIR", state / "mailbox")
    return tmp_path


@pytest.fixture
def mesh(home, mcp):
    """A configuration with two agents and an API key, as `make install`
    and a first daemon start would leave them."""
    mcp.CONFIG_FILE.parent.mkdir(parents=True)
    mcp.CONFIG_FILE.write_text(
        'title = "not an agent"\n'
        "[claude]\nkind = \"claude\"\nport = 18002\n"
        "[kimi]\nkind = \"kimi\"\nport = 18004\ntimeout = 30\n"
        "[broken]\nkind = \"kimi\"\n")
    mcp.KEY_FILE.write_text("k" * 64 + "\n")
    return {"claude": 18002, "kimi": 18004}
