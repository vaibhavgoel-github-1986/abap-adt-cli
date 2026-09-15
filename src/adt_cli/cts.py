"""Transport checks: which request already holds an object.

SAP locks an object in a *request*, not in a task. A colleague's task inside
that request still blocks you from recording the same object anywhere else, so
the only safe transport to push under is the one already holding the lock.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

from adt_cli.repository import RepoObject
from adt_cli.session import AdtSession

CHECKS = "/sap/bc/adt/cts/transportchecks"
CHECK_TYPE = (
    "application/vnd.sap.as+xml; charset=UTF-8; "
    "dataname=com.sap.adt.transport.service.checkData"
)


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
        f"<PGMID>R3TR</PGMID><OBJECT>{obj.type_code.split('/', 1)[0]}</OBJECT>"
        f"<OBJECTNAME>{obj.name}</OBJECTNAME><DEVCLASS></DEVCLASS>"
        f"<OPERATION>I</OPERATION><URI>{obj.uri}</URI>"
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
    gate = asyncio.Semaphore(concurrency)

    async def one(obj: RepoObject) -> Holder | None:
        async with gate:
            try:
                reply = await session.post(
                    CHECKS, content=_payload(obj), content_type=CHECK_TYPE, accept="*/*"
                )
            except Exception:  # noqa: BLE001 - advisory check, never fatal
                return None
        return _parse(reply.text)

    return list(await asyncio.gather(*(one(item) for item in items)))
