"""Pull: download a package into a local workspace.

Objects come through ZSYNC in abapGit's file format, which is what makes every
object type reachable - and what gives a class its separate local definitions,
local implementations, macros and test class files instead of just its main
source.
"""

from __future__ import annotations

import time
from typing import Annotated

import typer
from rich.tree import Tree

from adt_cli import layout, runtime, ui, zsync
from adt_cli.commands.options import DestOpt, PackageArg, SystemOpt, TraceOpt
from adt_cli.errors import AbapCliError, WorkspaceError
from adt_cli.workspace import MODE_ZSYNC, Workspace


@runtime.guard
def pull(
    package: PackageArg = "",
    system: SystemOpt = "",
    dest: DestOpt = None,
    se80: Annotated[
        bool | None, typer.Option("--se80/--flat", help="Lay files out as an SE80 tree.")
    ] = None,    subpackages: Annotated[
        bool | None,
        typer.Option("--subpackages/--no-subpackages", help="Include sub-package objects."),
    ] = None,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite local changes.")] = False,
    trace: TraceOpt = False,
) -> None:
    """Download a package into a local folder.

    Run inside an already-pulled folder to refresh it: the package, system and
    layout are taken from the last pull.
    """
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

    # A refresh keeps whatever shaped the original pull, so it cannot silently widen.
    layout_se80 = previous.se80 if (se80 is None and previous.exists) else bool(se80 is not False)
    with_subpackages = previous.subpackages if subpackages is None else subpackages
    target, password = runtime.connect(system or previous.system)

    started = time.perf_counter()

    async def body() -> tuple[Workspace, list[layout.FileRef], int]:
        async with runtime.zsync(target, password) as sap:
            ui.console.print(f"[dim]connected to {target.name}, exporting {package.upper()}...[/]")
            blob = await sap.export(package, include_subpackages=with_subpackages)

        entries = zsync.unpack(blob)
        if not entries:
            raise AbapCliError(f"{package.upper()} came back empty")

        config = next((data for name, data in entries if name == layout.REPO_CONFIG_FILE), None)
        logic = layout.folder_logic(config)

        space = Workspace(
            root=root,
            package=package.upper(),
            system=target.name,
            se80=layout_se80,
            mode=MODE_ZSYNC,
            subpackages=with_subpackages,
            folder_logic=logic,
        )
        refs = []
        with ui.console.status(f"writing {len(entries)} files..."):
            for canonical, data in entries:
                ref = layout.to_local_path(canonical, space.package, logic)
                space.store(ref, data)
                refs.append(ref)
        space.save()
        return space, refs, len(blob)

    space, refs, size = runtime.run(body)

    elapsed = time.perf_counter() - started
    objects = len({(ref.obj_type, ref.obj_name) for ref in refs if ref.obj_name})
    ui.console.print(
        f"pulled [bold]{space.package}[/] from [bold]{target.name}[/] "
        f"- {objects} objects in {len(space.files)} files, {size:,} bytes in {elapsed:.1f}s"
    )
    ui.console.print(f"[dim]{root}[/]\n")
    _print_tree(space.package, layout.tree_summary(refs))
    if dest:
        runtime.add_to_vscode(root)


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


def _print_tree(package: str, summary: dict[str, int]) -> None:
    tree = Tree(f"[bold]{package}[/]")
    nodes: dict[str, Tree] = {}
    for folder, count in summary.items():
        parent = tree
        trail = ""
        for part in folder.split("/"):
            trail = f"{trail}/{part}" if trail else part
            if trail not in nodes:
                nodes[trail] = parent.add(part)
            parent = nodes[trail]
        parent.label = f"{parent.label} [dim]({count})[/]"
    ui.console.print(tree)
