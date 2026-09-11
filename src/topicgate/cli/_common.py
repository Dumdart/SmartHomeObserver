"""Shared types and output helpers for TopicGate CLI commands."""

import sys
from collections.abc import Callable

from topicgate.app.app_dependencies import AppDependencies


DependenciesFactory = Callable[[], AppDependencies]


def print_error(error: Exception) -> None:
    message = error.args[0] if isinstance(error, KeyError) else str(error)
    print(f"Error: {message}", file=sys.stderr)
