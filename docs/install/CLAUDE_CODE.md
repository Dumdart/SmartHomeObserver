# Connect TopicGate to Claude Code

First [install TopicGate](OS_INSTALL.md), run `topicgate-gui`, and configure and connect a broker in **TopicGate Desktop**. Install the host itself before running its integration commands.

Choose one setup path. A **plugin** adds TopicGate skills and MCP configuration; **MCP-only** adds the server without those skills. Both start in **read-only mode**.

## Plugin integration (recommended)

The helper requires the `claude` CLI on PATH.

```console
topicgate-cli integration install claude
```

The helper installs or updates the user-scoped plugin and manages user-scoped MCP entries. It can reuse an existing plugin server in read-only mode; a new installation may also create a user entry. Check `/mcp` for duplicate servers.

Alternatively, install the plugin manually:

```console
claude plugin marketplace add Dumdart/TopicGate
claude plugin install topicgate@topicgate --scope user
```

The plugin launches `topicgate --mode read-only` and requires the local application on the host's PATH. Restart Claude Code and start a new session after installation or configuration changes.

## MCP-only alternative

Use this instead of installing the plugin:

```console
claude mcp add --transport stdio --scope user topicgate -- topicgate --mode read-only
```

If the host cannot find `topicgate`, copy the configuration from Desktop's **Help → MCP setup...**. Preserve its command, arguments, and `TOPICGATE_DATA_DIR`; see [executable recovery](UPGRADE_AND_RECOVERY.md#executable-not-found).

## Control mode and maintenance

To explicitly opt into control mode with the integration helper:

```console
topicgate-cli integration install claude --mode control
```

The helper writes a user-scoped `topicgate` entry. Claude namespaces plugin servers and, in newer versions, deduplicates them by endpoint rather than name alone. The helper does not inspect project/local entries, plugin enablement, or the host's effective deduplication. Check `/mcp`; if both plugin and user servers remain enabled, disable the plugin server and use the intended user entry. Verify the exposed tools in a new session. See [Claude's MCP precedence rules](https://code.claude.com/docs/en/mcp#scope-hierarchy-and-precedence).

For MCP-only setup, change the existing server's arguments to `--mode control`; do not add a second `topicgate-control` server. Read [Control mode and health](CONTROL_AND_HEALTH.md) before enabling privileged tools, and restart the host afterward.

```console
topicgate-cli integration status claude
topicgate-cli integration repair claude
topicgate-cli integration uninstall claude
```

These commands maintain the plugin integration. Repair preserves a single detected mode and falls back to read-only when ambiguous; use `--mode read-only` to explicitly return to the default. Uninstall leaves the shared marketplace and TopicGate application installed. See [upgrades and recovery](UPGRADE_AND_RECOVERY.md).
