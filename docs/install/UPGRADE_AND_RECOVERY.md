# Upgrade and recover TopicGate

## Upgrade

Back up your data before upgrading. Stop Desktop and all TopicGate MCP processes, upgrade the installation used by your host, then restart. TopicGate applies database migrations at startup. Refresh the agent integration separately as described below.

```console
uv tool upgrade topicgate
```

For pip installations:

```console
python -m pip install --upgrade topicgate
```

To uninstall the application, use `uv tool uninstall topicgate`, or `python -m pip uninstall topicgate` for a pip installation. This does not remove local data, broker passwords, or agent integrations.

## Executable not found

Open a new terminal. For `uv`, run `uv tool update-shell` and restart the agent host so it receives the updated PATH.

In TopicGate Desktop, open **Help → MCP setup...** and copy the generated configuration. Use its resolved command, complete arguments, and `TOPICGATE_DATA_DIR` in your host's format. Copying only the executable path may be insufficient: when no `topicgate` launcher is found, Desktop generates a Python executable plus module arguments. VS Code requires a `servers` wrapper; Codex uses its MCP registration/configuration format.

Run **Run local preflight** there to check the installation. This is not a test that the agent host has loaded the server. If the host sees different brokers, compare its data directory and OS user with Desktop's. Never put broker passwords in MCP configuration.

## Data and backups

| Platform | Default data directory |
| --- | --- |
| Windows | `%LOCALAPPDATA%\Dumdart\TopicGate` |
| Linux | `~/.local/share/TopicGate` |
| macOS | `~/Library/Application Support/TopicGate` |

Set `TOPICGATE_DATA_DIR` to override the location. The directory contains `topicgate.db` and may contain SQLite WAL sidecars; passwords remain in the operating-system credential store.

To back up or restore, stop TopicGate Desktop and every TopicGate MCP process, then copy the entire data directory.

## Recovery reset

1. Stop every TopicGate process.
2. Back up the data directory.
3. Rename the data directory.
4. Start `topicgate-gui` to create fresh local data.

This resets profiles, settings, subscriptions, and observations but not credential-store entries. Delete a profile through TopicGate Desktop when you intend to remove its password.

## Repair agent integrations

For the plugin integration, run only the pair for your host:

```console
topicgate-cli integration repair codex
topicgate-cli integration status codex
```

Replace `codex` with `claude`, `cursor`, or `copilot` (Copilot CLI, not VS Code). Repair keeps a single detected mode; absent or ambiguous mode information falls back to read-only. Add `--mode read-only` to repair when you explicitly want to remove control capabilities.

The helpers manage known `topicgate` and legacy `topicgate-control` entries, not every host scope or arbitrary server name. Check the host's actual enabled servers and tools after restarting. [Claude Code](CLAUDE_CODE.md) has an additional host-state inspection limitation. Cursor skills must be installed, updated, or removed through `/plugin`; its helper manages only marketplace and MCP configuration.

For MCP-only setups, update the existing host entry directly; running the integration helper also installs the plugin where supported. For VS Code, update the plugin through Extensions or the entry in its MCP configuration. Follow the [host guides](../../README.md#connect-an-agent).

To remove a helper-managed integration, run `topicgate-cli integration uninstall` followed by your host name. Shared marketplace configuration is retained. These commands do not upgrade or uninstall the TopicGate application. Restart the affected host and start a new session after changes.
