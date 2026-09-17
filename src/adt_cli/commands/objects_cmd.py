"""Delete: remove objects from SAP.

The only irreversible thing this CLI does, so it asks first and refuses to
guess a transport.
"""

from __future__ import annotations

from typing import Annotated

import typer

from adt_cli import cts, objects, repository, runtime, transports, ui
from adt_cli.commands.options import DestOpt, TraceOpt
from adt_cli.errors import AbapCliError
from adt_cli.workspace import Workspace


@runtime.guard
def types(
    all_types: Annotated[
        bool, typer.Option("--all", "-a", help="Also show types that are listed but never pulled.")
    ] = False,
) -> None:
    """List the object types this CLI can pull and push.

    Reads the same registry that pull and push use, so it always describes the
    installed build rather than the documentation.

    Types marked 'xml' are edited as the object's own document; the rest are
    plain ABAP or DDL source. --all also lists the types SAP reports but ADT
    serves no editable content for, which pull skips.
    """
    folder = ""
    for entry in objects.supported():
        if entry.folder != folder:
            folder = entry.folder
            ui.console.print(f"\n[bold]{folder}[/]")
        suffixes = ", ".join(part.suffix for part in entry.parts)
        shape = "  [dim]xml[/]" if entry.parts[0].is_object else ""
        ui.console.print(f"  {entry.code:9} [dim]{suffixes}[/]{shape}")

    ui.console.print(
        f"\n[dim]{len(objects.supported())} type(s) pull fetches and push writes back.\n"
        "Types marked xml are edited as the object's own document rather than\n"
        "as source text; everything else is plain ABAP or DDL.[/]"
    )

    if not all_types:
        ui.console.print("[dim]Run with --all to see what is deliberately skipped.[/]")
        return
    skipped = objects.listed_only()
    ui.console.print(
        f"\n[dim]{len(skipped)} type(s) SAP lists but ADT serves no editable content "
        f"for, so pull skips them:[/]\n  [dim]{', '.join(e.code for e in skipped)}[/]"
    )
    ui.console.print(
        "[dim]  Anything not named here - SEGW projects, SICF nodes, enterprise\n"
        "  service proxies - is skipped as well.[/]"
    )


@runtime.guard
def delete(
    names: Annotated[
        list[str], typer.Argument(help="Object names, e.g. ZCL_THING ZCE_VIEW.")
    ],
    dest: DestOpt = None,
    transport: Annotated[str, typer.Option(help="Transport request.")] = "",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be deleted.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation.")] = False,
    trace: TraceOpt = False,
) -> None:
    """Delete objects from SAP and from the workspace.

    The only irreversible thing this CLI does, so it asks first and will not
    guess a transport. Only objects the workspace already tracks can be named,
    which keeps a typo from deleting something unrelated.

    Examples:

      abap delete ZCL_THING --dry-run
      abap delete ZCL_THING ZIF_THING --transport DHAK900123
    """
    runtime.set_trace(trace)
    requested = cts.normalise_request(transport)
    root = runtime.resolve_root(dest, "")
    space = runtime.load_workspace(root)

    targets = _resolve(space, names)
    for local, obj in targets:
        ui.console.print(f"  {ui.MARKER_DELETED}  {obj.name}  [dim]({local})[/]")
    if dry_run:
        ui.console.print(f"\n[dim]{len(targets)} object(s) would be deleted  [DRY RUN][/]")
        return

    if not yes:
        ui.console.print(
            f"\n[red]This deletes {len(targets)} object(s) from {space.system}.[/] "
            "Deleted objects cannot be restored by this CLI."
        )
        if not typer.confirm("Continue?"):
            ui.console.print("cancelled")
            return

    target, password = runtime.connect(space.system)
    local_package = space.package.startswith("$")

    async def body() -> None:
        async with runtime.session(target, password) as adt:
            corrnr = ""
            if not local_package:
                corrnr = await _settle(adt, space, targets, requested, target.user)
            for local, obj in targets:
                async with adt.locked(obj.uri) as lock:
                    params = {"lockHandle": lock.handle}
                    if corrnr:
                        params["corrNr"] = corrnr
                    await adt.request(
                        "DELETE", obj.uri, params=params, allow=(200, 201, 202, 204)
                    )
                space.files.pop(local, None)
                space.save()
                path = space.resolve(local)
                if path.is_file():
                    path.unlink()
                ui.console.print(f"  [green]deleted[/] {obj.name}")

    runtime.run(body)
    ui.console.print(f"\n{len(targets)} object(s) deleted")


def _resolve(space: Workspace, names: list[str]) -> list[tuple[str, repository.RepoObject]]:
    """Map object names onto the workspace, so only tracked objects can be deleted."""
    by_name = {entry.name.upper(): local for local, entry in space.files.items()}
    targets, missing = [], []
    for raw in names:
        local = by_name.get(raw.strip().upper())
        if local is None:
            missing.append(raw)
            continue
        targets.append((local, space.object_for(local)))
    if missing:
        for name in missing:
            ui.problem(f"{name} is not in this workspace")
        raise AbapCliError("only objects tracked by the workspace can be deleted - re-pull first")
    return targets


async def _settle(
    adt, space: Workspace, targets: list[tuple[str, repository.RepoObject]],
    requested: str, user: str,
) -> str:
    """A deletion has to be recorded somewhere, so the transport rules still apply."""
    locals_ = [local for local, _ in targets]
    holders = await cts.holders(adt, [obj for _, obj in targets])
    plan = cts.reconcile(
        locals_, holders, requested=requested, package=space.package, user=user
    )
    for line in plan.notes:
        ui.console.print(f"[dim]{line}[/]")
    return plan.request


