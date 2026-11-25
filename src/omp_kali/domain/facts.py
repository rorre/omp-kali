from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..core.constants import GUI_LABEL, MANAGED_LABEL, WORKSPACE_LABEL


@dataclass(frozen=True)
class ContainerFacts:
    """What the managed container currently looks like, as observed from podman."""

    exists: bool = False
    running: bool = False
    image: str = ""
    labels: Mapping[str, str] = field(default_factory=dict)
    publish_specs: tuple[str, ...] = ()
    container_id: str = ""

    @property
    def managed(self) -> bool:
        return self.labels.get(MANAGED_LABEL) == "true"

    @property
    def workspace(self) -> str:
        return self.labels.get(WORKSPACE_LABEL, "")

    @property
    def gui(self) -> bool:
        return self.labels.get(GUI_LABEL) == "true"
