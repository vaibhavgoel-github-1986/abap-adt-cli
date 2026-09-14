"""ADT object type registry.

Maps an ADT type code (as returned by the repository APIs) to the things the
CLI needs to know: where the editable text lives relative to the object URI,
what to call the local file, and which SE80-style folder it belongs in.

Types absent from this table are still discovered and listed, they are just not
pulled as source. Adding one is a single line.
"""

from __future__ import annotations

from dataclasses import dataclass

SOURCE_MAIN = "/source/main"


@dataclass(frozen=True)
class ObjectType:
    code: str
    extension: str
    folder: str
    source_path: str = SOURCE_MAIN
    writable: bool = True

    @property
    def is_source(self) -> bool:
        return bool(self.source_path)


_TYPES: tuple[ObjectType, ...] = (
    # --- Class library ---------------------------------------------------
    ObjectType("CLAS/OC", ".clas.abap", "Class Library/Classes"),
    ObjectType("INTF/OI", ".intf.abap", "Class Library/Interfaces"),
    # --- Programs --------------------------------------------------------
    ObjectType("PROG/P", ".prog.abap", "Programs"),
    ObjectType("PROG/I", ".prog.abap", "Programs/Includes"),
    ObjectType("FUGR/FF", ".fugr.abap", "Function Groups/Function Modules"),
    ObjectType("FUGR/I", ".fugr.abap", "Function Groups/Includes"),
    # --- Core Data Services ----------------------------------------------
    ObjectType("DDLS/DF", ".ddls.asddls", "Core Data Services/Data Definitions"),
    ObjectType("DDLX/EX", ".ddlx.asddlxs", "Core Data Services/Metadata Extensions"),
    ObjectType("DCLS/DL", ".dcls.asdcls", "Core Data Services/Access Controls"),
    ObjectType("BDEF/BDO", ".bdef.asbdef", "Core Data Services/Behavior Definitions"),
    # --- Business services -----------------------------------------------
    ObjectType("SRVD/SRV", ".srvd.srvdsrv", "Business Services/Service Definitions"),
    # --- Dictionary ------------------------------------------------------
    ObjectType("TABL/DT", ".tabl.asddls", "Dictionary/Database Tables"),
    ObjectType("TTYP/DA", ".ttyp.asddls", "Dictionary/Table Types"),
    ObjectType("DTEL/DE", ".dtel.asddls", "Dictionary/Data Elements"),
    ObjectType("DOMA/DD", ".doma.asddls", "Dictionary/Domains"),
    # --- Non-source objects: metadata XML pulled, not writable by push -----
    ObjectType("SRVB/SVB", ".srvb.xml", "Business Services/Service Bindings", "", False),
    ObjectType("MSAG/N", ".msag.xml", "Message Classes", "", False),
    ObjectType("DEVC/K", ".devc.xml", "Packages", "", False),
    ObjectType("SUSH/S", ".sush.xml", "Authorization Default Values", "", False),
)

_BY_CODE: dict[str, ObjectType] = {entry.code: entry for entry in _TYPES}

UNKNOWN = ObjectType("", ".txt", "Other", "", False)


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


def source_uri(object_uri: str, kind: ObjectType) -> str:
    """Absolute URI of the editable text for an object."""
    if not kind.is_source:
        return object_uri
    if object_uri.endswith(SOURCE_MAIN):
        return object_uri
    return f"{object_uri}{kind.source_path}"
