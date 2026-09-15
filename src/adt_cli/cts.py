"""Transport checks: which request already holds an object.

SAP locks an object in a *request*, not in a task. A colleague's task inside
that request still blocks you from recording the same object anywhere else, so
the only safe transport to push under is the one already holding the lock.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

import httpx

from adt_cli import xmlutil
from adt_cli.errors import AdtError, ConfigError, ConflictError
from adt_cli.repository import RepoObject
from adt_cli.session import AdtSession

CHECKS = "/sap/bc/adt/cts/transportchecks"
CHECK_TYPE = (
    "application/vnd.sap.as+xml; charset=UTF-8; "
    "dataname=com.sap.adt.transport.service.checkData"
)
# <SID>K<number>, e.g. DHAK900123.
_REQUEST_ID = re.compile(r"^[A-Z][A-Z0-9]{2}K[0-9]{4,}$")


def normalise_request(value: str) -> str:
    """Upper-case a transport id and reject anything that cannot be one."""
    cleaned = value.strip().upper()
    if not cleaned:
        return ""
    if not _REQUEST_ID.match(cleaned):
        raise ConfigError(
            f"'{value}' is not a transport request id - expected something like DHAK900123"
        )
    return cleaned


@dataclass(frozen=True)
class Holder:
    """The request that owns an object's transport lock."""

    request: str
    owner: str
    text: str
    tasks: tuple[tuple[str, str], ...] = ()

    def accepts(self, transport: str) -> bool:
        """True when pushing under this transport records into the same request."""
        wanted = transport.upper()
        return wanted == self.request or any(wanted == task for task, _ in self.tasks)

    def task_of(self, user: str) -> str:
        for task, holder in self.tasks:
            if holder.upper() == user.upper():
                return task
        return ""

    def describe(self) -> str:
        detail = f"{self.request} ({self.owner}"
        if self.text:
            detail += f", '{self.text}'"
        return detail + ")"


def _tag(text: str, tag: str) -> str:
    found = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.S)
    return found.group(1).strip() if found else ""


def _payload(obj: RepoObject) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<asx:abap xmlns:asx="http://www.sap.com/abapxml" version="1.0"><asx:values><DATA>'
        f"<PGMID>R3TR</PGMID><OBJECT>{xmlutil.text(obj.type_code.split('/', 1)[0])}</OBJECT>"
        f"<OBJECTNAME>{xmlutil.text(obj.name)}</OBJECTNAME><DEVCLASS></DEVCLASS>"
        f"<OPERATION>I</OPERATION><URI>{xmlutil.text(obj.uri)}</URI>"
        "</DATA></asx:values></asx:abap>"
    )


def _parse(xml: str) -> Holder | None:
    locks = re.search(r"<LOCKS>(.*?)</LOCKS>", xml, re.S)
    if not locks:
        return None
    holder = re.search(r"<LOCK_HOLDER>(.*?)</LOCK_HOLDER>", locks.group(1), re.S)
    if not holder:
        return None
    header = re.search(r"<REQ_HEADER>(.*?)</REQ_HEADER>", holder.group(1), re.S)
    if not header:
        return None
    request = _tag(header.group(1), "TRKORR")
    if not request:
        return None
    tasks = tuple(
        (_tag(task, "TRKORR"), _tag(task, "AS4USER"))
        for task in re.findall(r"<CTS_TASK_HEADER>(.*?)</CTS_TASK_HEADER>", holder.group(1), re.S)
    )
    return Holder(
        request=request,
        owner=_tag(header.group(1), "AS4USER"),
        text=_tag(header.group(1), "AS4TEXT"),
        tasks=tuple(entry for entry in tasks if entry[0]),
    )


async def holders(
    session: AdtSession, items: list[RepoObject], *, concurrency: int = 16
) -> list[Holder | None]:
    """Existing transport lock per object, or None where it is free.

    A check that errors is reported as None rather than blocking the push; SAP
    itself refuses the write if it really is locked elsewhere.
    """
    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(obj: RepoObject) -> Holder | None:
        async with gate:
            try:
                reply = await session.post(
                    CHECKS, content=_payload(obj), content_type=CHECK_TYPE, accept="*/*"
                )
            except (AdtError, httpx.HTTPError):  # advisory check, never fatal
                return None
        return _parse(reply.text)

    return list(await asyncio.gather(*(one(item) for item in items)))


@dataclass(frozen=True)
class Plan:
    """The request a push will record under, plus what the user should be told."""

    request: str
    notes: tuple[str, ...] = ()


def reconcile(
    locals_: list[str],
    found: list[Holder | None],
    *,
    requested: str,
    package: str,
    user: str,
) -> Plan:
    """Reconcile the requested transport with the request SAP already locked in.

    SAP locks an object in exactly one request, so when the objects are already
    held there is nothing to choose - either the caller named that request, or
    it is adopted and reported.
    """
    held = {holder.request: holder for holder in found if holder is not None}
    local_package = package.startswith("$")

    if requested:
        clashes = [
            (local, holder)
            for local, holder in zip(locals_, found, strict=True)
            if holder is not None and not holder.accepts(requested)
        ]
        if clashes:
            names = ", ".join(sorted({holder.request for _, holder in clashes}))
            detail = "\n".join(
                f"  {local} is locked in {holder.describe()}" for local, holder in clashes
            )
            raise ConflictError(
                f"{len(clashes)} object(s) already locked in {names}, not {requested}:\n"
                f"{detail}\nSAP locks an object in one request only, so re-run with "
                f"--transport {names}"
            )
        return Plan(request=requested)

    if local_package:
        return Plan(request="")
    if len(held) > 1:
        names = ", ".join(sorted(held))
        raise ConflictError(
            f"package {package} is transportable and these objects span several "
            f"requests ({names}) - push them separately with --transport"
        )
    if not held:
        raise ConfigError(
            f"package {package} is transportable - pass --transport <TR> "
            "(only local $ packages can push without one)"
        )

    holder = next(iter(held.values()))
    notes = [f"using {holder.describe()} - it already holds these objects"]
    mine = holder.task_of(user)
    notes.append(
        f"  recording under your task {mine}"
        if mine
        else f"  SAP will open a task for {user} in it"
    )
    notes.extend(
        f"  {local} is in no request yet, it will be added to {holder.request}"
        for local, holder_found in zip(locals_, found, strict=True)
        if holder_found is None
    )
    return Plan(request=holder.request, notes=tuple(notes))
