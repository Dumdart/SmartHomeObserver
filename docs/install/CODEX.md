# Connect TopicGate to Codex

First [install TopicGate](OS_INSTALL.md), run `topicgate-gui`, and configure and connect a broker in **TopicGate Desktop**. Install the host itself before running its integration commands.

Choose one setup path. A **plugin** adds TopicGate skills and MCP configuration; **MCP-only** adds the server without those skills. Both start in **read-only mode**.

## Plugin integration (recommended)

The helper requires the `codex` CLI on PATH, including when connecting the Codex desktop app.

```console
topicgate-cli integration install codex
```

The helper installs or updates the plugin and configures a `topicgate` MCP entry with the resolved executable and data directory.

Alternatively, install the plugin manually:

```console
codex plugin marketplace add Dumdart/TopicGate
codex plugin add topicgate@topicgate
```

The plugin launches `topicgate --mode read-only` and requires the local application on the host's PATH. Restart Codex and start a new task after installation or configuration changes.

## MCP-only alternative

Use this instead of installing the plugin:

```console
codex mcp add topicgate -- topicgate --mode read-only
```

If the host cannot find `topicgate`, copy the configuration from Desktop's **Help → MCP setup...**. Preserve its command, arguments, and `TOPICGATE_DATA_DIR`; see [executable recovery](UPGRADE_AND_RECOVERY.md#executable-not-found).

## Control mode and maintenance

To explicitly opt into control mode with the integration helper:

```console
topicgate-cli integration install codex --mode control
```

For MCP-only setup, change the existing server's arguments to `--mode control`; do not add a second `topicgate-control` server. Read [Control mode and health](CONTROL_AND_HEALTH.md) before enabling privileged tools, and restart the host afterward.

```console
topicgate-cli integration status codex
topicgate-cli integration repair codex
topicgate-cli integration uninstall codex
```

These commands maintain the plugin integration. Repair preserves a single detected mode and falls back to read-only when ambiguous; use `--mode read-only` to explicitly return to the default. Uninstall leaves the shared marketplace and TopicGate application installed. See [upgrades and recovery](UPGRADE_AND_RECOVERY.md).
