BIN_DIR  := $(HOME)/.local/bin
UNIT_DIR := $(HOME)/.config/systemd/user
CONF_DIR := $(HOME)/.config/a2a-agents

.PHONY: install uninstall

install:
	install -d $(BIN_DIR) $(UNIT_DIR) $(CONF_DIR)
	install -m 0755 a2a-agentd a2a-mcp $(BIN_DIR)/
# a2a-send and a2a-post are a2a-mcp under other names: the script reads
# its own invocation name to pick the waiting or fire-and-forget mode.
	ln -sf a2a-mcp $(BIN_DIR)/a2a-send
	ln -sf a2a-mcp $(BIN_DIR)/a2a-post
	install -m 0644 a2a-agent@.service $(UNIT_DIR)/
	test -f $(CONF_DIR)/agents.toml || \
		install -m 0644 agents.toml.example $(CONF_DIR)/agents.toml
	systemctl --user daemon-reload

# Removes what install put in place. The configuration, the API key and
# the state (session maps, logs, mailboxes) are left alone.
uninstall:
	-systemctl --user stop 'a2a-agent@*'
	rm -f $(BIN_DIR)/a2a-agentd $(BIN_DIR)/a2a-mcp \
		$(BIN_DIR)/a2a-send $(BIN_DIR)/a2a-post
	rm -f $(UNIT_DIR)/a2a-agent@.service
	systemctl --user daemon-reload