@runtime.guard
def transport(
    number: Annotated[str, typer.Argument(help="Transport request or task.")],
    action: Annotated[
        str, typer.Argument(help="One of: list, add, remove.")
    ] = "list",
    names: Annotated[
        list[str] | None,
        typer.Argument(help="NAME, CLASS=>METHOD, or PGMID:TYPE:NAME."),
    ] = None,
    dest: DestOpt = None,
    system: Annotated[str, typer.Option("--system", "-s", help="Named system.")] = "",
    trace: TraceOpt = False,
) -> None:
    """Inspect a transport, or add and remove objects in it.

    Objects live in tasks inside a request, so either number may be given and
    the task you own is resolved for you.

    Names accept three forms: a plain object name, CLASS=>METHOD for a single
    method, or PGMID:TYPE:NAME when you need to be exact - which is what 'list'
    prints, so its output can be fed straight back in.

    Examples:

      abap transport DHAK900123
      abap transport DHAK900123 add ZCL_THING
      abap transport DHAK900123 add ZCL_THING=>CONSTRUCTOR
      abap transport DHAK900123 remove R3TR:CLAS:ZCL_THING
    """
    runtime.set_trace(trace)
    number = cts.normalise_request(number)
    wanted = [name.strip().upper() for name in (names or []) if name.strip()]
    if action not in ("list", "add", "remove"):
        raise AbapCliError(f"unknown action '{action}' - use list, add or remove")
    if action != "list" and not wanted:
        raise AbapCliError(f"'{action}' needs at least one object name")

    space = Workspace.load(runtime.resolve_root(dest, ""))
    target, password = runtime.connect(system or (space.system if space.exists else ""))

    async def body() -> None:
        async with runtime.session(target, password) as adt:
            if action == "list":
                await _list(adt, number)
                return
            if action == "add":
                entries = _entries(space, wanted)
                present = await transports.add(adt, number, entries)
                for entry in present:
                    ui.console.print(f"  [green]+[/]  {entry.describe()}")
                missing = _diff(entries, present)
                for entry in missing:
                    ui.problem(f"{entry.describe()} was not added")
                ui.console.print(f"\n{len(present)} object(s) in {number}")
                return
            # Removal can name an object that no longer exists locally, so the
            # type comes from the request itself.
            entries = await _from_transport(adt, number, wanted)
            still_there = await transports.remove(adt, number, entries)
            gone = _diff(entries, still_there)
            for entry in gone:
                ui.console.print(f"  [green]-[/]  {entry.describe()} removed")
            for entry in still_there:
                ui.problem(
                    f"{entry.describe()} is still in {number}"
                    + (" - the request holds its lock" if entry.locked else "")
                )
            if still_there:
                raise AbapCliError(
                    "SAP keeps an object in the request that owns its lock. Release or "
                    "delete the request, or move the object with a fresh pull and push."
                )
            ui.console.print(f"\n{len(gone)} object(s) removed from {number}")

    runtime.run(body)


async def _list(adt, number: str) -> None:
    objects, tasks = await transports.read(adt, number)
    for task in tasks:
        ui.console.print(f"[dim]task {task.number}  {task.owner}  {task.status}[/]")
    for entry in objects:
        marker = "[yellow]locked[/]" if entry.locked else "      "
        ui.console.print(f"  {marker}  {entry.describe()}")
    ui.console.print(f"\n{len(objects)} object(s) in {number}")


def _parse_key(token: str) -> transports.TransportObject | None:
    """An explicit object key, or None when the workspace has to supply the type.

    ``CLASS=>METHOD`` is the LIMU form SAP stores as a 30-character class name
    followed by the method, which is how one edited method lands in a transport
    on its own.
    """
    if token.count(":") == 2:
        pgmid, type_code, name = token.split(":")
        return transports.TransportObject(pgmid.upper(), type_code.upper(), name.upper(), False)
    if "=>" in token:
        owner, member = token.split("=>", 1)
        return transports.TransportObject(
            "LIMU", "METH", f"{owner.upper():<30}{member.upper()}", False
        )
    return None


async def _from_transport(
    adt, number: str, wanted: list[str]
) -> list[transports.TransportObject]:
    present, _ = await transports.read(adt, number)
    by_name = {entry.name.upper(): entry for entry in present}
    entries, missing = [], []
    for token in wanted:
        explicit = _parse_key(token)
        known = by_name.get(explicit.name if explicit else token)
        if known is None:
            missing.append(token)
        else:
            entries.append(known)
    if missing:
        for token in missing:
            ui.problem(f"{token} is not in {number}")
        raise AbapCliError("nothing to remove")
    return entries


def _entries(space: Workspace, wanted: list[str]) -> list[transports.TransportObject]:
    """Object types come from the workspace manifest unless the key is explicit."""
    by_name = {entry.name.upper(): entry for entry in space.files.values()}
    entries, missing = [], []
    for token in wanted:
        explicit = _parse_key(token)
        if explicit is not None:
            entries.append(explicit)
            continue
        known = by_name.get(token)
        if known is None:
            missing.append(token)
            continue
        entries.append(
            transports.TransportObject(
                pgmid="R3TR",
                type_code=known.type_code.split("/", 1)[0],
                name=known.name,
                locked=False,
            )
        )
    if missing:
        for token in missing:
            ui.problem(f"{token} is not in this workspace, so its object type is unknown")
        raise AbapCliError(
            "run this from a workspace that has the object, or name it explicitly "
            "as PGMID:TYPE:NAME"
        )
    return entries


def _diff(
    wanted: list[transports.TransportObject], present: list[transports.TransportObject]
) -> list[transports.TransportObject]:
    keys = {(entry.pgmid, entry.type_code, entry.name) for entry in present}
    return [
        entry for entry in wanted if (entry.pgmid, entry.type_code, entry.name) not in keys
    ]
