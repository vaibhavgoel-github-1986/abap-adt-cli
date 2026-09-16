"""Version comparison and the command that would replace an installation."""

from __future__ import annotations

from pathlib import Path

import pytest

from adt_cli import release
from adt_cli.errors import AbapCliError


@pytest.mark.parametrize(
    ("newer", "older"),
    [
        ("0.2.0", "0.1.0"),
        ("v1.0.0", "0.9.9"),
        ("1.10.0", "1.9.0"),  # not a string comparison
        ("2.0", "1.99.99"),
        ("1.2.0", "1.2.0rc1"),  # a release beats its own candidate
    ],
)
def test_is_newer(newer: str, older: str) -> None:
    assert release.is_newer(newer, older)
    assert not release.is_newer(older, newer)


def test_equal_versions_are_not_newer() -> None:
    assert not release.is_newer("1.2.3", "1.2.3")
    assert not release.is_newer("v1.2.3", "1.2.3")


def _install(method: str) -> release.Install:
    return release.Install(version="0.1.0", method=method, path=Path("/tmp"))


def test_pipx_upgrades_in_place_but_reinstalls_for_a_tag() -> None:
    assert release.upgrade_command(_install("pipx"), None) == [
        "pipx",
        "upgrade",
        release.DISTRIBUTION,
    ]
    pinned = release.upgrade_command(_install("pipx"), "v0.2.0")
    assert pinned[:3] == ["pipx", "install", "--force"]
    assert pinned[3].endswith("@v0.2.0")


def test_pip_installs_from_the_repository() -> None:
    command = release.upgrade_command(_install("pip"), None)
    assert command[1:4] == ["-m", "pip", "install"]
    assert command[-1] == release.SOURCE


def test_a_source_checkout_is_refused() -> None:
    with pytest.raises(AbapCliError, match="git pull"):
        release.upgrade_command(_install("source"), None)


@pytest.mark.parametrize("tag", ["; rm -rf /", "$(whoami)", "../../etc", "main"])
def test_a_tag_that_is_not_a_version_is_refused(tag: str) -> None:
    """The tag is interpolated into an install URL, so it is matched, not trusted."""
    with pytest.raises(AbapCliError, match="not a version tag"):
        release.upgrade_command(_install("pipx"), tag)
