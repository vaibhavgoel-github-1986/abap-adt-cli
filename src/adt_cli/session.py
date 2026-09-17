"""Async ADT session.

Two modes matter and they must not be confused:

* **stateless** - the default. Requests are independent, so they can run in
  parallel. This is what makes a package pull fast.
* **stateful**  - required for locks, because an ADT lock lives in the session
  that took it. SAP serialises these, so they are issued one at a time and the
  session is always returned to stateless afterwards to release server-side
  enqueues.

Every transport-level failure is translated into :class:`AdtError`, so callers
never have to know that httpx is underneath.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from adt_cli.errors import AdtError

ADT_ROOT = "/sap/bc/adt"
DISCOVERY = f"{ADT_ROOT}/discovery"
REDACTED_HEADERS = frozenset({"authorization", "cookie", "set-cookie", "x-csrf-token"})

DEFAULT_TIMEOUT = 120.0
# The handshake doubles as a reachability check. A host that is only routable
# through a VPN swallows the connection when the VPN is down, so waiting the
# full request timeout for it is indistinguishable from a hang.
HANDSHAKE_TIMEOUT = 5.0
MAX_RETRIES = 2
RETRY_BACKOFF = 0.5
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
TRACE_BODY_LIMIT = 8_000

log = logging.getLogger(__name__)

__all__ = ["AdtError", "AdtSession", "Lock"]


@dataclass
class Lock:
    uri: str
    handle: str
    transport: str = ""


def _explain(reply: httpx.Response) -> str:
    """ADT reports errors as XML with a human-readable message; dig it out."""
    try:
        text = reply.text or ""
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        return f"HTTP {reply.status_code}"
    for tag in ("localizedMessage", "message"):
        opened = text.find(f"<{tag}")
        if opened != -1:
            start = text.find(">", opened)
            end = text.find(f"</{tag}", start)
            if start != -1 and end != -1:
                message = text[start + 1 : end].strip()
                if message:
                    return message
    return f"HTTP {reply.status_code}"


def _is_csrf_failure(reply: httpx.Response) -> bool:
    """A 403 caused by an expired token, as opposed to a missing authorisation.

    SAP answers with the header ``x-csrf-token: Required``, so matching the
    literal token value is what distinguishes it from a real 403.
    """
    if reply.status_code != 403:
        return False
    if reply.headers.get("x-csrf-token", "").strip().lower() in ("required", "fetch"):
        return True
    try:
        return "csrf" in reply.text[:2000].lower()
    except (UnicodeDecodeError, httpx.ResponseNotRead):
        return False


def _is_tls_failure(exc: BaseException) -> bool:
    cause: BaseException | None = exc
    while cause is not None:
        if isinstance(cause, ssl.SSLError):
            return True
        cause = cause.__cause__
    return "certificate" in str(exc).lower()


def _transport_error(exc: Exception, host: str, deadline: float = 0.0) -> AdtError:
    """Turn a connection-level failure into something the user can act on."""
    if isinstance(exc, httpx.TimeoutException):
        if deadline:
            return AdtError(
                f"{host} did not answer within {deadline:.0f}s - it looks unreachable from "
                "here. Check that the VPN is connected and that 'abap systems' has the "
                "right host."
            )
        return AdtError(f"{host} did not answer in time - the system may be busy or unreachable")
    if _is_tls_failure(exc):
        return AdtError(
            f"TLS handshake with {host} failed: {exc}. If the system uses a self-signed "
            "certificate, re-register it with 'abap init --insecure'."
        )
    if isinstance(exc, httpx.ProxyError):
        return AdtError(f"the proxy refused the connection to {host}: {exc}")
    if isinstance(exc, httpx.ConnectError):
        hint = " - is the VPN connected?" if deadline else ""
        return AdtError(f"cannot reach {host}: {exc}{hint}")
    return AdtError(f"request to {host} failed: {exc}")


class AdtSession:
    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        client: str,
        *,
        verify_tls: bool = True,
        concurrency: int = 16,
        timeout: float = DEFAULT_TIMEOUT,
        trace: bool = False,
    ) -> None:
        if not host:
            raise AdtError("no host configured for this system")
        concurrency = max(1, concurrency)
        self.host = host.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.host,
            auth=httpx.BasicAuth(user, password),
            params={"sap-client": client},
            verify=verify_tls,
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=concurrency, max_keepalive_connections=concurrency
            ),
            follow_redirects=True,
        )
        self._csrf = ""
        self._stateful = False
        self._gate = asyncio.Semaphore(concurrency)
        self._write_lock = asyncio.Lock()
        self._trace = trace
        self.user = user
        self.client_number = client

    # ------------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> AdtSession:
        try:
            await self.connect()
        except BaseException:
            await self._client.aclose()
            raise
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def connect(self) -> None:
        # Short deadline and no retry: nothing has been sent yet, so failing
        # here costs the user nothing but the wait.
        request = self._client.build_request(
            "GET",
            DISCOVERY,
            headers={"x-csrf-token": "fetch"},
            timeout=HANDSHAKE_TIMEOUT,
        )
        reply = await self._send(request, retries=0, deadline=HANDSHAKE_TIMEOUT)
        if reply.status_code == 401:
            raise AdtError(
                f"authentication failed for {self.user} on {self.host} "
                "- check the user, password and client",
                401,
            )
        if reply.status_code == 403:
            raise AdtError(
                f"{self.user} may not use ADT on {self.host} "
                "- the developer authorisations (S_ADT_RES, S_DEVELOP) may be missing",
                403,
            )
        if reply.status_code >= 400:
            raise AdtError(_explain(reply), reply.status_code, reply.text)
        self._csrf = reply.headers.get("x-csrf-token", "")

    async def close(self) -> None:
        try:
            if self._stateful:
                await self._drop_stateful()
        finally:
            await self._client.aclose()

    # ------------------------------------------------------------------ requests

    def _headers(self, accept: str | None, content_type: str | None) -> dict[str, str]:
        headers = {
            "x-csrf-token": self._csrf,
            "X-sap-adt-sessiontype": "stateful" if self._stateful else "stateless",
        }
        if accept:
            headers["Accept"] = accept
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def _send(
        self, request: httpx.Request, *, retries: int = MAX_RETRIES, deadline: float = 0.0
    ) -> httpx.Response:
        """One round trip, with connection-level failures mapped to AdtError.

        Only idempotent methods are retried: replaying a PUT or a LOCK that may
        already have reached the server is worse than reporting the failure.
        ``deadline`` is only the timeout already set on the request, carried
        here so the failure can say what was waited for.
        """
        attempts = retries + 1 if request.method.upper() in IDEMPOTENT_METHODS else 1
        last: Exception | None = None
        for attempt in range(attempts):
            self._trace_request(request)
            try:
                reply = await self._client.send(request)
            except httpx.HTTPError as exc:
                last = exc
            else:
                self._trace_response(reply)
                if reply.status_code not in RETRYABLE_STATUS or attempt == attempts - 1:
                    return reply
                last = None
                log.debug("retrying %s after HTTP %s", request.url, reply.status_code)
            if attempt < attempts - 1:
                await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
        if last:
            raise _transport_error(last, self.host, deadline)
        raise AdtError(f"{request.url} failed")

    def _trace_request(self, request: httpx.Request) -> None:
        if not self._trace:
            return
        self._trace_message(
            "ADT REQUEST", request.method, str(request.url), request.headers, request.content
        )

    def _trace_response(self, reply: httpx.Response) -> None:
        if not self._trace:
            return
        self._trace_message(
            "ADT RESPONSE",
            str(reply.status_code),
            str(reply.url),
            reply.headers,
            reply.content,
        )

    @staticmethod
    def _trace_message(
        label: str,
        method_or_status: str,
        url: str,
        headers: httpx.Headers,
        content: bytes,
    ) -> None:
        visible_headers = "\n".join(
            f"{name}: {'<redacted>' if name.lower() in REDACTED_HEADERS else value}"
            for name, value in headers.items()
        )
        body = content[:TRACE_BODY_LIMIT].decode("utf-8", errors="replace")
        if len(content) > TRACE_BODY_LIMIT:
            body += f"\n... [{len(content) - TRACE_BODY_LIMIT:,} more bytes]"
        print(
            f"\n--- {label} ---\n{method_or_status} {url}\n{visible_headers}\n\n"
            f"{body}\n--- END {label} ---",
            file=sys.stderr,
        )

    async def request(
        self,
        method: str,
        uri: str,
        *,
        accept: str | None = None,
        content_type: str | None = None,
        content: str | bytes | None = None,
        params: dict[str, Any] | None = None,
        allow: tuple[int, ...] = (200, 201, 202),
    ) -> httpx.Response:
        reply = await self._attempt(method, uri, accept, content_type, content, params)
        # A stale CSRF token is reported as 403; refresh once and retry.
        if _is_csrf_failure(reply):
            await self.connect()
            reply = await self._attempt(method, uri, accept, content_type, content, params)
        if reply.status_code not in allow:
            raise AdtError(_explain(reply), reply.status_code, reply.text)
        return reply

    async def _attempt(
        self,
        method: str,
        uri: str,
        accept: str | None,
        content_type: str | None,
        content: str | bytes | None,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        async with self._gate:
            request = self._client.build_request(
                method,
                uri,
                headers=self._headers(accept, content_type),
                content=content,
                params=params,
            )
            return await self._send(request)

    async def get(self, uri: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", uri, **kwargs)

    async def post(self, uri: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", uri, **kwargs)

    # ------------------------------------------------------------------ stateful

    async def _drop_stateful(self) -> None:
        """Return to stateless, which releases every enqueue held by the session."""
        self._stateful = False
        try:
            request = self._client.build_request(
                "GET", DISCOVERY, headers=self._headers(None, None)
            )
            await self._send(request)
        except (AdtError, httpx.HTTPError) as exc:
            log.warning("could not return the session to stateless: %s", exc)

    @asynccontextmanager
    async def locked(self, object_uri: str) -> AsyncIterator[Lock]:
        """Hold an ADT lock for the duration of the block.

        Serialised on purpose: locks are session-bound, so concurrent locks in
        one session would interfere.
        """
        async with self._write_lock:
            self._stateful = True
            try:
                lock = await self._acquire(object_uri)
                try:
                    yield lock
                finally:
                    await self._release(lock)
            finally:
                await self._drop_stateful()

    async def _acquire(self, object_uri: str) -> Lock:
        reply = await self.request(
            "POST",
            object_uri,
            params={"_action": "LOCK", "accessMode": "MODIFY"},
            accept="application/vnd.sap.as+xml;charset=UTF-8;dataname=com.sap.adt.lock.Result",
        )
        handle = _between(reply.text, "<LOCK_HANDLE>", "</LOCK_HANDLE>")
        if not handle:
            raise AdtError(
                f"lock refused for {object_uri}: no handle returned "
                "- another user or session may hold it",
                reply.status_code,
            )
        return Lock(
            uri=object_uri,
            handle=handle,
            transport=_between(reply.text, "<CORRNR>", "</CORRNR>"),
        )

    async def _release(self, lock: Lock) -> None:
        """Best effort: a failed unlock must not mask why the write failed.

        Dropping back to stateless releases the enqueue in any case.
        """
        try:
            await self.request(
                "POST",
                lock.uri,
                params={"_action": "UNLOCK", "lockHandle": lock.handle},
                allow=(200, 201, 202, 204),
            )
        except (AdtError, httpx.HTTPError) as exc:
            log.warning("could not unlock %s: %s", lock.uri, exc)


def _between(text: str, start: str, end: str) -> str:
    opened = text.find(start)
    if opened == -1:
        return ""
    closed = text.find(end, opened + len(start))
    if closed == -1:
        return ""
    return text[opened + len(start) : closed].strip()
