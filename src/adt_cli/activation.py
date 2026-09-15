"""Activation: turning pushed source into something SAP will run.

Objects must be unlocked first, which push already guarantees — every object is
unlocked as soon as its write completes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from adt_cli import xmlutil
from adt_cli.repository import RepoObject
from adt_cli.session import AdtSession

ACTIVATION = "/sap/bc/adt/activation"

_PROPERTIES = re.compile(r"<(?:\w+:)?properties\b([^>]*)/?>")
_ATTR = re.compile(r'([\w:]+)="([^"]*)"')
# SAP returns <msg .../>, not <chkl:message>, and sometimes self-closed.
_MESSAGE = re.compile(
    r"<(?:\w+:)?(?:msg|message)\b([^>]*?)(?:/>|>(.*?)</(?:\w+:)?(?:msg|message)>)", re.S
)
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
        "<adtcore:objectReference "
        f"adtcore:uri={xmlutil.attr(obj.uri)} adtcore:name={xmlutil.attr(obj.name)}/>"
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
    # generationExecuted can be true on a failed run, so only this flag means success.
    outcome = Outcome(executed=flags.get("activationExecuted") == "true")

    for attributes, inner in _MESSAGE.findall(xml):
        fields = {
            name.split(":")[-1]: value for name, value in _ATTR.findall(attributes)
        }
        severity = fields.get("type", "").upper()[:1]
        if severity not in ("E", "A", "X", "W"):
            continue
        short = _SHORT_TEXT.search(inner or "")
        text = _MARKUP.sub("", short.group(1) if short else (inner or "")).strip()
        if not text:
            continue
        where = fields.get("objDescr", "").strip()
        line = fields.get("line", "").strip()
        if where and line and line not in ("0", "1"):
            text = f"{where} line {line}: {text}"
        elif where:
            text = f"{where}: {text}"
        (outcome.warnings if severity == "W" else outcome.errors).append(text)
    return outcome


async def activate(session: AdtSession, objects: list[RepoObject]) -> Outcome:
    """Activate every object in one run, the way Eclipse's mass activation does."""
    if not objects:
        return Outcome(executed=True)
    reply = await session.post(
        ACTIVATION,
        params={"method": "activate", "preauditRequested": "true"},
        content=_body(objects),
        content_type="application/xml",
        accept="*/*",
    )
    return _parse(reply.text)
