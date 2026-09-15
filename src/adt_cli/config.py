"""Connection profiles and credential handling.

Profiles live in ~/.abap-adt/config.json so the CLI works from any directory and
is not tied to any particular workspace.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import keyring
import keyring.backends.fail
import keyring.errors

from adt_cli.errors import ConfigError

CONFIG_HOME = Path.home() / ".abap-adt" / "config.json"
KEYRING_SERVICE = "abap-adt-cli"
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

__all__ = [
    "CONFIG_HOME",
    "ConfigError",
    "System",
    "default_system",
    "keychain_delete",
    "keychain_get",
    "keychain_store",
    "keyring_available",
    "list_systems",
    "password_for",
    "remove_system",
    "resolve",
    "save_system",
    "set_default",
]


@dataclass(frozen=True)
class System:
    name: str
    host: str
    user: str
    client: str
    verify_tls: bool = True
    description: str = ""

    @property
    def keychain_account(self) -> str:
        return f"{self.name}:{self.user}"

    def describe(self) -> str:
        return f"{self.user}@{self.host} client {self.client}"


def validate_name(name: str) -> str:
    cleaned = name.strip()
    if not _NAME.match(cleaned):
        raise ConfigError(
            f"'{name}' is not a usable system name - use letters, digits, '.', '-' or '_'"
        )
    return cleaned


def validate_host(host: str) -> str:
    """Only absolute http(s) URLs work as an httpx base_url."""
    cleaned = host.strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(
            f"'{host}' is not a host URL - expected something like https://sap.example.com:44300"
        )
    return cleaned


def validate_client(client: str) -> str:
    cleaned = client.strip()
    if not cleaned.isdigit() or len(cleaned) > 3:
        raise ConfigError(f"'{client}' is not a SAP client - expected up to three digits")
    return cleaned.zfill(3)


def _read_raw() -> dict:
    if not CONFIG_HOME.is_file():
        return {}
    try:
        raw = json.loads(CONFIG_HOME.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{CONFIG_HOME} is not valid JSON: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"cannot read {CONFIG_HOME}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"{CONFIG_HOME} must contain a JSON object")
    return raw


def list_systems() -> dict[str, dict]:
    systems = _read_raw().get("systems", {})
    if not isinstance(systems, dict):
        raise ConfigError(f"{CONFIG_HOME}: 'systems' must be an object")
    return {name: profile for name, profile in systems.items() if isinstance(profile, dict)}


def default_system() -> str:
    raw = _read_raw()
    systems = raw.get("systems", {})
    if raw.get("default_system"):
        return str(raw["default_system"])
    return next(iter(systems)) if len(systems) == 1 else ""


def save_system(system: System, make_default: bool = False) -> None:
    raw = _read_raw()
    systems = raw.setdefault("systems", {})
    systems[validate_name(system.name)] = {
        "host": validate_host(system.host),
        "user": system.user.strip(),
        "client": validate_client(system.client),
        "insecure": not system.verify_tls,
        "description": system.description,
    }
    if make_default or not raw.get("default_system"):
        raw["default_system"] = system.name

    _write_raw(raw)


def set_default(name: str) -> None:
    raw = _read_raw()
    if name not in raw.get("systems", {}):
        raise ConfigError(
            f"unknown system '{name}', available: {', '.join(sorted(raw.get('systems', {})))}"
        )
    raw["default_system"] = name
    _write_raw(raw)


def remove_system(name: str) -> None:
    raw = _read_raw()
    if name not in raw.get("systems", {}):
        raise ConfigError(f"unknown system '{name}'")
    del raw["systems"][name]
    if raw.get("default_system") == name:
        raw["default_system"] = next(iter(raw["systems"]), "")
    _write_raw(raw)


def _write_raw(raw: dict) -> None:
    try:
        CONFIG_HOME.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_HOME.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot write {CONFIG_HOME}: {exc}") from exc
    # Best effort; Windows ACLs don't map to POSIX permission bits.
    with contextlib.suppress(OSError):
        CONFIG_HOME.chmod(0o600)


def resolve(
    name: str = "",
    host: str = "",
    user: str = "",
    client: str = "",
    insecure: bool = False,
) -> System:
    """Build a System from config, environment and explicit flags (flags win)."""
    systems = list_systems()
    chosen = name or os.environ.get("ADT_SYSTEM", "") or default_system()

    # Folded in before the guard below so a fully specified environment works
    # with no config file at all, which is the CI case.
    host = host or os.environ.get("ADT_HOST", "")
    user = user or os.environ.get("ADT_USER", "")
    client = client or os.environ.get("ADT_CLIENT", "")

    if not (host and user and client):
        if not systems:
            raise ConfigError(f"no systems configured. Run 'abap init' or create {CONFIG_HOME}.")
        if not chosen:
            raise ConfigError(
                "several systems configured, pick one with --system: "
                + ", ".join(sorted(systems))
            )
        if chosen not in systems:
            raise ConfigError(
                f"unknown system '{chosen}', available: {', '.join(sorted(systems))}"
            )

    profile = systems.get(chosen, {})

    resolved_host = host or str(profile.get("host", ""))
    resolved_user = user or str(profile.get("user", ""))
    resolved_client = client or str(profile.get("client", ""))

    missing = [
        label
        for label, value in (
            ("host", resolved_host),
            ("user", resolved_user),
            ("client", resolved_client),
        )
        if not value
    ]
    if missing:
        raise ConfigError(f"missing {', '.join(missing)} for system '{chosen or 'ad hoc'}'")

    return System(
        name=chosen or "ad-hoc",
        host=validate_host(resolved_host),
        user=resolved_user,
        client=validate_client(resolved_client),
        verify_tls=not (insecure or profile.get("insecure", False)),
        description=str(profile.get("description", "")),
    )


# --------------------------------------------------------------------------- secrets
#
# keyring picks the native store per platform: Keychain on macOS, Credential
# Manager on Windows, Secret Service on Linux.


def keyring_available() -> bool:
    try:
        return not isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring)
    except keyring.errors.KeyringError:
        return False


def keychain_get(account: str) -> str:
    """Read a stored password, or '' when there is none or no backend."""
    try:
        return keyring.get_password(KEYRING_SERVICE, account) or ""
    except keyring.errors.KeyringError:
        return ""


def keychain_store(account: str, password: str) -> bool:
    try:
        keyring.set_password(KEYRING_SERVICE, account, password)
        return True
    except keyring.errors.KeyringError:
        return False


def keychain_delete(account: str) -> bool:
    try:
        keyring.delete_password(KEYRING_SERVICE, account)
        return True
    except keyring.errors.KeyringError:
        return False


def password_for(system: System) -> str:
    """$ABAP_PASSWORD, then the OS credential store. Empty means 'ask the user'."""
    return os.environ.get("ABAP_PASSWORD", "") or keychain_get(system.keychain_account)
