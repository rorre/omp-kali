from __future__ import annotations

import json
import os
import pwd
import shlex
import subprocess
import time
from typing import Any

from ..core import paths
from ..core.constants import CONTAINER_HOME
from ..core.errors import OmpKaliError
from ..core.constants import CONTAINER, PODMAN, VOLUME
from .podman import Podman
from ..domain.ports import PortMapping, relay_payloads
from ..core.util import eprint


class Execution:
    """Runs commands inside the container and manages the port relay."""

    def __init__(self, podman: Podman) -> None:
        self.podman = podman
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.uid = os.getuid()
        self.gid = os.getgid()

    def podman_exec(
        self,
        args: list[str],
        *,
        check: bool = True,
        detach: bool = False,
        stream: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return self.podman.exec(
            args,
            user=f"{self.uid}:{self.gid}",
            env={"HOME": CONTAINER_HOME, "USER": self.user},
            detach=detach,
            stream=stream,
            check=check,
        )

    def root_exec(
        self,
        args: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
        stream: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return self.podman.exec(
            args,
            user="0",
            env=env,
            detach=False,
            stream=stream,
            check=check,
            quiet=True,
        )

    def check_kali(self) -> str:
        if self.podman.dry_run:
            return "Kali (dry run)"
        result = self.root_exec(["cat", "/etc/os-release"], check=False)
        text = (result.stdout or "").strip()
        if result.returncode != 0 or ("ID=kali" not in text and "Kali" not in text):
            raise OmpKaliError(f"Container {CONTAINER} is not a Kali Linux container.")
        for line in text.splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
        return "Kali Linux"

    def relay_paths(self) -> tuple[str, str, str, str, str]:
        base = f"{CONTAINER_HOME}/.omp-kali"
        return (
            f"{base}/relay.py",
            f"{base}/relay.json",
            f"{base}/relay.pid",
            f"{base}/relay.status.json",
            f"{base}/relay.log",
        )

    def purge_host_state(self) -> None:
        """Remove the host-side relay configuration."""
        (paths.DATA_DIR / "relay.json").unlink(missing_ok=True)

    def _relay_alive(self, pid_path: str) -> bool:
        script = (
            f'pid=$(cat {shlex.quote(pid_path)} 2>/dev/null || true); '
            '[ -n "$pid" ] && kill -0 "$pid" 2>/dev/null'
        )
        return self.podman_exec(["sh", "-c", script], check=False).returncode == 0

    def restart_relays(self, mappings: list[PortMapping]) -> None:
        if not self.podman.observe().exists or not self.podman.observe().running:
            return
        relay, config_path, pid_path, status_path, log_path = self.relay_paths()
        payloads = relay_payloads(mappings)
        desired = {"mappings": payloads}
        current: dict[str, Any] | None = None
        result = self.podman_exec(["cat", config_path], check=False)
        if result.returncode == 0 and (result.stdout or "").strip():
            try:
                current = json.loads(result.stdout)
            except json.JSONDecodeError:
                current = None
        if not payloads:
            if current is not None or self._relay_alive(pid_path):
                script = (
                    f'if [ -f {shlex.quote(pid_path)} ]; then '
                    f'pid=$(cat {shlex.quote(pid_path)} 2>/dev/null || true); '
                    '[ -n "$pid" ] && kill "$pid" 2>/dev/null || true; '
                    f'rm -f {shlex.quote(pid_path)}; fi; '
                    f'rm -f {shlex.quote(status_path)}'
                )
                self.podman_exec(["sh", "-c", script], check=False)
            return
        if current == desired and self._relay_alive(pid_path):
            return
        kill_script = (
            f'if [ -f {shlex.quote(pid_path)} ]; then '
            f'pid=$(cat {shlex.quote(pid_path)} 2>/dev/null || true); '
            '[ -n "$pid" ] && kill "$pid" 2>/dev/null || true; '
            f'rm -f {shlex.quote(pid_path)}; fi; '
            f'rm -f {shlex.quote(status_path)}'
        )
        self.podman_exec(["sh", "-c", kill_script], check=False)
        time.sleep(0.3)
        host_config = paths.DATA_DIR / "relay.json"
        host_config.parent.mkdir(parents=True, exist_ok=True)
        host_config.write_text(json.dumps(desired, indent=2) + "\n", encoding="utf-8")
        self.podman.run([PODMAN, "cp", str(host_config), f"{CONTAINER}:{config_path}"], capture=True)
        self.podman.run(
            [PODMAN, "exec", "-u", "0", CONTAINER, "chown", f"{self.uid}:{self.gid}", config_path],
            capture=True,
            check=False,
        )
        self.podman_exec(
            [
                "python3",
                relay,
                "--config",
                config_path,
                "--pid-file",
                pid_path,
                "--log-file",
                log_path,
                "--status-file",
                status_path,
            ],
            detach=True,
        )
        time.sleep(0.4)
        status = self.podman_exec(["cat", status_path], check=False)
        raw = (status.stdout or "").strip()
        if not raw:
            eprint("warning: relay did not report readiness; see ~/.omp-kali/relay.log in the container")
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        for listener in data.get("listeners", []):
            eprint(f"relay: {listener['listen']} -> {listener['target']}")
        for error in data.get("errors", []):
            eprint(f"relay warning: {error}")

    def status(self) -> dict[str, Any]:
        facts = self.podman.observe()
        info: dict[str, Any] = {
            "exists": facts.exists,
            "running": facts.running,
            "managed": facts.managed,
            "image": facts.image,
            "kali": "",
            "home": CONTAINER_HOME,
            "volume": VOLUME,
            "workspace": facts.workspace,
            "gui": facts.gui,
            "omp_version": "",
        }
        if not facts.exists or not facts.running:
            return info
        info["kali"] = self.check_kali()
        version = self.podman_exec([f"{CONTAINER_HOME}/.local/bin/omp", "--version"], check=False)
        if version.stdout:
            info["omp_version"] = version.stdout.strip().splitlines()[0]
        return info
