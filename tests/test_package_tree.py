from __future__ import annotations

import re

from adt_cli import repository


def _objects(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<vfs:object name="{name}" type="{type_code}" '
        f'uri="/sap/bc/adt/oo/classes/{name.lower()}"/>'
        for type_code, name in rows
    )
    return f'<?xml version="1.0"?><vfs:virtualFoldersResult>{body}</vfs:virtualFoldersResult>'


def _nodes(children: list[str]) -> str:
    body = "".join(
        "<SEU_ADT_REPOSITORY_OBJ_NODE>"
        f"<OBJECT_TYPE>DEVC/K</OBJECT_TYPE><OBJECT_NAME>{name}</OBJECT_NAME>"
        f"<OBJECT_URI>/sap/bc/adt/packages/{name.lower()}</OBJECT_URI>"
        "</SEU_ADT_REPOSITORY_OBJ_NODE>"
        for name in children
    )
    return f'<?xml version="1.0"?><asx:abap>{body}</asx:abap>'


class _Reply:
    def __init__(self, text: str) -> None:
        self.text = text


class _Session:
    """A package hierarchy: name -> (objects directly in it, packages under it).

    Listing a package answers with its own objects *and* every descendant's,
    which is how SAP's package facet actually behaves.
    """

    def __init__(self, tree: dict[str, tuple[list[tuple[str, str]], list[str]]]) -> None:
        self.tree = tree
        self.listed: list[str] = []
        self.walked: list[str] = []

    def _subtree(self, package: str, seen: set[str] | None = None) -> list[tuple[str, str]]:
        seen = seen if seen is not None else set()
        if package in seen or package not in self.tree:
            return []
        seen.add(package)
        own, children = self.tree[package]
        rows = list(own)
        for child in children:
            rows += self._subtree(child, seen)
        return rows

    async def post(self, path, *, content, content_type, accept):
        package = re.search(r"<vfs:value>(.*?)</vfs:value>", content).group(1)
        self.listed.append(package)
        return _Reply(_objects(self._subtree(package)))

    async def request(self, method, path, *, params, **kwargs):
        package = params["parent_name"]
        self.walked.append(package)
        return _Reply(_nodes(self.tree.get(package, ([], []))[1]))


TREE = {
    "ZROOT": ([("CLAS/OC", "ZCL_ROOT")], ["ZCHILD_A", "ZCHILD_B"]),
    "ZCHILD_A": ([("CLAS/OC", "ZCL_A")], ["ZGRANDCHILD"]),
    "ZCHILD_B": ([("CLAS/OC", "ZCL_B")], []),
    "ZGRANDCHILD": ([("CLAS/OC", "ZCL_DEEP")], []),
}


async def test_the_whole_hierarchy_comes_down_by_default():
    session = _Session(TREE)

    found, visited = await repository.list_tree(session, "ZROOT")

    assert [obj.name for obj in found] == ["ZCL_A", "ZCL_B", "ZCL_DEEP", "ZCL_ROOT"]
    assert visited == ["ZCHILD_A", "ZCHILD_B", "ZGRANDCHILD", "ZROOT"]


async def test_not_recursing_keeps_only_what_sits_in_the_named_package():
    """SAP's listing already includes the children, so they have to be subtracted."""
    session = _Session(TREE)

    found, visited = await repository.list_tree(session, "ZROOT", recurse=False)

    assert [obj.name for obj in found] == ["ZCL_ROOT"]
    assert visited == ["ZROOT"]
    # The children still have to be listed - that is what identifies them.
    assert sorted(session.walked) == ["ZCHILD_A", "ZCHILD_B", "ZGRANDCHILD", "ZROOT"]


async def test_a_deep_object_is_dropped_by_no_subpackages():
    session = _Session(TREE)

    found, _ = await repository.list_tree(session, "ZCHILD_A", recurse=False)

    assert [obj.name for obj in found] == ["ZCL_A"]


async def test_a_cycle_visits_every_package_once():
    session = _Session(
        {
            "ZA": ([("CLAS/OC", "ZCL_A")], ["ZB"]),
            "ZB": ([("CLAS/OC", "ZCL_B")], ["ZA"]),
        }
    )

    found, visited = await repository.list_tree(session, "ZA")

    assert [obj.name for obj in found] == ["ZCL_A", "ZCL_B"]
    assert visited == ["ZA", "ZB"]
    assert sorted(session.listed) == ["ZA", "ZB"]


async def test_an_object_in_two_packages_is_kept_once():
    session = _Session(
        {
            "ZA": ([("CLAS/OC", "ZCL_SHARED")], ["ZB"]),
            "ZB": ([("CLAS/OC", "ZCL_SHARED")], []),
        }
    )

    found, _ = await repository.list_tree(session, "ZA")

    assert [obj.name for obj in found] == ["ZCL_SHARED"]
