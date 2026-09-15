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

from adt_cli import repository, runtime, ui
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
        bool | None, typer.Option("--se80/--flat", help="Lay files out as an SE80 tree.")
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

    layout_se80 = previous.se80 if se80 is None else se80
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

    async def body() -> tuple[Workspace, list[repository.Fetched]]:
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
            space = Workspace(
                root=root,
                package=package.upper(),
                system=target.name,
                se80=layout_se80,
                match=pattern,
                types=wanted,
                owner=owner,
            )
            results = await _fetch_with_progress(adt, found, jobs)
            for result in results:
                if result.ok:
                    space.write(result.obj, result.text)
            space.save()
            return space, results

    space, results = runtime.run(body)

    elapsed = time.perf_counter() - started
    written = [result for result in results if result.ok]
    failed = [result for result in results if not result.ok]
    total = sum(len(result.text) for result in written)
    ui.console.print(
        f"pulled [bold]{space.package}[/] from [bold]{target.name}[/] "
        f"- {len(written)} objects, {total:,} bytes in {elapsed:.1f}s"
    )
    ui.console.print(f"[dim]{root}[/]")
    if dest:
        runtime.add_to_vscode(root)
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
        task = progress.add_task(f"pulling {len(found)} objects", total=len(found))

        def on_progress(result: repository.Fetched) -> None:
            progress.advance(task)
            if not result.ok:
                progress.console.print(f"  [red]![/]  {result.obj.name}: {result.error}")

        return await repository.fetch_sources(
            adt, found, concurrency=jobs, on_progress=on_progress
        )
