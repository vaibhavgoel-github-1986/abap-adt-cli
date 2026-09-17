"""Translation between abapGit's flat file layout and an SE80-style folder tree.

abapGit stores everything flat under src/ as `<name>.<type>[.<extra>].<ext>`.
That is fine for a git diff but poor for browsing, so on disk we mirror the SE80
object tree instead. The canonical abapGit path is kept in the manifest, which is
what gets sent back to SAP on push - the local tree is purely a view.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

# SE80 node names, matching the folders shown when expanding a package.
SE80_FOLDERS: dict[str, str] = {
    # Dictionary
    "TABL": "Dictionary Objects/Database Tables",
    "VIEW": "Dictionary Objects/Views",
    "DTEL": "Dictionary Objects/Data Elements",
    "DOMA": "Dictionary Objects/Domains",
    "TTYP": "Dictionary Objects/Table Types",
    "SHLP": "Dictionary Objects/Search Helps",
    "ENQU": "Dictionary Objects/Lock Objects",
    "SQLT": "Dictionary Objects/Database Tables",
    "AUTH": "Dictionary Objects/Authorization Fields",
    "TYPE": "Dictionary Objects/Type Groups",
    # Core Data Services
    "DDLS": "Core Data Services/Data Definitions",
    "DDLX": "Core Data Services/Metadata Extensions",
    "DDLA": "Core Data Services/Annotation Definitions",
    "DCLS": "Core Data Services/Access Controls",
    "BDEF": "Behavior Definitions",
    "SRVD": "Service Definitions",
    "SRVB": "Service Bindings",
    # Class library
    "CLAS": "Class Library/Classes",
    "INTF": "Class Library/Interfaces",
    # Programs and friends
    "PROG": "Programs",
    "FUGR": "Function Groups",
    "XSLT": "Transformations",
    "MSAG": "Message Classes",
    "TRAN": "Transactions",
    "NROB": "Number Range Objects",
    "WAPA": "BSP Library",
    "SMIM": "MIME Objects",
    "W3MI": "MIME Objects",
    # Enhancements
    "ENHO": "Enhancements/Implementations",
    "ENHS": "Enhancements/Spots",
    "ENHC": "Enhancements/Composites",
    # Gateway
    "G4BA": "Gateway V4 Service Groups",
    "IWSV": "Gateway Services",
    "IWMO": "SAP Gateway: Models Metadata",
    "IWPR": "GW Service Builder Projects",
    "IWSG": "SAP Gateway: Service Groups",
    # Security / transport
    "SUSH": "Authorization Default Values",
    "SUSO": "Authorization Objects",
    "SICF": "SICF/TYP",
    "TOBJ": "Transport Object Definitions",
    "OA2P": "OAuth 2.0 Client Profiles",
    "OA2S": "OAuth 2.0 Scopes",
}

OTHER_FOLDER = "Other Objects"
SUBPACKAGE_FOLDER = "Subpackages"
PACKAGE_FILE = "package.devc.xml"
REPO_CONFIG_FILE = ".abapgit.xml"

# How a sub-folder name maps onto a package name. FULL spells the package out,
# PREFIX writes only the part that follows the parent package's name.
FOLDER_FULL = "FULL"
FOLDER_PREFIX = "PREFIX"
_FOLDER_LOGIC = re.compile(r"<FOLDER_LOGIC>\s*([A-Za-z]+)\s*</FOLDER_LOGIC>")

__all__ = [
    "FOLDER_FULL",
    "FOLDER_PREFIX",
    "PACKAGE_FILE",
    "REPO_CONFIG_FILE",
    "SE80_FOLDERS",
    "FileRef",
    "folder_for",
    "folder_logic",
    "package_for",
    "parse_filename",
    "to_local_path",
    "tree_summary",
]


def folder_logic(config: bytes | None) -> str:
    """FOLDER_LOGIC from .abapgit.xml, defaulting to abapGit's own default."""
    if not config:
        return FOLDER_PREFIX
    found = _FOLDER_LOGIC.search(config.decode("utf-8", errors="replace"))
    return found.group(1).upper() if found else FOLDER_PREFIX


def package_for(segments: list[str], top_package: str, logic: str) -> str:
    """Package that owns a file, from the sub-folders between src/ and the file."""
    package = top_package.upper()
    if not segments:
        return package
    if logic == FOLDER_PREFIX:
        for segment in segments:
            package = f"{package}_{segment}"
        return package.upper()
    # abapGit escapes '/' in namespaced names as '#'.
    return segments[-1].upper().replace("#", "/")


@dataclass(frozen=True)
class FileRef:
    """One file from the export zip, with both its canonical and local location."""

    canonical: str  # path inside the zip, e.g. "src/sub/zcl_x.clas.abap"
    local: str  # path on disk, e.g. "Class Library/Classes/ZCL_X/zcl_x.clas.abap"
    obj_type: str  # "CLAS", or "" for repo-level files
    obj_name: str
    package: str = ""  # the package that owns the object


def parse_filename(filename: str) -> tuple[str, str]:
    """`zcl_foo.clas.abap` -> ("ZCL_FOO", "CLAS"). Returns ("", "") if not an object file."""
    parts = filename.split(".")
    if len(parts) < 3:
        return "", ""
    name, obj_type = parts[0], parts[1]
    if not name or len(obj_type) != 4:
        return "", ""
    # abapGit escapes namespaces as '#'
    return name.replace("#", "/").upper(), obj_type.upper()


def folder_for(obj_type: str) -> str:
    return SE80_FOLDERS.get(obj_type, f"{OTHER_FOLDER}/{obj_type}")


def to_local_path(canonical: str, top_package: str, logic: str = FOLDER_FULL) -> FileRef:
    """Map a zip entry onto its SE80-style location on disk."""
    path = PurePosixPath(canonical.lstrip("/"))
    filename = path.name

    # .abapgit.xml and anything outside src/ stays at the root, untouched
    if path.parts and path.parts[0] != "src":
        return FileRef(canonical=canonical, local=filename, obj_type="", obj_name="")

    # directory segments below src/ are the sub-package chain
    segments = [segment.upper() for segment in path.parts[1:-1]]
    package = package_for(segments, top_package, logic)
    prefix = ""
    if segments:
        prefix = "/".join([SUBPACKAGE_FOLDER, *segments]) + "/"

    if filename == PACKAGE_FILE:
        return FileRef(
            canonical=canonical,
            local=f"{prefix}{PACKAGE_FILE}",
            obj_type="DEVC",
            obj_name=package,
            package=package,
        )

    obj_name, obj_type = parse_filename(filename)
    if not obj_type:
        return FileRef(
            canonical=canonical,
            local=f"{prefix}{filename}",
            obj_type="",
            obj_name="",
            package=package,
        )

    safe_name = obj_name.replace("/", "#")
    local = f"{prefix}{folder_for(obj_type)}/{safe_name}/{filename}"
    return FileRef(
        canonical=canonical,
        local=local,
        obj_type=obj_type,
        obj_name=obj_name,
        package=package,
    )


def tree_summary(refs: list[FileRef]) -> dict[str, int]:
    """Object counts per SE80 folder, for a readable pull summary."""
    counts: dict[str, set[str]] = {}
    for ref in refs:
        if not ref.obj_type or ref.obj_type == "DEVC":
            continue
        folder = folder_for(ref.obj_type)
        counts.setdefault(folder, set()).add(ref.obj_name)
    return {folder: len(names) for folder, names in sorted(counts.items())}
