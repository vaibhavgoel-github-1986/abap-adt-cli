"""HTTP client for the ZSYNC endpoint (/sap/bc/zsync).

ZSYNC wraps abapGit's object layer, so it reaches every object type - including
the ones ADT has no API for at all (SEGW projects, SICF nodes, classic views,
search helps). It is a separate service from ADT and must be installed in each
target system.

Unlike ADT this is a stateless endpoint: no CSRF token, no locks, no session
modes. One request moves a whole package.
"""

from __future__ import annotations

import io
import logging
import posixpath
import zipfile
from dataclasses import dataclass
from types import TracebackType

import httpx

from adt_cli.errors import AbapCliError

ENDPOINT = "/sap/bc/zsync"
# Exports and imports serialise a whole package, so they run far longer than ADT calls.
DEFAULT_TIMEOUT = 600.0
ZIP_MAGIC = b"PK"

log = logging.getLogger(__name__)

__all__ = ["ENDPOINT", "ImportResult", "ZsyncError", "ZsyncSession", "pack", "unpack"]


class ZsyncError(AbapCliError):
    """A ZSYNC request failed, or SAP reported a problem with the payload."""


@dataclass(frozen=True)
class ImportResult:
    status: str
    object_count: int
    error_count: int
    warning_count: int
    objects: list[dict]

    @classmethod
    def from_json(cls, payload: dict) -> ImportResult:
        return cls(
            status=str(payload.get("status", "?")),
            object_count=int(payload.get("object_cnt", 0) or 0),
            error_count=int(payload.get("error_cnt", 0) or 0),
            warning_count=int(payload.get("warning_cnt", 0) or 0),
            objects=payload.get("objects", []) or [],
        )

    @property
    def failed(self) -> bool:
        return self.error_count > 0


def _message(reply: httpx.Response) -> str:
    """ZSYNC reports failures as JSON; fall back to the raw body."""
    try:
        payload = reply.json()
    except ValueError:
        return f"HTTP {reply.status_code}: {reply.text[:500]}"
    if isinstance(payload, dict) and payload.get("message"):
        return str(payload["message"])
    return str(payload)[:500]


class ZsyncSession:
    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        client: str,
        *,
        verify_tls: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not host:
            raise ZsyncError("no host configured for this system")
        self.host = host.rstrip("/")
        self.client = client
        self._http = httpx.AsyncClient(
            base_url=self.host,
            auth=(user, password),
            verify=verify_tls,
            timeout=timeout,
            follow_redirects=True,
        )

    async def __aenter__(self) -> ZsyncSession:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    async def _call(
        self,
        action: str,
        params: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        query = {"sap-client": self.client, "action": action, **(params or {})}
        method = "POST" if content is not None else "GET"
        log.debug("zsync %s %s %s", method, ENDPOINT, query)
        try:
            reply = await self._http.request(
                method,
                ENDPOINT,
                params=query,
                content=content,
                headers={"Content-Type": "application/zip"} if content is not None else {},
            )
        except httpx.TimeoutException as exc:
            raise ZsyncError(
                f"{self.host}{ENDPOINT} did not answer in time - the package may be "
                f"large or the system busy ({exc})"
            ) from exc
        except httpx.HTTPError as exc:
            raise ZsyncError(f"cannot reach {self.host}{ENDPOINT}: {exc}") from exc

        if reply.status_code == 401:
            raise ZsyncError(f"authentication failed for {self.host} client {self.client}")
        if reply.status_code == 404:
            raise ZsyncError(
                f"{ENDPOINT} not found on {self.host} - the ZSYNC service is not "
                "installed or not active in this system"
            )
        return reply

    async def ping(self) -> dict:
        reply = await self._call("ping")
        if "json" not in reply.headers.get("content-type", ""):
            raise ZsyncError(
                f"unexpected reply from {ENDPOINT} (HTTP {reply.status_code}) - "
                "is ZSYNC installed?"
            )
        return reply.json()

    async def export(self, package: str, include_subpackages: bool = True) -> bytes:
        """Serialise a package to an abapGit-format zip."""
        reply = await self._call(
            "export",
            {
                "package": package.upper(),
                "ignore_subpackages": "" if include_subpackages else "X",
            },
        )
        if not reply.content.startswith(ZIP_MAGIC):
            raise ZsyncError(f"export of {package.upper()} failed: {_message(reply)}")
        return reply.content

    async def import_zip(
        self,
        package: str,
        archive: bytes,
        transport: str = "",
        dry_run: bool = False,
    ) -> ImportResult:
        """Deserialise an abapGit-format zip back into the system."""
        reply = await self._call(
            "import",
            {
                "package": package.upper(),
                "transport": transport,
                "dry_run": "X" if dry_run else "",
            },
            content=archive,
        )
        if "json" not in reply.headers.get("content-type", ""):
            raise ZsyncError(f"import failed: {_message(reply)}")
        if reply.status_code >= 500:
            raise ZsyncError(_message(reply))
        return ImportResult.from_json(reply.json())


def _safe_entry(name: str) -> str:
    """Reject archive paths that would escape the workspace root.

    The archive comes from the server, but a zip is still untrusted input: an
    absolute or '..' entry would let a write land anywhere on disk.
    """
    cleaned = name.lstrip("/")
    if not cleaned or cleaned.endswith("/"):
        return ""
    parts = posixpath.normpath(cleaned).split("/")
    if any(part == ".." for part in parts) or posixpath.isabs(cleaned):
        raise ZsyncError(f"refusing unsafe path in archive: {name}")
    return cleaned


def unpack(archive: bytes) -> list[tuple[str, bytes]]:
    """Archive entries as (canonical path, content), directories skipped."""
    try:
        opened = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise ZsyncError(f"the server did not return a readable zip: {exc}") from exc
    entries = []
    with opened as bundle:
        for info in bundle.infolist():
            if info.is_dir():
                continue
            canonical = _safe_entry(info.filename)
            if canonical:
                entries.append((canonical, bundle.read(info)))
    return entries


def pack(entries: dict[str, bytes]) -> bytes:
    """Build an abapGit-format zip from canonical path -> content."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for canonical, data in sorted(entries.items()):
            bundle.writestr(_safe_entry(canonical), data)
    return buffer.getvalue()
