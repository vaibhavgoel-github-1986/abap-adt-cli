"""Expanding container objects into the children ADT edits separately.

A function group is not one source. SAP stores its main program, its includes
and each function module as separate texts, and the package listing only names
the group. Eclipse resolves the rest through the repository node structure, and
so do we - otherwise a function module could never be pulled or pushed.

Everything else in the registry is already a leaf, so expansion is a no-op for
it and costs nothing.
"""

from __future__ import annotations

import asyncio

import httpx

from adt_cli.errors import AdtError
from adt_cli.repository import RepoObject, nodes
from adt_cli.session import AdtSession

# Group types whose children have to be resolved one level down.
CONTAINERS = frozenset({"FUGR/F", "FUGS/FX"})
# Children worth pulling: function modules and includes. FUGR/PX is the text
# element pool, which lives under a different resource and has no source.
CHILD_TYPES = frozenset({"FUGR/FF", "FUGR/I"})


async def children(session: AdtSession, parent: RepoObject) -> list[RepoObject]:
    """Function modules and includes belonging to a group, or [] for a leaf."""
    if parent.type_code not in CONTAINERS:
        return []
    found = await nodes(session, parent.type_code, parent.name)
    return [child for child in found if child.uri and child.type_code in CHILD_TYPES]


async def expand(
    session: AdtSession, items: list[RepoObject], *, concurrency: int = 16
) -> list[RepoObject]:
    """The given objects plus the children ADT edits separately.

    A group that cannot be expanded is kept as-is rather than failing the pull;
    its own source still comes down, only the children are missing.
    """
    targets = [obj for obj in items if obj.type_code in CONTAINERS]
    if not targets:
        return list(items)

    gate = asyncio.Semaphore(max(1, concurrency))

    async def one(parent: RepoObject) -> list[RepoObject]:
        async with gate:
            try:
                return await children(session, parent)
            except (AdtError, httpx.HTTPError):
                return []

    found = await asyncio.gather(*(one(parent) for parent in targets))

    seen = {(obj.type_code, obj.name) for obj in items}
    grown = list(items)
    for batch in found:
        for child in batch:
            key = (child.type_code, child.name)
            if key not in seen:
                seen.add(key)
                grown.append(child)
    grown.sort(key=lambda entry: (entry.type_code, entry.name))
    return grown
