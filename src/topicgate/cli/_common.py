"""Shared types and output helpers for TopicGate CLI commands."""

import sys
from collections.abc import Callable
from typing import Protocol

from topicgate.app.app_dependencies import AppDependencies
from topicgate.app.services.integration_service import IntegrationService


DependenciesFactory = Callable[[], AppDependencies]


class IntegrationDependencies(Protocol):
    integration_service: IntegrationService


IntegrationDependenciesFactory = Callable[[], IntegrationDependencies]


def print_error(error: Exception) -> None:
    message = error.args[0] if isinstance(error, KeyError) else str(error)
    print(f"Error: {message}", file=sys.stderr)
