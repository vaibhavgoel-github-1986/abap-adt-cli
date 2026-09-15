"""Errors the CLI reports as a message, and the exit codes they map to.

Anything raised as an :class:`AbapCliError` is a condition the user can act on,
so the top level prints it and exits. Everything else is a bug and keeps its
traceback.
"""

from __future__ import annotations

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_CONFLICT = 3
EXIT_INTERRUPTED = 130


class AbapCliError(Exception):
    """Base class for user-facing failures."""

    exit_code: int = EXIT_ERROR


class ConfigError(AbapCliError):
    """Bad or missing connection profile, credentials or command arguments."""

    exit_code = EXIT_USAGE


class WorkspaceError(AbapCliError):
    """The local folder or its manifest is missing, unreadable or inconsistent."""


class ConflictError(AbapCliError):
    """The push would overwrite work that changed in SAP, or clashes with a transport."""

    exit_code = EXIT_CONFLICT


class AdtError(AbapCliError):
    """An ADT request failed. Carries the server's own message where possible."""

    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body
