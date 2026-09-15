"""Connection profiles: init, systems, login, logout, ping."""

from __future__ import annotations

from typing import Annotated

import typer

from adt_cli import config, runtime, ui
from adt_cli.commands.options import SystemOpt, TraceOpt
from adt_cli.config import System


@runtime.guard
def init(
    name: Annotated[str, typer.Option(prompt="System name (e.g. dev-100)")],
    host: Annotated[str, typer.Option(prompt="Host URL (https://host:port)")],
    user: Annotated[str, typer.Option(prompt="SAP user")],
    client: Annotated[str, typer.Option(prompt="SAP client")],
    description: Annotated[str, typer.Option(help="Free text, shown by 'abap systems'.")] = "",
    insecure: Annotated[bool, typer.Option(help="Skip TLS verification.")] = False,
) -> None:
    """Add a system to ~/.abap-adt/config.json."""
    if insecure:
        ui.warn("TLS verification will be skipped for this system")
    config.save_system(
        System(
            name=name,
            host=host,
            user=user,
            client=client,
            verify_tls=not insecure,
            description=description,
        ),
        make_default=True,
    )
    ui.console.print(f"saved [bold]{name}[/] to {config.CONFIG_HOME}")
    ui.console.print(f"next: [bold]abap login --system {name}[/]")


@runtime.guard
def systems() -> None:
    """List configured systems."""
    entries = config.list_systems()
    if not entries:
        ui.console.print("no systems configured, run [bold]abap init[/]")
        return
    ui.console.print(ui.systems_table(entries, config.default_system()))


@runtime.guard
def login(system: SystemOpt = "") -> None:
    """Store the SAP password in the OS keychain."""
    target = config.resolve(name=system)
    password = typer.prompt(f"SAP password for {target.describe()}", hide_input=True)
    if not password:
        ui.fail("no password supplied")
    if not config.keychain_store(target.keychain_account, password):
        ui.fail(
            "no usable keychain backend on this machine - "
            "set $ABAP_PASSWORD instead, or install a keyring backend"
        )
    ui.console.print(f"[green]stored[/] credentials for {target.name}")


@runtime.guard
def logout(system: SystemOpt = "") -> None:
    """Remove stored credentials."""
    target = config.resolve(name=system)
    removed = config.keychain_delete(target.keychain_account)
    ui.console.print("[green]removed[/]" if removed else "nothing stored")


@runtime.guard
def ping(system: SystemOpt = "", trace: TraceOpt = False) -> None:
    """Check that the ADT endpoint is reachable."""
    runtime.set_trace(trace)
    target, password = runtime.connect(system)

    async def body() -> str:
        async with runtime.session(target, password) as adt:
            reply = await adt.get("/sap/bc/adt/compatibility/graph")
            return f"{len(reply.content):,} bytes"

    detail = runtime.run(body)
    ui.console.print(
        f"[green]ADT alive[/] on {target.name} client {target.client} "
        f"as {target.user}  [dim]({detail})[/]"
    )
