"""ADT object type registry.

Maps an ADT type code (as returned by the repository APIs) to the things the
CLI needs to know: where the editable text lives relative to the object URI,
what to call the local file, and which SE80-style folder it belongs in.

Types absent from this table are still discovered and listed, they are just not
pulled as source. Adding one is a single line.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

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
    ObjectType("VIEW/DV", ".view.xml", "Dictionary/Views", "", False),
    ObjectType("ENHO/XHB", ".enho.xml", "Enhancements", "", False),
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


def type_for_file(filename: str) -> ObjectType | None:
    """Reverse of the naming scheme, for files SAP has never seen."""
    for entry in _TYPES:
        if filename.endswith(entry.extension):
            return entry
    return None


def name_for_file(filename: str, kind: ObjectType) -> str:
    return filename.rsplit("/", 1)[-1][: -len(kind.extension)].upper()


def source_uri(object_uri: str, kind: ObjectType) -> str:
    """Absolute URI of the editable text for an object."""
    if not kind.is_source:
        return object_uri
    if object_uri.endswith(SOURCE_MAIN):
        return object_uri
    return f"{object_uri}{kind.source_path}"


# --- abapGit file -> ADT source endpoint -------------------------------------
#
# Pull writes abapGit's whole file set; push sends back only the members ADT can
# write, which is where the fine-grained transport entries come from. A class is
# five separate files here, each with its own ADT endpoint. Anything missing from
# this table has no ADT source endpoint at all.


@dataclass(frozen=True)
class AdtTarget:
    suffix: str
    type_code: str
    collection: str
    source_path: str = SOURCE_MAIN


_ADT_TARGETS: tuple[AdtTarget, ...] = (
    # Longest suffixes first so 'zcl_x.clas.abap' cannot shadow the includes.
    AdtTarget(
        ".clas.locals_def.abap", "CLAS/OC", "/sap/bc/adt/oo/classes", "/includes/definitions"
    ),
    AdtTarget(
        ".clas.locals_imp.abap", "CLAS/OC", "/sap/bc/adt/oo/classes", "/includes/implementations"
    ),
    AdtTarget(".clas.macros.abap", "CLAS/OC", "/sap/bc/adt/oo/classes", "/includes/macros"),
    AdtTarget(
        ".clas.testclasses.abap", "CLAS/OC", "/sap/bc/adt/oo/classes", "/includes/testclasses"
    ),
    AdtTarget(".clas.abap", "CLAS/OC", "/sap/bc/adt/oo/classes"),
    AdtTarget(".intf.abap", "INTF/OI", "/sap/bc/adt/oo/interfaces"),
    AdtTarget(".prog.abap", "PROG/P", "/sap/bc/adt/programs/programs"),
    AdtTarget(".ddls.asddls", "DDLS/DF", "/sap/bc/adt/ddic/ddl/sources"),
    AdtTarget(".ddlx.asddlxs", "DDLX/EX", "/sap/bc/adt/ddic/ddlx/sources"),
    AdtTarget(".dcls.asdcls", "DCLS/DL", "/sap/bc/adt/acm/dcl/sources"),
    AdtTarget(".bdef.asbdef", "BDEF/BDO", "/sap/bc/adt/bo/behaviordefinitions"),
    AdtTarget(".srvd.srvdsrv", "SRVD/SRV", "/sap/bc/adt/ddic/srvd/sources"),
)


def adt_target(path: str) -> AdtTarget | None:
    """The ADT endpoint that can write an abapGit file, or None when there is none."""
    filename = path.rsplit("/", 1)[-1].lower()
    for target in _ADT_TARGETS:
        if filename.endswith(target.suffix):
            return target
    return None


def adt_object_uri(target: AdtTarget, name: str) -> str:
    # Namespaced names carry '/', which has to survive as a path segment.
    return f"{target.collection}/{quote(name.lower(), safe='')}"
