"""Calling an OData service over the session ADT already authenticated.

SAP publishes Gateway (v2) and RAP (v4) services under different roots, and a
v4 path is built from the *service binding*, not from the service definition,
which is why a namespace is usually needed to reach one.

The transport, credentials, TLS and tracing are the session's, so an OData call
costs no extra logon and appears in ``--trace`` like any other request.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from adt_cli.errors import AdtError, ConfigError
from adt_cli.session import AdtSession

V2_ROOT = "/sap/opu/odata/sap"
V4_ROOT = "/sap/opu/odata4/sap"
JSON_TYPE = "application/json"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
# Methods SAP requires a CSRF token for.
WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def service_root(service: str, *, namespace: str = "", version: str = "v4") -> str:
    """Base path of a service, without any entity."""
    name = service.strip().strip("/")
    if not name:
        raise ConfigError("a service name is required")
    if version == "v2":
        return f"{V2_ROOT}/{name.upper()}"
    # SAP spells the v4 path in lower case, and '0001' is the binding version.
    binding = (namespace or name).lower()
    return f"{V4_ROOT}/{binding}/srvd_a2x/sap/{name.lower()}/0001"


def uri_for(service: str, entity: str = "", *, namespace: str = "", version: str = "v4") -> str:
    root = service_root(service, namespace=namespace, version=version)
    target = entity.strip().lstrip("/")
    return f"{root}/{target}" if target else root


def parse_query(pairs: list[str]) -> dict[str, str]:
    """``$top=10`` becomes ``{'$top': '10'}``.

    Split on the first ``=`` only, so a filter may contain its own.
    """
    found: dict[str, str] = {}
    for raw in pairs:
        key, sign, value = raw.partition("=")
        if not sign or not key.strip():
            raise ConfigError(f"'{raw}' is not KEY=VALUE, e.g. '$top=10'")
        found[key.strip()] = value
    return found


def parse_body(raw: str) -> Any:
    """A JSON document, or ``@path`` to read one from a file."""
    text = raw.strip()
    if text.startswith("@"):
        path = Path(text[1:]).expanduser()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigError(f"cannot read {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"the body is not valid JSON: {exc}") from exc


async def _csrf_token(session: AdtSession, root: str) -> str:
    """A token from the service itself.

    Gateway issues CSRF tokens per service, so the one ADT handed out at logon
    is not accepted here - the service root has to be asked for its own.
    """
    reply = await session.request(
        "GET",
        root,
        accept=JSON_TYPE,
        headers={"x-csrf-token": "fetch"},
        allow=(200, 201, 202, 204, 400, 403, 404, 405, 501),
    )
    return reply.headers.get("x-csrf-token", "")


def explain(body: str) -> str:
    """The service's own message from an OData error document, '' if absent.

    v4 carries the text directly, v2 wraps it in ``message.value``. Without
    this a failing call reports only its status, and the useful part - which
    filter is missing, which field is unknown - is thrown away.
    """
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return ""
    node = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(node, dict):
        return ""
    message = node.get("message")
    if isinstance(message, dict):
        message = message.get("value", "")
    return str(message or "").strip()


async def call(
    session: AdtSession,
    method: str,
    service: str,
    entity: str = "",
    *,
    namespace: str = "",
    version: str = "v4",
    query: dict[str, str] | None = None,
    body: Any = None,
) -> tuple[int, Any]:
    """Send one request to an OData service; returns (status, parsed body).

    The body comes back decoded when it is JSON and as raw text when it is not,
    so a Gateway error page is shown rather than swallowed.
    """
    verb = method.strip().upper()
    if verb not in METHODS:
        raise ConfigError(f"'{method}' is not an HTTP method - use {', '.join(METHODS)}")
    if version not in ("v2", "v4"):
        raise ConfigError(f"'{version}' is not an OData version - use v2 or v4")
    if body is not None and verb not in WRITES:
        raise ConfigError(f"{verb} takes no request body")

    root = service_root(service, namespace=namespace, version=version)
    uri = uri_for(service, entity, namespace=namespace, version=version)
    params = dict(query or {})
    # v2 answers XML unless asked otherwise; v4 is JSON already.
    if version == "v2" and verb == "GET":
        params.setdefault("$format", "json")

    headers = {}
    if verb in WRITES:
        token = await _csrf_token(session, root)
        if token:
            headers["x-csrf-token"] = token

    try:
        reply = await session.request(
            verb,
            uri,
            accept=JSON_TYPE,
            content_type=JSON_TYPE if body is not None else None,
            content=json.dumps(body).encode("utf-8") if body is not None else None,
            params=params or None,
            headers=headers or None,
            allow=(200, 201, 202, 204),
        )
    except AdtError as exc:
        detail = explain(exc.body)
        if detail:
            raise AdtError(detail, exc.status, exc.body) from exc
        raise
    return reply.status_code, _decode(reply.text)


def _decode(text: str) -> Any:
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def rows(payload: Any) -> list[dict] | None:
    """The result set of a collection response, or None for anything else.

    v2 wraps it in ``d.results`` and v4 in ``value``; a single entity has
    neither, and is not a result set.
    """
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("value"), list):
        return payload["value"]
    wrapper = payload.get("d")
    if isinstance(wrapper, dict) and isinstance(wrapper.get("results"), list):
        return wrapper["results"]
    if isinstance(wrapper, list):
        return wrapper
    return None


async def metadata(
    session: AdtSession, service: str, *, namespace: str = "", version: str = "v4"
) -> str:
    root = service_root(service, namespace=namespace, version=version)
    reply = await session.request("GET", f"{root}/$metadata", accept="application/xml")
    if not reply.text.strip():
        raise AdtError(f"{service} returned no metadata - is the service published?")
    return reply.text
