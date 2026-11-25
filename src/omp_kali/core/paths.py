from __future__ import annotations

import json
import os
from pathlib import Path

from .errors import OmpKaliError


def _xdg(env_name: str, fallback: str) -> Path:
    value = os.environ.get(env_name)
    if value:
        return Path(value).expanduser()
    return Path.home() / fallback


DATA_DIR = Path(
    os.environ.get("OMP_KALI_DATA_DIR", str(_xdg("XDG_DATA_HOME", ".local/share") / "omp-kali"))
).expanduser()
STATE_DIR = Path(
    os.environ.get("OMP_KALI_STATE_DIR", str(_xdg("XDG_STATE_HOME", ".local/state") / "omp-kali"))
).expanduser()

SKILLS_DIR = "skills"
AGENTS_DIR = "agents"
MCP_DIR = "mcp"
MCP_FILE = "mcp.json"
MCP_SERVERS_DIR = "servers"
PROVISIONING_DIR = "provisioning"
PROVISION_LOG = "provision.log"


def repo_root() -> Path:
    """Locate the omp-kali checkout that owns skills/agents/mcp."""
    override = os.environ.get("OMP_KALI_REPO")
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_dir():
            raise OmpKaliError(f"OMP_KALI_REPO is not a directory: {path}")
        return path

    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent

    cwd = Path.cwd().resolve()
    if (
        (cwd / "pyproject.toml").is_file()
        and (cwd / SKILLS_DIR).is_dir()
        and (cwd / AGENTS_DIR).is_dir()
    ):
        return cwd

    raise OmpKaliError(
        "Cannot locate the omp-kali repository (skills/, agents/, mcp/). "
        "Set OMP_KALI_REPO to the checkout path."
    )


def ensure_repo_assets() -> tuple[Path, Path, Path, Path]:
    """Create the modular asset directories/files in the repository if absent."""
    root = repo_root()
    skills = root / SKILLS_DIR
    agents = root / AGENTS_DIR
    mcp_dir = root / MCP_DIR
    mcp_file = mcp_dir / MCP_FILE
    skills.mkdir(parents=True, exist_ok=True)
    agents.mkdir(parents=True, exist_ok=True)
    (root / PROVISIONING_DIR).mkdir(parents=True, exist_ok=True)
    (mcp_dir / MCP_SERVERS_DIR).mkdir(parents=True, exist_ok=True)
    mcp_dir.mkdir(parents=True, exist_ok=True)
    if not mcp_file.exists():
        mcp_file.write_text(
            json.dumps(
                {
                    "$schema": (
                        "https://raw.githubusercontent.com/can1357/oh-my-pi/main/"
                        "packages/coding-agent/src/config/mcp-schema.json"
                    ),
                    "mcpServers": {},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return root, skills, agents, mcp_file
