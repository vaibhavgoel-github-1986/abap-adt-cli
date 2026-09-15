"""Review: status and diff.

Both answer a different question. ``status`` says *which* objects moved and
works offline; ``diff`` says *what* moved and needs the server copy.
"""

from __future__ import annotations

from typing import Annotated

import typer

from adt_cli import repository, runtime, ui, workspace
from adt_cli.commands.options import DestOpt, JobsOpt, PackageArg, SystemOpt, TraceOpt
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
            async with runtime.session(target, password) as adt:
                return await runtime.remote_drift(adt, space, sorted(space.files))

        drifted, unreadable = runtime.run(body)
        for local in unreadable:
            ui.console.print(f"  [yellow]?[/]  {local} could not be read")

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
    jobs: JobsOpt = 16,
    trace: TraceOpt = False,
) -> None:
    """Show line differences between the server and your local files."""
    runtime.set_trace(trace)
    root = runtime.resolve_root(dest, package)
    space = runtime.load_workspace(root)

    # The manifest keeps hashes, not text, so the comparison has to come from the
    # server. Same fetch status --remote does, kept instead of discarded.
    present = []
    for local in sorted(space.files):
        if not space.writable(local):
            continue
        if space.resolve(local).is_file():
            present.append(local)
        else:
            ui.console.print(f"  {ui.MARKER_DELETED}  {local} deleted locally, skipped")
    if not present:
        ui.console.print("nothing to compare")
        return

    target, password = runtime.connect(system or space.system)

    async def body() -> list[repository.Fetched]:
        async with runtime.session(target, password, jobs) as adt:
            return await repository.fetch_sources(
                adt, [space.object_for(local) for local in present], concurrency=jobs
            )

    results = runtime.run(body)

    changed = 0
    overwrites = 0
    for local, result in zip(present, results, strict=True):
        if not result.ok:
            ui.err_console.print(f"  [yellow]?[/]  {local} could not be read: {result.error}")
            continue
        mine = space.read(local)
        if mine == result.text:
            continue
        changed += 1
        if changed == 1:
            ui.diff_legend(target.name)
        marker, note = _attribution(space, local, mine, result.text, target.name)
        if marker != ui.MARKER_MINE:
            overwrites += 1
        if mine.split() == result.text.split():
            note += ", whitespace only"
        ui.diff_header(marker, local, note)
        ui.diff_body(result.text, mine)

    if not changed:
        ui.console.print(f"[green]no differences[/] - local files match {target.name}")
        return
    ui.console.print(f"{changed} object(s) differ from {target.name}")
    if overwrites:
        ui.console.print(
            f"[red]{overwrites} of them changed on {target.name} since your pull[/] - "
            "pushing would overwrite that work, re-pull instead"
        )


def _attribution(
    space: Workspace, local: str, mine: str, theirs_text: str, system: str
) -> tuple[str, str]:
    """A diff on its own cannot say which side moved; the baseline hash can."""
    base = space.files[local].sha256
    yours = workspace.sha256(mine) != base
    theirs = workspace.sha256(theirs_text) != base
    if yours and theirs:
        return ui.MARKER_BOTH, f"changed by you AND on {system}"
    if theirs:
        return ui.MARKER_THEIRS, f"changed on {system}, not by you"
    return ui.MARKER_MINE, "changed by you"
