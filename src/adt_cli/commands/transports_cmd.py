"""The transport organizer itself: what SE09 would show, and creating a request."""

from __future__ import annotations

from typing import Annotated

import typer

from adt_cli import runtime, transports, ui
from adt_cli.commands.options import DestOpt, TraceOpt
from adt_cli.errors import AbapCliError
from adt_cli.workspace import Workspace


def _wanted(request: transports.Request, customizing: bool | None) -> bool:
    return customizing is None or request.customizing is customizing


def _only(yes: bool, no: bool) -> bool | None:
    """Two opposing flags as one filter. Neither, or both, means no filter."""
    return None if yes == no else yes


@runtime.guard
def transports_(
    action: Annotated[str, typer.Argument(help="'list' (default) or 'new'.")] = "list",
    description: Annotated[
        str, typer.Argument(help="Description of the request, for 'new'.")
    ] = "",
    released: Annotated[
        bool, typer.Option("--released", help="Only released requests.")
    ] = False,
    unreleased: Annotated[
        bool, typer.Option("--unreleased", help="Only modifiable (not yet released) requests.")
    ] = False,
    workbench: Annotated[
        bool, typer.Option("--workbench", help="Only workbench requests.")
    ] = False,
    customizing: Annotated[
        bool, typer.Option("--customizing", help="Only customizing requests.")
    ] = False,
    user: Annotated[
        str, typer.Option("--user", "-u", help="Whose requests to list. Defaults to you.")
    ] = "",
    target: Annotated[
        str, typer.Option("--target", help="Transport target for 'new'. SAP decides if omitted.")
    ] = "",
    objects: Annotated[
        bool, typer.Option("--objects", help="Also list the objects in each request.")
    ] = False,
    dest: DestOpt = None,
    system: Annotated[str, typer.Option("--system", "-s", help="Named system.")] = "",
    trace: TraceOpt = False,
) -> None:
    """List your transport requests, or create one.

    This is SE09 without the GUI: every request you own, split into workbench
    and customizing, modifiable and released. The filters narrow that list;
    passing neither of a pair, or both, means no narrowing.

    'new' creates an empty request and prints its number, ready to hand to
    'abap push --transport'. The target is left to SAP unless --target says
    otherwise, so the package's transport layer decides where it goes.

    Examples:

      abap transports                          everything you own
      abap transports --unreleased             only what you can still change
      abap transports --released --workbench   released workbench requests
      abap transports --user ANOTHER_DEV       somebody else's
      abap transports new 'O2CSM-1234 fix'     a workbench request
      abap transports new 'config' --customizing
    """
    runtime.set_trace(trace)
    if action not in ("list", "new"):
        raise AbapCliError(f"unknown action '{action}' - use list or new")
    if action == "new" and not description.strip():
        raise AbapCliError("'new' needs a description, e.g. abap transports new 'O2CSM-1234 fix'")
    if action == "list" and description:
        raise AbapCliError(
            f"'list' takes no argument - did you mean: abap transports new '{description}'?"
        )
    if action == "new" and workbench and customizing:
        raise AbapCliError("a request is either workbench or customizing, not both")

    space = Workspace.load(runtime.resolve_root(dest, ""))
    target_system, password = runtime.connect(system or (space.system if space.exists else ""))

    async def body() -> None:
        async with runtime.session(target_system, password) as adt:
            if action == "new":
                made = await transports.create(
                    adt,
                    description,
                    type_code=(
                        transports.CUSTOMIZING if customizing else transports.WORKBENCH
                    ),
                    target=target,
                )
                ui.console.print(f"created [bold]{made.number}[/]  {made.description}")
                for task in made.tasks:
                    ui.console.print(f"[dim]  task {task.number}  {task.owner}[/]")
                ui.console.print(f"[dim]abap push --transport {made.number}[/]")
                return
            await _list(
                adt, user, _only(released, unreleased), _only(customizing, workbench), objects
            )

    runtime.run(body)


async def _list(
    adt, user: str, released: bool | None, customizing: bool | None, with_objects: bool
) -> None:
    found = await transports.owned_by(adt, user, released=released)
    shown = [request for request in found if _wanted(request, customizing)]
    if not shown:
        hidden = len(found) - len(shown)
        detail = f" ({hidden} filtered out)" if hidden else ""
        ui.console.print(f"[dim]no transports matched{detail}[/]")
        return
    category = ""
    for request in sorted(
        shown, key=lambda entry: (entry.category, entry.released, entry.number)
    ):
        heading = f"{request.category} / {request.status}"
        if heading != category:
            category = heading
            ui.console.print(f"\n[bold]{heading}[/]")
        colour = "dim" if request.released else "green"
        ui.console.print(f"  [{colour}]{request.number}[/]  {request.description}")
        for task in request.tasks:
            ui.console.print(f"[dim]      task {task.number}  {task.owner}[/]")
        if with_objects:
            entries, _ = await transports.read(adt, request.number)
            for entry in entries:
                ui.console.print(f"[dim]        {entry.describe()}[/]")
    ui.console.print(f"\n{len(shown)} request(s)")
