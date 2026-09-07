"""Optional wire test: starts its own loopback Mosquitto with isolated config."""

import asyncio
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

from topicgate.app.app_dependencies import AppDependencies
from topicgate.core.config.mqtt_config import MqttConfig
from topicgate.core.models.subscription import Subscription
from topicgate.mcp.api.expectation_api import ExpectationAPI
from topicgate.mcp.requests.expectation_requests import CreateExpectationRequest


@pytest.fixture
def local_mqtt(tmp_path: Path):
    executable = shutil.which("mosquitto")
    if executable is None:
        pytest.skip(
            "Install Mosquitto on PATH to run the disposable loopback wire test."
        )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = tmp_path / "mosquitto.conf"
    config.write_text(
        f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n",
        encoding="utf-8",
    )
    process = subprocess.Popen(
        [executable, "-c", str(config)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        yield port, process
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


async def test_local_wire_wait_without_reconnect(
    local_mqtt, tmp_path, credential_store
):
    port, process = local_mqtt
    for _ in range(100):
        if process.poll() is not None:
            pytest.fail("Disposable Mosquitto failed to start.")
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            break
        except OSError:
            await asyncio.sleep(0.05)
    else:
        pytest.fail("Disposable Mosquitto did not become ready.")
    deps = AppDependencies(tmp_path / "data", credential_store)
    try:
        broker = deps.runtime.create_broker(
            "Isolated wire test", MqttConfig("127.0.0.1", port, "", "")
        )
        await deps.runtime.activate_broker(broker.id)
        await deps.runtime.add_subscription(broker.id, Subscription("test/health/#"))
        api = ExpectationAPI(
            deps.expectation_management_service, deps.broker_resolver, deps.runtime
        )
        api.create_health_expectation(
            broker.id,
            CreateExpectationRequest.model_validate(
                {
                    "name": "Wire connection",
                    "description": "Local disposable broker connected",
                    "target": {"kind": "broker"},
                    "condition": {
                        "kind": "equal",
                        "expected": {"encoding": "text", "value": "connected"},
                    },
                }
            ),
        )
        connected_at = deps.runtime.active_repo.connected_at
        result = await deps.health_wait_service.wait(
            broker.id,
            stable_for_seconds=0.1,
            poll_interval_seconds=0.1,
            timeout_seconds=2,
        )
        assert result["outcome"] == "satisfied"
        assert deps.runtime.active_repo.connected_at == connected_at
        assert result["connection_side_effects"] == []
    finally:
        await deps.runtime.stop()
        deps.topic_messages.close()
        deps._db_context.dispose()
