from __future__ import annotations

import httpx
import pytest

from adt_cli.errors import AdtError
from adt_cli.session import (
    HANDSHAKE_TIMEOUT,
    AdtSession,
    _explain,
    _is_csrf_failure,
    _transport_error,
)


def _reply(status: int, text: str = "", headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, text=text, headers=headers, request=httpx.Request("GET", "https://sap/x")
    )


def test_explain_prefers_the_server_message():
    body = '<exc><localizedMessage lang="EN">Object is locked</localizedMessage></exc>'
    assert _explain(_reply(403, body)) == "Object is locked"


def test_explain_falls_back_to_the_status():
    assert _explain(_reply(500)) == "HTTP 500"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (httpx.ConnectTimeout("slow"), "did not answer in time"),
        (httpx.ConnectError("refused"), "cannot reach"),
        (httpx.ProxyError("nope"), "proxy refused"),
    ],
)
def test_transport_errors_are_explained(error, expected):
    assert expected in str(_transport_error(error, "https://sap"))


def test_certificate_failures_point_at_insecure():
    message = str(_transport_error(httpx.ConnectError("certificate verify failed"), "https://sap"))
    assert "--insecure" in message


def test_the_handshake_deadline_names_the_vpn():
    message = str(_transport_error(httpx.ConnectTimeout("slow"), "https://sap", HANDSHAKE_TIMEOUT))
    assert "within 5s" in message
    assert "VPN" in message


async def test_an_unreachable_host_fails_on_the_handshake_without_retrying():
    attempts = 0

    async def never_answers(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectTimeout("no route")

    session = AdtSession(host="https://sap", user="u", password="p", client="100")
    session._client = httpx.AsyncClient(
        base_url="https://sap", transport=httpx.MockTransport(never_answers)
    )

    with pytest.raises(AdtError, match="within 5s"):
        await session.connect()
    assert attempts == 1


def test_a_session_needs_a_host():
    with pytest.raises(AdtError, match="no host"):
        AdtSession(host="", user="u", password="p", client="100")


def test_expired_csrf_token_is_detected():
    assert _is_csrf_failure(_reply(403, headers={"x-csrf-token": "Required"}))
    assert _is_csrf_failure(_reply(403, "CSRF token validation failed"))


def test_a_real_403_is_not_mistaken_for_a_csrf_failure():
    assert not _is_csrf_failure(_reply(403, "You are not authorized to display object"))
    assert not _is_csrf_failure(_reply(401, "CSRF"))
