from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.errors import OmpKaliError

SCHEMA_URL = (
    "https://raw.githubusercontent.com/can1357/oh-my-pi/main/"
    "packages/coding-agent/src/config/mcp-schema.json"
)
NAME_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
TOP_LEVEL_KEYS = {"$schema", "name", "description", "enabled", "install", "server"}
INSTALL_KEYS = {"apt", "pip", "npm", "commands"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class McpManifest:
    name: str
    server: dict[str, Any]
    install: dict[str, tuple[str, ...]]
    enabled: bool
    description: str
    path: Path

    @property
    def install_hash(self) -> str:
        return _sha256(self.install) if self.has_install else ""

    @property
    def has_install(self) -> bool:
        return any(self.install.values())

    def install_summary(self) -> str:
        parts = []
        for key in ("apt", "pip", "npm", "commands"):
            values = self.install.get(key, ())
            if values:
                parts.append(f"{key}={' '.join(values) if key != 'commands' else f'{len(values)} command(s)'}")
        return "; ".join(parts) if parts else "no install steps"

    def server_summary(self) -> str:
        if "url" in self.server:
            return f"{self.server.get('type', 'http')} {self.server['url']}"
        command = self.server.get("command")
        args = self.server.get("args", [])
        if command:
            rendered = " ".join(str(part) for part in args)
            return f"stdio {command} {rendered}".strip()
        return "server"


def _string_list(value: Any, *, where: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise OmpKaliError(f"{where} must be a list of strings")
    return tuple(value)


def load_manifests(servers_dir: Path) -> list[McpManifest]:
    """Load mcp/servers/*.json manifests. Missing directory means no manifests."""
    if not servers_dir.is_dir():
        return []
    manifests: list[McpManifest] = []
    seen: set[str] = set()
    for path in sorted(servers_dir.glob("*.json")):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OmpKaliError(f"MCP manifest {path}: invalid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise OmpKaliError(f"MCP manifest {path}: top level must be an object")

        unknown = set(raw) - TOP_LEVEL_KEYS
        if unknown:
            raise OmpKaliError(f"MCP manifest {path}: unknown keys: {', '.join(sorted(unknown))}")

        name = raw.get("name")
        if not isinstance(name, str) or not name or not NAME_RE.fullmatch(name):
            raise OmpKaliError(f"MCP manifest {path}: name must match {NAME_RE.pattern}")
        if name in seen:
            raise OmpKaliError(f"MCP manifest {path}: duplicate server name {name!r}")
        seen.add(name)

        server = raw.get("server")
        if not isinstance(server, dict):
            raise OmpKaliError(f"MCP manifest {path}: server must be an object")
        if "command" not in server and "url" not in server:
            raise OmpKaliError(f"MCP manifest {path}: server needs command or url")

        install_raw = raw.get("install", {})
        if not isinstance(install_raw, dict):
            raise OmpKaliError(f"MCP manifest {path}: install must be an object")
        unknown_install = set(install_raw) - INSTALL_KEYS
        if unknown_install:
            raise OmpKaliError(
                f"MCP manifest {path}: unknown install keys: {', '.join(sorted(unknown_install))}"
            )
        install = {
            key: _string_list(install_raw.get(key), where=f"{path}: install.{key}")
            for key in INSTALL_KEYS
        }

        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise OmpKaliError(f"MCP manifest {path}: enabled must be a boolean")
        description = raw.get("description", "")
        if not isinstance(description, str):
            raise OmpKaliError(f"MCP manifest {path}: description must be a string")

        manifests.append(
            McpManifest(
                name=name,
                server=server,
                install=install,
                enabled=enabled,
                description=description,
                path=path,
            )
        )
    return manifests


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"$schema": SCHEMA_URL, "mcpServers": {}}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OmpKaliError(f"MCP config {path}: invalid JSON: {exc}") from exc
    if not isinstance(config, dict):
        raise OmpKaliError(f"MCP config {path}: top level must be an object")
    if not isinstance(config.get("mcpServers", {}), dict):
        raise OmpKaliError(f"MCP config {path}: mcpServers must be an object")
    return config


def merge_servers(config: dict[str, Any], manifests: list[McpManifest]) -> dict[str, Any]:
    merged = copy.deepcopy(config)
    servers = merged.setdefault("mcpServers", {})
    for manifest in manifests:
        if manifest.enabled:
            servers[manifest.name] = copy.deepcopy(manifest.server)
    return merged


def write_config(path: Path, config: dict[str, Any]) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(config, indent=2) + "\n"
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == payload:
        return False
    path.write_text(payload, encoding="utf-8")
    return True
