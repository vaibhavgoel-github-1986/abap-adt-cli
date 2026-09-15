"""Transport Organizer operations: what a request contains, and changing it.

SAP records objects in *tasks* inside a request, so the endpoints here accept
either number and resolve to the task the current user owns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.sax.saxutils import quoteattr

from adt_cli.session import AdtSession

REQUESTS = "/sap/bc/adt/cts/transportrequests"
ORGANIZER = "application/vnd.sap.adt.transportorganizer.v1+xml"

_OBJECT = re.compile(r"<tm:abap_object\b([^>]*)>")
_TASK = re.compile(r"<tm:task\b([^>]*)>")
_ATTR = re.compile(r'tm:(\w+)="([^"]*)"')


@dataclass(frozen=True)
class TransportObject:
    pgmid: str
    type_code: str
    name: str
    locked: bool
    position: str = ""
    description: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.pgmid, self.type_code, self.name)

    def describe(self) -> str:
        return f"{self.pgmid} {self.type_code} {self.name}"


@dataclass(frozen=True)
class Task:
    number: str
    owner: str
    status: str


def _attrs(fragment: str) -> dict[str, str]:
    return dict(_ATTR.findall(fragment))


def _parse_objects(xml: str) -> list[TransportObject]:
    seen: dict[tuple[str, str, str], TransportObject] = {}
    for fragment in _OBJECT.findall(xml):
        fields = _attrs(fragment)
        name = fields.get("name", "")
        if not name:
            continue
        entry = TransportObject(
            pgmid=fields.get("pgmid", ""),
            type_code=fields.get("type", ""),
            name=name,
            locked=fields.get("lock_status", "") == "X",
            position=fields.get("position", ""),
            description=fields.get("obj_info", "") or fields.get("obj_desc", ""),
        )
        # The same object appears under both the request and its task.
        seen.setdefault(entry.key, entry)
    return sorted(seen.values(), key=lambda entry: (entry.type_code, entry.name))


def _parse_tasks(xml: str) -> list[Task]:
    return [
        Task(
            number=fields.get("number", ""),
            owner=fields.get("owner", ""),
            status=fields.get("status_text", ""),
        )
        for fields in (_attrs(fragment) for fragment in _TASK.findall(xml))
        if fields.get("number")
    ]


async def read(session: AdtSession, number: str) -> tuple[list[TransportObject], list[Task]]:
    reply = await session.get(f"{REQUESTS}/{number.upper()}", accept=ORGANIZER)
    return _parse_objects(reply.text), _parse_tasks(reply.text)


async def task_for(session: AdtSession, number: str, user: str) -> str:
    """Objects live in tasks, so a request number has to be resolved to one."""
    _, tasks = await read(session, number)
    if not tasks:
        return number.upper()
    mine = [task for task in tasks if task.owner.upper() == user.upper()]
    return (mine or tasks)[0].number


def _payload(number: str, action: str, objects: list[TransportObject]) -> str:
    entries = "".join(
        f'<tm:abap_object tm:pgmid="{entry.pgmid}" tm:type="{entry.type_code}"'
        f" tm:name={quoteattr(entry.name)}"
        + (f' tm:position="{entry.position}"' if entry.position else "")
        + "/>"
        for entry in objects
    )
    # Removal identifies the entry by position, and the request element carries
    # no number - exactly what Eclipse sends. Anything else is a silent no-op.
    opening = "<tm:request>" if action == "removeobject" else f'<tm:request tm:number="{number}">'
    return (
        '<?xml version="1.0" encoding="ASCII"?>'
        f'<tm:root xmlns:tm="http://www.sap.com/cts/adt/tm" tm:number="{number}"'
        f' tm:useraction="{action}">'
        f"{opening}{entries}</tm:request>"
        "</tm:root>"
    )


async def _apply(
    session: AdtSession, number: str, action: str, objects: list[TransportObject]
) -> None:
    await session.request(
        "PUT",
        f"{REQUESTS}/{number}",
        content=_payload(number, action, objects),
        content_type="text/plain",
        accept=ORGANIZER,
        allow=(200, 201, 202, 204),
    )


async def add(
    session: AdtSession, number: str, objects: list[TransportObject]
) -> list[TransportObject]:
    """Add objects to a request. Returns the ones that are in it afterwards."""
    task = await task_for(session, number, session.user)
    await _apply(session, task, "addobject", objects)
    present, _ = await read(session, task)
    wanted = {entry.key for entry in objects}
    return [entry for entry in present if entry.key in wanted]


async def remove(
    session: AdtSession, number: str, objects: list[TransportObject]
) -> list[TransportObject]:
    """Remove objects from a request. Returns the ones still in it afterwards.

    The entry is addressed by its position in the request, so the current
    contents are read first rather than trusting the caller's copy.
    """
    task = await task_for(session, number, session.user)
    wanted = {entry.key for entry in objects}
    present, _ = await read(session, task)
    targets = [entry for entry in present if entry.key in wanted]
    if targets:
        await _apply(session, task, "removeobject", targets)
    remaining, _ = await read(session, task)
    return [entry for entry in remaining if entry.key in wanted]
