from __future__ import annotations

from adt_cli import expand
from adt_cli.repository import RepoObject

NODES = """<?xml version="1.0" encoding="utf-8"?>
<asx:abap xmlns:asx="http://www.sap.com/abapxml">
 <SEU_ADT_REPOSITORY_OBJ_NODE>
  <OBJECT_TYPE>FUGR/FF</OBJECT_TYPE><OBJECT_NAME></OBJECT_NAME><OBJECT_URI></OBJECT_URI>
 </SEU_ADT_REPOSITORY_OBJ_NODE>
 <SEU_ADT_REPOSITORY_OBJ_NODE>
  <OBJECT_TYPE>FUGR/FF</OBJECT_TYPE><OBJECT_NAME>Z_FM_ONE</OBJECT_NAME>
  <OBJECT_URI>/sap/bc/adt/functions/groups/zfg/fmodules/z_fm_one</OBJECT_URI>
 </SEU_ADT_REPOSITORY_OBJ_NODE>
 <SEU_ADT_REPOSITORY_OBJ_NODE>
  <OBJECT_TYPE>FUGR/I</OBJECT_TYPE><OBJECT_NAME>LZFGTOP</OBJECT_NAME>
  <OBJECT_URI>/sap/bc/adt/functions/groups/zfg/includes/lzfgtop</OBJECT_URI>
 </SEU_ADT_REPOSITORY_OBJ_NODE>
 <SEU_ADT_REPOSITORY_OBJ_NODE>
  <OBJECT_TYPE>FUGR/PX</OBJECT_TYPE><OBJECT_NAME>SAPLZFG</OBJECT_NAME>
  <OBJECT_URI>/sap/bc/adt/textelements/functiongroups/zfg</OBJECT_URI>
 </SEU_ADT_REPOSITORY_OBJ_NODE>
</asx:abap>
"""


class _Reply:
    text = NODES


class _Session:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def request(self, method, path, **kwargs):
        self.calls.append({"method": method, "path": path, **kwargs})
        return _Reply()


async def test_group_expands_into_modules_and_includes():
    session = _Session()
    group = RepoObject("ZFG", "FUGR/F", "/sap/bc/adt/functions/groups/zfg")

    found = await expand.children(session, group)

    assert [(obj.type_code, obj.name) for obj in found] == [
        ("FUGR/FF", "Z_FM_ONE"),
        ("FUGR/I", "LZFGTOP"),
    ]
    assert session.calls[0]["params"]["parent_name"] == "ZFG"


async def test_leaf_objects_are_not_expanded():
    session = _Session()
    leaf = RepoObject("ZCL_A", "CLAS/OC", "/sap/bc/adt/oo/classes/zcl_a")

    assert await expand.children(session, leaf) == []
    assert session.calls == []


async def test_expand_keeps_originals_and_deduplicates():
    session = _Session()
    items = [
        RepoObject("ZFG", "FUGR/F", "/sap/bc/adt/functions/groups/zfg"),
        RepoObject("Z_FM_ONE", "FUGR/FF", "/already/known"),
    ]

    grown = await expand.expand(session, items)

    names = [(obj.type_code, obj.name) for obj in grown]
    assert names.count(("FUGR/FF", "Z_FM_ONE")) == 1
    assert ("FUGR/I", "LZFGTOP") in names
    assert ("FUGR/F", "ZFG") in names
