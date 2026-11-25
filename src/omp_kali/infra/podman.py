from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Protocol

from ..core.constants import CONTAINER, MANAGED_LABEL, PODMAN
from ..core.errors import OmpKaliError
from ..core.util import eprint, shjoin
from ..domain.facts import ContainerFacts


class Podman(Protocol):
    """The seam between container decisions and podman execution."""

    @property
    def dry_run(self) -> bool: ...

    def note(self, message: str) -> None: ...

    def log(self, text: str) -> None: ...

    def open_log(self, path: Path) -> None: ...

    def close_log(self) -> None: ...

    def observe(self) -> ContainerFacts: ...

    def exec(
        self,
        args: list[str],
        *,
        user: str | None = None,
        env: dict[str, str] | None = None,
        detach: bool = False,
        stream: bool = False,
        interactive: bool = False,
        tty: bool = False,
        workdir: str | None = None,
        check: bool = True,
        capture: bool | None = None,
        quiet: bool | None = None,
    ) -> subprocess.CompletedProcess[str]: ...

    def run(
        self,
        argv: list[str],
        *,
        capture: bool = False,
        check: bool = True,
        quiet: bool = False,
        input_text: str | None = None,
        stream: bool = False,
    ) -> subprocess.CompletedProcess[str]: ...

    def volume_exists(self, name: str) -> bool: ...

    def image_exists(self, tag: str) -> bool: ...

    def require_tools(self) -> None: ...


