"""The transport organizer itself: what SE09 would show, and changing a request."""

from __future__ import annotations

from typing import Annotated

import typer

from adt_cli import cts, runtime, transports, ui
from adt_cli.commands.options import DestOpt, TraceOpt
from adt_cli.errors import AbapCliError
from adt_cli.workspace import Workspace

ACTIONS = ("list", "new", "attr", "delete")


def _wanted(request: transports.Request, customizing: bool | None) -> bool:
    return customizing is None or request.customizing is customizing


def _only(yes: bool, no: bool) -> bool | None:
    """Two opposing flags as one filter. Neither, or both, means no filter."""
    return None if yes == no else yes


def _pair(token: str) -> tuple[str, str]:
    """NAME=VALUE, split on the first '=' so the value may contain more."""
    name, sign, value = token.partition("=")
    if not sign or not name.strip():
        raise AbapCliError(f"'{token}' is not NAME=VALUE, e.g. Z_JIRA_US=O2CSM-1234")
    return name.strip().upper(), value.strip()


@runtime.guard
def transports_(
    action: Annotated[
        str, typer.Argument(help="'list' (default), 'new', 'attr' or 'delete'.")
    ] = "list",
    args: Annotated[
        list[str] | None,
        typer.Argument(help="Description for 'new'; TR then NAME=VALUE... for 'attr'."),
    ] = None,
    attr: Annotated[
        list[str] | None,
        typer.Option("--attr", "-a", help="NAME=VALUE to set on a new request. Repeatable."),
    ] = None,
    names: Annotated[
        bool, typer.Option("--names", help="List the CTS attributes this system defines.")
    ] = False,
    released: Annotated[bool, typer.Option("--released", help="Only released requests.")] = False,
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
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the confirmation for 'delete'.")
    ] = False,
    dest: DestOpt = None,
    system: Annotated[str, typer.Option("--system", "-s", help="Named system.")] = "",
    trace: TraceOpt = False,
) -> None:
    """List your transport requests, create one, or set its attributes.

    This is SE09 without the GUI: every request you own, split into workbench
    and customizing, modifiable and released. The filters narrow that list;
    passing neither of a pair, or both, means no narrowing.

    'new' creates an empty request and prints its number, ready to hand to
    'abap push --transport'. The target is left to SAP unless --target says
    otherwise, so the package's transport layer decides where it goes.

    'attr' shows or sets the CTS attributes of a request - Jira keys and the
    like. Setting one the request already carries replaces its value.

    'delete' removes a request or a single task. SAP refuses once it has been
    released, and the contents are shown before anything happens.

    Examples:

      abap transports                          everything you own
      abap transports --unreleased             only what you can still change
      abap transports --released --workbench   released workbench requests
      abap transports --user ANOTHER_DEV       somebody else's
      abap transports new 'O2CSM-1234 fix'     a workbench request
      abap transports new 'config' --customizing
      abap transports new 'fix' -a Z_JIRA_US=O2CSM-1234
      abap transports attr DHAK900123          what it carries
      abap transports attr DHAK900123 Z_JIRA_US=O2CSM-1234
      abap transports attr --names             what this system defines
      abap transports delete DHAK900123        drop an unwanted request
    """
    runtime.set_trace(trace)
    rest = [token for token in (args or []) if token.strip()]
    pairs = [_pair(token) for token in (attr or [])]
    if action not in ACTIONS:
        raise AbapCliError(f"unknown action '{action}' - use {', '.join(ACTIONS)}")
    if action == "new":
        if not rest:
            raise AbapCliError("'new' needs a description, e.g. abap transports new 'fix'")
        if len(rest) > 1:
            raise AbapCliError("'new' takes one description - quote it if it has spaces")
        if workbench and customizing:
            raise AbapCliError("a request is either workbench or customizing, not both")
    if action == "attr" and not names:
        if not rest:
            raise AbapCliError("'attr' needs a transport, e.g. abap transports attr DHAK900123")
        pairs += [_pair(token) for token in rest[1:]]
    if action == "delete" and len(rest) != 1:
        raise AbapCliError("'delete' needs one transport, e.g. abap transports delete DHAK900123")
    if action == "list":
        if rest:
            raise AbapCliError(f"'list' takes no argument - did you mean 'new {rest[0]}'?")
        if pairs:
            raise AbapCliError("--attr applies to 'new' and 'attr', not 'list'")
    # Checked before connecting, so a typo costs nothing.
    number = cts.normalise_request(rest[0]) if action in ("attr", "delete") and rest else ""

    space = Workspace.load(runtime.resolve_root(dest, ""))
    profile, password = runtime.connect(system or (space.system if space.exists else ""))

    async def body() -> None:
        async with runtime.session(profile, password) as adt:
            if action == "new":
                await _new(adt, rest[0], customizing, target, pairs)
            elif action == "attr" and names:
                await _catalogue(adt)
            elif action == "attr":
                await _attributes(adt, number, pairs)
            elif action == "delete":
                await _delete(adt, number, yes)
            else:
                await _list(
                    adt, user, _only(released, unreleased), _only(customizing, workbench), objects
                )

    runtime.run(body)


