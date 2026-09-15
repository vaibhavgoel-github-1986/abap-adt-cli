"""Shared command plumbing: credentials, sessions, workspace discovery.

Commands describe *what* they want to do; this module owns how a session is
opened, how an async body is run, and how failures become exit codes.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

import typer

from adt_cli import config, repository, ui, workspace
from adt_cli.config import System
from adt_cli.errors import EXIT_INTERRUPTED, AbapCliError, WorkspaceError
from adt_cli.session import AdtSession
from adt_cli.workspace import Workspace

T = TypeVar("T")

_trace_enabled = False


def set_trace(enabled: bool) -> None:
    """Accepted either side of the subcommand: 'abap --trace push' and 'abap push --trace'."""
    global _trace_enabled
    _trace_enabled = _trace_enabled or enabled
    if _trace_enabled:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")


def guard(func: Callable[..., T]) -> Callable[..., T]:
    """Turn expected failures into a message plus an exit code.

    Wrapping every command here is what keeps tracebacks for real bugs only.
    """

    @functools.wraps(func)
    def wrapper(*args: object, **kwargs: object) -> T:
        try:
            return func(*args, **kwargs)
        except AbapCliError as exc:
            ui.fail(str(exc), exc.exit_code)
        except KeyboardInterrupt:
            ui.fail("interrupted", EXIT_INTERRUPTED)

    return wrapper


def run(body: Callable[[], Awaitable[T]]) -> T:
    """Run an async command body. Failures surface through :func:`guard`."""
    return asyncio.run(body())


def connect(system_name: str) -> tuple[System, str]:
    """Resolve a system profile and the password to use with it."""
    system = config.resolve(name=system_name)
    if not system.verify_tls:
        ui.warn(f"TLS verification is disabled for {system.name}")
    password = config.password_for(system)
    if not password:
        password = typer.prompt(f"SAP password for {system.describe()}", hide_input=True)
    if not password:
        ui.fail("no password supplied")
    return system, password


def session(system: System, password: str, concurrency: int = 16) -> AdtSession:
    return AdtSession(
        host=system.host,
        user=system.user,
        password=password,
        client=system.client,
        verify_tls=system.verify_tls,
        concurrency=concurrency,
        trace=_trace_enabled,
    )


def resolve_root(dest: Path | None, package: str) -> Path:
    """Running inside a pulled workspace uses that folder, never a nested copy."""
    if dest:
        return dest.expanduser().resolve()
    here = Path.cwd().resolve()
    current = Workspace.load(here)
    if not package or (current.exists and current.package == package.upper()):
        return here
    return (here / package.upper()).resolve()


def load_workspace(root: Path) -> Workspace:
    space = Workspace.load(root)
    if not space.exists:
        raise WorkspaceError(f"no manifest in {root}, run 'abap pull' first")
    return space


async def remote_drift(
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
    for local, result in zip(targets, results, strict=True):
        if not result.ok:
            unreadable.append(local)
        elif workspace.sha256(result.text) != space.files[local].sha256:
            drifted.append(local)
    return drifted, unreadable


def add_to_vscode(root: Path) -> None:
    """Add a pulled workspace folder to the active VS Code workspace."""
    code = shutil.which("code")
    if not code:
        ui.note("VS Code 'code' command not found; folder was not added")
        return
    try:
        subprocess.run(
            [code, "--add", str(root)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        ui.note("VS Code did not respond; folder was not added")
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = exc.stderr.strip() if isinstance(exc, subprocess.CalledProcessError) else str(exc)
        ui.note(f"could not add folder to VS Code: {detail}")
