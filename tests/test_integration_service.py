from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from topicgate.app.models.integration import (
    IntegrationActionKind,
    IntegrationPlan,
    IntegrationResult,
    McpMode,
    McpServerState,
    PlatformIntegrationState,
)
from topicgate.app.models.mcp_setup import McpSetupInformation
from topicgate.app.services.integration_service import IntegrationService


def service() -> IntegrationService:
    information = McpSetupInformation(
        version="1.5.2",
        executable_path=Path("/opt/topicgate/bin/topicgate"),
        command=str(Path("/opt/topicgate/bin/topicgate")),
        command_prefix_arguments=(),
        data_path=Path("/var/lib/topicgate"),
        database_path=Path("/var/lib/topicgate/topicgate.db"),
    )
    return IntegrationService(information)


def test_install_builds_shared_spec_and_skips_an_aligned_platform() -> None:
    platform = MagicMock()
    platform.name = "codex"
    current = PlatformIntegrationState("codex", available=True)
    platform.plan.return_value = IntegrationPlan(
        desired=MagicMock(), current=current, actions=()
    )

    result = service().install(platform, McpMode.CONTROL)

    desired = platform.plan.call_args.args[0]
    assert desired.platform == "codex"
    assert desired.mode is McpMode.CONTROL
    assert desired.package_version == "1.5.2"
    assert desired.arguments == ("--mode", "control")
    assert desired.environment == (
        ("TOPICGATE_DATA_DIR", str(Path("/var/lib/topicgate"))),
    )
    assert result.state is current
    assert not result.changed
    platform.apply.assert_not_called()


def test_repair_preserves_the_single_configured_mode() -> None:
    platform = MagicMock()
    platform.name = "codex"
    platform.inspect.return_value = PlatformIntegrationState(
        "codex",
        available=True,
        servers=(
            McpServerState(
                "topicgate",
                "topicgate",
                ("--mode", "control"),
            ),
        ),
    )
    expected = IntegrationResult(platform.inspect.return_value, changed=False)
    platform.plan.return_value = IntegrationPlan(
        desired=MagicMock(),
        current=platform.inspect.return_value,
        actions=(),
    )

    result = service().repair(platform)

    assert result.changed is False
    assert platform.plan.call_args.args[0].mode is McpMode.CONTROL
    assert expected.state is result.state


@pytest.mark.parametrize(
    ("arguments", "expected"),
    (
        (("--mode=control",), McpMode.CONTROL),
        (("--mode", "read-only", "--mode=control"), McpMode.CONTROL),
        (("--mode=control", "--mode", "read-only"), McpMode.READ_ONLY),
    ),
)
def test_mcp_server_mode_supports_equals_form_and_last_occurrence(
    arguments: tuple[str, ...],
    expected: McpMode,
) -> None:
    server = McpServerState("topicgate", "topicgate", arguments)

    assert server.mode is expected


def test_status_reports_package_plugin_version_mismatch() -> None:
    platform = MagicMock()
    platform.name = "codex"
    current = PlatformIntegrationState(
        "codex",
        available=True,
        marketplace_configured=True,
        plugin_installed=True,
        plugin_version="1.4.0",
    )
    platform.inspect.return_value = current
    platform.plan.return_value = IntegrationPlan(
        desired=MagicMock(),
        current=current,
        actions=(
            SimpleNamespace(kind=IntegrationActionKind.INSTALL_PLUGIN),
        ),
    )

    state = service().status(platform)

    assert state.issues == (
        "TopicGate plugin version does not match the installed "
        "TopicGate package 1.5.2.",
    )


def test_status_does_not_report_a_disabled_plugin_as_outdated() -> None:
    platform = MagicMock()
    platform.name = "codex"
    current = PlatformIntegrationState(
        "codex",
        available=True,
        marketplace_configured=True,
        plugin_installed=True,
        plugin_version="1.5.2",
        plugin_enabled=False,
        issues=("TopicGate plugin is disabled.",),
    )
    platform.inspect.return_value = current
    platform.plan.return_value = IntegrationPlan(
        desired=MagicMock(),
        current=current,
        actions=(
            SimpleNamespace(kind=IntegrationActionKind.INSTALL_PLUGIN),
        ),
    )

    state = service().status(platform)

    assert state.issues == ("TopicGate plugin is disabled.",)
