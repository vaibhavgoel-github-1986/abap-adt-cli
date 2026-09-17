from __future__ import annotations

from pathlib import Path

from adt_cli import runtime


def test_local_packages_are_scoped_to_their_owner(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    root = runtime.resolve_root(None, "$TMP", owner="vaibhago")

    assert root == (tmp_path / "VAIBHAGO" / "$TMP").resolve()


def test_named_local_packages_are_not_owner_scoped(tmp_path, monkeypatch):
    """$ZADT_VSP is an ordinary package that merely cannot be transported."""
    monkeypatch.chdir(tmp_path)

    root = runtime.resolve_root(None, "$ZADT_VSP", owner="vaibhago")

    assert root == (tmp_path / "$ZADT_VSP").resolve()


def test_transportable_packages_have_no_owner_level(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    root = runtime.resolve_root(None, "ZS4INTCPQ", owner="vaibhago")

    assert root == (tmp_path / "ZS4INTCPQ").resolve()


def test_local_package_without_an_owner_keeps_the_flat_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert runtime.resolve_root(None, "$TMP") == (tmp_path / "$TMP").resolve()


def test_explicit_dest_always_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    root = runtime.resolve_root(Path("elsewhere"), "$TMP", owner="vaibhago")

    assert root == (tmp_path / "elsewhere").resolve()


def test_is_local_only_matches_dollar_packages():
    assert runtime.is_local("$TMP")
    assert runtime.is_local("$ZADT_VSP")
    assert not runtime.is_local("ZS4INTCPQ")


def test_only_tmp_counts_as_shared():
    assert runtime.is_shared("$TMP")
    assert runtime.is_shared("$tmp")
    assert not runtime.is_shared("$ZADT_VSP")
    assert not runtime.is_shared("ZS4INTCPQ")
