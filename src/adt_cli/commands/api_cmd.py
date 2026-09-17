"""Call an OData service published by the same system."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from adt_cli import odata, runtime, ui
from adt_cli.commands.options import DestOpt, TraceOpt
from adt_cli.errors import AbapCliError
from adt_cli.workspace import Workspace


@runtime.guard
def api(
    method: Annotated[
        str, typer.Argument(help="GET, POST, PUT, PATCH, DELETE or HEAD.")
    ] = "GET",
    service: Annotated[str, typer.Argument(help="Service name, e.g. ZSD_EXAMPLE_API.")] = "",
    entity: Annotated[
        str, typer.Argument(help="Entity or path, e.g. Customers or Customers('C1')/Orders.")
    ] = "",
    namespace: Annotated[
        str,
        typer.Option("--namespace", "-n", help="Service binding, for v4. Usually ZSB_*."),
    ] = "",
    v2: Annotated[bool, typer.Option("--v2", help="A Gateway v2 service, not RAP v4.")] = False,
    query: Annotated[
        list[str] | None,
        typer.Option("--query", "-q", help="Query option, e.g. '$top=10'. Repeatable."),
    ] = None,
    body: Annotated[
        str,
        typer.Option("--body", "-b", help="JSON body, or @file.json, for POST/PUT/PATCH."),
    ] = "",
    metadata: Annotated[
        bool, typer.Option("--metadata", help="Fetch $metadata instead of calling an entity.")
    ] = False,
    raw: Annotated[bool, typer.Option("--raw", help="Print the response verbatim.")] = False,
    dest: DestOpt = None,
    system: Annotated[str, typer.Option("--system", "-s", help="Named system.")] = "",
    trace: TraceOpt = False,
) -> None:
    """Call an OData service on the system, reusing your ADT logon.

    The same session, credentials and TLS settings a pull uses, so no separate
    logon is needed and --trace shows the exchange like any other request.

    A v4 service is published under its *binding*, not its definition, so pass
    --namespace when the two differ - it is the ZSB_* object. A v2 Gateway
    service needs only its own name and --v2.

    Writes fetch a CSRF token from the service first, because Gateway issues
    those per service and will not accept the one ADT handed out at logon.

    Examples:

      abap api GET ZSD_EXAMPLE_API Customers -n ZSB_EXAMPLE_API
      abap api GET ZSD_EXAMPLE_API Customers -q '$top=5' -q '$select=Name'
      abap api GET ZMM_EXAMPLE_SRV Products --v2
      abap api GET ZSD_EXAMPLE_API --metadata
      abap api POST ZSD_EXAMPLE_API Customers -b '{"Name":"ACME"}'
      abap api PATCH ZSD_EXAMPLE_API "Customers('C1')" -b @change.json
    """
    runtime.set_trace(trace)
    if not service:
        raise AbapCliError("a service name is required, e.g. abap api GET ZSD_EXAMPLE_API Items")
    verb = method.strip().upper()
    if not metadata and not entity and verb != "GET":
        raise AbapCliError(f"{verb} needs an entity, e.g. abap api {verb} {service} Items")
    version = "v2" if v2 else "v4"
    options = odata.parse_query(query or [])
    payload = odata.parse_body(body) if body else None

    space = Workspace.load(runtime.resolve_root(dest, ""))
    profile, password = runtime.connect(system or (space.system if space.exists else ""))

    async def body_() -> None:
        async with runtime.session(profile, password) as adt:
            if metadata:
                ui.raw(
                    await odata.metadata(
                        adt, service, namespace=namespace, version=version
                    )
                )
                return
            status, answer = await odata.call(
                adt,
                verb,
                service,
                entity,
                namespace=namespace,
                version=version,
                query=options,
                body=payload,
            )
            _report(status, answer, raw)

    runtime.run(body_)


def _report(status: int, answer: object, raw: bool) -> None:
    if answer is None:
        ui.console.print(f"[green]{status}[/] [dim]no content[/]")
        return
    if raw or isinstance(answer, str):
        ui.raw(answer if isinstance(answer, str) else json.dumps(answer))
        return
    ui.console.print_json(json.dumps(answer))
    found = odata.rows(answer)
    if found is not None:
        ui.console.print(f"\n[dim]{status} - {len(found)} row(s)[/]")
    else:
        ui.console.print(f"\n[dim]{status}[/]")
