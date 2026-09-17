"""Local workspace: where objects land on disk and what changed since the pull.

The manifest records the ADT URI and a hash per object, which is what lets push
send only what you actually edited without needing git.

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
from pathlib import Path

from adt_cli import objects
from adt_cli.errors import WorkspaceError
from adt_cli.repository import RepoObject

MANIFEST_DIR = ".adt"
MANIFEST_FILE = "manifest.json"
MANIFEST_VERSION = 1


def canonical(text: str) -> str:
    """The form SAP actually stores.

    ADT serves CRLF and drops a trailing newline on write, so comparing raw text
    would report every pushed file as changed on the server for ever after. An
    editor adding a final newline is invisible for the same reason.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")


def sha256(text: str) -> str:
    return hashlib.sha256(canonical(text).encode("utf-8")).hexdigest()


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
    # URI segment of the editable text this file holds, '' for the main source.
    part: str = ""


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
                "files": {
                    local: {
                        "name": entry.name,
                        "type": entry.type_code,
                        "uri": entry.uri,
                        "sha256": entry.sha256,
                        "part": entry.part,
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

    def local_path(self, obj: RepoObject, part: objects.Part | None = None) -> str:
        filename = obj.filename_for(part.suffix) if part else obj.filename
        if self.se80:
            return f"{obj.kind.folder}/{obj.folder_name}/{filename}"
        return f"src/{filename}"

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

    def write(self, obj: RepoObject, text: str, part: objects.Part | None = None) -> str:
        local = self.local_path(obj, part)
        target = self.resolve(local)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise WorkspaceError(f"cannot write {target}: {exc}") from exc
        self.files[local] = Entry(
            name=obj.name,
            type_code=obj.type_code,
            uri=obj.uri,
            sha256=sha256(text),
            part=part.path if part else "",
        )
        return local

    def read(self, local: str) -> str:
        return read_source_file(self.resolve(local))

    # ------------------------------------------------------------------ diffing

    def scan(self) -> tuple[list[str], list[str]]:
        """Return (modified, deleted) local paths against the pulled baseline."""
        modified, deleted = [], []
        for local, entry in sorted(self.files.items()):
            target = self.resolve(local)
            if not target.is_file():
                deleted.append(local)
            elif sha256(read_source_file(target)) != entry.sha256:
                modified.append(local)
        return modified, deleted

    def untracked(self) -> list[str]:
        """Source files on disk that the manifest has never seen."""
        found = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root)
            # Skips .adt, .git and anything else not meant to reach SAP.
            if any(part.startswith(".") for part in relative.parts):
                continue
            local = relative.as_posix()
            if local not in self.files and objects.type_for_file(local):
                found.append(local)
        return sorted(found)

    def object_for(self, local: str) -> RepoObject:
        try:
            entry = self.files[local]
        except KeyError as exc:
            raise WorkspaceError(f"'{local}' is not tracked by this workspace") from exc
        return RepoObject(name=entry.name, type_code=entry.type_code, uri=entry.uri)

    def part_for(self, local: str) -> objects.Part | None:
        """Which editable text a file writes back to.

        The file name decides it: every part has its own suffix, and an object
        part's URI segment is empty, so the recorded path cannot tell an object
        part apart from one that was never recorded.
        """
        found = objects.part_for_file(local)
        return found[1] if found else None

    def writable(self, local: str) -> bool:
        return local in self.files and objects.lookup(self.files[local].type_code).writable


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
                part=str(meta.get("part", "")),
            )
        except KeyError as exc:
            raise WorkspaceError(f"{source}: entry '{local}' is missing {exc}") from exc
    return entries
