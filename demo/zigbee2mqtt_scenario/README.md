# Zigbee2MQTT scenario

This hardware-free demo starts a disposable local Mosquitto broker and guides
you through a deterministic Zigbee2MQTT-shaped scenario in TopicGate. It needs
neither Zigbee hardware nor Home Assistant.

For the concise public journey built from this data, see the
[release-matched diagnosis](https://github.com/Dumdart/TopicGate/blob/master/docs/DEMO.md):
observation → expectation failure → explanation → recovery, followed by a
read-only agent example.

## Prerequisites

- Docker with Docker Compose
- Python 3.11 or newer
- `uv`
- Bash, such as Git Bash on Windows

From the repository root, install the project dependencies if needed:

```console
uv sync --extra apps --extra test
```

## Run the guided scenario

From the repository root, run one command:

```bash
bash demo/zigbee2mqtt_scenario/run_demo.sh
```

The script does the setup for you. It starts the broker, creates and tests the
`Zigbee2MQTT Demo` broker profile, and adds the `zigbee2mqtt/#` subscription.
It then pauses at each of these steps:

1. Start TopicGate Desktop yourself with `uv run topicgate-gui`, select
   `Zigbee2MQTT Demo`, and connect.
2. Continue to publish the full scenario. Inspect the healthy, offline,
   retained, and missing conditions while they are observed as **Live**.
3. Continue and close TopicGate completely. The publisher stops.
4. Restart TopicGate and reconnect. Continue to resume only the healthy device.
5. The kitchen state becomes **Live** again, while the previously stored,
   non-retained attic state is now **Stale**.
6. Inspect the result, close TopicGate, and continue once more. The script
   removes the broker and only the TopicGate configuration it created.

The script deliberately does not launch the GUI. This keeps the beginner flow
visible and avoids hiding a second process behind the terminal.

## Stop and recover

Press Ctrl+C at any prompt to stop the publisher and remove the demo broker and
its volumes. If TopicGate may still be open, the script preserves its small
ownership record instead of changing TopicGate's database underneath the app.
Close TopicGate, then finish cleanup with:

```bash
bash demo/zigbee2mqtt_scenario/run_demo.sh cleanup
```

Cleanup uses a dedicated Compose project name and removes its containers,
network, volumes, and orphans. If the profile or subscription existed before
the demo, it is validated and preserved. A conflicting pre-existing
configuration is reported instead of overwritten.

## Interpret the conditions

TopicGate's state badge describes when TopicGate observed a message, not the
device's decoded health:

- `kitchen_sensor` is healthy. Its exact state repeats every five seconds and
  becomes **Live** in both phases.
- `garage_sensor/availability` is **Live**, while its decoded payload explicitly
  reports `{"state":"offline"}`.
- `attic_sensor` is non-retained and published only in the first phase. After
  TopicGate restarts, its stored observation predates the new observation
  session and is genuinely **Stale**. Its fixed historical `last_seen` value is
  payload data; TopicGate does not derive its badge from that field.
- `basement_freezer` is retained by the broker. The client that published it
  disconnects immediately, so a later subscriber still receives the value.
- `nursery_sensor` is listed in `zigbee2mqtt/bridge/devices` but its expected
  state topic is never published. That inventory makes the missing topic an
  assertable completeness condition.

## Canonical scenario

<!-- scenario-table:start -->
| Device | Condition | Topic | Evidence |
| --- | --- | --- | --- |
| `kitchen_sensor` | healthy | `zigbee2mqtt/kitchen_sensor` | Online availability and state refreshed every five seconds. |
| `garage_sensor` | offline | `zigbee2mqtt/garage_sensor/availability` | Availability is explicitly offline. |
| `attic_sensor` | stale | `zigbee2mqtt/attic_sensor` | Non-retained state is published only in the full phase, then cached. |
| `basement_freezer` | retained/disconnected | `zigbee2mqtt/basement_freezer` | A short-lived client publishes retained state, then disconnects. |
| `nursery_sensor` | missing expected topic | `zigbee2mqtt/nursery_sensor` | Listed in bridge/devices but intentionally never published. |
<!-- scenario-table:end -->

All JSON is encoded with sorted keys and fixed values. Print this catalog with:

```console
uv run python demo/zigbee2mqtt_scenario/publisher.py --describe
```

## Automated checks

The ordinary unit tests use an in-memory publisher fake:

```console
uv run pytest tests/test_zigbee2mqtt_scenario.py tests/test_topicgate_cli.py
```

The explicit smoke test chooses an unused local port, starts its own Compose
project, subscribes before publishing, asserts the exact full-phase messages,
checks the retained value with a late subscriber, verifies that the healthy
phase omits the attic state, and always tears the project down:

```console
uv run python demo/zigbee2mqtt_scenario/smoke_test.py
```

This real-broker check is intentionally separate from the default pytest suite
because it requires a running Docker engine.
