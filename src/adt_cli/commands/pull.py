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

from adt_cli import config, expand, repository, runtime, ui
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
    subpackages: Annotated[
        bool | None,
        typer.Option(
            "--subpackages/--no-subpackages",
            help="Include the packages below this one (default), or this package alone.",
        ),
    ] = None,
    jobs: JobsOpt = 30,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite local changes.")] = False,
    match: Annotated[
        str, typer.Option("--match", "-m", help="Object name pattern, e.g. 'ZCL_EX*'.")
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
    """Download a package into a local folder, in parallel.

    The whole package hierarchy comes down by default. SAP lists a package's
    sub-packages as part of the package itself, so --no-subpackages has to
    subtract them: it pulls only the objects that sit directly in the package
    named.

    Every editable text becomes its own file, so a class arrives as its main
    source plus any local definitions, local implementations, macros and test
    classes it has, and a function group brings its includes and function
    modules with it.

    Run inside an already-pulled folder with no arguments to refresh it: the
    package, system, layout and any --match/--type/--user narrowing are taken
    from the last pull, so a refresh can never silently widen.

    Object types ADT serves no editable content for are skipped and counted in
    one line. Run 'abap types' to see what is covered.

    Examples:

      abap pull ZMY_PACKAGE                    into ./ZMY_PACKAGE
      abap pull '$TMP'                         into ./<YOUR_USER>/$TMP
      abap pull ZMY_PACKAGE --no-subpackages   that one package only
      abap pull ZMY_PACKAGE --type CLAS,DDLS   only those types
      abap pull ZMY_PACKAGE --match 'ZCL_A*'   only matching names
      abap pull --force                        refresh, discarding local edits
    """
    runtime.set_trace(trace)
    # $TMP is filtered to one developer, so the folder is named after them too.
    # Resolving the profile reads config only - no connection yet.
    scope = user
    if not scope and runtime.is_shared(package):
        scope = config.resolve(name=system).user
    root = runtime.resolve_root(dest, package, owner=scope)
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
    # Likewise for breadth: a refresh reaches exactly as far as the pull it repeats.
    recurse = (
        (previous.subpackages if previous.exists else True)
        if subpackages is None
        else subpackages
    )
    # A refresh keeps whatever narrowed the original pull, so it cannot silently widen.
    pattern = match or previous.match
    wanted = parse_types(types) or previous.types
    target, password = runtime.connect(system or previous.system)

    # $TMP is one package shared by every developer, so narrow it to your own
    # objects unless asked otherwise. '*' is the explicit way back to everybody.
    owner = user or previous.owner
    if not owner and runtime.is_shared(package):
        owner = target.user
    owner_filter = "" if owner == "*" else owner.upper()

    started = time.perf_counter()

    async def body() -> tuple[Workspace, list[repository.Fetched], int, list[str]]:
        async with runtime.session(target, password, jobs) as adt:
            scope = f" owned by {owner_filter}" if owner_filter else ""
            tree = " and its sub-packages" if recurse else ""
            ui.console.print(
                f"[dim]connected to {target.name}, listing {package}{tree}{scope}...[/]"
            )
            found, packages = await repository.list_tree(
                adt,
                package,
                pattern=pattern or "*",
                owner=owner_filter,
                recurse=recurse,
                concurrency=jobs,
            )
            spread = f" in {len(packages)} packages" if len(packages) > 1 else ""
            ui.console.print(f"[dim]{len(found)} objects{spread}[/]")
            if wanted:
                found = [obj for obj in found if wanted_type(obj.type_code, wanted)]
            if not found:
                raise AbapCliError(f"nothing in {package.upper()} matched")
            # Function groups only name themselves; their includes and function
            # modules are separate ADT objects that have to be resolved first.
            found = await expand.expand(adt, found, concurrency=jobs)
            # A type with no editable source can never be pushed back, so pulling
            # its metadata would only fill the workspace with read-only files.
            reachable = [
                obj
                for obj in found
                if obj.kind.is_source and repository.is_adt_resource(obj)
            ]
            elsewhere = len(found) - len(reachable)
            space = Workspace(
                root=root,
                package=package.upper(),
                system=target.name,
                se80=layout_se80,
                subpackages=recurse,
                match=pattern,
                types=wanted,
                owner=owner,
            )
            results = await _fetch_with_progress(adt, reachable, jobs)
            for result in results:
                if result.ok and not result.absent:
                    space.write(result.obj, result.text, result.part)
            space.save()
            return space, results, elsewhere, packages

    space, results, elsewhere, packages = runtime.run(body)

    elapsed = time.perf_counter() - started
    written = [result for result in results if result.ok and not result.absent]
    failed = [result for result in results if not result.ok]
    # An object ADT lists but cannot serve, such as a generated extension view.
    unserved = {
        (result.obj.type_code, result.obj.name)
        for result in results
        if result.absent and (result.part is None or result.part.is_main)
    }
    total = sum(len(result.text) for result in written)
    objects = len({(result.obj.type_code, result.obj.name) for result in written})
    spread = f" across {len(packages)} packages" if len(packages) > 1 else ""
    ui.console.print(
        f"pulled [bold]{space.package}[/] from [bold]{target.name}[/] "
        f"- {objects} objects in {len(written)} files{spread}, "
        f"{total:,} bytes in {elapsed:.1f}s"
    )
    ui.console.print(f"[dim]{root}[/]")
    if dest:
        runtime.add_to_vscode(root)
    if elsewhere or unserved:
        ui.console.print(
            f"[dim]{elsewhere + len(unserved)} object(s) have no editable content "
            "in ADT and were skipped[/]"
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
