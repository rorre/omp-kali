from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from ..core.errors import OmpKaliError
from ..core.util import parse_port

DIRECTIONS = ("host-to-container", "container-to-host")
ABBR = {"host-to-container": "h2c", "container-to-host": "c2h"}
DEFAULT_BIND = "127.0.0.1"
RELAY_TARGET = "host.containers.internal"


def _validate_bind(bind: str) -> str:
    try:
        address = ipaddress.ip_address(bind)
    except ValueError as exc:
        raise OmpKaliError(f"--bind must be a literal IPv4 address, got {bind!r}") from exc
    if address.version != 4:
        raise OmpKaliError("--bind currently supports IPv4 addresses only")
    return str(address)


@dataclass(frozen=True)
class PortMapping:
    direction: str
    host: int
    container: int
    bind: str = DEFAULT_BIND
    target_host: str = RELAY_TARGET
    approved_at: str = ""

    def __post_init__(self) -> None:
        if self.direction not in DIRECTIONS:
            raise OmpKaliError(f"Unknown port direction: {self.direction!r}")
        object.__setattr__(self, "host", parse_port(self.host))
        object.__setattr__(self, "container", parse_port(self.container))
        object.__setattr__(self, "bind", _validate_bind(self.bind))
        if not self.target_host:
            raise OmpKaliError("target_host cannot be empty")

    @property
    def id(self) -> str:
        return f"{ABBR[self.direction]}:{self.bind}:{self.host}:{self.container}"

    @property
    def method(self) -> str:
        return "relay" if self.direction == "host-to-container" else "publish"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "PortMapping":
        try:
            return cls(
                direction=data["direction"],
                host=data["host"],
                container=data["container"],
                bind=data.get("bind", DEFAULT_BIND),
                target_host=data.get("target_host", RELAY_TARGET),
                approved_at=data.get("approved_at", ""),
            )
        except (KeyError, TypeError) as exc:
            raise OmpKaliError(f"Invalid port mapping in state: {data!r}") from exc

    def publish_spec(self) -> str | None:
        if self.direction != "container-to-host":
            return None
        return f"{self.bind}:{self.host}:{self.container}"

    def relay_payload(self) -> dict[str, Any] | None:
        if self.direction != "host-to-container":
            return None
        return {
            "id": self.id,
            "listen_host": self.bind,
            "container": self.container,
            "target_host": self.target_host,
            "target_port": self.host,
        }

    def permission_message(self) -> str:
        if self.direction == "host-to-container":
            return (
                f"Allow the host service at {self.target_host}:{self.host} to be reachable "
                f"inside the container at {self.bind}:{self.container}?\n"
                "This starts a TCP relay inside the container; the container can reach that "
                "host endpoint for as long as the container runs."
            )
        return (
            f"Publish container port {self.container}/tcp on the host at {self.bind}:{self.host}?\n"
            "This exposes the container service to the host and requires recreating the "
            "container (its home directory and sessions are preserved)."
        )

    def describe(self) -> str:
        if self.direction == "host-to-container":
            return f"{self.target_host}:{self.host} -> {self.bind}:{self.container} (in container)"
        return f"{self.bind}:{self.container} (in container) -> {self.bind}:{self.host} (on host)"


def approved_now(mapping: PortMapping) -> PortMapping:
    return PortMapping(
        direction=mapping.direction,
        host=mapping.host,
        container=mapping.container,
        bind=mapping.bind,
        target_host=mapping.target_host,
        approved_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def mappings_from_state(state: dict[str, Any]) -> list[PortMapping]:
    result: list[PortMapping] = []
    for entry in state.get("ports", []):
        if not isinstance(entry, dict):
            raise OmpKaliError(f"Invalid port entry in state: {entry!r}")
        result.append(PortMapping.from_json(entry))
    return result


def mappings_to_state(mappings: Iterable[PortMapping]) -> list[dict[str, Any]]:
    return [mapping.to_json() for mapping in mappings]


def desired_publish_specs(mappings: Iterable[PortMapping]) -> list[str]:
    specs = [spec for mapping in mappings if (spec := mapping.publish_spec())]
    return sorted(specs)


def relay_payloads(mappings: Iterable[PortMapping]) -> list[dict[str, Any]]:
    return [payload for mapping in mappings if (payload := mapping.relay_payload())]


def find_conflict(mappings: Iterable[PortMapping], candidate: PortMapping) -> PortMapping | None:
    for existing in mappings:
        if existing.id == candidate.id:
            return existing
        if existing.direction == "container-to-host" and candidate.direction == "container-to-host":
            if existing.bind == candidate.bind and existing.host == candidate.host:
                return existing
        if existing.direction == "host-to-container" and candidate.direction == "host-to-container":
            if existing.bind == candidate.bind and existing.container == candidate.container:
                return existing
    return None
