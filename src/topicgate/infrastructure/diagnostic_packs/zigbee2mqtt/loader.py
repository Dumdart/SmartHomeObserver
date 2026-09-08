import json
from decimal import Decimal
from importlib.resources import files
from typing import Any

from topicgate.core.interfaces.diagnostic_pack import PackReference
from topicgate.core.models.health import Condition
from topicgate.core.models.health import EqualCondition
from topicgate.core.models.health import FreshnessCondition
from topicgate.core.models.health import NumericRangeCondition
from topicgate.core.models.health import TopicExistsCondition
from topicgate.infrastructure.diagnostic_packs.zigbee2mqtt.diagnostic_pack import (
    PackCheck,
    Zigbee2MqttDiagnosticPack,
)

PACK_RESOURCE = "v1/pack.json"


def load_zigbee2mqtt_pack() -> Zigbee2MqttDiagnosticPack:
    resource = files(__package__).joinpath(PACK_RESOURCE)
    data = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("Zigbee2MQTT diagnostic pack must be a JSON object.")

    reference = PackReference(
        pack_id=_required_string(data, "pack_id"),
        version=_required_string(data, "version"),
    )
    raw_checks = data.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise ValueError("Zigbee2MQTT diagnostic pack must contain checks.")
    return Zigbee2MqttDiagnosticPack(
        reference,
        tuple(_parse_check(item) for item in raw_checks),
    )


def _parse_check(value: Any) -> PackCheck:
    if not isinstance(value, dict):
        raise ValueError("Diagnostic pack checks must be JSON objects.")
    json_field = value.get("json_field")
    if json_field is not None and not isinstance(json_field, str):
        raise ValueError("Diagnostic check json_field must be a string.")
    return PackCheck(
        check_id=_required_string(value, "id"),
        name=_required_string(value, "name"),
        description=_required_string(value, "description"),
        topic=_required_string(value, "topic"),
        json_field=json_field,
        condition=_parse_condition(value.get("condition")),
    )


def _parse_condition(value: Any) -> Condition:
    if not isinstance(value, dict):
        raise ValueError("Diagnostic check condition must be a JSON object.")
    kind = value.get("kind")
    if kind == "equal":
        return EqualCondition(_required_scalar(value, "expected"))
    if kind == "numeric_range":
        return NumericRangeCondition(
            Decimal(_required_number(value, "minimum")),
            Decimal(_required_number(value, "maximum")),
        )
    if kind == "freshness":
        return FreshnessCondition(float(_required_number(value, "max_age_seconds")))
    if kind == "topic_exists":
        return TopicExistsCondition()
    raise ValueError(f"Unsupported diagnostic condition kind: {kind!r}.")


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"Diagnostic pack field {key!r} must be a string.")
    return item


def _required_scalar(value: dict[str, Any], key: str) -> bytes:
    item = value.get(key)
    if not isinstance(item, (str, int, float)) or isinstance(item, bool):
        raise ValueError(f"Diagnostic pack field {key!r} must be a scalar.")
    return str(item).encode("utf-8")


def _required_number(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, (int, float)) or isinstance(item, bool):
        raise ValueError(f"Diagnostic pack field {key!r} must be a number.")
    return str(item)
