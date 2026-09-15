"""Creating objects that do not exist in SAP yet.

A new local file has no ADT URI to write to, so the object has to be created
first — the same two-step Eclipse performs when you add a class: create the
shell in a package and transport, then write the source into it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.sax.saxutils import quoteattr

from adt_cli.session import AdtSession

_LABEL = re.compile(r"@EndUserText\.label\s*:\s*'([^']*)'")


@dataclass(frozen=True)
class Blueprint:
    path: str
    element: str
    namespace: str
    extra: str = ""


BLUEPRINTS: dict[str, Blueprint] = {
    "CLAS/OC": Blueprint(
        "/sap/bc/adt/oo/classes",
        "class:abapClass",
        'xmlns:class="http://www.sap.com/adt/oo/classes"',
        ' class:final="true" class:visibility="public"',
    ),
    "INTF/OI": Blueprint(
        "/sap/bc/adt/oo/interfaces",
        "intf:abapInterface",
        'xmlns:intf="http://www.sap.com/adt/oo/interfaces"',
    ),
    "PROG/P": Blueprint(
        "/sap/bc/adt/programs/programs",
        "program:abapProgram",
        'xmlns:program="http://www.sap.com/adt/programs/programs"',
    ),
    "DDLS/DF": Blueprint(
        "/sap/bc/adt/ddic/ddl/sources",
        "ddl:ddlSource",
        'xmlns:ddl="http://www.sap.com/adt/ddic/ddlsources"',
    ),
    "DDLX/EX": Blueprint(
        "/sap/bc/adt/ddic/ddlx/sources",
        "ddlx:ddlxSource",
        'xmlns:ddlx="http://www.sap.com/adt/ddic/ddlxsources"',
    ),
    "DCLS/DL": Blueprint(
        "/sap/bc/adt/acm/dcl/sources",
        "dcl:dclSource",
        'xmlns:dcl="http://www.sap.com/adt/acm/dclsources"',
    ),
    "SRVD/SRV": Blueprint(
        "/sap/bc/adt/ddic/srvd/sources",
        "srvd:srvdSource",
        'xmlns:srvd="http://www.sap.com/adt/ddic/srvdsources"',
        ' srvd:srvdSourceType="S"',
    ),
}


def supported(type_code: str) -> bool:
    return type_code in BLUEPRINTS


def uri_for(type_code: str, name: str) -> str:
    return f"{BLUEPRINTS[type_code].path}/{name.lower()}"


def describe(source: str, fallback: str) -> str:
    """SAP insists on a description; the CDS label is the closest thing in source."""
    label = _LABEL.search(source)
    return label.group(1) if label else fallback


async def create(
    session: AdtSession,
    type_code: str,
    name: str,
    package: str,
    description: str,
    *,
    transport: str = "",
) -> str:
    """Create an empty object and return its ADT URI."""
    plan = BLUEPRINTS[type_code]
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<{plan.element} {plan.namespace}"
        ' xmlns:adtcore="http://www.sap.com/adt/core"'
        f" adtcore:description={quoteattr(description)}"
        f" adtcore:name={quoteattr(name)}"
        f' adtcore:type="{type_code}"'
        f" adtcore:responsible={quoteattr(session.user.upper())}{plan.extra}>"
        f"<adtcore:packageRef adtcore:name={quoteattr(package)}/>"
        f"</{plan.element}>"
    )
    await session.post(
        plan.path,
        content=body,
        content_type="application/*",
        params={"corrNr": transport} if transport else None,
        allow=(200, 201, 202),
    )
    return uri_for(type_code, name)
