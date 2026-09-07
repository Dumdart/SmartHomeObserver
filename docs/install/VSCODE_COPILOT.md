# Connect TopicGate to VS Code or GitHub Copilot CLI

First [install TopicGate](OS_INSTALL.md), run `topicgate-gui`, and configure a broker.

## VS Code

Enable `chat.plugins.enabled`, then add the marketplace to `settings.json`:

```json
"chat.plugins.marketplaces": ["Dumdart/TopicGate"]
```

In Extensions, search for `@agentPlugins` and install TopicGate. Start a new chat.

## GitHub Copilot CLI

```console
copilot plugin marketplace add Dumdart/TopicGate
copilot plugin install topicgate@topicgate
```

Start a new session and run `/skills list`.

## MCP only

Create `.vscode/mcp.json`:

```json
{
  "servers": {
    "topicgate": {
      "type": "stdio",
      "command": "topicgate",
      "args": ["--mode", "read-only"]
    }
  }
}
```

Use `--mode control` only in a trusted workspace. Control mode can change connections, subscriptions, observations, and device state.

For profile provisioning and health expectations, see [Control mode and health verification](CONTROL_AND_HEALTH.md), including restart, tool-exposure checks, and credential limitations.
