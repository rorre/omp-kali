from __future__ import annotations

import os
import pwd
import shlex
import sys
from pathlib import Path

from ..core.constants import CONTAINER_HOME
from ..domain.desired import container_workdir
from .podman import Podman
from ..core.util import shjoin


class Shell:
    """Builds and runs the interactive shell command inside the container."""

    def __init__(self, podman: Podman) -> None:
        self.podman = podman
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.uid = os.getuid()
        self.gid = os.getgid()

    def enter(
        self,
        *,
        cwd: Path,
        gui: bool,
        pass_env: dict[str, str],
        omp_args: list[str],
        shell: bool,
        shell_command: str | None,
    ) -> int:
        command = (
            "exec bash"
            if shell
            else (
                "exec bash -c " + shlex.quote(shell_command)
                if shell_command
                else "exec " + shjoin([f"{CONTAINER_HOME}/.local/bin/omp", *omp_args])
            )
        )
        workdir = container_workdir(cwd)
        inner = f"cd {shlex.quote(str(workdir))} || exit 1\n{command}\n"
        env = {
            "HOME": CONTAINER_HOME,
            "USER": self.user,
            "SHELL": "/bin/bash",
            "PATH": f"{CONTAINER_HOME}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "TERM": os.environ.get("TERM", "xterm-256color"),
        }
        if gui:
            for key in (
                "DISPLAY",
                "WAYLAND_DISPLAY",
                "XDG_RUNTIME_DIR",
                "XDG_SESSION_TYPE",
                "DBUS_SESSION_BUS_ADDRESS",
            ):
                value = os.environ.get(key)
                if value:
                    env[key] = value
        env.update(pass_env)
        result = self.podman.exec(
            ["bash", "-c", inner],
            user=f"{self.uid}:{self.gid}",
            env=env,
            interactive=True,
            tty=sys.stdin.isatty(),
            workdir=str(workdir),
            capture=False,
            check=False,
            quiet=False,
        )
        return result.returncode
