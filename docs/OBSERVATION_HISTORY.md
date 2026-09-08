# Observation history

TopicGate event history contains individual MQTT receipts observed by TopicGate.
It is not authoritative broker history. The `mqtt_message` table remains the
latest stored value per broker/topic; diagnostic failure episodes are separate.

The history migration starts with an empty event store. It does not manufacture
events from latest-state rows. Broker deletion cascades to that broker's events;
downgrading past the history migration discards event history while preserving
latest-state rows. Observation UUIDs identify immutable events, and a separate
persistent insertion sequence supports stable snapshots without timestamp guesses.
