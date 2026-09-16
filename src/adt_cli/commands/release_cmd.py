"""Update: replace this installation with a published release."""

from __future__ import annotations

from typing import Annotated

import typer

from adt_cli import release, runtime, ui
from adt_cli.commands.options import TraceOpt


@runtime.guard
def update(
    check: Annotated[
        bool, typer.Option("--check", help="Report what would happen, change nothing.")
    ] = False,
    version: Annotated[
        str, typer.Option("--version", help="Install this tag instead of the newest.")
    ] = "",
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Reinstall even when nothing is newer.")
    ] = False,
    trace: TraceOpt = False,
) -> None:
    """Install the newest published release of this CLI."""
    runtime.set_trace(trace)
    install = release.current()
    ui.console.print(f"installed: [bold]{install.describe()}[/]")

    target = version.strip()
    if not target:
        newest = release.latest()
        if newest is None:
            ui.console.print(
                "[yellow]no releases published yet[/] - nothing to update to.\n"
                "[dim]Tag one with 'git tag v0.2.0 && git push --tags'.[/]"
            )
            return
        ui.console.print(f"latest:    [bold]{newest}[/]")
        if not release.is_newer(newest, install.version) and not force:
            ui.console.print("[green]up to date[/]")
            return
        target = newest

    command = release.upgrade_command(install, target)
    ui.console.print(f"[dim]would run: {' '.join(command)}[/]")
    if check:
        ui.console.print(f"\n[dim]{target} would be installed  [CHECK ONLY][/]")
        return

    ui.console.print(f"installing {target}...")
    release.run(command)
    ui.console.print(f"[green]installed[/] {target} - run 'abap version' to confirm")
