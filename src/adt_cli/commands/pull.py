"""Pull: download a package into a local workspace."""

from __future__ import annotations

import time
from typing import Annotated

import typer
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from adt_cli import expand, repository, runtime, ui
from adt_cli.commands.options import DestOpt, JobsOpt, PackageArg, SystemOpt, TraceOpt
from adt_cli.errors import AbapCliError, WorkspaceError
from adt_cli.workspace import Workspace


def parse_types(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def wanted_type(code: str, wanted: list[str]) -> bool:
    """Accepts both the full ADT code and its bare form: CLAS/OC and CLAS."""
    code = code.upper()
    return code in wanted or code.split("/", 1)[0] in wanted


@runtime.guard
def pull(
    package: PackageArg = "",
    system: SystemOpt = "",
    dest: DestOpt = None,
    se80: Annotated[
        bool | None,
        typer.Option(
            "--se80/--flat",
            help="SE80 folder tree (default), or one flat src/ folder.",
        ),
    ] = None,
    jobs: JobsOpt = 16,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite local changes.")] = False,
    match: Annotated[
        str, typer.Option("--match", "-m", help="Object name pattern, e.g. 'ZCL_SUBS*'.")
    ] = "",
    types: Annotated[
        str, typer.Option("--type", "-t", help="Comma-separated ADT types, e.g. 'CLAS,DDLS'.")
    ] = "",
    user: Annotated[
        str,
        typer.Option(
            "--user",
            "-u",
            help="Object owner. Defaults to you for local $ packages; '*' means everyone.",
        ),
    ] = "",
    trace: TraceOpt = False,
) -> None:
    """Download a package into a local folder, in parallel."""
    runtime.set_trace(trace)
    root = runtime.resolve_root(dest, package)
    previous = Workspace.load(root)

    if not package:
        if not previous.exists:
            raise WorkspaceError(
                f"no package given and no manifest in {root} - try 'abap pull <PACKAGE>'"
            )
        package = previous.package

    if previous.exists and not force:
        _refuse_to_discard(previous)

    # A refresh keeps the layout it was pulled with; a fresh pull defaults to SE80.
    layout_se80 = (previous.se80 if previous.exists else True) if se80 is None else se80
    # A refresh keeps whatever narrowed the original pull, so it cannot silently widen.
    pattern = match or previous.match
    wanted = parse_types(types) or previous.types
    target, password = runtime.connect(system or previous.system)

    # $TMP is one package shared by every developer, so narrow it to your own
    # objects unless asked otherwise. '*' is the explicit way back to everybody.
    owner = user or previous.owner
    if not owner and package.startswith("$"):
        owner = target.user
    owner_filter = "" if owner == "*" else owner.upper()

    started = time.perf_counter()

    async def body() -> tuple[Workspace, list[repository.Fetched], int]:
        async with runtime.session(target, password, jobs) as adt:
            scope = f" owned by {owner_filter}" if owner_filter else ""
            ui.console.print(f"[dim]connected to {target.name}, listing {package}{scope}...[/]")
            found = await repository.list_package(
                adt, package, pattern=pattern or "*", owner=owner_filter
            )
            if wanted:
                found = [obj for obj in found if wanted_type(obj.type_code, wanted)]
            if not found:
                raise AbapCliError(f"nothing in {package.upper()} matched")
            # Function groups only name themselves; their includes and function
            # modules are separate ADT objects that have to be resolved first.
            found = await expand.expand(adt, found, concurrency=jobs)
            reachable = [obj for obj in found if repository.is_adt_resource(obj)]
            elsewhere = len(found) - len(reachable)
            space = Workspace(
                root=root,
                package=package.upper(),
                system=target.name,
                se80=layout_se80,
                match=pattern,
                types=wanted,
                owner=owner,
            )
            results = await _fetch_with_progress(adt, reachable, jobs)
            for result in results:
                if result.ok and not result.absent:
                    space.write(result.obj, result.text, result.part)
            space.save()
            return space, results, elsewhere

    space, results, elsewhere = runtime.run(body)

    elapsed = time.perf_counter() - started
    written = [result for result in results if result.ok and not result.absent]
    failed = [result for result in results if not result.ok]
    # An object ADT lists but cannot serve, such as a Gateway Service Builder project.
    unserved = {
        (result.obj.type_code, result.obj.name)
        for result in results
        if result.absent and result.part is None
    }
    total = sum(len(result.text) for result in written)
    objects = len({(result.obj.type_code, result.obj.name) for result in written})
    ui.console.print(
        f"pulled [bold]{space.package}[/] from [bold]{target.name}[/] "
        f"- {objects} objects in {len(written)} files, {total:,} bytes in {elapsed:.1f}s"
    )
    ui.console.print(f"[dim]{root}[/]")
    if dest:
        runtime.add_to_vscode(root)
    if elsewhere or unserved:
        ui.console.print(
            f"[dim]{elsewhere + len(unserved)} object(s) have no ADT representation "
            "and were skipped[/]"
        )
    if failed:
        ui.console.print(f"[dim]{len(failed)} object(s) failed to pull[/]")


def _refuse_to_discard(previous: Workspace) -> None:
    modified, deleted = previous.scan()
    if not modified and not deleted:
        return
    for local in modified:
        ui.err_console.print(f"  [yellow]M[/]  {local}")
    for local in deleted:
        ui.err_console.print(f"  [red]D[/]  {local}")
    raise AbapCliError(
        f"{len(modified)} modified, {len(deleted)} deleted - push them, "
        "or re-run with --force to discard and overwrite"
    )


async def _fetch_with_progress(adt, found, jobs: int) -> list[repository.Fetched]:
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=ui.console,
    ) as progress:
        # One request per editable part, so a class counts for five.
        requests = sum(len(obj.kind.parts) if obj.kind.is_source else 1 for obj in found)
        task = progress.add_task(f"pulling {len(found)} objects", total=requests)

        def on_progress(result: repository.Fetched) -> None:
            progress.advance(task)
            if not result.ok:
                progress.console.print(f"  [red]![/]  {result.label}: {result.error}")

        return await repository.fetch_sources(
            adt, found, concurrency=jobs, on_progress=on_progress
        )
