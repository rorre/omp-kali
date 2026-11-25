from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.constants import WORKSPACE_ROOT
from .facts import ContainerFacts


def container_workdir(cwd: Path) -> Path:
    """Deterministic in-container path for one host directory."""
    return Path(WORKSPACE_ROOT) / (cwd.name or "root")


@dataclass(frozen=True)
class DesiredContainer:
    image: str
    workspace: str
    gui: bool
    publish_specs: tuple[str, ...]


def recreate_reason(facts: ContainerFacts, desired: DesiredContainer) -> str | None:
    """The first reason the observed container must be recreated, or None."""
    if not facts.exists:
        return None
    if facts.image and facts.image != desired.image:
        return f"Container image changed ({facts.image} -> {desired.image})."
    if facts.workspace != desired.workspace or facts.gui != desired.gui:
        return (
            f"Container workspace/GUI changed ({facts.workspace or '(none)'} -> "
            f"{desired.workspace}, gui={desired.gui})."
        )
    if set(facts.publish_specs) != set(desired.publish_specs):
        actual = ", ".join(sorted(facts.publish_specs)) or "(none)"
        wanted = ", ".join(sorted(desired.publish_specs)) or "(none)"
        return f"Published ports changed (actual: {actual} -> desired: {wanted})."
    return None
