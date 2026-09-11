# Install TopicGate by operating system

TopicGate requires Python 3.11+ and access to an MQTT 5-compatible broker. Use an isolated `uv` tool installation where possible.

| Platform | Status |
| --- | --- |
| Windows Desktop | Verified; primary release environment. |
| Ubuntu Desktop | Verified with desktop startup, broker connection, subscriptions, and restart/reconnect. |
| Ubuntu Server | Passwordless read-only MCP is partially verified; authenticated unattended use is unsupported. |
| Other Linux distributions and macOS | Not verified end to end. |

## Windows

If needed, [install uv](https://docs.astral.sh/uv/getting-started/installation/), then open a new terminal. Install TopicGate:

```powershell
uv tool install topicgate
```

Alternatively, if you already manage a Python 3.11+ environment, install with pip:

```powershell
python -m pip install topicgate
```

Run TopicGate Desktop:

```powershell
topicgate-gui
```

Passwords are stored in Windows Credential Locker.

## Ubuntu Desktop

Do not use `sudo pip` or `pip --break-system-packages`. Install uv and TopicGate:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv tool install --python 3.11 topicgate
uv tool update-shell
```

Open a new terminal, then start TopicGate Desktop from the logged-in graphical session:

```bash
topicgate-gui
```

Do not run the desktop with `sudo` or through SSH. PySide6 and the Secret Service credential store require the logged-in desktop session. After configuring a password-protected broker, verify reconnection after a complete logout and login.

## Configure a broker and optionally connect an agent

1. In TopicGate Desktop, open **Profiles...** beside the broker selector and add a profile with your broker's host, port, TLS settings, and credentials.
2. Select the profile, connect, and use **Add subscription** with a bounded filter such as `home/+/temperature`.
3. Check that observed values arrive. Non-retained messages appear only while TopicGate is listening.

Broker passwords stay in the local operating-system credential store; they are not passed through MCP. Desktop users can stop here: no agent configuration is required.

For AI access, follow [Codex](CODEX.md), [Claude Code](CLAUDE_CODE.md), [Cursor](CURSOR.md), or [VS Code / Copilot](VSCODE_COPILOT.md). Install the agent host on the same machine and under the same OS user as TopicGate for the normal local workflow. Remote/container hosts need their own executable and access to the intended local data; a workstation path will not work there.

The host guides describe the current repository CLI. If your installed release does not recognize an integration command or host name, use the guide's manual plugin or MCP-only alternative. Check available hosts with `topicgate-cli integration install --help`.

**Help → MCP setup...** provides the executable, shared data directory, copyable configuration, and local preflight checks. Selecting Control there only changes the configuration preview; it does not reconfigure a running agent.

| Command | Purpose |
| --- | --- |
| `topicgate-gui` | Open TopicGate Desktop. |
| `topicgate-cli` | Administration commands; use `--help` to list subcommands. |
| `topicgate` | Start the stdio MCP server in read-only mode. |
| `topicgate --mode read-only` | Explicitly select the same default mode. |
| `topicgate --mode control` | Start the MCP server with privileged tools enabled. |

Your host normally starts the MCP server. Do not run the blocking stdio command in a terminal as a Desktop launch or connectivity test; use `topicgate --help` to verify command availability. Control mode is a separate [explicit opt-in](CONTROL_AND_HEALTH.md).

## Headless broker configuration

The `topicgate-cli` command configures broker profiles and subscriptions without starting the desktop. Add a profile with an MQTT password prompt:

```console
topicgate-cli profile add --name "Home" --host mqtt.example.com --port 1883 --username topicgate
```

Use `--use-tls` to enable TLS or `--no-password` to skip the password prompt for a passwordless broker. Existing profile names are left unchanged, so the command can be safely repeated. List profiles or test every configured profile with:

```console
topicgate-cli profile list
topicgate-cli profile test
```

Add and remove subscriptions by profile name:

```console
topicgate-cli sub add --name "Home" --topic "home/+/temperature" --qos 1
topicgate-cli sub remove --name "Home" --topic "home/+/temperature"
```

Subscription QoS can be `0`, `1`, or `2`. The optional `--retain-as-published` flag preserves the broker's retain flag, and `--retain-handling` accepts `0`, `1`, or `2`.

> Profiles created by the headless command become available to newly started TopicGate processes immediately. Restart an already-running desktop or MCP process to reload externally created profiles.

## Ubuntu Server

The CLI, migrations, disconnected read-only startup, and clean shutdown have been manually verified in a headless environment. The published base package still declares PySide6 as a dependency; it does not provide a separate headless installation extra. Use `topicgate-cli` for passwordless headless broker configuration.

Authenticated unattended operation is unsupported because Secret Service may be locked or unavailable after SSH login. Do not use plaintext keyrings, `keyrings.alt`, or database edits as production workarounds. A null keyring is suitable only for passwordless testing.

## Verify

On a desktop system, check command availability and launch the application. On a headless server, run only the help command.

```console
topicgate --help
topicgate-gui
```

After setup, restart TopicGate Desktop and verify broker reconnection. See [Upgrades and recovery](UPGRADE_AND_RECOVERY.md) for maintenance and troubleshooting.
