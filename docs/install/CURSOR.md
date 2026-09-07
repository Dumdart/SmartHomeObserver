# Connect TopicGate to Cursor

First [install TopicGate](OS_INSTALL.md), run `topicgate-gui`, and configure a broker.

```console
cursor-agent plugin marketplace add https://github.com/Dumdart/TopicGate
```

Run `cursor-agent`, enter `/plugin`, and install TopicGate from the Marketplace tab. The plugin uses read-only mode.

For MCP without plugin skills, create `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "topicgate": {
      "command": "topicgate",
      "args": ["--mode", "read-only"]
    }
  }
}
```

Use `--mode control` only in a trusted project. Control mode can change connections, subscriptions, observations, and device state.

For profile provisioning and health expectations, see [Control mode and health verification](CONTROL_AND_HEALTH.md), including restart, tool-exposure checks, and credential limitations.
