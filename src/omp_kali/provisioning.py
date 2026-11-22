from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import OmpKaliError

SCRIPT_DIR = "provisioning"
STATE_FILE = "provision-state.json"


@dataclass(frozen=True)
class ProvisionScript:
    name: str
    path: Path
    sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_scripts(repo_root: Path) -> list[ProvisionScript]:
    directory = repo_root / SCRIPT_DIR
    if not directory.is_dir():
        return []
    return [
        ProvisionScript(path.name, path, sha256_file(path))
        for path in sorted(directory.glob("*.sh"))
        if path.is_file()
    ]


def state_path(state_dir: Path) -> Path:
    return Path(state_dir) / STATE_FILE


def default_state(container_id: str) -> dict[str, Any]:
    return {"version": 1, "container_id": container_id, "mcp": {}, "scripts": {}}


def load_state(state_dir: Path, container_id: str) -> dict[str, Any]:
    path = state_path(state_dir)
    if not path.exists():
        return default_state(container_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OmpKaliError(f"Cannot read provisioning state {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise OmpKaliError(f"Provisioning state {path} must contain an object")
    state = default_state(container_id)
    state.update(raw)
    if not isinstance(state.get("mcp"), dict):
        state["mcp"] = {}
    if not isinstance(state.get("scripts"), dict):
        state["scripts"] = {}
    if state.get("container_id") != container_id:
        state = default_state(container_id)
    if state.get("version") != 1:
        state = default_state(container_id)
    return state


def save_state(state_dir: Path, state: dict[str, Any]) -> None:
    path = state_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
