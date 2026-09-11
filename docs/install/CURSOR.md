# Connect TopicGate to Cursor

First [install TopicGate](OS_INSTALL.md), run `topicgate-gui`, and configure and connect a broker in **TopicGate Desktop**. Install the host itself before running its integration commands.

Choose one setup path. A **plugin** adds TopicGate skills and MCP configuration; **MCP-only** adds the server without those skills. Both start in **read-only mode**.

## Plugin integration

Install Cursor Agent CLI (`cursor-agent`) first. TopicGate can configure its marketplace and user-level MCP server:

```console
topicgate-cli integration install cursor
```

Inspect, repair, or remove that configuration with:

```console
topicgate-cli integration status cursor
topicgate-cli integration repair cursor
topicgate-cli integration uninstall cursor
```

Cursor Agent does not currently expose non-interactive plugin installation.
After the command completes, run `cursor-agent`, open `/plugin`, and install or
update TopicGate from the Marketplace tab to add its skills. The MCP integration
works independently of that optional interactive step.

### Manual plugin installation

```console
cursor-agent plugin marketplace add https://github.com/Dumdart/TopicGate
```

Run `cursor-agent`, enter `/plugin`, and install TopicGate from the Marketplace tab. The plugin uses read-only mode.

## MCP-only alternative

For MCP without plugin skills, merge this into `.cursor/mcp.json` in your project, or `~/.cursor/mcp.json` for all projects:

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

If the host cannot find `topicgate`, use the command, arguments, and data-directory environment from Desktop's **Help → MCP setup...**. Restart Cursor after changes and start a new session. Check the enabled MCP servers if you combined manual and helper setup.

## Control mode

Explicitly opt in only in a trusted environment:

```console
topicgate-cli integration install cursor --mode control
```

For MCP-only setup, change the existing entry's `args` to `["--mode", "control"]` and restart Cursor. The helper does not install, update, or remove the interactive plugin; use `/plugin` for those actions.

For profile provisioning and health expectations, see [Control mode and health verification](CONTROL_AND_HEALTH.md), including restart, tool-exposure checks, and credential limitations.
