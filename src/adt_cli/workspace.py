"""Local workspace: where objects land on disk and what changed since the pull.

The manifest records the ADT URI and a hash per object, which is what lets push
send only what you actually edited without needing git.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from adt_cli import objects
from adt_cli.repository import RepoObject

MANIFEST_DIR = ".adt"
MANIFEST_FILE = "manifest.json"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Entry:
    name: str
    type_code: str
    uri: str
    sha256: str


@dataclass
class Workspace:
    root: Path
    package: str = ""
    system: str = ""
    pulled_at: str = ""
    se80: bool = False
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
        raw = json.loads(target.read_text(encoding="utf-8"))
        return cls(
            root=root,
            package=raw.get("package", ""),
            system=raw.get("system", ""),
            pulled_at=raw.get("pulled_at", ""),
            se80=raw.get("se80", False),
            files={
                local: Entry(
                    name=meta["name"],
                    type_code=meta["type"],
                    uri=meta["uri"],
                    sha256=meta["sha256"],
                )
                for local, meta in raw.get("files", {}).items()
            },
        )

    def save(self) -> None:
        self.pulled_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "package": self.package,
                    "system": self.system,
                    "pulled_at": self.pulled_at,
                    "se80": self.se80,
                    "files": {
                        local: {
                            "name": entry.name,
                            "type": entry.type_code,
                            "uri": entry.uri,
                            "sha256": entry.sha256,
                        }
                        for local, entry in sorted(self.files.items())
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    # ------------------------------------------------------------------ layout

    def local_path(self, obj: RepoObject) -> str:
        if self.se80:
            return f"{obj.kind.folder}/{obj.filename}"
        return f"src/{obj.filename}"

    def write(self, obj: RepoObject, text: str) -> str:
        local = self.local_path(obj)
        target = self.root / local
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        self.files[local] = Entry(
            name=obj.name, type_code=obj.type_code, uri=obj.uri, sha256=sha256(text)
        )
        return local

    # ------------------------------------------------------------------ diffing

    def scan(self) -> tuple[list[str], list[str]]:
        """Return (modified, deleted) local paths against the pulled baseline."""
        modified, deleted = [], []
        for local, entry in sorted(self.files.items()):
            target = self.root / local
            if not target.is_file():
                deleted.append(local)
            elif sha256(target.read_text(encoding="utf-8")) != entry.sha256:
                modified.append(local)
        return modified, deleted

    def object_for(self, local: str) -> RepoObject:
        entry = self.files[local]
        return RepoObject(name=entry.name, type_code=entry.type_code, uri=entry.uri)

    def writable(self, local: str) -> bool:
        return objects.lookup(self.files[local].type_code).writable
