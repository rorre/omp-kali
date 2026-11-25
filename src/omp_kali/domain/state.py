from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import OmpKaliError


@dataclass
class State:
    """The persisted configuration and provision state for one container."""

    version: int = 1
    workspace: str = ""
    gui: bool = False
    ports: list[dict[str, Any]] = field(default_factory=list)
    applied_publish: list[str] = field(default_factory=list)
    container_id: str = ""
    scripts: dict[str, str] = field(default_factory=dict)
    mcp: dict[str, str] = field(default_factory=dict)


class StatePersistence(ABC):
    """The seam between domain state and its storage format."""

    @abstractmethod
    def load_main(self) -> dict[str, Any]:
        """Return the raw main-state mapping, or an empty mapping when absent."""

    @abstractmethod
    def load_provision(self) -> dict[str, Any]:
        """Return the raw provision-state mapping, or an empty mapping when absent."""

    @abstractmethod
    def save_main(self, payload: dict[str, Any]) -> None:
        """Persist the main-state mapping."""

    @abstractmethod
    def save_provision(self, payload: dict[str, Any]) -> None:
        """Persist the provision-state mapping."""

    @abstractmethod
    def clear(self) -> None:
        """Remove all persisted state."""


class FileStatePersistence(StatePersistence):
    """JSON-file persistence adapter for the state seam."""

    def __init__(self, state_dir: Path) -> None:
        self.main_path = state_dir / "state.json"
        self.provision_path = state_dir / "provision-state.json"

    def load_main(self) -> dict[str, Any]:
        if not self.main_path.exists():
            return {}
        try:
            raw = json.loads(self.main_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OmpKaliError(f"Cannot read state file {self.main_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise OmpKaliError(f"State file {self.main_path} must contain a JSON object")
        if raw.get("version") != 1:
            raise OmpKaliError(
                f"Unsupported state version in {self.main_path}: {raw.get('version')!r}"
            )
        if not isinstance(raw.get("ports"), list):
            raise OmpKaliError(f"State file {self.main_path}: ports must be a list")
        if not isinstance(raw.get("applied_publish"), list):
            raise OmpKaliError(f"State file {self.main_path}: applied_publish must be a list")
        return raw

    def load_provision(self) -> dict[str, Any]:
        if not self.provision_path.exists():
            return {}
        try:
            raw = json.loads(self.provision_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise OmpKaliError(
                f"Cannot read provisioning state {self.provision_path}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise OmpKaliError(
                f"Provisioning state {self.provision_path} must contain an object"
            )
        if raw.get("version") != 1:
            return {}
        return raw

    def save_main(self, payload: dict[str, Any]) -> None:
        self.main_path.parent.mkdir(parents=True, exist_ok=True)
        self._write(self.main_path, payload)

    def save_provision(self, payload: dict[str, Any]) -> None:
        self.provision_path.parent.mkdir(parents=True, exist_ok=True)
        self._write(self.provision_path, payload)

    def clear(self) -> None:
        self.main_path.unlink(missing_ok=True)
        self.provision_path.unlink(missing_ok=True)

    @staticmethod
    def _write(path: Path, payload: dict[str, Any]) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)


class StateStore:
    """Loads and saves the main state and the provision state through the persistence seam."""

    def __init__(self, persistence: StatePersistence) -> None:
        self.persistence = persistence

    def load(self) -> State:
        main = self.persistence.load_main()
        provision = self.persistence.load_provision()
        return State(
            version=main.get("version", 1),
            workspace=main.get("workspace", ""),
            gui=main.get("gui", False),
            ports=main.get("ports", []),
            applied_publish=main.get("applied_publish", []),
            container_id=provision.get("container_id", ""),
            scripts=provision.get("scripts", {}),
            mcp=provision.get("mcp", {}),
        )

    def save(self, state: State) -> None:
        self.persistence.save_main(
            {
                "version": state.version,
                "workspace": state.workspace,
                "gui": state.gui,
                "ports": state.ports,
                "applied_publish": state.applied_publish,
            }
        )
        self.persistence.save_provision(
            {
                "version": state.version,
                "container_id": state.container_id,
                "scripts": state.scripts,
                "mcp": state.mcp,
            }
        )

    def clear(self) -> None:
        self.persistence.clear()
