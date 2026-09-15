"""Command line interface.

This module only wires commands onto the Typer app; the work lives in
``adt_cli.commands`` and the shared plumbing in ``adt_cli.runtime``.
"""

from __future__ import annotations

import typer

from adt_cli import __version__, runtime, ui
from adt_cli.commands import profiles, review
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
def version() -> None:
    """Show the CLI version."""
    ui.console.print(f"abap-adt-cli {__version__}")


app.command()(profiles.init)
app.command()(profiles.systems)
app.command()(profiles.login)
app.command()(profiles.logout)
app.command()(profiles.ping)
app.command()(pull_cmd.pull)
app.command()(review.status)
app.command()(review.diff)
app.command()(push_cmd.push)
