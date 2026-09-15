"""Option and argument types shared by more than one command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

SystemOpt = Annotated[str, typer.Option("--system", "-s", help="Named system from config.")]
PackageArg = Annotated[str, typer.Argument(help="ABAP package, e.g. ZEXAMPLE_API")]
DestOpt = Annotated[Path | None, typer.Option("--dest", "-d", help="Local folder.")]
JobsOpt = Annotated[int, typer.Option("--jobs", "-j", help="Parallel requests.", min=1, max=64)]
TraceOpt = Annotated[
    bool,
    typer.Option("--trace", help="Log ADT requests and responses to stderr; secrets are redacted."),
]
