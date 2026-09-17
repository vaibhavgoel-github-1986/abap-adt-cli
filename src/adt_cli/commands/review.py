"""Review: status and diff.

Both answer a different question. ``status`` says *which* objects moved and
works offline; ``diff`` says *what* moved and needs the server copy.
"""

from __future__ import annotations

from typing import Annotated

import typer

from adt_cli import runtime, ui, workspace
from adt_cli.commands.options import DestOpt, PackageArg, SystemOpt, TraceOpt
from adt_cli.workspace import Workspace


@runtime.guard
def status(
    package: PackageArg = "",
    system: SystemOpt = "",
    dest: DestOpt = None,
    remote: Annotated[
        bool, typer.Option("--remote", "-r", help="Also check what changed on the server.")
    ] = False,
    trace: TraceOpt = False,
) -> None:
    """Show locally modified objects. Offline unless --remote is given."""
    runtime.set_trace(trace)
    root = runtime.resolve_root(dest, package)
    space = runtime.load_workspace(root)

    modified, deleted = space.scan()
    fresh = space.untracked()
    ui.console.print(f"[bold]{space.package}[/] from {space.system}, pulled {space.pulled_at}")

    drifted: list[str] = []
    if remote:
        target, password = runtime.connect(system or space.system)

        async def body() -> tuple[list[str], list[str]]:
            async with runtime.zsync(target, password) as sap:
                current = await runtime.remote_archive(sap, space)
            return runtime.archive_drift(space, current, sorted(space.files))

        drifted, vanished = runtime.run(body)
        for local in vanished:
            ui.console.print(f"  [yellow]?[/]  {local} no longer exists on the server")

    for local in modified:
        marker = ui.MARKER_BOTH if local in drifted else ui.MARKER_MINE
        suffix = "  [dim](also changed on server)[/]" if local in drifted else ""
        ui.console.print(f"  {marker}  {local}{suffix}")
    for local in fresh:
        ui.console.print(f"  {ui.MARKER_NEW}  {local}  [dim](new, not in SAP yet)[/]")
    for local in deleted:
        ui.console.print(f"  {ui.MARKER_DELETED}  {local}")
    for local in drifted:
        if local not in modified:
            ui.console.print(f"  {ui.MARKER_THEIRS}  {local}  [dim](changed on server)[/]")

    if not modified and not deleted and not drifted and not fresh:
        ui.console.print("  [green]clean[/] - no local changes")
        return

    summary = f"\n{len(modified)} modified, {len(fresh)} new, {len(deleted)} deleted"
    if remote:
        summary += f", {len(drifted)} changed on server"
    ui.console.print(summary)

    conflicts = [local for local in modified if local in drifted]
    if conflicts:
        ui.console.print(
            f"[red]{len(conflicts)} conflict(s)[/] - push will refuse these until you re-pull"
        )


@runtime.guard
def diff(
    package: PackageArg = "",
    system: SystemOpt = "",
    dest: DestOpt = None,
    trace: TraceOpt = False,
) -> None:
    """Show line differences between the server and your local files."""
    runtime.set_trace(trace)
    root = runtime.resolve_root(dest, package)
    space = runtime.load_workspace(root)

    # The manifest keeps hashes, not text, so the comparison has to come from the
    # server. One export covers every file, the same call status --remote makes.
    present = []
    for local in sorted(space.files):
        if space.resolve(local).is_file():
            present.append(local)
        else:
            ui.console.print(f"  {ui.MARKER_DELETED}  {local} deleted locally, skipped")
    if not present:
        ui.console.print("nothing to compare")
        return

    target, password = runtime.connect(system or space.system)

    async def body() -> dict[str, bytes]:
        async with runtime.zsync(target, password) as sap:
            return await runtime.remote_archive(sap, space)

    remote = runtime.run(body)

    changed = 0
    overwrites = 0
    for local in present:
        theirs_raw = remote.get(space.canonical_for(local))
        if theirs_raw is None:
            ui.err_console.print(f"  [yellow]?[/]  {local} is not on {target.name}")
            continue
        mine_raw = space.read_bytes(local)
        if mine_raw == theirs_raw:
            continue
        mine, theirs = _as_text(mine_raw), _as_text(theirs_raw)
        if mine is None or theirs is None:
            ui.console.print(f"  {ui.MARKER_MINE}  {local}  [dim](binary, differs)[/]")
            changed += 1
            continue
        changed += 1
        if changed == 1:
            ui.diff_legend(target.name)
        marker, note = _attribution(space, local, mine_raw, theirs_raw, target.name)
        if marker != ui.MARKER_MINE:
            overwrites += 1
        if mine.split() == theirs.split():
            note += ", whitespace only"
        ui.diff_header(marker, local, note)
        ui.diff_body(theirs, mine)

    if not changed:
        ui.console.print(f"[green]no differences[/] - local files match {target.name}")
        return
    ui.console.print(f"{changed} file(s) differ from {target.name}")
    if overwrites:
        ui.console.print(
            f"[red]{overwrites} of them changed on {target.name} since your pull[/] - "
            "pushing would overwrite that work, re-pull instead"
        )


def _as_text(data: bytes) -> str | None:
    """None for the binary members abapGit exports, such as MIME objects."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _attribution(
    space: Workspace, local: str, mine: bytes, theirs: bytes, system: str
) -> tuple[str, str]:
    """A diff on its own cannot say which side moved; the baseline hash can."""
    base = space.files[local].sha256
    yours = workspace.sha256(mine) != base
    theirs_moved = workspace.sha256(theirs) != base
    if yours and theirs_moved:
        return ui.MARKER_BOTH, f"changed by you AND on {system}"
    if theirs_moved:
        return ui.MARKER_THEIRS, f"changed on {system}, not by you"
    return ui.MARKER_MINE, "changed by you"