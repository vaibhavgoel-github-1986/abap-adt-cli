"""Package enumeration and source transfer."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from adt_cli import objects, xmlutil
from adt_cli.errors import AdtError
from adt_cli.session import AdtSession

VIRTUAL_FOLDERS = "/sap/bc/adt/repository/informationsystem/virtualfolders/contents"
REQUEST_TYPE = "application/vnd.sap.adt.repository.virtualfolders.request.v1+xml"
RESULT_TYPE = "application/vnd.sap.adt.repository.virtualfolders.result.v1+xml"

_OBJECT = re.compile(r"<vfs:object\b([^>]*)/?>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')
_UNSAFE_IN_FILENAME = re.compile(r"[^a-z0-9_$#\-.]")


@dataclass
class RepoObject:
    name: str
    type_code: str
    uri: str

    @property
    def kind(self) -> objects.ObjectType:
        return objects.lookup(self.type_code)

    @property
    def filename(self) -> str:
        """Local file name.

        Object names come from the server, so anything that could climb out of
        the workspace folder is rewritten rather than trusted. '/' appears in
        namespaced names and becomes '#', the way SE80 spells it.
        """
        stem = _UNSAFE_IN_FILENAME.sub("_", self.name.lower().replace("/", "#"))
        stem = stem.strip(".").replace("..", "_")
        return f"{stem or 'unnamed'}{self.kind.extension}"


@dataclass
class Fetched:
    obj: RepoObject
    text: str
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def normalise(text: str) -> str:
    """ADT serves CRLF. Store LF locally so a file survives a read/write round
    trip unchanged, otherwise every object looks modified straight after a pull."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _request_body(package: str, pattern: str, owner: str) -> str:
    preselection = (
        '  <vfs:preselection facet="package">\n'
        f"    <vfs:value>{xmlutil.text(package)}</vfs:value>\n"
        "  </vfs:preselection>\n"
    )
    if owner:
        preselection += (
            '  <vfs:preselection facet="owner">\n'
            f"    <vfs:value>{xmlutil.text(owner)}</vfs:value>\n"
            "  </vfs:preselection>\n"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<vfs:virtualFoldersRequest xmlns:vfs="http://www.sap.com/adt/ris/virtualFolders"'
        f" objectSearchPattern={xmlutil.attr(pattern)}>\n"
        f"{preselection}"
        "  <vfs:facetorder/>\n"
        "</vfs:virtualFoldersRequest>"
    )


async def list_package(
    session: AdtSession, package: str, pattern: str = "*", owner: str = ""
) -> list[RepoObject]:
    """Every object in a package, in a single round trip."""
    reply = await session.post(
        VIRTUAL_FOLDERS,
        content=_request_body(package.upper(), pattern, owner.upper()),
        content_type=REQUEST_TYPE,
        accept=RESULT_TYPE,
    )
    found: list[RepoObject] = []
    for match in _OBJECT.finditer(reply.text):
        attrs = dict(_ATTR.findall(match.group(1)))
        name, uri = attrs.get("name"), attrs.get("uri")
        if name and uri:
            found.append(RepoObject(name=name, type_code=attrs.get("type", ""), uri=uri))
    found.sort(key=lambda entry: (entry.type_code, entry.name))
    return found


async def fetch_sources(
    session: AdtSession,
    items: list[RepoObject],
    *,
    concurrency: int = 16,
    on_progress: Callable[[Fetched], None] | None = None,
) -> list[Fetched]:
    """Download every object in parallel.

    Source-based objects get their editable text. Non-source objects (service
    bindings, message classes, packages, ...) have no ``/source/main`` - their
    ADT metadata XML is pulled instead, so every listed object lands on disk.

    ``on_progress``, if given, fires once per object as soon as it lands, so a
    caller can drive a progress bar without waiting for the whole batch.
    """
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(obj: RepoObject) -> Fetched:
        kind = obj.kind
        uri = objects.source_uri(obj.uri, kind) if kind.is_source else obj.uri
        accept = "text/plain" if kind.is_source else "*/*"
        async with gate:
            try:
                reply = await session.get(uri, accept=accept)
            except (AdtError, httpx.HTTPError) as exc:  # reported per object, never fatal
                result = Fetched(obj, "", str(exc))
            else:
                result = Fetched(obj, normalise(reply.text))
        if on_progress:
            on_progress(result)
        return result

    return list(await asyncio.gather(*(one(item) for item in items)))


async def read_source(session: AdtSession, obj: RepoObject) -> str:
    reply = await session.get(
        objects.source_uri(obj.uri, obj.kind), accept="text/plain"
    )
    return normalise(reply.text)


async def write_source(
    session: AdtSession,
    obj: RepoObject,
    text: str,
    *,
    transport: str = "",
) -> None:
    """Replace an object's source.

    The lock is what carries transport semantics: SAP records the change against
    the individual includes that actually differ, which is how a one-method edit
    ends up as a single LIMU METH entry instead of locking the whole class.
    """
    if not obj.kind.writable:
        raise AdtError(f"{obj.name} is a {obj.type_code} object and has no writable source")
    await write_source_at(
        session, obj.uri, objects.source_uri(obj.uri, obj.kind), text, transport=transport
    )


async def write_source_at(
    session: AdtSession,
    object_uri: str,
    source_uri: str,
    text: str,
    *,
    transport: str = "",
) -> None:
    """Write one source endpoint of an object, which may be an include of it.

    The lock is taken on the object, but the write targets a single include, so
    SAP records the change as one LIMU entry rather than the whole class.
    """
    async with session.locked(object_uri) as lock:
        params = {"lockHandle": lock.handle}
        if transport:
            params["corrNr"] = transport
        await session.request(
            "PUT",
            source_uri,
            content=text.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
            params=params,
        )
