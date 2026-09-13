"""Async ADT session.

Two modes matter and they must not be confused:

* **stateless** - the default. Requests are independent, so they can run in
  parallel. This is what makes a package pull fast.
* **stateful**  - required for locks, because an ADT lock lives in the session
  that took it. SAP serialises these, so they are issued one at a time and the
  session is always returned to stateless afterwards to release server-side
  enqueues.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

import httpx

ADT_ROOT = "/sap/bc/adt"
DISCOVERY = f"{ADT_ROOT}/discovery"


class AdtError(RuntimeError):
    """An ADT request failed. Carries the server's own message where possible."""

    def __init__(self, message: str, status: int | None = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


@dataclass
class Lock:
    uri: str
    handle: str
    transport: str = ""


def _explain(reply: httpx.Response) -> str:
    """ADT reports errors as XML with a human-readable message; dig it out."""
    text = reply.text or ""
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
        timeout: float = 120.0,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=host.rstrip("/"),
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
        self.user = user
        self.client_number = client

    # ------------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> AdtSession:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def connect(self) -> None:
        reply = await self._client.get(DISCOVERY, headers={"x-csrf-token": "fetch"})
        if reply.status_code == 401:
            raise AdtError("authentication failed", 401)
        if reply.status_code >= 400:
            raise AdtError(_explain(reply), reply.status_code, reply.text)
        self._csrf = reply.headers.get("x-csrf-token", "")

    async def close(self) -> None:
        if self._stateful:
            await self._drop_stateful()
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
        async with self._gate:
            reply = await self._client.request(
                method,
                uri,
                headers=self._headers(accept, content_type),
                content=content,
                params=params,
            )
        # A stale CSRF token is reported as 403; refresh once and retry.
        if reply.status_code == 403 and "csrf" in reply.headers.get(
            "x-csrf-token", ""
        ).lower():
            await self.connect()
            async with self._gate:
                reply = await self._client.request(
                    method,
                    uri,
                    headers=self._headers(accept, content_type),
                    content=content,
                    params=params,
                )
        if reply.status_code not in allow:
            raise AdtError(_explain(reply), reply.status_code, reply.text)
        return reply

    async def get(self, uri: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", uri, **kwargs)

    async def post(self, uri: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", uri, **kwargs)

    # ------------------------------------------------------------------ stateful

    async def _set_stateful(self, on: bool) -> None:
        self._stateful = on

    async def _drop_stateful(self) -> None:
        """Return to stateless, which releases every enqueue held by the session."""
        self._stateful = False
        try:
            await self._client.get(DISCOVERY, headers=self._headers(None, None))
        except httpx.HTTPError:
            pass

    @asynccontextmanager
    async def locked(self, object_uri: str) -> AsyncIterator[Lock]:
        """Hold an ADT lock for the duration of the block.

        Serialised on purpose: locks are session-bound, so concurrent locks in
        one session would interfere.
        """
        async with self._write_lock:
            await self._set_stateful(True)
            try:
                reply = await self.request(
                    "POST",
                    object_uri,
                    params={"_action": "LOCK", "accessMode": "MODIFY"},
                    accept="application/vnd.sap.as+xml;charset=UTF-8;dataname=com.sap.adt.lock.Result",
                )
                handle = _between(reply.text, "<LOCK_HANDLE>", "</LOCK_HANDLE>")
                transport = _between(reply.text, "<CORRNR>", "</CORRNR>")
                if not handle:
                    raise AdtError("lock refused: no handle returned", reply.status_code)
                lock = Lock(uri=object_uri, handle=handle, transport=transport)
                try:
                    yield lock
                finally:
                    await self.request(
                        "POST",
                        object_uri,
                        params={"_action": "UNLOCK", "lockHandle": handle},
                        allow=(200, 201, 202, 204),
                    )
            finally:
                await self._drop_stateful()


def _between(text: str, start: str, end: str) -> str:
    opened = text.find(start)
    if opened == -1:
        return ""
    closed = text.find(end, opened + len(start))
    if closed == -1:
        return ""
    return text[opened + len(start) : closed].strip()
