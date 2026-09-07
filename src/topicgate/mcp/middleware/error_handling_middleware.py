import logging

from fastmcp.exceptions import ToolError, ValidationError
from fastmcp.server.middleware import Middleware, MiddlewareContext

from topicgate.app.services.control_operation_service import ControlOperationConflict
from topicgate.app.services.broker_resolver import BrokerNotFoundError


error_logger = logging.getLogger("topicgate.mcp.errors")


def _root_error(error: Exception) -> Exception:
    if isinstance(error, ToolError) and isinstance(error.__cause__, Exception):
        return error.__cause__
    return error


class ErrorHandlingMiddleware(Middleware):
    """Convert domain failures to useful, non-sensitive MCP tool errors."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        try:
            return await call_next(context)
        except Exception as error:
            root_error = _root_error(error)
            message = self._client_message(root_error)
            if message is None:
                error_logger.error(
                    "Unexpected MCP tool failure tool=%s error_type=%s",
                    context.message.name,
                    type(root_error).__name__,
                )
                message = "The tool could not be completed due to an internal error."
            raise ToolError(message) from error

    @staticmethod
    def _client_message(error: Exception) -> str | None:
        if isinstance(error, (ControlOperationConflict, BrokerNotFoundError)):
            return str(error)
        if isinstance(error, (ToolError, ValidationError)):
            return str(error)
        if isinstance(error, KeyError):
            identifier = error.args[0] if error.args else "requested item"
            return f"Not found: {identifier}"
        if isinstance(error, ValueError):
            return str(error)
        if isinstance(error, PermissionError):
            return "The operation is not permitted."
        if isinstance(error, TimeoutError):
            return "The MQTT broker operation timed out."
        if isinstance(error, ConnectionError):
            return "The MQTT broker operation failed."
        return None
