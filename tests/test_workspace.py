from __future__ import annotations

import json

import pytest

from adt_cli import objects
from adt_cli.errors import WorkspaceError
from adt_cli.repository import RepoObject
from adt_cli.workspace import Workspace, sha256


def _space(tmp_path) -> Workspace:
    # Flat layout keeps these assertions about scanning, not about folder shape.
    return Workspace(root=tmp_path, package="ZTEST", system="dev-100", se80=False)


def _se80(tmp_path) -> Workspace:
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


def test_se80_is_the_default_layout(tmp_path):
    assert Workspace(root=tmp_path).se80 is True


def test_hashing_ignores_what_sap_normalises_away():
    """SAP drops the trailing newline on write, so it must not count as a change."""
    assert sha256("CLASS x.\nENDCLASS.") == sha256("CLASS x.\nENDCLASS.\n")
    assert sha256("CLASS x.\nENDCLASS.") == sha256("CLASS x.\r\nENDCLASS.\r\n")
    assert sha256("CLASS x.\nENDCLASS.") != sha256("CLASS x.\nENDCLASS. \" edit")


def test_pushed_file_does_not_drift_against_the_server(tmp_path):
    space = _space(tmp_path)
    obj = RepoObject("ZCL_A", "CLAS/OC", "/uri")
    local = space.write(obj, "CLASS zcl_a.\nENDCLASS.\n")

    # What ADT hands back after storing it: CRLF, and no trailing newline.
    assert sha256("CLASS zcl_a.\r\nENDCLASS.") == space.files[local].sha256


def test_se80_clubs_every_part_of_an_object_in_one_folder(tmp_path):
    space = _se80(tmp_path)
    obj = RepoObject("ZCL_A", "CLAS/OC", "/sap/bc/adt/oo/classes/zcl_a")
    kind = objects.lookup("CLAS/OC")

    main = space.write(obj, "x", kind.parts[0])
    tests = space.write(obj, "y", kind.parts[4])

    assert main == "Class Library/Classes/ZCL_A/zcl_a.clas.abap"
    assert tests == "Class Library/Classes/ZCL_A/zcl_a.clas.testclasses.abap"


def test_function_group_children_land_beside_their_group(tmp_path):
    space = _se80(tmp_path)
    group = RepoObject("ZFG", "FUGR/F", "/sap/bc/adt/functions/groups/zfg")
    module = RepoObject(
        "Z_FM_ONE", "FUGR/FF", "/sap/bc/adt/functions/groups/zfg/fmodules/z_fm_one"
    )

    assert space.local_path(group) == "Function Groups/ZFG/zfg.fugr.abap"
    assert space.local_path(module) == "Function Groups/ZFG/z_fm_one.fugr.abap"


def test_namespaced_names_stay_inside_the_workspace(tmp_path):
    space = _se80(tmp_path)
    obj = RepoObject("/NS/ZCL_A", "CLAS/OC", "/sap/bc/adt/oo/classes/%2fns%2fzcl_a")

    local = space.local_path(obj)

    assert ".." not in local
    assert local == "Class Library/Classes/#NS#ZCL_A/#ns#zcl_a.clas.abap"
