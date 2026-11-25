"""Test doubles for the podman seam.

FakeRunner records subprocess argv and replays canned CompletedProcess results.
FakePodman implements the Podman protocol with a mutable ContainerFacts and a
call recorder, so container decisions can be exercised without podman.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

from omp_kali.podman import CONTAINER, PODMAN, ContainerFacts


class FakeRunner:
    def __init__(self, *, dry_run: bool = False, verbose: bool = False) -> None:
        self.dry_run = dry_run
        self.verbose = verbose
        self.log_file: object | None = None
        self.calls: list[list[str]] = []
        self.responses: list[subprocess.CompletedProcess[str]] = []
        self.logs: list[str] = []

    def log(self, text: str) -> None:
        self.logs.append(text)

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
        self.calls.append(list(cmd))
        if self.responses:
            return self.responses.pop(0)
        return subprocess.CompletedProcess(cmd, 0, "", "")


class FakePodman:
    def __init__(
        self,
        *,
        facts: ContainerFacts | None = None,
        dry_run: bool = False,
        volume_exists_result: bool = False,
        image_exists_result: bool = False,
        run_returncode: int = 0,
        exec_returncode: int = 0,
        exec_stdout: str = "",
        exec_stderr: str = "",
    ) -> None:
        self.facts = facts or ContainerFacts()
        self.dry_run = dry_run
        self.volume_exists_result = volume_exists_result
        self.image_exists_result = image_exists_result
        self.run_returncode = run_returncode
        self.exec_returncode = exec_returncode
        self.exec_stdout = exec_stdout
        self.exec_stderr = exec_stderr
        self.run_calls: list[list[str]] = []
        self.exec_calls: list[dict[str, object]] = []
        self.note_calls: list[str] = []
        self.log_calls: list[str] = []
        self.open_log_calls: list[Path] = []
        self.close_log_calls = 0
        self.require_tools_calls = 0

    def note(self, message: str) -> None:
        self.note_calls.append(message)

    def log(self, text: str) -> None:
        self.log_calls.append(text)

    def open_log(self, path: Path) -> None:
        self.open_log_calls.append(path)

    def close_log(self) -> None:
        self.close_log_calls += 1

    def observe(self) -> ContainerFacts:
        return self.facts

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
        self.exec_calls.append(
            {
                "cmd": cmd,
                "user": user,
                "env": env,
                "detach": detach,
                "stream": stream,
                "interactive": interactive,
                "tty": tty,
                "workdir": workdir,
                "check": check,
                "capture": capture,
                "quiet": quiet,
            }
        )
        return subprocess.CompletedProcess(
            cmd, self.exec_returncode, self.exec_stdout, self.exec_stderr
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
        self.run_calls.append(list(argv))
        if "run" in argv:
            self.facts = dataclasses.replace(self.facts, exists=True, running=True)
        elif "start" in argv:
            self.facts = dataclasses.replace(self.facts, exists=True, running=True)
        elif "rm" in argv:
            self.facts = dataclasses.replace(self.facts, exists=False, running=False)
        return subprocess.CompletedProcess(argv, self.run_returncode, "", "")

    def volume_exists(self, name: str) -> bool:
        return self.volume_exists_result

    def image_exists(self, tag: str) -> bool:
        return self.image_exists_result

    def require_tools(self) -> None:
        self.require_tools_calls += 1
