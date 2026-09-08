import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastmcp import Client, FastMCP

from topicgate.core.models.support_bundle import SupportBundleArtifacts
from topicgate.mcp.api.support_bundle_api import SupportBundleAPI


JSON_BUNDLE = {
    "schema_version": "1.0",
    "bundle_id": "support-redacted",
    "payloads_included": False,
    "diagnostic_results": {
        "limit": 2,
        "total": 4,
        "returned": 2,
        "omitted": 2,
    },
    "brokers": [
        {
            "broker_alias": "broker-redacted-001",
            "topics": [{"topic_alias": "topic-redacted-001"}],
            "topic_results": {
                "limit": 1,
                "total": 3,
                "returned": 1,
                "omitted": 2,
            },
            "limitations": ["current_state_only"],
        }
    ],
    "limitations": ["diagnostic_results_omitted"],
}
MANIFEST = {
    "schema_version": "1.0",
    "bundle_id": "support-redacted",
    "policies": [
        {
            "category": "credentials",
            "strategy": "structurally_excluded",
            "occurrence_count": 1,
        },
        {
            "category": "payloads_and_evidence",
            "strategy": "structurally_excluded",
            "occurrence_count": 1,
        },
    ],
}
MARKDOWN_BUNDLE = """# TopicGate support bundle

- Payloads included: `no`
- Diagnostics omitted: `2`
"""


def _api() -> tuple[SupportBundleAPI, MagicMock]:
    exporter = MagicMock()
    exporter.export.return_value = SupportBundleArtifacts(
        json.dumps(JSON_BUNDLE),
        MARKDOWN_BUNDLE,
        json.dumps(MANIFEST),
        ("Diagnostics omitted by bounds: 2.",),
    )
    return SupportBundleAPI(exporter), exporter


@pytest.mark.parametrize("control_enabled", (False, True))
async def test_support_bundle_tool_registers_in_both_modes(
    control_enabled: bool,
) -> None:
    api, _ = _api()
    mcp = FastMCP("test")
    api.register(mcp, control_enabled=control_enabled)

    async with Client(mcp) as client:
        tools = {item.name: item for item in await client.list_tools()}

    assert set(tools) == {"get_support_bundle"}
    selected = tools["get_support_bundle"]
    assert selected.annotations.readOnlyHint is True
    assert selected.annotations.openWorldHint is False
    assert selected.inputSchema["properties"]["format"]["enum"] == [
        "json",
        "markdown",
    ]


async def test_support_bundle_tool_renders_json_and_markdown_with_manifest() -> None:
    api, exporter = _api()
    mcp = FastMCP("test")
    api.register(mcp)

    async with Client(mcp) as client:
        json_result = (await client.call_tool("get_support_bundle", {})).data
        markdown_result = (
            await client.call_tool(
                "get_support_bundle",
                {"format": "markdown"},
            )
        ).data

    assert json_result["content"] == JSON_BUNDLE
    assert markdown_result["content"] == MARKDOWN_BUNDLE
    assert json_result["redaction_manifest"] == MANIFEST
    assert markdown_result["redaction_manifest"] == MANIFEST
    assert json_result["warnings"] == ["Diagnostics omitted by bounds: 2."]
    assert json_result["content"]["diagnostic_results"]["omitted"] == 2
    assert json_result["content"]["brokers"][0]["topic_results"]["omitted"] == 2
    assert exporter.export.call_count == 2
    assert all(
        call.args == () and call.kwargs == {}
        for call in exporter.export.call_args_list
    )


async def test_support_bundle_tool_rejects_invalid_format() -> None:
    api, exporter = _api()
    with pytest.raises(ValueError, match="format must be"):
        api.get_support_bundle("xml")  # type: ignore[arg-type]
    exporter.export.assert_not_called()

    mcp = FastMCP("test")
    api.register(mcp)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_support_bundle",
            {"format": "xml"},
            raise_on_error=False,
        )
    assert result.is_error


def test_support_bundle_tool_has_no_payload_or_path_escape_parameter(
    tmp_path: Path,
) -> None:
    api, _ = _api()
    before = tuple(tmp_path.iterdir())

    result = api.get_support_bundle()

    assert tuple(tmp_path.iterdir()) == before
    assert set(inspect.signature(api.get_support_bundle).parameters) == {"format"}
    rendered = json.dumps(result)
    assert "payloads_included\": true" not in rendered.lower()
    assert "password" not in rendered.lower()
    assert "credential-store-id" not in rendered.lower()
