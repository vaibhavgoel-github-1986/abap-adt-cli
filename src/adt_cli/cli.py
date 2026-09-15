"""Command line interface."""

from __future__ import annotations

import asyncio
import difflib
import shutil
import subprocess
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

from adt_cli import __version__, activation, config, cts, repository, workspace
from adt_cli.config import ConfigError, System
from adt_cli.session import AdtError, AdtSession
from adt_cli.workspace import Workspace

app = typer.Typer(
    name="abap",
    help="Fast ABAP development from the command line, straight onto the SAP ADT API.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

SystemOpt = Annotated[str, typer.Option("--system", "-s", help="Named system from config.")]
PackageArg = Annotated[str, typer.Argument(help="ABAP package, e.g. ZEXAMPLE_API")]
TraceOpt = Annotated[
    bool,
    typer.Option("--trace", help="Log ADT requests and responses to stderr; secrets are redacted."),
]

_trace_enabled = False


def _parse_types(raw: str) -> list[str]:
    return [part.strip().upper() for part in raw.split(",") if part.strip()]


def _wanted_type(code: str, wanted: list[str]) -> bool:
    """Accepts both the full ADT code and its bare form: CLAS/OC and CLAS."""
    code = code.upper()
    return code in wanted or code.split("/", 1)[0] in wanted


def _show_blanks(text: str) -> str:
    """A trailing-space-only change is invisible in a diff, so spell it out."""
    body = text.rstrip(" \t")
    return body + text[len(body) :].replace("\t", "→").replace(" ", "·")


def _hunk_line(header: str) -> str:
    """'@@ -8,7 +8,7 @@' carries a line number nobody should have to decode."""
    parts = header.split()
    if len(parts) > 2 and parts[2].startswith("+"):
        return f"  line {parts[2][1:].split(',')[0]}"
    return header


def _set_trace(enabled: bool) -> None:
    """Accepted either side of the subcommand: 'abap --trace push' and 'abap push --trace'."""
    global _trace_enabled
    _trace_enabled = _trace_enabled or enabled


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
        trace=_trace_enabled,
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


async def _remote_drift(
    adt: AdtSession, space: Workspace, locals_: list[str], jobs: int = 16
) -> tuple[list[str], list[str]]:
    """Locals whose server copy no longer matches the pulled baseline.

    The manifest hash is what the server handed us at pull time, so a mismatch
    means somebody else (SE80, Eclipse, another push) changed the object since.
    Returns (drifted, unreadable).
    """
    targets = [local for local in locals_ if space.writable(local)]
    if not targets:
        return [], []
    results = await repository.fetch_sources(
        adt, [space.object_for(local) for local in targets], concurrency=jobs
    )
    drifted, unreadable = [], []
    for local, result in zip(targets, results):
        if not result.ok:
            unreadable.append(local)
        elif workspace.sha256(result.text) != space.files[local].sha256:
            drifted.append(local)
    return drifted, unreadable


def _add_to_vscode(root: Path) -> None:
    """Add a pulled workspace folder to the active VS Code workspace."""
    code = shutil.which("code")
    if not code:
        console.print("[yellow]note[/] VS Code 'code' command not found; folder was not added")
        return
    try:
        subprocess.run(
            [code, "--add", str(root)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        console.print(f"[yellow]note[/] could not add folder to VS Code: {detail}")


# --------------------------------------------------------------------------- commands


@app.callback()
def main(trace: TraceOpt = False) -> None:
    """Configure command-wide options."""
    _set_trace(trace)


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
    description: Annotated[str, typer.Option(help="Free text, shown by 'abap systems'.")] = "",
    insecure: Annotated[bool, typer.Option(help="Skip TLS verification.")] = False,
) -> None:
    """Add a system to ~/.abap-adt/config.json."""
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
    console.print(f"next: [bold]abap login --system {name}[/]")


@app.command()
def systems() -> None:
    """List configured systems."""
    entries = config.list_systems()
    if not entries:
        console.print("no systems configured, run [bold]abap init[/]")
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
def ping(system: SystemOpt = "", trace: TraceOpt = False) -> None:
    """Check that the ADT endpoint is reachable."""
    _set_trace(trace)
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
    match: Annotated[
        str,
        typer.Option("--match", "-m", help="Object name pattern, e.g. 'ZCL_SUBS*'."),
    ] = "",
    types: Annotated[
        str,
        typer.Option("--type", "-t", help="Comma-separated ADT types, e.g. 'CLAS,DDLS'."),
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
    _set_trace(trace)
    root = _resolve_root(dest, package)
    previous = Workspace.load(root)

    if not package:
        if not previous.exists:
            _fail(f"no package given and no manifest in {root} - try 'abap pull <PACKAGE>'")
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
    # A refresh keeps whatever narrowed the original pull, so it cannot silently widen.
    pattern = match or previous.match
    wanted = _parse_types(types) or previous.types
    target, password = _connect(system or previous.system)
    started = time.perf_counter()

    # $TMP is one package shared by every developer, so narrow it to your own
    # objects unless asked otherwise. '*' is the explicit way back to everybody.
    owner = user or previous.owner
    if not owner and package.startswith("$"):
        owner = target.user
    owner_filter = "" if owner == "*" else owner.upper()

    async def run() -> tuple[Workspace, list[repository.Fetched]]:
        async with _session(target, password, jobs) as adt:
            scope = f" owned by {owner_filter}" if owner_filter else ""
            console.print(f"[dim]connected to {target.name}, listing {package}{scope}...[/]")
            found = await repository.list_package(
                adt, package, pattern=pattern or "*", owner=owner_filter
            )
            if wanted:
                found = [obj for obj in found if _wanted_type(obj.type_code, wanted)]
            if not found:
                _fail(f"nothing in {package.upper()} matched")
            space = Workspace(
                root=root,
                package=package.upper(),
                system=target.name,
                se80=layout_se80,
                match=pattern,
                types=wanted,
                owner=owner,
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
    if dest:
        _add_to_vscode(root)
    if failed:
        console.print(f"[dim]{len(failed)} object(s) failed to pull[/]")


@app.command()
def status(
    package: PackageArg = "",
    system: SystemOpt = "",
    dest: Optional[Path] = typer.Option(None, "--dest", "-d", help="Local folder."),
    remote: Annotated[
        bool, typer.Option("--remote", "-r", help="Also check what changed on the server.")
    ] = False,
    trace: TraceOpt = False,
) -> None:
    """Show locally modified objects. Offline unless --remote is given."""
    _set_trace(trace)
    root = _resolve_root(dest, package)
    space = Workspace.load(root)
    if not space.exists:
        _fail(f"no manifest in {root}, run 'abap pull' first")

    modified, deleted = space.scan()
    console.print(f"[bold]{space.package}[/] from {space.system}, pulled {space.pulled_at}")

    drifted: list[str] = []
    if remote:
        target, password = _connect(system or space.system)

        async def check() -> tuple[list[str], list[str]]:
            async with _session(target, password) as adt:
                return await _remote_drift(adt, space, sorted(space.files))

        try:
            drifted, unreadable = asyncio.run(check())
        except AdtError as exc:
            _fail(str(exc))
            return
        for local in unreadable:
            console.print(f"  [yellow]?[/]  {local} could not be read")

    for local in modified:
        marker = "[red]C[/]" if local in drifted else "[yellow]M[/]"
        suffix = "  [dim](also changed on server)[/]" if local in drifted else ""
        console.print(f"  {marker}  {local}{suffix}")
    for local in deleted:
        console.print(f"  [red]D[/]  {local}")
    for local in drifted:
        if local not in modified:
            console.print(f"  [blue]R[/]  {local}  [dim](changed on server)[/]")

    if not modified and not deleted and not drifted:
        console.print("  [green]clean[/] - no local changes")
        return

    conflicts = [local for local in modified if local in drifted]
    summary = f"\n{len(modified)} modified, {len(deleted)} deleted"
    if remote:
        summary += f", {len(drifted)} changed on server"
    console.print(summary)
    if conflicts:
        console.print(
            f"[red]{len(conflicts)} conflict(s)[/] - push will refuse these until you re-pull"
        )


@app.command()
def diff(
    package: PackageArg = "",
    system: SystemOpt = "",
    dest: Optional[Path] = typer.Option(None, "--dest", "-d", help="Local folder."),
    jobs: Annotated[int, typer.Option("--jobs", "-j", help="Parallel requests.")] = 16,
    trace: TraceOpt = False,
) -> None:
    """Show line differences between the server and your local files."""
    _set_trace(trace)
    root = _resolve_root(dest, package)
    space = Workspace.load(root)
    if not space.exists:
        _fail(f"no manifest in {root}, run 'abap pull' first")

    # The manifest keeps hashes, not text, so the comparison has to come from the
    # server. Same fetch status --remote does, kept instead of discarded.
    tracked = [local for local in sorted(space.files) if space.writable(local)]
    present = [local for local in tracked if (root / local).is_file()]
    for local in tracked:
        if local not in present:
            console.print(f"  [red]D[/]  {local} deleted locally, skipped")
    if not present:
        console.print("nothing to compare")
        return

    target, password = _connect(system or space.system)

    async def run() -> list[repository.Fetched]:
        async with _session(target, password, jobs) as adt:
            return await repository.fetch_sources(
                adt, [space.object_for(local) for local in present], concurrency=jobs
            )

    try:
        results = asyncio.run(run())
    except AdtError as exc:
        _fail(str(exc))
        return

    changed = 0
    overwrites = 0
    for local, result in zip(present, results):
        if not result.ok:
            err_console.print(f"  [yellow]?[/]  {local} could not be read: {result.error}")
            continue
        mine = (root / local).read_text(encoding="utf-8")
        if mine == result.text:
            continue
        changed += 1
        if changed == 1:
            console.print(
                f"[dim]'-' is {target.name} as it stands now, "
                "'+' is your local copy - what push would make it[/]"
            )
            console.print("[dim]trailing spaces shown as ·, tabs as →[/]\n")
        # The baseline hash is the only thing that can say who moved.
        base = space.files[local].sha256
        yours = workspace.sha256(mine) != base
        theirs = workspace.sha256(result.text) != base
        if yours and theirs:
            marker, note = "[red]C[/]", f"changed by you AND on {target.name}"
        elif theirs:
            marker, note = "[blue]R[/]", f"changed on {target.name}, not by you"
        else:
            marker, note = "[yellow]M[/]", "changed by you"
        if theirs:
            overwrites += 1
        if mine.split() == result.text.split():
            note += ", whitespace only"
        console.print(f"  {marker}  {local}  [dim]({note})[/]")
        # The first two entries are the ---/+++ headers, replaced by the note above.
        for line in list(
            difflib.unified_diff(result.text.splitlines(), mine.splitlines(), lineterm="")
        )[2:]:
            if line.startswith("@@"):
                console.print(_hunk_line(line), style="cyan", markup=False, highlight=False)
                continue
            if line.startswith("+"):
                style = "green"
            elif line.startswith("-"):
                style = "red"
            else:
                # Context lines are shown as-is; marking their blanks is just noise.
                console.print(line, markup=False, highlight=False)
                continue
            console.print(
                line[:1] + _show_blanks(line[1:]),
                style=style,
                markup=False,
                highlight=False,
            )
        console.print()

    if not changed:
        console.print(f"[green]no differences[/] - local files match {target.name}")
        return
    console.print(f"{changed} object(s) differ from {target.name}")
    if overwrites:
        console.print(
            f"[red]{overwrites} of them changed on {target.name} since your pull[/] - "
            "pushing would overwrite that work, re-pull instead"
        )


def _report_activation(outcome: activation.Outcome, count: int, system: str) -> bool:
    for warning in outcome.warnings:
        console.print(f"  [yellow]warning[/] {warning}")
    for error in outcome.errors:
        err_console.print(f"  [red]![/]  {error}")
    if outcome.ok:
        console.print(f"[green]activated[/] {count} object(s) on {system}")
        return True
    if not outcome.executed:
        err_console.print("[red]error[/] activation did not run")
    else:
        err_console.print(
            f"[red]error[/] {len(outcome.errors)} activation error(s) - "
            "the objects stay inactive until they are fixed"
        )
    return False


@app.command()
def push(
    package: PackageArg = "",
    system: Annotated[
        str,
        typer.Option(
            "--system",
            "-s",
            help="Must match the system the workspace was pulled from.",
        ),
    ] = "",
    dest: Optional[Path] = typer.Option(None, "--dest", "-d", help="Local folder."),
    transport: Annotated[str, typer.Option(help="Transport request.")] = "",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be sent.")] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Push even if the object changed on the server.")
    ] = False,
    activate_after: Annotated[
        bool, typer.Option("--activate", help="Activate the pushed objects afterwards.")
    ] = False,
    trace: TraceOpt = False,
) -> None:
    """Upload locally modified objects. They stay inactive until activated."""
    _set_trace(trace)
    root = _resolve_root(dest, package)
    space = Workspace.load(root)
    if not space.exists:
        _fail(f"no manifest in {root}, run 'abap pull' first")

    # The baseline hashes and object URIs in the manifest only describe the system
    # the files came from, so pushing them anywhere else is never a safe diff.
    if system and system != space.system:
        _fail(
            f"workspace was pulled from {space.system}, refusing to push to {system} - "
            f"pull the package from {system} into its own folder if that is the real target"
        )

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

    # Local packages ($TMP and friends) are never transported. For everything else
    # the transport is resolved against whatever request already holds the objects.
    local_package = space.package.startswith("$")

    for local in modified:
        console.print(f"  [yellow]M[/]  {local}")
    if dry_run:
        console.print(f"\n[dim]{len(modified)} object(s) would be pushed  [DRY RUN][/]")
        return

    target, password = _connect(space.system)

    def settle_transport(found: list[cts.Holder | None]) -> str:
        """Reconcile the requested transport with the request SAP already locked in."""
        held = {
            holder.request: holder for holder in found if holder is not None
        }
        if transport:
            clashes = [
                (local, holder)
                for local, holder in zip(modified, found)
                if holder is not None and not holder.accepts(transport)
            ]
            for local, holder in clashes:
                err_console.print(
                    f"  [red]![/]  {local} is locked in {holder.describe()}"
                )
            if clashes:
                names = ", ".join(sorted({holder.request for _, holder in clashes}))
                _fail(
                    f"{len(clashes)} object(s) already locked in {names}, not {transport} - "
                    f"SAP locks an object in one request only, so re-run with --transport {names}"
                )
            return transport

        if local_package:
            return ""
        if len(held) > 1:
            names = ", ".join(sorted(held))
            _fail(
                f"package {space.package} is transportable and these objects span "
                f"several requests ({names}) - push them separately with --transport"
            )
        if not held:
            _fail(
                f"package {space.package} is transportable - pass --transport <TR> "
                "(only local $ packages can push without one)"
            )

        # Exactly one request already owns these objects, and SAP would reject any
        # other, so there is nothing to choose: adopt it and say so.
        holder = next(iter(held.values()))
        mine = holder.task_of(target.user)
        console.print(f"[dim]using {holder.describe()} - it already holds these objects[/]")
        if mine:
            console.print(f"[dim]  recording under your task {mine}[/]")
        else:
            console.print(f"[dim]  SAP will open a task for {target.user} in it[/]")
        for local, found_holder in zip(modified, found):
            if found_holder is None:
                console.print(
                    f"  [yellow]+[/]  {local} is in no request yet, "
                    f"it will be added to {holder.request}"
                )
        return holder.request

    async def run() -> activation.Outcome | None:
        async with _session(target, password) as adt:
            if not force:
                drifted, unreadable = await _remote_drift(adt, space, modified)
                for local in unreadable:
                    err_console.print(f"  [yellow]?[/]  {local} could not be read back")
                if drifted:
                    for local in drifted:
                        err_console.print(
                            f"  [red]C[/]  {local} also changed on {target.name} since your pull"
                        )
                    _fail(
                        f"{len(drifted)} object(s) changed on the server - pushing would "
                        "overwrite that work. Re-pull to inspect, or use --force to overwrite."
                    )
            found = (
                []
                if local_package and not transport
                else await cts.holders(adt, [space.object_for(local) for local in modified])
            )
            corrnr = settle_transport(found) if not local_package or transport else ""
            pushed = []
            for local in modified:
                obj = space.object_for(local)
                text = (root / local).read_text(encoding="utf-8")
                await repository.write_source(adt, obj, text, transport=corrnr)
                # Re-baseline as we go: a later failure must not make an already
                # pushed object look unpushed.
                space.files[local].sha256 = workspace.sha256(text)
                space.save()
                pushed.append(obj)
                console.print(f"  [green]pushed[/] {obj.name}")
            if not activate_after:
                return None
            # Every object is already unlocked, so one run covers the whole batch.
            console.print(f"\nactivating {len(pushed)} object(s)...")
            return await activation.activate(adt, pushed)

    try:
        outcome = asyncio.run(run())
    except AdtError as exc:
        _fail(str(exc))
        return
    console.print(f"\n{len(modified)} object(s) pushed")
    if outcome is not None and not _report_activation(outcome, len(modified), target.name):
        raise typer.Exit(1)
