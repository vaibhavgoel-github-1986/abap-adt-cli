"""Package enumeration and source transfer."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote

import httpx

from adt_cli import objects, xmlutil
from adt_cli.errors import AdtError
from adt_cli.session import ADT_ROOT, AdtSession

VIRTUAL_FOLDERS = "/sap/bc/adt/repository/informationsystem/virtualfolders/contents"
REQUEST_TYPE = "application/vnd.sap.adt.repository.virtualfolders.request.v1+xml"
RESULT_TYPE = "application/vnd.sap.adt.repository.virtualfolders.result.v1+xml"

_OBJECT = re.compile(r"<vfs:object\b([^>]*)/?>")
_ATTR = re.compile(r'(\w+)="([^"]*)"')
_UNSAFE_IN_FILENAME = re.compile(r"[^a-z0-9_$#\-.]")
# Function modules and includes live under their group's URI; everything else
# owns its own path.
_CONTAINER = re.compile(r"/functions/groups/([^/]+)")


def _safe_stem(value: str) -> str:
    """File-system safe form of an object name.

    Names come from the server, so anything that could climb out of the
    workspace folder is rewritten rather than trusted. '/' appears in namespaced
    names and becomes '#', the way SE80 spells it.
    """
    stem = _UNSAFE_IN_FILENAME.sub("_", unquote(value).lower().replace("/", "#"))
    return stem.strip(".").replace("..", "_") or "unnamed"


@dataclass
class RepoObject:
    name: str
    type_code: str
    uri: str

    @property
    def kind(self) -> objects.ObjectType:
        return objects.lookup(self.type_code)

    @property
    def stem(self) -> str:
        return _safe_stem(self.name)

    @property
    def container(self) -> str:
        """Owning function group, '' when the object stands on its own."""
        found = _CONTAINER.search(self.uri)
        return found.group(1) if found else ""

    @property
    def folder_name(self) -> str:
        """Folder holding every file of this object.

        Function modules and includes are filed under their group, so a group
        and its children stay together the way SE80 shows them.
        """
        return _safe_stem(self.container or self.name).upper()

    def filename_for(self, suffix: str) -> str:
        return f"{self.stem}{suffix}"

    @property
    def filename(self) -> str:
        return self.filename_for(self.kind.extension)


@dataclass
class Fetched:
    obj: RepoObject
    text: str
    error: str = ""
    part: objects.Part | None = None
    # ADT answered 404: the object or include simply is not there to read.
    absent: bool = False

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def suffix(self) -> str:
        return self.part.suffix if self.part else self.obj.kind.extension

    @property
    def label(self) -> str:
        """Object name, qualified by the part when it is not the main source."""
        if self.part is None or self.part.is_main:
            return self.obj.name
        return f"{self.obj.name} ({self.part.path.rsplit('/', 1)[-1]})"


def normalise(text: str) -> str:
    """ADT serves CRLF. Store LF locally so a file survives a read/write round
    trip unchanged, otherwise every object looks modified straight after a pull."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def has_code(text: str) -> bool:
    """False for an include SAP filled with nothing but its placeholder comments.

    An unused class include still answers with a commented banner explaining
    what it is for. Writing those out would add two dead files per class.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("*", '"')):
            return True
    return False


def is_adt_resource(obj: RepoObject) -> bool:
    """False when another SAP service owns the object.

    A package can list enterprise service proxies, whose URIs point at
    /sap/bc/esproxy. ADT cannot serve those at all, so asking only produces a
    404 per object.
    """
    return obj.uri.startswith(f"{ADT_ROOT}/")


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


async def fetch_parts(
    session: AdtSession,
    jobs: list[tuple[RepoObject, objects.Part | None]],
    *,
    concurrency: int = 16,
    on_progress: Callable[[Fetched], None] | None = None,
) -> list[Fetched]:
    """Download the given (object, part) pairs, one result per pair in order.

    ``part`` is None for objects that have no source endpoint, in which case
    their ADT metadata XML is fetched instead.
    """
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(obj: RepoObject, part: objects.Part | None) -> Fetched:
        uri = objects.part_uri(obj.uri, part) if part else obj.uri
        accept = "text/plain" if part else "*/*"
        async with gate:
            try:
                reply = await session.get(uri, accept=accept)
            except AdtError as exc:
                # 404 means absence, not failure: an include the class never
                # created, or a type ADT lists but has no resource for.
                if exc.status == 404 and (part is None or not part.is_main):
                    result = Fetched(obj, "", part=part, absent=True)
                else:
                    result = Fetched(obj, "", str(exc), part)
            except httpx.HTTPError as exc:  # reported per part, never fatal
                result = Fetched(obj, "", str(exc), part)
            else:
                result = Fetched(obj, normalise(reply.text), part=part)
        if on_progress:
            on_progress(result)
        return result

    return list(await asyncio.gather(*(one(obj, part) for obj, part in jobs)))


async def fetch_sources(
    session: AdtSession,
    items: list[RepoObject],
    *,
    concurrency: int = 16,
    on_progress: Callable[[Fetched], None] | None = None,
) -> list[Fetched]:
    """Download every editable text of every object, in parallel.

    Source objects yield one result per :class:`~adt_cli.objects.Part`, so a
    class comes back as its main source plus local definitions, local
    implementations, macros and test classes. Non-source objects (service
    bindings, message classes, packages, ...) have no source endpoint at all -
    their ADT metadata XML is pulled instead, so every listed object lands on
    disk.

    Parts an object does not use are dropped: SAP answers 404 for an include
    that was never created, and returns only a commented placeholder for one
    that exists but is empty. Neither becomes a file.

    ``on_progress``, if given, fires once per fetched text as soon as it lands,
    so a caller can drive a progress bar without waiting for the whole batch.
    """
    jobs = [
        (obj, part)
        for obj in items
        for part in (obj.kind.parts if obj.kind.is_source else (None,))
    ]
    results = await fetch_parts(
        session, jobs, concurrency=concurrency, on_progress=on_progress
    )
    return [
        found
        for found in results
        if found.error
        or found.absent
        or (found.part is None and found.text.strip())
        or (found.part is not None and (found.part.is_main or has_code(found.text)))
    ]


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
    part: objects.Part | None = None,
    transport: str = "",
) -> None:
    """Replace one editable text of an object.

    The lock is taken on the object but the write targets a single include, so
    SAP records the change against the include that actually differs - which is
    how a one-method edit ends up as a single LIMU entry instead of locking the
    whole class.
    """
    if not obj.kind.writable:
        raise AdtError(f"{obj.name} is a {obj.type_code} object and has no writable source")
    target = objects.part_uri(obj.uri, part) if part else objects.source_uri(obj.uri, obj.kind)
    async with session.locked(obj.uri) as lock:
        params = {"lockHandle": lock.handle}
        if transport:
            params["corrNr"] = transport
        await session.request(
            "PUT",
            target,
            content=text.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
            params=params,
        )