async def _new(
    adt, description: str, customizing: bool, target: str, pairs: list[tuple[str, str]]
) -> None:
    made = await transports.create(
        adt,
        description,
        type_code=transports.CUSTOMIZING if customizing else transports.WORKBENCH,
        target=target,
    )
    ui.console.print(f"created [bold]{made.number}[/]  {made.description}")
    for task in made.tasks:
        ui.console.print(f"[dim]  task {task.number}  {task.owner}[/]")
    # Attributes are separate calls: SAP sets them one at a time, by position.
    for name, value in pairs:
        landed = await transports.set_attribute(adt, made.number, name, value)
        ui.console.print(f"  [green]+[/]  {landed.describe()}")
    ui.console.print(f"[dim]abap push --transport {made.number}[/]")


async def _attributes(adt, number: str, pairs: list[tuple[str, str]]) -> None:
    for name, value in pairs:
        landed = await transports.set_attribute(adt, number, name, value)
        ui.console.print(f"  [green]+[/]  {landed.describe()}")
    present = await transports.attributes(adt, number)
    if not present:
        ui.console.print(f"[dim]{number} carries no attributes[/]")
        return
    if pairs:
        ui.console.print("")
    for entry in present:
        ui.console.print(f"  {entry.describe()}")
    ui.console.print(f"\n{len(present)} attribute(s) on {number}")


async def _delete(adt, number: str, yes: bool) -> None:
    entries, tasks = await transports.read(adt, number)
    ui.console.print(f"[bold]{number}[/]")
    for task in tasks:
        ui.console.print(f"[dim]  task {task.number}  {task.owner}  {task.status}[/]")
    for entry in entries:
        ui.console.print(f"  {ui.MARKER_DELETED}  {entry.describe()}")
    if not yes:
        held = f" and the {len(entries)} object(s) in it" if entries else ""
        ui.console.print(
            f"\n[red]This deletes {number}{held}.[/] "
            "A deleted request cannot be restored by this CLI."
        )
        if not typer.confirm("Continue?"):
            ui.console.print("cancelled")
            return
    await transports.delete(adt, number)
    ui.console.print(f"[green]deleted[/] {number}")


async def _catalogue(adt) -> None:
    defined = await transports.attribute_names(adt)
    for entry in defined:
        detail = f"  [dim]{entry.description}[/]" if entry.description else ""
        ui.console.print(f"  {entry.name}{detail}")
    ui.console.print(f"\n{len(defined)} attribute(s) defined on this system")


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
    for request in sorted(shown, key=lambda entry: (entry.category, entry.released, entry.number)):
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
