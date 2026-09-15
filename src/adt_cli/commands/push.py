"""Push: upload locally modified objects, optionally activating them."""

from __future__ import annotations

from typing import Annotated

import typer

from adt_cli import activation, creation, cts, objects, repository, runtime, ui, workspace
from adt_cli.commands.options import DestOpt, PackageArg, TraceOpt
from adt_cli.errors import EXIT_ERROR, AbapCliError, ConflictError
from adt_cli.workspace import Workspace


@runtime.guard
def push(
    package: PackageArg = "",
    system: Annotated[
        str,
        typer.Option("--system", "-s", help="Must match the system the workspace was pulled from."),
    ] = "",
    dest: DestOpt = None,
    transport: Annotated[str, typer.Option(help="Transport request.")] = "",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be sent.")] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Push even if the object changed on the server.")
    ] = False,
    activate_after: Annotated[
        bool, typer.Option("--activate", help="Activate the pushed objects afterwards.")
    ] = False,
    trace: TraceOpt = False,
) -> None:
    """Upload locally modified objects. They stay inactive until activated."""
    runtime.set_trace(trace)
    requested = cts.normalise_request(transport)
    root = runtime.resolve_root(dest, package)
    space = runtime.load_workspace(root)

    # The baseline hashes and object URIs in the manifest only describe the system
    # the files came from, so pushing them anywhere else is never a safe diff.
    if system and system != space.system:
        raise ConflictError(
            f"workspace was pulled from {space.system}, refusing to push to {system} - "
            f"pull the package from {system} into its own folder if that is the real target"
        )

    modified = _plan_files(space, dry_run)
    if modified is None:
        return
    fresh = space.untracked()

    target, password = runtime.connect(space.system)
    # Local packages ($TMP and friends) are never transported.
    local_package = space.package.startswith("$")

    async def body() -> activation.Outcome | None:
        async with runtime.session(target, password) as adt:
            if not force:
                await _refuse_on_drift(adt, space, modified, target.name)
            corrnr = ""
            if not local_package or requested:
                holders = await cts.holders(
                    adt, [space.object_for(local) for local in modified]
                )
                plan = cts.reconcile(
                    modified,
                    holders,
                    requested=requested,
                    package=space.package,
                    user=target.user,
                )
                for line in plan.notes:
                    ui.console.print(f"[dim]{line}[/]")
                corrnr = plan.request

            pushed = await _create(adt, space, fresh, corrnr)
            pushed += await _upload(adt, space, modified, corrnr)
            if not activate_after:
                return None
            # Every object is already unlocked, so one run covers the whole batch.
            ui.console.print(f"\nactivating {len(pushed)} object(s)...")
            return await activation.activate(adt, pushed)

    outcome = runtime.run(body)
    ui.console.print(f"\n{len(modified) + len(fresh)} object(s) pushed")
    if outcome is not None and not _report_activation(
        outcome, len(modified) + len(fresh), target.name
    ):
        raise typer.Exit(EXIT_ERROR)


def _plan_files(space: Workspace, dry_run: bool) -> list[str] | None:
    """The objects push would send, or None when there is nothing left to do."""
    modified, deleted = space.scan()
    fresh = space.untracked()
    if deleted:
        ui.note(f"{len(deleted)} deleted file(s) are ignored by push")
    if not modified and not fresh:
        ui.console.print("nothing to push - no local changes")
        return None

    blocked = [local for local in modified if not space.writable(local)]
    unsupported = [
        local for local in fresh if not creation.supported(objects.type_for_file(local).code)
    ]
    if blocked:
        for local in blocked:
            ui.problem(f"{local} is not a writable source object")
        raise AbapCliError("refusing to push non-source objects")
    if unsupported:
        for local in unsupported:
            ui.problem(f"{local} is a new object of a type this CLI cannot create yet")
        raise AbapCliError("create these in Eclipse/ADT first, then re-pull")

    for local in fresh:
        ui.console.print(f"  {ui.MARKER_NEW}  {local}  [dim](new)[/]")
    for local in modified:
        ui.console.print(f"  {ui.MARKER_MINE}  {local}")
    if dry_run:
        count = len(modified) + len(fresh)
        ui.console.print(f"\n[dim]{count} object(s) would be pushed  [DRY RUN][/]")
        return None
    return modified


async def _refuse_on_drift(adt, space: Workspace, modified: list[str], system: str) -> None:
    drifted, unreadable = await runtime.remote_drift(adt, space, modified)
    for local in unreadable:
        ui.problem(f"{local} could not be read back")
    if not drifted:
        return
    for local in drifted:
        ui.err_console.print(
            f"  {ui.MARKER_BOTH}  {local} also changed on {system} since your pull"
        )
    raise ConflictError(
        f"{len(drifted)} object(s) changed on the server - pushing would overwrite that "
        "work. Re-pull to inspect, or use --force to overwrite."
    )


async def _create(
    adt, space: Workspace, fresh: list[str], transport: str
) -> list[repository.RepoObject]:
    """New files have no ADT URI yet, so the object is created before its source."""
    created = []
    for local in fresh:
        kind = objects.type_for_file(local)
        name = objects.name_for_file(local, kind)
        text = space.read(local)
        uri = await creation.create(
            adt,
            kind.code,
            name,
            space.package,
            creation.describe(text, name),
            transport=transport,
        )
        obj = repository.RepoObject(name=name, type_code=kind.code, uri=uri)
        await repository.write_source(adt, obj, text, transport=transport)
        space.files[local] = workspace.Entry(
            name=name, type_code=kind.code, uri=uri, sha256=workspace.sha256(text)
        )
        space.save()
        created.append(obj)
        ui.console.print(f"  [green]created[/] {name}")
    return created


async def _upload(
    adt, space: Workspace, modified: list[str], transport: str
) -> list[repository.RepoObject]:
    pushed = []
    for local in modified:
        obj = space.object_for(local)
        text = space.read(local)
        await repository.write_source(adt, obj, text, transport=transport)
        # Re-baseline as we go: a later failure must not make an already pushed
        # object look unpushed.
        space.files[local].sha256 = workspace.sha256(text)
        space.save()
        pushed.append(obj)
        ui.console.print(f"  [green]pushed[/] {obj.name}")
    return pushed


def _report_activation(outcome: activation.Outcome, count: int, system: str) -> bool:
    for warning in outcome.warnings:
        ui.console.print(f"  [yellow]warning[/] {warning}")
    for error in outcome.errors:
        ui.problem(error)
    if outcome.ok:
        ui.console.print(f"[green]activated[/] {count} object(s) on {system}")
        return True
    if not outcome.executed:
        ui.err_console.print("[red]error[/] activation did not run")
    else:
        ui.err_console.print(
            f"[red]error[/] {len(outcome.errors)} activation error(s) - "
            "the objects stay inactive until they are fixed"
        )
    return False
