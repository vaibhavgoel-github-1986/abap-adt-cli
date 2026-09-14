"""Command line interface."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from adt_cli import __version__, config, repository, workspace
from adt_cli.config import ConfigError, System
from adt_cli.session import AdtError, AdtSession
from adt_cli.workspace import Workspace

app = typer.Typer(
    name="adt",
    help="Fast ABAP development from the command line, straight onto the SAP ADT API.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

SystemOpt = Annotated[str, typer.Option("--system", "-s", help="Named system from config.")]
PackageArg = Annotated[str, typer.Argument(help="ABAP package, e.g. ZEXAMPLE_API")]


def _fail(message: str) -> None:
    err_console.print(f"[bold red]error[/] {message}")
    raise typer.Exit(1)


def _connect(system_name: str) -> tuple[System, str]:
    try:
        system = config.resolve(name=system_name)
    except ConfigError as exc:
        _fail(str(exc))
        raise
    password = config.password_for(system)
    if not password:
        password = typer.prompt(f"SAP password for {system.describe()}", hide_input=True)
        if not password:
            _fail("no password supplied")
    return system, password


def _session(system: System, password: str, concurrency: int = 16) -> AdtSession:
    return AdtSession(
        host=system.host,
        user=system.user,
        password=password,
        client=system.client,
        verify_tls=system.verify_tls,
        concurrency=concurrency,
    )


def _resolve_root(dest: Path | None, package: str) -> Path:
    """Running inside a pulled workspace uses that folder, never a nested copy."""
    if dest:
        return dest.resolve()
    here = Path.cwd().resolve()
    current = Workspace.load(here)
    if not package or (current.exists and current.package == package.upper()):
        return here
    return (here / package.upper()).resolve()


# --------------------------------------------------------------------------- commands


@app.command()
def version() -> None:
    """Show the CLI version."""
    console.print(f"abap-adt-cli {__version__}")


@app.command()
def init(
    name: Annotated[str, typer.Option(prompt="System name (e.g. dev-100)")],
    host: Annotated[str, typer.Option(prompt="Host URL (https://host:port)")],
    user: Annotated[str, typer.Option(prompt="SAP user")],
    client: Annotated[str, typer.Option(prompt="SAP client")],
    description: Annotated[str, typer.Option(help="Free text, shown by 'adt systems'.")] = "",
    insecure: Annotated[bool, typer.Option(help="Skip TLS verification.")] = False,
) -> None:
    """Add a system to ~/.adt/config.json."""
    system = System(
        name=name,
        host=host.rstrip("/"),
        user=user,
        client=client,
        verify_tls=not insecure,
        description=description,
    )
    config.save_system(system, make_default=True)
    console.print(f"saved [bold]{name}[/] to {config.CONFIG_HOME}")
    console.print(f"next: [bold]adt login --system {name}[/]")


@app.command()
def systems() -> None:
    """List configured systems."""
    entries = config.list_systems()
    if not entries:
        console.print("no systems configured, run [bold]adt init[/]")
        return
    default = config.default_system()
    table = Table(box=None, pad_edge=False)
    for column in ("", "name", "host", "client", "user", "description"):
        table.add_column(column)
    for name, profile in sorted(entries.items()):
        table.add_row(
            "*" if name == default else " ",
            name,
            str(profile.get("host", "")),
            str(profile.get("client", "")),
            str(profile.get("user", "")),
            str(profile.get("description", "")),
        )
    console.print(table)


@app.command()
def login(system: SystemOpt = "") -> None:
    """Store the SAP password in the OS keychain."""
    try:
        target = config.resolve(name=system)
    except ConfigError as exc:
        _fail(str(exc))
        return
    password = typer.prompt(f"SAP password for {target.describe()}", hide_input=True)
    if not password:
        _fail("no password supplied")
    if config.keychain_store(target.keychain_account, password):
        console.print(f"[green]stored[/] credentials for {target.name}")
    else:
        _fail("no usable keychain backend on this machine")


@app.command()
def logout(system: SystemOpt = "") -> None:
    """Remove stored credentials."""
    try:
        target = config.resolve(name=system)
    except ConfigError as exc:
        _fail(str(exc))
        return
    console.print(
        "[green]removed[/]" if config.keychain_delete(target.keychain_account) else "nothing stored"
    )


@app.command()
def ping(system: SystemOpt = "") -> None:
    """Check that the ADT endpoint is reachable."""
    target, password = _connect(system)

    async def run() -> str:
        async with _session(target, password) as adt:
            reply = await adt.get("/sap/bc/adt/compatibility/graph")
            return f"{len(reply.content):,} bytes"

    try:
        detail = asyncio.run(run())
    except AdtError as exc:
        _fail(str(exc))
        return
    console.print(
        f"[green]ADT alive[/] on {target.name} client {target.client} as {target.user}  [dim]({detail})[/]"
    )


@app.command()
def pull(
    package: PackageArg = "",
    system: SystemOpt = "",
    dest: Optional[Path] = typer.Option(None, "--dest", "-d", help="Target folder."),
    se80: Annotated[
        Optional[bool], typer.Option("--se80/--flat", help="Lay files out as an SE80 tree.")
    ] = None,
    jobs: Annotated[int, typer.Option("--jobs", "-j", help="Parallel requests.")] = 16,
    force: Annotated[bool, typer.Option("--force", "-f", help="Overwrite local changes.")] = False,
) -> None:
    """Download a package into a local folder, in parallel."""
    root = _resolve_root(dest, package)
    previous = Workspace.load(root)

    if not package:
        if not previous.exists:
            _fail(f"no package given and no manifest in {root} - try 'adt pull <PACKAGE>'")
        package = previous.package

    if previous.exists and not force:
        modified, deleted = previous.scan()
        if modified or deleted:
            for local in modified:
                err_console.print(f"  [yellow]M[/]  {local}")
            for local in deleted:
                err_console.print(f"  [red]D[/]  {local}")
            _fail(
                f"{len(modified)} modified, {len(deleted)} deleted - push them, "
                "or re-run with --force to discard and overwrite"
            )

    layout_se80 = previous.se80 if se80 is None else se80
    target, password = _connect(system or previous.system)
    started = time.perf_counter()

    async def run() -> tuple[Workspace, list[repository.Fetched]]:
        async with _session(target, password, jobs) as adt:
            console.print(f"[dim]connected to {target.name}, listing {package}...[/]")
            found = await repository.list_package(adt, package)
            space = Workspace(
                root=root, package=package.upper(), system=target.name, se80=layout_se80
            )
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(f"pulling {len(found)} objects", total=len(found))

                def on_progress(result: repository.Fetched) -> None:
                    progress.advance(task)
                    if not result.ok:
                        progress.console.print(
                            f"  [red]![/]  {result.obj.name}: {result.error}"
                        )

                results = await repository.fetch_sources(
                    adt, found, concurrency=jobs, on_progress=on_progress
                )
            for result in results:
                if result.ok:
                    space.write(result.obj, result.text)
            space.save()
            return space, results

    try:
        space, results = asyncio.run(run())
    except AdtError as exc:
        _fail(str(exc))
        return

    elapsed = time.perf_counter() - started
    written = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    total = sum(len(r.text) for r in written)
    console.print(
        f"pulled [bold]{space.package}[/] from [bold]{target.name}[/] "
        f"- {len(written)} objects, {total:,} bytes in {elapsed:.1f}s"
    )
    console.print(f"[dim]{root}[/]")
    if failed:
        console.print(f"[dim]{len(failed)} object(s) failed to pull[/]")


@app.command()
def status(
    package: PackageArg = "",
    dest: Optional[Path] = typer.Option(None, "--dest", "-d", help="Local folder."),
) -> None:
    """Show locally modified objects. Works offline."""
    root = _resolve_root(dest, package)
    space = Workspace.load(root)
    if not space.exists:
        _fail(f"no manifest in {root}, run 'adt pull' first")

    modified, deleted = space.scan()
    console.print(f"[bold]{space.package}[/] from {space.system}, pulled {space.pulled_at}")
    for local in modified:
        console.print(f"  [yellow]M[/]  {local}")
    for local in deleted:
        console.print(f"  [red]D[/]  {local}")
    if not modified and not deleted:
        console.print("  [green]clean[/] - no local changes")
        return
    console.print(f"\n{len(modified)} modified, {len(deleted)} deleted")


@app.command()
def push(
    package: PackageArg = "",
    system: SystemOpt = "",
    dest: Optional[Path] = typer.Option(None, "--dest", "-d", help="Local folder."),
    transport: Annotated[str, typer.Option(help="Transport request.")] = "",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be sent.")] = False,
) -> None:
    """Upload locally modified objects and activate them."""
    root = _resolve_root(dest, package)
    space = Workspace.load(root)
    if not space.exists:
        _fail(f"no manifest in {root}, run 'adt pull' first")

    modified, deleted = space.scan()
    if deleted:
        console.print(f"[yellow]note[/] {len(deleted)} deleted file(s) are ignored by push")
    if not modified:
        console.print("nothing to push - no local changes")
        return

    blocked = [local for local in modified if not space.writable(local)]
    if blocked:
        for local in blocked:
            err_console.print(f"  [red]![/]  {local} is not a writable source object")
        _fail("refusing to push non-source objects")

    for local in modified:
        console.print(f"  [yellow]M[/]  {local}")
    if dry_run:
        console.print(f"\n[dim]{len(modified)} object(s) would be pushed  [DRY RUN][/]")
        return

    target, password = _connect(system or space.system)

    async def run() -> None:
        async with _session(target, password) as adt:
            for local in modified:
                obj = space.object_for(local)
                text = (root / local).read_text(encoding="utf-8")
                await repository.write_source(adt, obj, text, transport=transport)
                # Re-baseline as we go: a later failure must not make an already
                # pushed object look unpushed.
                space.files[local].sha256 = workspace.sha256(text)
                space.save()
                console.print(f"  [green]pushed[/] {obj.name}")

    try:
        asyncio.run(run())
    except AdtError as exc:
        _fail(str(exc))
        return
    console.print(f"\n{len(modified)} object(s) pushed")
