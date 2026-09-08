# Observation history

TopicGate event history contains individual MQTT receipts observed by TopicGate.
It is not authoritative broker history. The `mqtt_message` table remains the
latest stored value per broker/topic; diagnostic failure episodes are separate.

The history migration starts with an empty event store. It does not manufacture
events from latest-state rows. Broker deletion cascades to that broker's events;
downgrading past the history migration discards event history while preserving
latest-state rows. Observation UUIDs identify immutable events, and a separate
persistent insertion sequence supports stable snapshots without timestamp guesses.

Recording is disabled by default and enabled independently for each broker.
Disabling stops admission and drains already admitted events without deleting them.
Only receipts during enabled observation periods can appear in history; reconnects
and retained MQTT messages are new receipts, not reconstructions of past activity.

History uses a FIFO queue bounded by 1,000 pending events and 16 MiB of pending
payloads, including writes in flight. Batches contain at most 100 events. When
full, new history copies are dropped while current state and health evaluation
continue. Failed batches roll back and later writes may proceed; recording
counters expose admissions, commits, pending writes, drops, and failures.

Sessions checkpoint counters to persistence. A previous unclosed session means
potentially incomplete history, not an exact count of events lost in a crash.
Shutdown gives history ten seconds to drain and reports failures or timeout.
Exactly-once delivery across crashes is not guaranteed. Pending history is drained
before broker deletion; a drain timeout aborts deletion.

## Opt-in and independent retention

Open **Stored observations → History settings**, select a broker, check
**Record new events for this broker (opt-in)** and apply. The shared history
limits start at seven days, 100,000 events per broker, no per-topic cap, and
256 MiB of stored payloads globally. Age and per-topic caps can be left blank.
Large history retention limits may slow startup and history queries.

The size budget counts stored payload bytes, not database file bytes, indexes,
or filesystem allocation. Zero-byte events still count toward event limits.
Policy changes validate before persistence. Pruning applies age, per-topic count,
per-broker count, then the global byte budget, always oldest first with UUIDs
breaking timestamp ties. Each eviction is attributed to its first applicable limit.

Pruning removes at most 500 events per transaction (adjustable downward), releases
locks between batches, and resumes at most once per second when more work may
remain. Idle checks default to 60 seconds and are adjustable. Enforcement is
eventual: usage may temporarily exceed limits between batches. Settings report
broker usage and global eviction totals, last prune, and pending enforcement.
Turning recording off does not stop retention enforcement or erase saved events.
