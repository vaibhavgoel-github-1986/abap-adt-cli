"""Local workspace: where objects land on disk and what changed since the pull.

The manifest records one entry per *file*, not per object: abapGit represents a
single class as up to six files (main source, local definitions, local
implementations, macros, test classes, metadata XML), and each of them is
separately editable. ``canonical`` keeps the path the file had inside the
archive, which is what push has to send back.

Everything that touches the file system goes through this module, so the rules
are enforced in one place: paths stay inside the workspace root, reads report
the offending file, and the manifest is replaced atomically.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from adt_cli import layout, objects
from adt_cli.errors import WorkspaceError
from adt_cli.repository import RepoObject

MANIFEST_DIR = ".adt"
MANIFEST_FILE = "manifest.json"
MANIFEST_VERSION = 2

# How the workspace was filled, which decides how push sends it back.
MODE_ZSYNC = "zsync"
MODE_ADT = "adt"


def sha256(value: str | bytes) -> str:
    """Hash a file's contents. Text is hashed as UTF-8 so both paths agree."""
    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def read_source_file(path: Path) -> str:
    """Read a tracked file, turning IO and decoding failures into a clear error."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise WorkspaceError(
            f"{path} is not UTF-8 text - ABAP sources must be saved as UTF-8"
        ) from exc
    except OSError as exc:
        raise WorkspaceError(f"cannot read {path}: {exc}") from exc


@dataclass
class Entry:
    name: str
    type_code: str
    uri: str
    sha256: str
    canonical: str = ""
    package: str = ""

    @property
    def obj_key(self) -> tuple[str, str]:
        """Identity of the owning object, ('', '') for repo-level files."""
        return (self.type_code, self.name) if self.name and self.type_code else ("", "")


@dataclass
class Workspace:
    root: Path
    package: str = ""
    system: str = ""
    pulled_at: str = ""
    se80: bool = True
    match: str = ""
    types: list[str] = field(default_factory=list)
    owner: str = ""
    mode: str = MODE_ZSYNC
    subpackages: bool = True
    folder_logic: str = layout.FOLDER_FULL
    files: dict[str, Entry] = field(default_factory=dict)

    # ------------------------------------------------------------------ storage

    @property
    def path(self) -> Path:
        return self.root / MANIFEST_DIR / MANIFEST_FILE

    @property
    def exists(self) -> bool:
        return bool(self.files)

    @classmethod
    def load(cls, root: Path) -> Workspace:
        target = root / MANIFEST_DIR / MANIFEST_FILE
        if not target.is_file():
            return cls(root=root)
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise WorkspaceError(
                f"{target} is corrupt ({exc}) - delete it and run 'abap pull' again"
            ) from exc
        except OSError as exc:
            raise WorkspaceError(f"cannot read {target}: {exc}") from exc
        if not isinstance(raw, dict):
            raise WorkspaceError(f"{target} is not a manifest object")
        return cls(
            root=root,
            package=str(raw.get("package", "")),
            system=str(raw.get("system", "")),
            pulled_at=str(raw.get("pulled_at", "")),
            se80=bool(raw.get("se80", True)),
            match=str(raw.get("match", "")),
            types=[str(entry) for entry in raw.get("types", [])],
            owner=str(raw.get("owner", "")),
            # Manifests written before ZSYNC became the default hold ADT sources.
            mode=str(raw.get("mode", MODE_ADT)),
            subpackages=bool(raw.get("subpackages", True)),
            folder_logic=str(raw.get("folder_logic", layout.FOLDER_FULL)),
            files=_load_entries(raw.get("files", {}), target),
        )

    def save(self) -> None:
        """Write the manifest atomically.

        Push re-baselines after every object, so a crash mid-write must never
        leave a half-written manifest: the baseline is the only record of what
        has already been sent.
        """
        self.pulled_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        payload = json.dumps(
            {
                "version": MANIFEST_VERSION,
                "package": self.package,
                "system": self.system,
                "pulled_at": self.pulled_at,
                "se80": self.se80,
                "match": self.match,
                "types": self.types,
                "owner": self.owner,
                "mode": self.mode,
                "subpackages": self.subpackages,
                "folder_logic": self.folder_logic,
                "files": {
                    local: {
                        "name": entry.name,
                        "type": entry.type_code,
                        "uri": entry.uri,
                        "sha256": entry.sha256,
                        "canonical": entry.canonical,
                        "package": entry.package,
                    }
                    for local, entry in sorted(self.files.items())
                },
            },
            indent=2,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temp_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=f".{MANIFEST_FILE}.", suffix=".tmp"
            )
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as stream:
                    stream.write(payload + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, self.path)
            except BaseException:
                Path(temp_name).unlink(missing_ok=True)
                raise
        except OSError as exc:
            raise WorkspaceError(f"cannot write {self.path}: {exc}") from exc

    # ------------------------------------------------------------------ layout

    def local_path(self, obj: RepoObject) -> str:
        if self.se80:
            return f"{obj.kind.folder}/{obj.filename}"
        return f"src/{obj.filename}"

    def local_for(self, ref: layout.FileRef) -> str:
        """Where an archive entry lands on disk. Flat mirrors the archive exactly."""
        return ref.local if self.se80 else ref.canonical

    def canonical_for(self, local: str) -> str:
        """Archive path for a local file, including ones the manifest has not seen."""
        entry = self.files.get(local)
        if entry and entry.canonical:
            return entry.canonical
        if not self.se80:
            return local
        return f"src/{Path(local).name}"

    def package_of(self, local: str) -> str:
        """Package that owns a file.

        Tracked files carry it from the pull. A file you just created does not,
        so it is read back from the sub-folder it was dropped into - putting a
        new class next to its siblings is enough to place it correctly.
        """
        entry = self.files.get(local)
        if entry and entry.package:
            return entry.package
        parts = PurePosixPath(local).parts
        if not self.se80 and len(parts) > 2 and parts[0] == "src":
            return layout.package_for(
                [part.upper() for part in parts[1:-1]], self.package, self.folder_logic
            )
        return self.package

    def resolve(self, local: str) -> Path:
        """Absolute path of a tracked file, refusing anything outside the root.

        Local paths are derived from server-supplied object names and are read
        back from a manifest that a user can edit, so neither is trusted.
        """
        root = self.root.resolve()
        candidate = (root / local).resolve()
        if candidate != root and root not in candidate.parents:
            raise WorkspaceError(f"manifest entry '{local}' points outside {root}")
        return candidate

    def write(self, obj: RepoObject, text: str) -> str:
        local = self.local_path(obj)
        target = self.resolve(local)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise WorkspaceError(f"cannot write {target}: {exc}") from exc
        self.files[local] = Entry(
            name=obj.name, type_code=obj.type_code, uri=obj.uri, sha256=sha256(text)
        )
        return local

    def store(self, ref: layout.FileRef, data: bytes) -> str:
        """Write one archive entry and track it. Bytes, because MIME objects are binary."""
        local = self.local_for(ref)
        target = self.resolve(local)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except OSError as exc:
            raise WorkspaceError(f"cannot write {target}: {exc}") from exc
        self.files[local] = Entry(
            name=ref.obj_name,
            type_code=ref.obj_type,
            uri="",
            sha256=sha256(data),
            canonical=ref.canonical,
            package=ref.package,
        )
        return local

    def read(self, local: str) -> str:
        return read_source_file(self.resolve(local))

    def read_bytes(self, local: str) -> bytes:
        path = self.resolve(local)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise WorkspaceError(f"cannot read {path}: {exc}") from exc

    # ------------------------------------------------------------------ diffing

    def scan(self) -> tuple[list[str], list[str]]:
        """Return (modified, deleted) local paths against the pulled baseline."""
        modified, deleted = [], []
        for local, entry in sorted(self.files.items()):
            target = self.resolve(local)
            if not target.is_file():
                deleted.append(local)
            elif sha256(self.read_bytes(local)) != entry.sha256:
                modified.append(local)
        return modified, deleted

    def untracked(self) -> list[str]:
        """Files on disk that the manifest has never seen."""
        found = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root)
            # Skips .adt, .git and anything else not meant to reach SAP.
            if any(part.startswith(".") for part in relative.parts):
                continue
            local = relative.as_posix()
            if local in self.files:
                continue
            if self.mode == MODE_ZSYNC:
                if layout.parse_filename(path.name)[1]:
                    found.append(local)
            elif objects.type_for_file(local):
                found.append(local)
        return sorted(found)

    def identify(self, local: str) -> tuple[str, str]:
        """(type, name) of the object a file belongs to, '' when it is repo-level."""
        entry = self.files.get(local)
        if entry and entry.obj_key != ("", ""):
            return entry.obj_key
        name, obj_type = layout.parse_filename(Path(local).name)
        return obj_type, name

    def objects_for(self, locals_: list[str]) -> set[tuple[str, str]]:
        found = {self.identify(local) for local in locals_}
        return {key for key in found if key != ("", "")}

    def files_of_objects(self, keys: set[tuple[str, str]]) -> list[str]:
        """Every tracked file belonging to the given objects.

        abapGit deserialises an object from its whole file set, so sending only
        the edited member of a class would drop its test classes.
        """
        return sorted(local for local in self.files if self.identify(local) in keys)

    def object_for(self, local: str) -> RepoObject:
        try:
            entry = self.files[local]
        except KeyError as exc:
            raise WorkspaceError(f"'{local}' is not tracked by this workspace") from exc
        return RepoObject(name=entry.name, type_code=entry.type_code, uri=entry.uri)

    def writable(self, local: str) -> bool:
        if local not in self.files:
            return False
        # abapGit round-trips every type it can serialise, so nothing is read-only.
        if self.mode == MODE_ZSYNC:
            return True
        return objects.lookup(self.files[local].type_code).writable


def _load_entries(raw: object, source: Path) -> dict[str, Entry]:
    if not isinstance(raw, dict):
        raise WorkspaceError(f"{source} has no usable 'files' section")
    entries: dict[str, Entry] = {}
    for local, meta in raw.items():
        if not isinstance(meta, dict):
            raise WorkspaceError(f"{source}: entry '{local}' is malformed")
        try:
            entries[str(local)] = Entry(
                name=str(meta["name"]),
                type_code=str(meta["type"]),
                uri=str(meta["uri"]),
                sha256=str(meta["sha256"]),
                canonical=str(meta.get("canonical", "")),
                package=str(meta.get("package", "")),
            )
        except KeyError as exc:
            raise WorkspaceError(f"{source}: entry '{local}' is missing {exc}") from exc
    return entries
