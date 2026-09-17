"""Transport Organizer operations: what a request contains, and changing it.

SAP records objects in *tasks* inside a request, so the endpoints here accept
either number and resolve to the task the current user owns.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from xml.sax.saxutils import quoteattr

from adt_cli.errors import AdtError, ConfigError
from adt_cli.session import AdtSession

REQUESTS = "/sap/bc/adt/cts/transportrequests"
ORGANIZER = "application/vnd.sap.adt.transportorganizer.v1+xml"
# SE09's own tree: every request of a user, split by category and by status.
TREE = "application/vnd.sap.adt.transportorganizertree.v1+xml"
# Which attributes this system defines, with their descriptions.
VALUE_HELP = f"{REQUESTS}/valuehelp/attribute"
NAMED_ITEMS = "application/xml, application/vnd.sap.adt.nameditems.v1+xml"

# tm:type on a request. SAP spells the two categories as single letters.
WORKBENCH = "K"
CUSTOMIZING = "W"
# tm:type on the task inside it. SAP creates the task 'Unclassified' whatever
# the newrequest payload says, and an unclassified task refuses every object
# with "changes are only allowed in correction/repair" - so it is set after.
TASK_TYPE = {WORKBENCH: "S", CUSTOMIZING: "Q"}
# requestStatus in the tree query. SAP defaults it to R, so asking for
# everything means asking twice.
MODIFIABLE = "D"
RELEASED = "R"
# AS4TEXT, the field the description is stored in.
DESCRIPTION_LIMIT = 60

_OBJECT = re.compile(r"<tm:abap_object\b([^>]*)>")
_TASK = re.compile(r"<tm:task\b([^>]*)>")
_REQUEST = re.compile(r"<tm:request\b([^>]*)>")
_ATTRIBUTE = re.compile(r"<tm:attributes\b([^>]*?)/?>")
_ATTR = re.compile(r'tm:(\w+)="([^"]*)"')
_NAMED_ITEM = re.compile(
    r"<nameditem:name>(.*?)</nameditem:name>\s*"
    r"(?:<nameditem:description>(.*?)</nameditem:description>)?",
    re.S,
)
# Document order is what says which category and status a request sits under.
_NODE = re.compile(
    r"<(/?)tm:(workbench|customizing|released|modifiable|request|task)\b([^>]*?)(/?)>"
)


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


@dataclass(frozen=True)
class Attribute:
    """One CTS attribute on a request, such as a Jira key.

    ``position`` is how SAP addresses an existing attribute when changing it,
    and it shifts as attributes come and go, so it is never cached.
    """

    name: str
    value: str
    position: str = ""
    description: str = ""

    def describe(self) -> str:
        detail = f" ({self.description})" if self.description else ""
        return f"{self.name}{detail} = {self.value}"


@dataclass(frozen=True)
class Request:
    """One request as SE09 lists it, with the tasks underneath it."""

    number: str
    description: str
    owner: str
    type_code: str
    released: bool
    target: str = ""
    changed: str = ""
    tasks: tuple[Task, ...] = field(default_factory=tuple)

    @property
    def customizing(self) -> bool:
        return self.type_code.upper() == CUSTOMIZING

    @property
    def category(self) -> str:
        return "customizing" if self.customizing else "workbench"

    @property
    def status(self) -> str:
        return "released" if self.released else "modifiable"

    def describe(self) -> str:
        return f"{self.number}  {self.description}"


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


def _parse_tree(xml: str) -> list[Request]:
    """Requests in SE09's tree, which says category and status by nesting alone.

    A request carries ``tm:type`` and ``tm:status`` too, but only the enclosing
    section is authoritative: SAP labels the collections, and a request whose
    attributes disagree with the folder it was filed under is still in that
    folder.
    """
    found: list[Request] = []
    customizing = False
    released = False
    pending: dict | None = None
    tasks: list[Task] = []

    def flush() -> None:
        nonlocal pending
        if pending is not None:
            found.append(Request(**pending, tasks=tuple(tasks)))
            pending = None
        tasks.clear()

    for closing, tag, body, self_closing in _NODE.findall(xml):
        if tag in ("workbench", "customizing"):
            flush()
            customizing = not closing and tag == "customizing"
            continue
        if tag in ("released", "modifiable"):
            flush()
            released = not closing and tag == "released"
            continue
        if tag == "request":
            flush()
            if closing:
                continue
            fields = _attrs(body)
            if not fields.get("number"):
                continue
            pending = {
                "number": fields["number"],
                "description": fields.get("desc", ""),
                "owner": fields.get("owner", ""),
                "type_code": fields.get("type", "") or (CUSTOMIZING if customizing else WORKBENCH),
                "released": released,
                "target": fields.get("target", ""),
                "changed": fields.get("lastchanged_timestamp", ""),
            }
            if self_closing:
                flush()
            continue
        if tag == "task" and not closing and pending is not None:
            fields = _attrs(body)
            if fields.get("number"):
                tasks.append(
                    Task(
                        number=fields["number"],
                        owner=fields.get("owner", ""),
                        status=fields.get("status_text") or fields.get("status", ""),
                    )
                )
    flush()
    return found


async def owned_by(
    session: AdtSession, user: str = "", *, released: bool | None = None
) -> list[Request]:
    """Every request SE09 shows for a user, newest first.

    ``requestStatus`` has to be named: left out, SAP answers with released
    requests only, which silently hides everything still open. Asking for both
    is therefore two queries. ``'*'`` as the user is SAP's own spelling of
    everybody.

    The category is filtered by the caller instead, because the section a
    request is filed under is what the tree is authoritative about.
    """
    if released is True:
        wanted = (RELEASED,)
    elif released is False:
        wanted = (MODIFIABLE,)
    else:
        wanted = (MODIFIABLE, RELEASED)
    whose = (user or session.user).upper()
    replies = await asyncio.gather(
        *(
            session.get(
                REQUESTS, params={"user": whose, "requestStatus": status}, accept=TREE
            )
            for status in wanted
        )
    )
    seen: dict[str, Request] = {}
    for reply in replies:
        for request in _parse_tree(reply.text):
            seen.setdefault(request.number, request)
    return sorted(seen.values(), key=lambda entry: entry.number, reverse=True)


def _new_payload(description: str, type_code: str, target: str, owner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<tm:root xmlns:tm="http://www.sap.com/cts/adt/tm" tm:useraction="newrequest">'
        f"<tm:request tm:desc={quoteattr(description)} tm:type={quoteattr(type_code)}"
        f" tm:target={quoteattr(target)} tm:cts_project=\"\">"
        f"<tm:task tm:owner={quoteattr(owner)}/>"
        "</tm:request></tm:root>"
    )


async def set_task_type(session: AdtSession, task: str, task_type: str) -> None:
    """Classify a task. Addressed by the task number, not by its request."""
    await session.request(
        "PUT",
        f"{REQUESTS}/{task.upper()}",
        content=(
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<tm:root xmlns:tm="http://www.sap.com/cts/adt/tm" tm:number={quoteattr(task)}'
            ' tm:useraction="changetasktype">'
            f"<tm:task tm:type={quoteattr(task_type)}/>"
            "</tm:root>"
        ),
        content_type="text/plain",
        accept=ORGANIZER,
        allow=(200, 201, 202, 204),
    )


async def create(
    session: AdtSession, description: str, *, type_code: str = WORKBENCH, target: str = ""
) -> Request:
    """Create an empty request and return it, task included.

    The target is left to SAP when not given, so the transport layer of the
    package decides where the request goes rather than a guess made here.
    """
    text = description.strip()
    if not text:
        raise ConfigError("a transport needs a description")
    if len(text) > DESCRIPTION_LIMIT:
        raise ConfigError(
            f"the description is {len(text)} characters - SAP stores at most "
            f"{DESCRIPTION_LIMIT}"
        )
    reply = await session.post(
        REQUESTS,
        content=_new_payload(text, type_code, target, session.user.upper()),
        content_type="text/plain",
        accept=ORGANIZER,
        allow=(200, 201, 202),
    )
    made = [_attrs(fragment) for fragment in _REQUEST.findall(reply.text)]
    number = next((entry.get("number", "") for entry in made if entry.get("number")), "")
    if not number:
        raise AdtError("SAP created no request - the reply carried no number", body=reply.text)
    fields = made[0]
    # The create reply names no task, so the request is read back to find it.
    _, tasks = await read(session, number)
    for task in tasks:
        await set_task_type(session, task.number, TASK_TYPE.get(type_code, "S"))
    return Request(
        number=number,
        description=fields.get("desc", text),
        owner=fields.get("owner", session.user.upper()),
        type_code=fields.get("type", "") or type_code,
        released=False,
        target=fields.get("target", target),
        tasks=tuple(tasks),
    )


async def task_for(session: AdtSession, number: str, user: str) -> str:
    """Objects live in tasks, so a request number has to be resolved to one."""
    _, tasks = await read(session, number)
    if not tasks:
        return number.upper()
    mine = [task for task in tasks if task.owner.upper() == user.upper()]
    return (mine or tasks)[0].number


async def delete(session: AdtSession, number: str) -> None:
    """Delete a request, or a single task inside one.

    SAP refuses on its own once a request is released or still holds locks, so
    the server's message is what the caller sees rather than a guess made here.
    """
    await session.request(
        "DELETE",
        f"{REQUESTS}/{number.upper()}",
        accept=ORGANIZER,
        allow=(200, 201, 202, 204),
    )


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


# --------------------------------------------------------------------- attributes


def _parse_attributes(xml: str) -> list[Attribute]:
    found = []
    for fragment in _ATTRIBUTE.findall(xml):
        fields = _attrs(fragment)
        name = fields.get("attribute", "")
        if name:
            found.append(
                Attribute(
                    name=name,
                    value=fields.get("value", ""),
                    position=fields.get("position", ""),
                    description=fields.get("description", ""),
                )
            )
    return sorted(found, key=lambda entry: entry.position)


async def attributes(session: AdtSession, number: str) -> list[Attribute]:
    """The CTS attributes currently on a request."""
    reply = await session.get(f"{REQUESTS}/{number.upper()}", accept=ORGANIZER)
    return _parse_attributes(reply.text)


async def attribute_names(session: AdtSession, pattern: str = "*") -> list[Attribute]:
    """Attributes this system defines, which is what a request may carry.

    Values are empty here: this is the catalogue, not what any request holds.
    """
    reply = await session.get(VALUE_HELP, params={"name": pattern}, accept=NAMED_ITEMS)
    return [
        Attribute(name=name.strip(), value="", description=(description or "").strip())
        for name, description in _NAMED_ITEM.findall(reply.text)
        if name.strip()
    ]


def _attribute_payload(number: str, action: str, attribute: Attribute) -> str:
    position = (
        f" tm:position={quoteattr(attribute.position)}" if attribute.position else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<tm:root xmlns:tm="http://www.sap.com/cts/adt/tm" tm:number={quoteattr(number)}'
        f' tm:useraction="{action}">'
        "<tm:request>"
        f"<tm:attributes tm:attribute={quoteattr(attribute.name)}"
        f" tm:value={quoteattr(attribute.value)}{position}/>"
        "</tm:request></tm:root>"
    )


async def set_attribute(session: AdtSession, number: str, name: str, value: str) -> Attribute:
    """Put an attribute on a request, replacing the value if it is already there.

    SAP has two actions, not one: ``addattribute`` for a name the request does
    not carry yet, and ``modifyattribute`` for one it does - and the latter
    identifies the attribute by position, not by name. Positions shift as
    attributes are added, so the current list is read immediately before the
    write rather than remembered.
    """
    number = number.upper()
    wanted = name.strip().upper()
    if not wanted:
        raise ConfigError("an attribute needs a name")
    present = await attributes(session, number)
    existing = next((entry for entry in present if entry.name.upper() == wanted), None)
    action = "modifyattribute" if existing else "addattribute"
    target = Attribute(
        name=existing.name if existing else wanted,
        value=value,
        position=existing.position if existing else "",
    )
    await session.request(
        "PUT",
        f"{REQUESTS}/{number}",
        content=_attribute_payload(number, action, target),
        content_type="text/plain",
        accept=ORGANIZER,
        allow=(200, 201, 202, 204),
    )
    # SAP answers 200 even when nothing was stored, so the result is read back.
    after = await attributes(session, number)
    landed = next((entry for entry in after if entry.name.upper() == wanted), None)
    if landed is None or landed.value != value:
        raise AdtError(
            f"{number} did not accept {wanted} - is it a CTS attribute on this system? "
            "'abap transports attr --names' lists the ones that are."
        )
    return landed
