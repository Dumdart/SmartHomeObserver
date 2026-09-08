import json
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.tools import tool

from topicgate.app.services.support_bundle_export_service import (
    SupportBundleExporter,
)
from topicgate.mcp.api.mcp_api import MCPApi


class SupportBundleAPI(MCPApi):
    """Expose bounded redacted diagnostics without host filesystem access."""

    def __init__(self, exporter: SupportBundleExporter) -> None:
        self._exporter = exporter

    def register(self, mcp: FastMCP, *, control_enabled: bool = False) -> None:
        mcp.add_tool(self.get_support_bundle)

    @tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def get_support_bundle(
        self,
        format: Literal["json", "markdown"] = "json",
    ) -> dict[str, Any]:
        """Return bounded, redacted TopicGate diagnostics and their manifest.

        Side effects: None; this reads local diagnostic state and never writes files,
        connects, subscribes, publishes, or includes MQTT payloads.
        Required state: The local TopicGate database must be readable; disconnected
        brokers and incomplete collection are represented as explicit limitations.
        Identifiers: None; configured brokers use per-bundle aliases.
        Failures: Fails for an unsupported format or diagnostic collection error.
        """
        if format not in ("json", "markdown"):
            raise ValueError("format must be 'json' or 'markdown'.")
        artifacts = self._exporter.export()
        content: dict[str, Any] | str = (
            json.loads(artifacts.json)
            if format == "json"
            else artifacts.markdown
        )
        return {
            "format": format,
            "content": content,
            "redaction_manifest": json.loads(artifacts.manifest),
            "warnings": list(artifacts.warnings),
        }
