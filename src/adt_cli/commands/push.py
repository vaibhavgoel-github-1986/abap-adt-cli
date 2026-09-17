"""Push: send edited files back through ADT.

Pull brings down abapGit's whole file set, but only some of those files have an
ADT source endpoint. Those are written individually, so SAP records a one-method
edit as a single LIMU entry instead of locking the whole class. Files without an
endpoint - metadata XML, SEGW projects, SICF nodes - are reported and skipped.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Annotated

import typer

from adt_cli import (
    activation,
    creation,
    cts,
    layout,
    objects,
    repository,
    runtime,
    ui,
    workspace,
)
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
    yes: Annotated[
        bool, typer.Option("--yes", "-y", help="Skip the prompt about unsupported files.")
    ] = False,
    trace: TraceOpt = False,
) -> None:
    """Upload locally modified objects. They stay inactive until activated."""
    runtime.set_trace(trace)
    requested = cts.normalise_request(transport)
    root = runtime.resolve_root(dest, package)
    space = runtime.load_workspace(root)

    # The baseline hashes in the manifest only describe the system the files came
    # from, so pushing them anywhere else is never a safe diff.
    if system and system != space.system:
        raise ConflictError(
            f"workspace was pulled from {space.system}, refusing to push to {system} - "
            f"pull the package from {system} into its own folder if that is the real target"
        )

    modified, deleted = space.scan()
    fresh = space.untracked()
    if deleted:
        ui.note(f"{len(deleted)} deleted file(s) are ignored by push")
    if not modified and not fresh:
        ui.console.print("nothing to push - no local changes")
        return

    changed = sorted(set(modified) | set(fresh))
    supported = [local for local in changed if objects.adt_target(local)]
    unsupported = [local for local in changed if not objects.adt_target(local)]

    for local in changed:
        marker = ui.MARKER_NEW if local in fresh else ui.MARKER_MINE
        note = "  [dim](new)[/]" if local in fresh else ""
        if local in unsupported:
            note += "  [yellow](no ADT endpoint, will be skipped)[/]"
        ui.console.print(f"  {marker}  {local}{note}")

    if unsupported and not _confirm(unsupported, supported, yes, dry_run):
        raise AbapCliError("nothing pushed")

    if dry_run:
        ui.console.print(f"\n[dim]{len(supported)} file(s) would be pushed  [DRY RUN][/]")
        return

    target, password = runtime.connect(space.system)
    plan = [_Item(local, space) for local in supported]

    async def body() -> activation.Outcome | None:
        async with runtime.zsync(target, password) as sap:
            if not force:
                await _refuse_on_drift(sap, space, modified, target.name)

        async with runtime.session(target, password) as adt:
            corrnr = await _transport_for(adt, space, plan, requested, target.user)
            for item in plan:
                if item.local in fresh:
                    await _create(adt, space, item, corrnr)
                await repository.write_source_at(
                    adt, item.object_uri, item.source_uri, space.read(item.local), transport=corrnr
                )
                ui.console.print(f"  [green]pushed[/] {item.label}")
            if not activate_after:
                return None
            touched = _distinct_objects(plan)
            ui.console.print(f"\nactivating {len(touched)} object(s)...")
            return await activation.activate(adt, touched)

    outcome = runtime.run(body)

    for item in plan:
        entry = space.files.get(item.local)
        if entry is not None:
            entry.sha256 = workspace.sha256(space.read_bytes(item.local))
    space.save()

    ui.console.print(f"\n{len(plan)} file(s) pushed")
    if outcome is not None and not _report_activation(outcome, len(plan), target.name):
        raise typer.Exit(EXIT_ERROR)


class _Item:
    """One file to push, with the ADT endpoint that accepts it."""

    def __init__(self, local: str, space: Workspace) -> None:
        target = objects.adt_target(local)
        if target is None:
            raise AbapCliError(f"{local} has no ADT source endpoint")
        name, _ = _identity(local)
        self.local = local
        self.name = name
        self.type_code = target.type_code
        self.package = space.package_of(local)
        self.object_uri = objects.adt_object_uri(target, name)
        self.source_uri = f"{self.object_uri}{target.source_path}"

    @property
    def label(self) -> str:
        member = Path(self.local).name.split(".", 2)[-1]
        return f"{self.type_code.split('/')[0]} {self.name}  [dim]{member}[/]"

    def as_object(self) -> repository.RepoObject:
        return repository.RepoObject(
            name=self.name, type_code=self.type_code, uri=self.object_uri
        )


def _identity(local: str) -> tuple[str, str]:
    name, obj_type = layout.parse_filename(Path(local).name)
    return name, obj_type


def _distinct_objects(plan: list[_Item]) -> list[repository.RepoObject]:
    """One entry per object, so a class edited in three files activates once."""
    seen: dict[str, repository.RepoObject] = {}
    for item in plan:
        seen.setdefault(item.object_uri, item.as_object())
    return list(seen.values())


def _confirm(unsupported: list[str], supported: list[str], yes: bool, dry_run: bool) -> bool:
    by_type: dict[str, list[str]] = defaultdict(list)
    for local in unsupported:
        _, obj_type = _identity(local)
        by_type[obj_type or "?"].append(local)

    ui.console.print(
        f"\n[yellow]{len(unsupported)} changed file(s) have no ADT endpoint "
        "and cannot be pushed yet:[/]"
    )
    for obj_type, locals_ in sorted(by_type.items()):
        ui.console.print(f"  [bold]{obj_type}[/]  {len(locals_)} file(s)")
        for local in locals_[:5]:
            ui.console.print(f"     [dim]{local}[/]")
        if len(locals_) > 5:
            ui.console.print(f"     [dim]... and {len(locals_) - 5} more[/]")

    if not supported:
        ui.console.print("\n[red]none of the changed files can be pushed through ADT[/]")
        return False
    if yes or dry_run:
        return True
    return typer.confirm(
        f"\nPush the {len(supported)} supported file(s) and skip the rest?", default=False
    )


async def _transport_for(
    adt, space: Workspace, plan: list[_Item], requested: str, user: str
) -> str:
    # Local packages ($TMP and friends) are never transported.
    if space.package.startswith("$") and not requested:
        return ""
    holders = await cts.holders(adt, _distinct_objects(plan))
    decided = cts.reconcile(
        [item.local for item in plan],
        holders,
        requested=requested,
        package=space.package,
        user=user,
    )
    for line in decided.notes:
        ui.console.print(f"[dim]{line}[/]")
    return decided.request


async def _create(adt, space: Workspace, item: _Item, transport: str) -> None:
    if not creation.supported(item.type_code):
        raise AbapCliError(
            f"{item.local} is a new {item.type_code} object and this CLI cannot create it yet - "
            "create it in Eclipse/ADT first, then re-pull"
        )
    text = space.read(item.local)
    await creation.create(
        adt,
        item.type_code,
        item.name,
        item.package,
        creation.describe(text, item.name),
        transport=transport,
    )
    ui.console.print(f"  [green]created[/] {item.name} in {item.package}")


async def _refuse_on_drift(sap, space: Workspace, modified: list[str], system: str) -> None:
    """Drift is checked against a fresh abapGit export, not ADT.

    The manifest baseline was written by the abapGit serialiser, so only another
    export is a like-for-like comparison.
    """
    remote = await runtime.remote_archive(sap, space)
    drifted, vanished = runtime.archive_drift(space, remote, modified)
    for local in vanished:
        ui.problem(f"{local} no longer exists on {system}")
    if not drifted:
        return
    for local in drifted:
        ui.err_console.print(
            f"  {ui.MARKER_BOTH}  {local} also changed on {system} since your pull"
        )
    raise ConflictError(
        f"{len(drifted)} file(s) changed on the server - pushing would overwrite that "
        "work. Re-pull to inspect, or use --force to overwrite."
    )


def _report_activation(outcome: activation.Outcome, count: int, system: str) -> bool:
    for warning in outcome.warnings:
        ui.console.print(f"  [yellow]warning[/] {warning}")
    for error in outcome.errors:
        ui.problem(error)
    if outcome.ok:
        ui.console.print(f"[green]activated[/] {count} file(s) on {system}")
        return True
    if not outcome.executed:
        ui.err_console.print("[red]error[/] activation did not run")
    else:
        ui.err_console.print(
            f"[red]error[/] {len(outcome.errors)} activation error(s) - "
            "the objects stay inactive until they are fixed"
        )
    return False
