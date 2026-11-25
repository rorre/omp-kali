from __future__ import annotations

import os
import pwd
from pathlib import Path

from ..core.constants import (
    CONTAINER,
    CONTAINER_HOME,
    GUI_LABEL,
    MANAGED_LABEL,
    PODMAN,
    VOLUME,
    WORKSPACE_LABEL,
)
from ..domain.desired import container_workdir


def _gui_mounts() -> list[str]:
    mounts: list[str] = []
    x11 = Path("/tmp/.X11-unix") / f"X{os.environ.get('DISPLAY', ':0').lstrip(':')}"
    if x11.exists():
        mounts.extend(["--volume", f"{x11}:{x11}:rw"])
    xauth = os.environ.get("XAUTHORITY")
    if xauth and Path(xauth).exists():
        mounts.extend(["--volume", f"{xauth}:/run/omp-kali/Xauthority:ro"])
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        for name in (os.environ.get("WAYLAND_DISPLAY"), "bus"):
            if not name:
                continue
            source = Path(runtime) / name
            if source.exists():
                mounts.extend(["--volume", f"{source}:{source}:rw"])
    return mounts


def create_command(cwd: Path, gui: bool, publish_specs: list[str], repo: Path, image: str) -> list[str]:
    """Build the raw podman create-and-start command."""
    uid = os.getuid()
    cmd = [
        PODMAN,
        "run",
        "-d",
        "--name",
        CONTAINER,
        "--hostname",
        CONTAINER,
        "--label",
        f"{MANAGED_LABEL}=true",
        "--label",
        f"{WORKSPACE_LABEL}={cwd}",
        "--label",
        f"{GUI_LABEL}={'true' if gui else 'false'}",
        "--userns=keep-id",
        "--security-opt",
        "label=disable",
        "--env",
        f"HOME={CONTAINER_HOME}",
        "--env",
        f"USER={pwd.getpwuid(uid).pw_name}",
        "--env",
        "LANG=C.UTF-8",
        "--volume",
        f"{VOLUME}:{CONTAINER_HOME}:rw",
        "--volume",
        f"{cwd}:{container_workdir(cwd)}:rw",
        "--volume",
        f"{repo / 'skills'}:{CONTAINER_HOME}/.omp/agent/skills:rw",
        "--volume",
        f"{repo / 'agents'}:{CONTAINER_HOME}/.omp/agent/agents:rw",
        "--volume",
        f"{repo / 'mcp' / 'mcp.json'}:{CONTAINER_HOME}/.omp/agent/mcp.json:rw",
    ]
    for spec in publish_specs:
        cmd.extend(["--publish", spec])
    if gui:
        cmd.extend(_gui_mounts())
    cmd.extend([image, "sleep", "infinity"])
    return cmd
