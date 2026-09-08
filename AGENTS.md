# TopicGate repository instructions

## Working approach

- Inspect the affected code, nearby tests, and current call paths before editing.
- Work in small, focused, testable steps. Keep changes scoped to the requested behavior and avoid unrelated refactors.
- Follow existing architecture and naming patterns before introducing new abstractions. Prefer simple, explicit interfaces and reuse existing logic.
- Preserve user changes in a dirty worktree. Review the final diff and remove only temporary changes introduced for the task.

## Architecture

- Keep `topicgate.core` independent of GUI, MCP, and infrastructure concerns. Put domain models, policies, and interfaces there.
- Put application orchestration and service composition in `topicgate.app`. Use `AppDependencies` and `TopicGateRuntime` as the principal composition and lifecycle seams.
- Put external adapters in `topicgate.infrastructure`, including MQTT, SQLAlchemy/Alembic persistence, credentials, filesystem access, and diagnostic-pack loading.
- Keep desktop behavior in `topicgate.gui`, MCP transports and APIs in `topicgate.mcp`, CLI entry points in `topicgate.cli`, and formatting/view contracts in `topicgate.presentation`.
- Do not let GUI or MCP entry points duplicate domain or application behavior. Expose shared behavior through an application service and keep entry points thin.
- Keep broker-scoped state explicitly keyed by broker identity. Do not conflate a selected broker profile with a successfully connected MQTT session.
- Treat the `mqtt_message` table as the latest-state projection. Do not use it as immutable observation history; history requires a separate model, repository, and migration.

## Python style

- Use Python 3.11+, PEP 8, four-space indentation, and type hints on public APIs.
- Use `snake_case` for modules, functions, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants.
- Prefer small, single-purpose functions and explicit dependencies over globals or hidden service lookup.
- Add comments only for non-obvious intent, constraints, or decisions. Begin necessary complex-logic comments with `# Check ...`.
- Do not leave TODOs, debug output, dead code, or compatibility shims without an active requirement.

## Persistence, credentials, and external behavior

- Make schema changes through the packaged Alembic environment under `src/topicgate/infrastructure/database/alembic`; update migration tests and distribution coverage with it.
- Keep credentials in the credential-store abstraction. Never write secrets, credential-store identifiers, broker passwords, or raw sensitive payloads to logs, snapshots, support bundles, fixtures, or committed files.
- Treat MQTT callbacks, reconnects, background writers, and GUI/async bridges as concurrent boundaries. Preserve atomic state updates, bounded waits, and deterministic shutdown behavior.
- Treat MCP tool names, parameters, response shapes, and error semantics as public contracts. Call out externally visible MQTT or MCP changes in the PR.
- Keep optional app/dashboard imports behind the `apps` extra so the base package and MCP server remain installable without optional UI dependencies.

## Tests and validation

- Add or update regression tests for behavioral changes and bug fixes. Place tests in the nearest existing `tests/test_*.py` module and match established fixture/fake patterns.
- Isolate filesystem, OS credentials, databases, clocks, and MQTT state with temporary paths, fixtures, or fakes. Tests must not require a live broker, desktop session, credential store, or network access unless explicitly marked as an integration test.
- Run the narrowest relevant tests while iterating, then run the full suite before finishing:

  ```powershell
  uv run pytest tests/test_relevant_module.py -q
  uv run pytest
  ```

- Install the complete development dependency set with `uv sync --extra apps --extra test` when needed.
- Run `uv build` for packaging, entry-point, package-data, migration, plugin-bundle, or release-related changes. For release metadata changes, also run `python -m release.run_ci`.
- If a required check cannot run, report the exact command, failure, and what remains unverified.

## Repository map and documentation

- Before answering codebase-wide questions, check `graphify-out/graph.json` and query it with `graphify query "<question>"`; verify important conclusions against the current source.
- Run `graphify . --update` after major architecture changes and before opening a PR that changes repository relationships.
- Update user-facing documentation when commands, configuration, setup, upgrade/recovery behavior, MCP workflows, or screenshots change.
- Keep generated artifacts, local databases, caches, and credentials out of commits. Update `.gitignore` when a new local-only artifact is introduced.

## Commits and pull requests

- Keep commits atomic and use a scope-led imperative subject, for example `MqttState: Add Startup Hydration`.
- PR descriptions must explain the problem and solution, link the relevant issue, and list the exact validation commands run.
- Include before/after screenshots for GUI changes.
- Explicitly call out migrations, configuration or dependency changes, compatibility impact, and externally visible MQTT or MCP behavior.
