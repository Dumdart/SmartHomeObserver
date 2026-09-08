import json
from pathlib import Path
import shutil
import subprocess
import sys

from fastmcp import Client


REPOSITORY_ROOT = Path(__file__).parents[1]
PLUGIN_ROOT = REPOSITORY_ROOT / "topicgate-plugin"


def test_plugin_bundle_matches_codex_ingestion_contract() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    interface = manifest["interface"]
    marketplace = json.loads(
        (REPOSITORY_ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    marketplace_plugin = next(
        plugin for plugin in marketplace["plugins"] if plugin["name"] == manifest["name"]
    )

    assert marketplace_plugin["source"]["path"] == "./topicgate-plugin"
    assert manifest["author"]["name"]
    assert manifest["mcpServers"] == "./.mcp.json"
    contract = (PLUGIN_ROOT / "CONTRACT.md").read_text(encoding="utf-8")
    assert "MCP contract `1.0`" in contract
    assert (PLUGIN_ROOT / ".mcp-control.json").is_file()
    assert interface["longDescription"]
    assert interface["developerName"]
    assert interface["category"]
    assert interface["capabilities"]
    assert 1 <= len(interface["defaultPrompt"]) <= 3

    for asset_field in ("composerIcon", "logo"):
        asset = PLUGIN_ROOT / interface[asset_field].removeprefix("./")
        assert asset.is_file()


def test_plugin_bundle_matches_claude_code_ingestion_contract() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )
    marketplace = json.loads(
        (REPOSITORY_ROOT / ".claude-plugin" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    marketplace_plugin = next(
        plugin for plugin in marketplace["plugins"] if plugin["name"] == manifest["name"]
    )

    assert marketplace_plugin["source"] == "./topicgate-plugin"
    assert marketplace_plugin["version"] == manifest["version"]
    assert manifest["author"]["name"]
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert (PLUGIN_ROOT / manifest["skills"].removeprefix("./")).is_dir()
    assert (PLUGIN_ROOT / manifest["mcpServers"].removeprefix("./")).is_file()


def test_plugin_bundle_matches_copilot_and_cursor_agent_plugins_contract() -> None:
    manifest = json.loads(
        (PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8")
    )
    mcp_config = json.loads(
        (PLUGIN_ROOT / "mcp.json").read_text(encoding="utf-8")
    )
    marketplace = json.loads(
        (REPOSITORY_ROOT / ".claude-plugin" / "marketplace.json").read_text(
            encoding="utf-8"
        )
    )
    marketplace_plugin = next(
        plugin for plugin in marketplace["plugins"] if plugin["name"] == manifest["name"]
    )
    server_config = mcp_config["mcpServers"]["topicgate"]

    assert manifest["$schema"] == (
        "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    )
    assert marketplace_plugin["source"] == "./topicgate-plugin"
    assert marketplace_plugin["version"] == manifest["version"]
    assert (PLUGIN_ROOT / "skills").is_dir()
    assert mcp_config["$schema"] == (
        "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
    )
    assert server_config["type"] == "stdio"
    assert server_config["command"] == "topicgate"
    assert server_config["args"] == ["--mode", "read-only"]


def test_plugin_skills_have_valid_frontmatter() -> None:
    skill_files = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))

    assert skill_files
    for skill_file in skill_files:
        lines = skill_file.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "---"
        closing_delimiter = lines.index("---", 1)
        frontmatter = lines[1:closing_delimiter]
        assert f"name: {skill_file.parent.name}" in frontmatter
        assert any(line.startswith("description: ") for line in frontmatter)


async def test_cached_plugin_bundle_exposes_read_only_tools(
    tmp_path: Path,
) -> None:
    cached_plugin = tmp_path / "topicgate"
    shutil.copytree(PLUGIN_ROOT, cached_plugin)
    config = json.loads((cached_plugin / ".mcp.json").read_text(encoding="utf-8"))
    server_config = config["mcpServers"]["topicgate"]

    assert "$schema" not in config
    assert server_config["command"] == "topicgate"
    assert server_config["args"] == ["--mode", "read-only"]
    assert "env" not in server_config

    # Check the bundle contract separately from PATH discovery in this test process.
    server_config["command"] = sys.executable
    server_config["args"] = ["-m", "topicgate", "--mode", "read-only"]
    server_config["env"] = {
        "PYTHONPATH": str(REPOSITORY_ROOT / "src"),
        "TOPICGATE_DATA_DIR": str(tmp_path / "data"),
    }

    async with Client(config) as client:
        tools = await client.list_tools()

    assert {tool.name for tool in tools} == {
        "get_broker_snapshot",
        "get_connection_status",
        "get_topic_state",
        "inspect_broker",
        "list_brokers",
        "list_subscriptions",
        "list_topics",
        "query_failure_history",
        "list_health_expectations",
    }


def test_topicgate_package_supports_python_module_execution() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "topicgate", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0
    assert "Run the TopicGate MCP server." in result.stdout


async def test_copied_control_bundle_exposes_provisioning_and_health(tmp_path: Path) -> None:
    cached_plugin = tmp_path / "topicgate"
    shutil.copytree(PLUGIN_ROOT, cached_plugin)
    config = json.loads((cached_plugin / ".mcp-control.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["topicgate"]
    assert server["args"] == ["--mode", "control"]
    server["command"] = sys.executable
    server["args"] = ["-m", "topicgate", "--mode", "control"]
    server["env"] = {"TOPICGATE_DATA_DIR": str(tmp_path / "data")}
    async with Client(config) as client:
        names = {item.name for item in await client.list_tools()}
        assert {"create_broker", "activate_broker", "add_subscription",
                "list_health_expectations", "create_health_expectation",
                "update_health_expectation", "delete_health_expectation",
                "get_health_report", "wait_for_broker_health", "query_failure_history"} <= names
        result = await client.call_tool("create_broker", {
            "request": {"name": "Saved only", "host": "127.0.0.1", "port": 18830}
        })
        assert result.data["status"] == "saved"
        assert not result.data["connected"]
