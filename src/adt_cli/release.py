"""Which version is installed, and whether a newer one has been published.

The version lives in ``__init__.py`` and nowhere else; ``pyproject.toml`` reads
it from there. Two copies of a number nobody checks is how a binary ends up
reporting three different versions of itself.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

import httpx

from adt_cli import __version__
from adt_cli.errors import AbapCliError

DISTRIBUTION = "abap-adt-cli"
REPOSITORY = "vaibhavgoel-github-1986/abap-adt-cli"
API = f"https://api.github.com/repos/{REPOSITORY}"
SOURCE = f"git+https://github.com/{REPOSITORY}"

# A tag is interpolated into an install URL, so it is matched, never trusted.
TAG = re.compile(r"^v?\d+(\.\d+)*([-.a-zA-Z0-9]*)$")


@dataclass(frozen=True)
class Install:
    version: str
    method: str  # pipx | pip | source
    path: Path

    def describe(self) -> str:
        return f"{DISTRIBUTION} {self.version} ({self.method}, {self.path})"


def current() -> Install:
    """What is actually running, not what the source tree claims."""
    try:
        version = metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        version = __version__
    prefix = Path(sys.prefix)
    if "pipx" in prefix.parts:
        method = "pipx"
    elif (prefix / "pyvenv.cfg").is_file() or sys.prefix != sys.base_prefix:
        method = "pip"
    else:
        method = "source"
    return Install(version=version, method=method, path=prefix)


def parts(version: str) -> tuple:
    """Comparable form of a version string, digits first, suffix last.

    A suffix sorts *before* the bare release, so 1.2.0rc1 < 1.2.0.
    """
    body = version.strip().lstrip("vV")
    numbers, suffix = [], ""
    for chunk in re.split(r"[.]", body):
        digits = re.match(r"^(\d+)(.*)$", chunk)
        if not digits:
            suffix = chunk
            break
        numbers.append(int(digits.group(1)))
        if digits.group(2):
            suffix = digits.group(2)
            break
    return (tuple(numbers), 0 if suffix else 1, suffix)


def is_newer(candidate: str, than: str) -> bool:
    return parts(candidate) > parts(than)


def latest(timeout: float = 10.0) -> str | None:
    """The newest published tag, or None when nothing is published yet."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            reply = client.get(
                f"{API}/releases/latest", headers={"Accept": "application/vnd.github+json"}
            )
            if reply.status_code == 200:
                return str(reply.json().get("tag_name") or "") or None
            # A repository can have tags without a published release.
            reply = client.get(f"{API}/tags", headers={"Accept": "application/vnd.github+json"})
            if reply.status_code == 200:
                names = [str(tag.get("name", "")) for tag in reply.json()]
                published = sorted((n for n in names if n), key=parts, reverse=True)
                return published[0] if published else None
    except httpx.HTTPError as exc:
        raise AbapCliError(f"could not reach GitHub: {exc}") from exc
    return None


def upgrade_command(install: Install, tag: str | None) -> list[str]:
    """The command that would replace this installation."""
    if tag and not TAG.match(tag):
        raise AbapCliError(f"refusing to install '{tag}': not a version tag")
    target = f"{SOURCE}@{tag}" if tag else SOURCE
    if install.method == "pipx":
        # pipx upgrade cannot change the ref, so a pinned tag has to reinstall.
        if tag:
            return ["pipx", "install", "--force", target]
        return ["pipx", "upgrade", DISTRIBUTION]
    if install.method == "pip":
        return [sys.executable, "-m", "pip", "install", "--upgrade", target]
    raise AbapCliError(
        "this looks like a source checkout, not an installed copy - "
        "update it with 'git pull' instead"
    )


def run(command: list[str]) -> str:
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    except FileNotFoundError as exc:
        raise AbapCliError(f"{command[0]} is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise AbapCliError("the upgrade did not finish within five minutes") from exc
    if done.returncode != 0:
        raise AbapCliError((done.stderr or done.stdout).strip() or "the upgrade failed")
    return (done.stdout or "").strip()
