"""Activation: turning pushed source into something SAP will run.

Objects must be unlocked first, which push already guarantees — every object is
unlocked as soon as its write completes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from adt_cli.repository import RepoObject
from adt_cli.session import AdtSession

ACTIVATION = "/sap/bc/adt/activation"

_PROPERTIES = re.compile(r"<(?:\w+:)?properties\b([^>]*)/?>")
_ATTR = re.compile(r'([\w:]+)="([^"]*)"')
_MESSAGE = re.compile(r"<(?:\w+:)?message\b([^>]*)>(.*?)</(?:\w+:)?message>", re.S)
_TYPE = re.compile(r'(?:^|\s)(?:\w+:)?type="([^"]*)"')
_SHORT_TEXT = re.compile(r"<(?:\w+:)?shortText[^>]*>(.*?)</(?:\w+:)?shortText>", re.S)
_MARKUP = re.compile(r"<[^>]+>")


@dataclass
class Outcome:
    executed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.executed and not self.errors


def _body(objects: list[RepoObject]) -> str:
    references = "".join(
        f'<adtcore:objectReference adtcore:uri="{obj.uri}" adtcore:name="{obj.name}"/>'
        for obj in objects
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<adtcore:objectReferences xmlns:adtcore="http://www.sap.com/adt/core">'
        f"{references}</adtcore:objectReferences>"
    )


def _parse(xml: str) -> Outcome:
    properties = _PROPERTIES.search(xml)
    flags = dict(_ATTR.findall(properties.group(1))) if properties else {}
    outcome = Outcome(
        executed=flags.get("activationExecuted") == "true"
        or flags.get("generationExecuted") == "true"
    )
    for attributes, inner in _MESSAGE.findall(xml):
        kind = _TYPE.search(attributes)
        short = _SHORT_TEXT.search(inner)
        text = _MARKUP.sub("", short.group(1) if short else inner).strip()
        if not text:
            continue
        severity = (kind.group(1) if kind else "").upper()[:1]
        if severity in ("E", "A", "X"):
            outcome.errors.append(text)
        elif severity == "W":
            outcome.warnings.append(text)
    return outcome


async def activate(session: AdtSession, objects: list[RepoObject]) -> Outcome:
    """Activate every object in one run, the way Eclipse's mass activation does."""
    reply = await session.post(
        ACTIVATION,
        params={"method": "activate", "preauditRequested": "true"},
        content=_body(objects),
        content_type="application/xml",
        accept="*/*",
    )
    return _parse(reply.text)
