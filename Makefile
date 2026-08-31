BIN_DIR  := $(HOME)/.local/bin
UNIT_DIR := $(HOME)/.config/systemd/user
CONF_DIR := $(HOME)/.config/a2a-agents

.PHONY: install
install:
	install -d $(BIN_DIR) $(UNIT_DIR) $(CONF_DIR)
	install -m 0755 a2a-agentd a2a-chat a2a-mcp $(BIN_DIR)/
# a2a-post is a2a-chat under another name: the script reads its own
# invocation name and skips waiting for the reply.
	ln -sf a2a-chat $(BIN_DIR)/a2a-post
	install -m 0644 a2a-agent@.service $(UNIT_DIR)/
	test -f $(CONF_DIR)/agents.toml || \
		install -m 0644 agents.toml.example $(CONF_DIR)/agents.toml
	systemctl --user daemon-reload
