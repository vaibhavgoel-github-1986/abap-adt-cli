"""Command line interface.

This module only wires commands onto the Typer app; the work lives in
``adt_cli.commands`` and the shared plumbing in ``adt_cli.runtime``.
"""

from __future__ import annotations

from typing import Annotated

import typer

from adt_cli import release, runtime, ui
from adt_cli.commands import (
    api_cmd,
    objects_cmd,
    profiles,
    release_cmd,
    review,
    transports_cmd,
)
from adt_cli.commands import pull as pull_cmd
from adt_cli.commands import push as push_cmd
from adt_cli.commands.options import TraceOpt

app = typer.Typer(
    name="abap",
    help="Fast ABAP development from the command line, straight onto the SAP ADT API.",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main(trace: TraceOpt = False) -> None:
    """Configure command-wide options."""
    runtime.set_trace(trace)


@app.command()
def version(
    check: Annotated[
        bool, typer.Option("--check", help="Also ask GitHub whether a newer release exists.")
    ] = False,
) -> None:
    """Show the CLI version."""
    install = release.current()
    ui.console.print(install.describe())
    if not check:
        return
    newest = release.latest()
    if newest is None:
        ui.console.print("[dim]no releases published yet[/]")
    elif release.is_newer(newest, install.version):
        ui.console.print(f"[yellow]{newest} is available[/] - run 'abap update'")
    else:
        ui.console.print("[green]up to date[/]")


app.command()(profiles.init)
app.command()(profiles.systems)
app.command()(profiles.login)
app.command()(profiles.logout)
app.command()(profiles.ping)
app.command()(pull_cmd.pull)
app.command()(review.status)
app.command()(review.diff)
app.command()(push_cmd.push)
app.command()(objects_cmd.types)
app.command()(objects_cmd.delete)
app.command()(objects_cmd.transport)
app.command("transports")(transports_cmd.transports_)
app.command()(api_cmd.api)
app.command()(release_cmd.update)
