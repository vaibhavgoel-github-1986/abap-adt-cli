from __future__ import annotations

from typer.testing import CliRunner

from adt_cli.cli import app
from adt_cli.errors import EXIT_USAGE

runner = CliRunner()


def test_version_runs():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "abap-adt-cli" in result.output


def test_status_without_a_manifest_fails_without_a_traceback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "no manifest" in result.output


def test_push_rejects_a_bad_transport_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["push", "--transport", "not-a-tr"])
    assert result.exit_code == EXIT_USAGE
    assert "not a transport request id" in result.output


def test_unknown_system_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr("adt_cli.config.CONFIG_HOME", tmp_path / "config.json")
    monkeypatch.delenv("ADT_SYSTEM", raising=False)
    monkeypatch.delenv("ADT_HOST", raising=False)
    result = runner.invoke(app, ["login", "--system", "nope"])
    assert result.exit_code == EXIT_USAGE
    assert "no systems configured" in result.output
