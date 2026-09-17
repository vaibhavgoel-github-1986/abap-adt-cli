"""Terminal output.

Every command prints through here, so wording, colour and the difference
between stdout and stderr are decided in one place rather than per command.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from typing import NoReturn

import typer
from rich.console import Console
from rich.table import Table

from adt_cli.errors import EXIT_ERROR

console = Console()
err_console = Console(stderr=True)

MARKER_MINE = "[yellow]M[/]"
MARKER_THEIRS = "[blue]R[/]"
MARKER_BOTH = "[red]C[/]"
MARKER_DELETED = "[red]D[/]"
MARKER_NEW = "[green]A[/]"


def note(message: str) -> None:
    console.print(f"[yellow]note[/] {message}")


def raw(text: str) -> None:
    """A payload, byte for byte.

    Rich wraps at the terminal width and reads '[...]' as markup, either of
    which would corrupt XML or JSON on its way into a file or a pipe.
    """
    print(text)


def warn(message: str) -> None:
    err_console.print(f"[yellow]warning[/] {message}")


def problem(message: str) -> None:
    err_console.print(f"  [red]![/]  {message}")


def fail(message: str, exit_code: int = EXIT_ERROR) -> NoReturn:
    """Report a user-facing failure and stop the command."""
    for index, line in enumerate(str(message).splitlines() or [""]):
        err_console.print(f"[bold red]error[/] {line}" if index == 0 else line)
    raise typer.Exit(exit_code)


def systems_table(entries: dict[str, dict], default: str) -> Table:
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
    return table


# --------------------------------------------------------------------------- diff


def show_blanks(text: str) -> str:
    """A trailing-space-only change is invisible in a diff, so spell it out."""
    body = text.rstrip(" \t")
    return body + text[len(body) :].replace("\t", "→").replace(" ", "·")


def hunk_line(header: str) -> str:
    """'@@ -8,7 +8,7 @@' carries a line number nobody should have to decode."""
    parts = header.split()
    if len(parts) > 2 and parts[2].startswith("+"):
        return f"  line {parts[2][1:].split(',')[0]}"
    return header


def diff_legend(system: str) -> None:
    console.print(
        f"[dim]'-' is {system} as it stands now, "
        "'+' is your local copy - what push would make it[/]"
    )
    console.print("[dim]trailing spaces shown as ·, tabs as →[/]\n")


def diff_header(marker: str, local: str, note_text: str) -> None:
    console.print(f"  {marker}  {local}  [dim]({note_text})[/]")


def diff_body(server_text: str, local_text: str) -> None:
    """Print a unified diff without the ---/+++ headers, which the caller replaces."""
    lines: Iterable[str] = list(
        difflib.unified_diff(server_text.splitlines(), local_text.splitlines(), lineterm="")
    )[2:]
    for line in lines:
        if line.startswith("@@"):
            console.print(hunk_line(line), style="cyan", markup=False, highlight=False)
        elif line.startswith(("+", "-")):
            style = "green" if line.startswith("+") else "red"
            console.print(
                line[:1] + show_blanks(line[1:]), style=style, markup=False, highlight=False
            )
        else:
            # Context lines are shown as-is; marking their blanks is just noise.
            console.print(line, markup=False, highlight=False)
    console.print()