class Runner:
    """Subprocess execution: dry-run, capture, stream-tee, and log-file plumbing."""

    def __init__(self, *, dry_run: bool = False, verbose: bool = False) -> None:
        self.dry_run = dry_run
        self.verbose = verbose
        self.log_file: object | None = None

    def log(self, text: str) -> None:
        if self.log_file is None:
            return
        self.log_file.write(text)
        self.log_file.flush()

    def run(
        self,
        cmd: list[str],
        *,
        capture: bool = False,
        check: bool = True,
        quiet: bool = False,
        input_text: str | None = None,
        stream: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if self.dry_run:
            eprint("+ " + shjoin(cmd))
            self.log(f"$ {shjoin(cmd)}\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.log(f"\n[{time.strftime('%Y-%m-%dT%H:%M:%S%z')}] $ {shjoin(cmd)}\n")
        if self.verbose and not quiet:
            eprint("+ " + shjoin(cmd))
        streamed = stream and input_text is None
        try:
            if streamed:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                chunks: list[str] = []
                assert process.stdout is not None
                try:
                    for line in process.stdout:
                        chunks.append(line)
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        self.log(line)
                finally:
                    process.stdout.close()
                result = subprocess.CompletedProcess(cmd, process.wait(), "".join(chunks), "")
            else:
                result = subprocess.run(
                    cmd, text=True, capture_output=capture, input=input_text, check=False
                )
        except FileNotFoundError as exc:
            raise OmpKaliError(
                f"Required command not found: {cmd[0]}. Install podman, then retry."
            ) from exc
        if check and result.returncode != 0:
            detail = ""
            if capture and not streamed:
                detail = ((result.stdout or "") + (result.stderr or "")).strip()
            if detail:
                raise OmpKaliError(f"Command failed: {shjoin(cmd)}\n{detail}")
            raise OmpKaliError(f"Command failed ({result.returncode}): {shjoin(cmd)}")
        return result


class RealPodman:
    """The podman adapter: typed queries, argv assembly, and logging over a Runner."""

    def __init__(self, runner: Runner) -> None:
        self.runner = runner

    @property
    def dry_run(self) -> bool:
        return self.runner.dry_run

    def note(self, message: str) -> None:
        eprint(message)
        self.runner.log(message + "\n")

    def log(self, text: str) -> None:
        self.runner.log(text)

    def open_log(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.runner.log_file = path.open("a", encoding="utf-8")
        os.chmod(path, 0o600)

    def close_log(self) -> None:
        if self.runner.log_file is not None:
            self.runner.log_file.close()
            self.runner.log_file = None

    def observe(self) -> ContainerFacts:
        if self.runner.dry_run:
            return ContainerFacts(labels={MANAGED_LABEL: "true"})
        exists = (
            self.run(
                [PODMAN, "container", "exists", CONTAINER],
                capture=True,
                check=False,
                quiet=True,
            ).returncode
            == 0
        )
        if not exists:
            return ContainerFacts()
        return ContainerFacts(
            exists=True,
            running=self._inspect("{{.State.Running}}").lower() == "true",
            image=self._inspect("{{.Config.Image}}"),
            labels=self._labels(),
            publish_specs=tuple(self._publish_specs()),
            container_id=self._inspect("{{.Id}}"),
        )

    def _inspect(self, fmt: str) -> str:
        if self.runner.dry_run:
            return ""
        result = self.run(
            [PODMAN, "inspect", "-f", fmt, CONTAINER],
            capture=True,
            check=False,
            quiet=True,
        )
        return (result.stdout or "").strip()

    def _labels(self) -> dict[str, str]:
        raw = self._inspect("{{json .Config.Labels}}")
        if not raw:
            return {}
        try:
            labels = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return labels if isinstance(labels, dict) else {}

    def _publish_specs(self) -> list[str]:
        raw = self._inspect("{{json .HostConfig.PortBindings}}")
        if not raw:
            return []
        try:
            bindings = json.loads(raw)
        except json.JSONDecodeError:
            return []
        specs: list[str] = []
        for container_port, entries in (bindings or {}).items():
            if not container_port.endswith("/tcp"):
                continue
            port = container_port.split("/", 1)[0]
            for entry in entries or []:
                host_ip = entry.get("HostIp") or "0.0.0.0"
                host_port = entry.get("HostPort")
                if host_port:
                    specs.append(f"{host_ip}:{host_port}:{port}")
        return sorted(specs)

    def exec(
        self,
        args: list[str],
        *,
        user: str | None = None,
        env: dict[str, str] | None = None,
        detach: bool = False,
        stream: bool = False,
        interactive: bool = False,
        tty: bool = False,
        workdir: str | None = None,
        check: bool = True,
        capture: bool | None = None,
        quiet: bool | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [PODMAN, "exec"]
        if detach:
            cmd.append("-d")
        if interactive:
            cmd.append("-i")
        if tty:
            cmd.append("-t")
        if user is not None:
            cmd.extend(["-u", user])
        for key, value in (env or {}).items():
            cmd.extend(["-e", f"{key}={value}"])
        if workdir is not None:
            cmd.extend(["-w", workdir])
        cmd.append(CONTAINER)
        cmd.extend(args)
        return self.runner.run(
            cmd,
            capture=(not stream) if capture is None else capture,
            check=check,
            quiet=(not detach) if quiet is None else quiet,
            stream=stream,
        )

    def run(
        self,
        argv: list[str],
        *,
        capture: bool = False,
        check: bool = True,
        quiet: bool = False,
        input_text: str | None = None,
        stream: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return self.runner.run(
            argv,
            capture=capture,
            check=check,
            quiet=quiet,
            input_text=input_text,
            stream=stream,
        )

    def volume_exists(self, name: str) -> bool:
        if self.runner.dry_run:
            return False
        return (
            self.run(
                [PODMAN, "volume", "exists", name],
                capture=True,
                check=False,
                quiet=True,
            ).returncode
            == 0
        )

    def image_exists(self, tag: str) -> bool:
        if self.runner.dry_run:
            return False
        return (
            self.run(
                [PODMAN, "image", "exists", tag],
                capture=True,
                check=False,
                quiet=True,
            ).returncode
            == 0
        )

    def require_tools(self) -> None:
        if shutil.which(PODMAN) is None:
            raise OmpKaliError("podman is required. Install podman, then retry.")
