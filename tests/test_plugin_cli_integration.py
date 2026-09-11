import sys

from topicgate.infrastructure.integrations._plugin_cli import _normal_path


def test_normal_path_preserves_case_on_case_sensitive_platform(
    monkeypatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    assert _normal_path("/opt/TopicGate/topicgate") != _normal_path(
        "/opt/topicgate/topicgate"
    )


def test_normal_path_ignores_case_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    assert _normal_path("C:/TopicGate/topicgate.exe") == _normal_path(
        "C:/topicgate/topicgate.exe"
    )
