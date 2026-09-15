from __future__ import annotations

import json

import pytest

from adt_cli.errors import WorkspaceError
from adt_cli.repository import RepoObject
from adt_cli.workspace import Workspace, sha256


def _space(tmp_path) -> Workspace:
    return Workspace(root=tmp_path, package="ZTEST", system="dev-100")


def test_write_then_scan_is_clean(tmp_path):
    space = _space(tmp_path)
    obj = RepoObject("ZCL_A", "CLAS/OC", "/sap/bc/adt/oo/classes/zcl_a")
    local = space.write(obj, "class zcl_a.\n")
    space.save()

    assert local == "src/zcl_a.clas.abap"
    assert space.scan() == ([], [])

    (tmp_path / local).write_text("class zcl_a. \" edited\n", encoding="utf-8")
    assert space.scan() == ([local], [])

    (tmp_path / local).unlink()
    assert space.scan() == ([], [local])


def test_save_is_atomic_and_reloads(tmp_path):
    space = _space(tmp_path)
    space.write(RepoObject("ZCL_A", "CLAS/OC", "/uri"), "x")
    space.save()

    reloaded = Workspace.load(tmp_path)
    assert reloaded.package == "ZTEST"
    assert reloaded.files["src/zcl_a.clas.abap"].sha256 == sha256("x")
    leftovers = list((tmp_path / ".adt").glob("*.tmp"))
    assert leftovers == []


def test_corrupt_manifest_is_reported(tmp_path):
    manifest = tmp_path / ".adt" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{not json", encoding="utf-8")

    with pytest.raises(WorkspaceError, match="corrupt"):
        Workspace.load(tmp_path)


def test_manifest_entry_missing_field_is_reported(tmp_path):
    manifest = tmp_path / ".adt" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"files": {"src/a.abap": {"name": "A", "type": "CLAS/OC"}}}), encoding="utf-8"
    )

    with pytest.raises(WorkspaceError, match="missing"):
        Workspace.load(tmp_path)


def test_path_outside_root_is_refused(tmp_path):
    space = _space(tmp_path)
    with pytest.raises(WorkspaceError, match="outside"):
        space.resolve("../../etc/passwd")


def test_object_name_cannot_escape_the_workspace():
    obj = RepoObject("../../etc/passwd", "CLAS/OC", "/uri")
    assert "/" not in obj.filename
    assert ".." not in obj.filename


def test_non_utf8_file_is_reported(tmp_path):
    space = _space(tmp_path)
    space.write(RepoObject("ZCL_A", "CLAS/OC", "/uri"), "x")
    (tmp_path / "src" / "zcl_a.clas.abap").write_bytes(b"\xff\xfe binary")

    with pytest.raises(WorkspaceError, match="UTF-8"):
        space.scan()


def test_untracked_object_is_reported(tmp_path):
    with pytest.raises(WorkspaceError, match="not tracked"):
        _space(tmp_path).object_for("src/nope.abap")
