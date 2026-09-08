# Support bundles and safe sharing

TopicGate can collect bounded diagnostics through either Desktop or MCP. Both
delivery paths use the same application exporter, redaction manifest, limits,
serialization, and omission metadata.

## Desktop ZIP export

Choose **Help → Export support bundle…**. Review the manifest preview, choose
whether to include MQTT payloads, and select a destination. The suggested name is
timestamp-only and contains no broker or topic details.

The ZIP contains:

- `support-bundle.json`: structured bounded diagnostics;
- `README.md`: a human-readable rendering of the same bundle;
- `redaction-manifest.json`: the redaction categories, strategies, and counts.

Payloads are excluded by default. Enabling them requires a second confirmation.
Payload size is bounded, but TopicGate cannot determine whether a payload contains
personal data, device identifiers, access tokens, or other secrets. Review all
three files before sharing an opted-in archive.

## MCP access

`get_support_bundle(format="json")` returns structured JSON plus the manifest.
Use `format="markdown"` for a human-readable rendering. The tool is passive,
available in read-only and control modes, writes no file on the MCP host, accepts
no path or broker selector, and never includes MQTT payloads. Payload inclusion is
intentionally unavailable through MCP because responses may enter model context,
transcripts, logs, or external tooling.

## Redaction and remaining metadata risk

Credentials, passwords, connection secrets, credential-store identifiers, broker
names and IDs, hosts, usernames, and local filesystem/configuration paths are
structurally excluded. Topic names and broker identities are replaced with aliases.
Topic aliases are deterministic within one bundle so related records can be
correlated, but a new bundle uses new aliases.

The bundle still contains support-relevant metadata such as TopicGate, Python, and
operating-system versions; timestamps; ports; TLS use; connection and health
status; subscription settings; message and dropped-message counts; freshness; and
result totals. This metadata can reveal details about an environment even without
credentials or raw topic names.

Always retain and inspect collection warnings, limitations, omitted counts, and
truncation flags. An incomplete or truncated bundle is not proof that omitted state
was healthy or safe. Share the smallest bundle needed with a trusted recipient and
delete exported archives when they are no longer required.
