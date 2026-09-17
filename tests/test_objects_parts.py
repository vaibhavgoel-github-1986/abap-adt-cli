from __future__ import annotations

from adt_cli import objects, repository
from adt_cli.repository import RepoObject


def test_class_has_a_part_per_include():
    kind = objects.lookup("CLAS/OC")
    paths = [part.path for part in kind.parts]
    assert paths == [
        "/source/main",
        "/includes/definitions",
        "/includes/implementations",
        "/includes/macros",
        "/includes/testclasses",
    ]


def test_single_part_types_default_to_main_source():
    kind = objects.lookup("INTF/OI")
    assert [part.path for part in kind.parts] == ["/source/main"]
    assert kind.main is not None


def test_non_source_types_have_no_parts():
    # VIEW answers with a SAP GUI shim, so there is nothing to fetch or write.
    assert objects.lookup("VIEW/DV").parts == ()
    assert not objects.lookup("VIEW/DV").is_source


def test_xml_backed_types_have_an_object_part():
    for code in ("DTEL/DE", "DOMA/DD", "MSAG/N", "SRVB/SVB"):
        kind = objects.lookup(code)
        assert kind.is_source, code
        assert len(kind.parts) == 1, code
        assert kind.parts[0].is_object, code
        assert kind.main is None, code


def test_object_part_uri_is_the_object_itself():
    part = objects.lookup("MSAG/N").parts[0]
    uri = "/sap/bc/adt/messageclass/zmsg_utils"
    assert objects.part_uri(uri, part) == uri


def test_longest_suffix_wins_when_resolving_a_file():
    kind, part = objects.part_for_file("zcl_a.clas.testclasses.abap")
    assert kind.code == "CLAS/OC"
    assert part.path == "/includes/testclasses"

    kind, part = objects.part_for_file("zcl_a.clas.abap")
    assert part.path == "/source/main"


def test_object_name_strips_the_part_suffix():
    kind = objects.lookup("CLAS/OC")
    assert objects.name_for_file("zcl_a.clas.testclasses.abap", kind) == "ZCL_A"
    assert objects.name_for_file("zcl_a.clas.abap", kind) == "ZCL_A"


def test_part_uri_is_appended_once():
    part = objects.lookup("CLAS/OC").parts[4]
    uri = "/sap/bc/adt/oo/classes/zcl_a"
    assert objects.part_uri(uri, part) == f"{uri}/includes/testclasses"
    assert objects.part_uri(f"{uri}/includes/testclasses", part) == f"{uri}/includes/testclasses"


def test_filename_per_part():
    obj = RepoObject("ZCL_A", "CLAS/OC", "/uri")
    assert obj.filename_for(".clas.testclasses.abap") == "zcl_a.clas.testclasses.abap"
    assert obj.filename == "zcl_a.clas.abap"


def test_placeholder_include_is_not_code():
    placeholder = '*"* use this source file for any type of declarations\n*"* you need\n'
    assert not repository.has_code(placeholder)
    assert repository.has_code(placeholder + "CLASS lcl_x DEFINITION.\n")
    assert not repository.has_code("   \n\n")
