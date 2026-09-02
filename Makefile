BIN_DIR  := $(HOME)/.local/bin
UNIT_DIR := $(HOME)/.config/systemd/user
CONF_DIR := $(HOME)/.config/a2a-agents

.PHONY: install uninstall test lint check

install:
	install -d "$(BIN_DIR)" "$(UNIT_DIR)" "$(CONF_DIR)"
	install -m 0755 a2a-agentd a2a-mcp "$(BIN_DIR)/"
# a2a-send and a2a-post are a2a-mcp under other names: the script reads
# its own invocation name to pick the waiting or fire-and-forget mode.
	ln -sf a2a-mcp "$(BIN_DIR)/a2a-send"
	ln -sf a2a-mcp "$(BIN_DIR)/a2a-post"
	install -m 0644 a2a-agent@.service "$(UNIT_DIR)/"
	test -f "$(CONF_DIR)/agents.toml" || \
		install -m 0644 agents.toml.example "$(CONF_DIR)/agents.toml"
	systemctl --user daemon-reload

# Removes what install put in place. The configuration, the API key and
# the state (session maps, logs, mailboxes) are left alone. Instances are
# disabled first, or their default.target.wants links would dangle:
# disable takes no glob, so the enabled ones are listed by name.
uninstall:
	-systemctl --user list-unit-files --plain --no-legend 'a2a-agent@*' \
		| awk '$$2 == "enabled" { print $$1 }' \
		| xargs -r systemctl --user disable
	-systemctl --user stop 'a2a-agent@*'
	rm -f "$(BIN_DIR)/a2a-agentd" "$(BIN_DIR)/a2a-mcp" \
		"$(BIN_DIR)/a2a-send" "$(BIN_DIR)/a2a-post"
	rm -f "$(UNIT_DIR)/a2a-agent@.service"
	systemctl --user daemon-reload

# The checks run from the project environment (pyproject.toml) that uv
# creates on first use; the scripts themselves never need it. --locked:
# a stale uv.lock is an error to fix with `uv lock`, not something a
# check quietly rewrites.
test:
	uv run --locked pytest

lint:
	uv run --locked ruff check

check: lint test
