"""ADT object type registry.

Maps an ADT type code (as returned by the repository APIs) to the things the
CLI needs to know: which editable texts the object has, what to call each of
them on disk, and which SE80-style folder it belongs in.

An object is rarely a single file. A class keeps its local definitions, local
implementations, macros and test classes in separate ADT includes, and each one
is edited and transported on its own. :class:`Part` is that unit - a file suffix
paired with the URI segment that serves it.

Types absent from this table are still discovered and listed, they are just not
pulled as source. Adding one is a single line.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SOURCE_MAIN = "/source/main"


@dataclass(frozen=True)
class Part:
    """One editable text belonging to an object."""

    suffix: str
    path: str

    @property
    def is_main(self) -> bool:
        return self.path == SOURCE_MAIN


@dataclass(frozen=True)
class ObjectType:
    code: str
    extension: str
    folder: str
    source_path: str = SOURCE_MAIN
    writable: bool = True
    parts: tuple[Part, ...] = field(default=())

    def __post_init__(self) -> None:
        # Most types have exactly one editable text; spell it out so callers
        # never have to special-case the single-part shape.
        if not self.parts and self.source_path:
            object.__setattr__(self, "parts", (Part(self.extension, self.source_path),))

    @property
    def is_source(self) -> bool:
        return bool(self.source_path)

    @property
    def main(self) -> Part | None:
        return next((part for part in self.parts if part.is_main), None)


# A class is five separate ADT includes; the four beyond /source/main are what
# make test classes and local helpers editable.
_CLASS_PARTS: tuple[Part, ...] = (
    Part(".clas.abap", SOURCE_MAIN),
    Part(".clas.locals_def.abap", "/includes/definitions"),
    Part(".clas.locals_imp.abap", "/includes/implementations"),
    Part(".clas.macros.abap", "/includes/macros"),
    Part(".clas.testclasses.abap", "/includes/testclasses"),
)

_TYPES: tuple[ObjectType, ...] = (
    # --- Class library ---------------------------------------------------
    ObjectType("CLAS/OC", ".clas.abap", "Class Library/Classes", parts=_CLASS_PARTS),
    ObjectType("INTF/OI", ".intf.abap", "Class Library/Interfaces"),
    # --- Programs --------------------------------------------------------
    ObjectType("PROG/P", ".prog.abap", "Programs"),
    ObjectType("PROG/I", ".prog.abap", "Programs/Includes"),
    # --- Function groups -------------------------------------------------
    # A group's own source is its main program; the includes and function
    # modules under it are discovered per object, see :mod:`adt_cli.expand`.
    # They share the group's folder so everything stays together.
    ObjectType("FUGR/F", ".fugr.abap", "Function Groups"),
    ObjectType("FUGS/FX", ".fugr.abap", "Function Groups"),
    ObjectType("FUGR/FF", ".fugr.abap", "Function Groups"),
    ObjectType("FUGR/I", ".fugr.abap", "Function Groups"),
    # --- Core Data Services ----------------------------------------------
    ObjectType("DDLS/DF", ".ddls.asddls", "Core Data Services/Data Definitions"),
    ObjectType("DDLX/EX", ".ddlx.asddlxs", "Core Data Services/Metadata Extensions"),
    ObjectType("DDLA/ADF", ".ddla.asddla", "Core Data Services/Annotation Definitions"),
    ObjectType("DCLS/DL", ".dcls.asdcls", "Core Data Services/Access Controls"),
    ObjectType("BDEF/BDO", ".bdef.asbdef", "Core Data Services/Behavior Definitions"),
    # --- Business services -----------------------------------------------
    ObjectType("SRVD/SRV", ".srvd.srvdsrv", "Business Services/Service Definitions"),
    # --- Dictionary ------------------------------------------------------
    ObjectType("TABL/DT", ".tabl.asddls", "Dictionary/Database Tables"),
    ObjectType("TABL/DS", ".strc.asddls", "Dictionary/Structures"),
    ObjectType("TYPE/DG", ".type.abap", "Dictionary/Type Groups"),
    ObjectType("XSLT/VT", ".xslt.xml", "Transformations"),
    # --- Non-source objects: metadata XML pulled, not writable by push -----
    ObjectType("TTYP/DA", ".ttyp.xml", "Dictionary/Table Types", "", False),
    ObjectType("DTEL/DE", ".dtel.xml", "Dictionary/Data Elements", "", False),
    ObjectType("DOMA/DD", ".doma.xml", "Dictionary/Domains", "", False),
    ObjectType("ENQU/DL", ".enqu.xml", "Dictionary/Lock Objects", "", False),
    ObjectType("VIEW/DV", ".view.xml", "Dictionary/Views", "", False),
    ObjectType("ENHO/XHB", ".enho.xml", "Enhancements", "", False),
    ObjectType("SRVB/SVB", ".srvb.xml", "Business Services/Service Bindings", "", False),
    ObjectType("MSAG/N", ".msag.xml", "Message Classes", "", False),
    ObjectType("DEVC/K", ".devc.xml", "Packages", "", False),
    ObjectType("SUSH/S", ".sush.xml", "Authorization Default Values", "", False),
)

_BY_CODE: dict[str, ObjectType] = {entry.code: entry for entry in _TYPES}

UNKNOWN = ObjectType("", ".txt", "Other", "", False)

# Longest suffix first, so '.clas.testclasses.abap' is never shadowed by a
# shorter suffix that also matches.
_BY_SUFFIX: tuple[tuple[str, ObjectType, Part], ...] = tuple(
    sorted(
        ((part.suffix, entry, part) for entry in _TYPES for part in entry.parts),
        key=lambda row: len(row[0]),
        reverse=True,
    )
)


def lookup(code: str) -> ObjectType:
    """Resolve a type code, tolerating the bare form (``CLAS`` for ``CLAS/OC``)."""
    if code in _BY_CODE:
        return _BY_CODE[code]
    prefix = code.split("/", 1)[0]
    for entry in _TYPES:
        if entry.code.split("/", 1)[0] == prefix:
            return entry
    return UNKNOWN


def known_codes() -> list[str]:
    return sorted(_BY_CODE)


def type_for_file(filename: str) -> ObjectType | None:
    """Reverse of the naming scheme, for files SAP has never seen."""
    found = part_for_file(filename)
    return found[0] if found else None


def part_for_file(filename: str) -> tuple[ObjectType, Part] | None:
    """The type and the specific editable text a local file maps onto."""
    name = filename.rsplit("/", 1)[-1].lower()
    for suffix, entry, part in _BY_SUFFIX:
        if name.endswith(suffix):
            return entry, part
    return None


def name_for_file(filename: str, kind: ObjectType) -> str:
    """Object name from a local file name, with any part suffix removed."""
    stem = filename.rsplit("/", 1)[-1]
    found = part_for_file(stem)
    suffix = found[1].suffix if found else kind.extension
    if suffix and stem.lower().endswith(suffix):
        return stem[: -len(suffix)].upper()
    return stem.upper()


def source_uri(object_uri: str, kind: ObjectType) -> str:
    """Absolute URI of the main editable text for an object."""
    if not kind.is_source:
        return object_uri
    if object_uri.endswith(SOURCE_MAIN):
        return object_uri
    return f"{object_uri}{kind.source_path}"


def part_uri(object_uri: str, part: Part) -> str:
    """Absolute URI of one editable text of an object."""
    if object_uri.endswith(part.path):
        return object_uri
    return f"{object_uri}{part.path}"
