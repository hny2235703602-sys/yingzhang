"""Stable, user-facing application errors without provider response bodies."""


class AppError(Exception):
    """Expose a stable code and a safe message to application callers."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)
