BIN_DIR  := $(HOME)/.local/bin
UNIT_DIR := $(HOME)/.config/systemd/user
CONF_DIR := $(HOME)/.config/a2a-agents

.PHONY: install
install:
	install -d $(BIN_DIR) $(UNIT_DIR) $(CONF_DIR)
	install -m 0755 a2a-agentd a2a-mcp $(BIN_DIR)/
# a2a-send and a2a-post are a2a-mcp under other names: the script reads
# its own invocation name to pick the waiting or fire-and-forget mode.
	ln -sf a2a-mcp $(BIN_DIR)/a2a-send
	ln -sf a2a-mcp $(BIN_DIR)/a2a-post
	rm -f $(BIN_DIR)/a2a-chat
	install -m 0644 a2a-agent@.service $(UNIT_DIR)/
	test -f $(CONF_DIR)/agents.toml || \
		install -m 0644 agents.toml.example $(CONF_DIR)/agents.toml
	systemctl --user daemon-reload
