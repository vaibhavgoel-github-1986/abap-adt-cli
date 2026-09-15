"""XML helpers.

ADT payloads are built as strings, so every value that comes from a package
name, an object name or a user-supplied pattern has to be escaped before it is
interpolated - otherwise a single ``&`` produces a malformed request.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr


def text(value: str) -> str:
    """Escape a value for use as element content."""
    return escape(str(value))


def attr(value: str) -> str:
    """Escape and quote a value for use as an attribute, quotes included."""
    return quoteattr(str(value))
