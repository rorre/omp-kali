from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..core import paths
from ..domain.desired import DesiredContainer
from ..domain.facts import ContainerFacts
from ..domain.ports import PortMapping
from ..domain.state import FileStatePersistence, State, StateStore
from ..infra.execution import Execution
from ..infra.podman import Podman
from ..infra.provisioning import Provisioning
from ..infra.shell import Shell
from .lifecycle import Lifecycle


class Container:
    """Thin orchestrator over the lifecycle, provisioning, execution, and shell modules."""

    def __init__(self, podman: Podman, store: StateStore | None = None) -> None:
        self.podman = podman
        self.store = store or StateStore(FileStatePersistence(paths.STATE_DIR))
        self.execution = Execution(podman)
        self.provisioning = Provisioning(podman, self.execution, self.store)
        self.lifecycle = Lifecycle(podman, self.provisioning, self.execution)
        self.shell = Shell(podman)

    # -- lifecycle -----------------------------------------------------

    def ensure(
        self,
        state: State,
        *,
        assume_yes: bool = False,
        cwd: Path,
        gui: bool,
        provision: bool = True,
    ) -> None:
        self.lifecycle.ensure(
            state, assume_yes=assume_yes, cwd=cwd, gui=gui, provision=provision
        )

    def apply(
        self,
        state: State,
        *,
        desired: DesiredContainer,
        repo: Path,
        provision: bool = True,
        facts: ContainerFacts | None = None,
    ) -> ContainerFacts:
        return self.lifecycle.apply(
            state, desired=desired, repo=repo, provision=provision, facts=facts
        )

    def request_recreate(
        self,
        state: State,
        *,
        cwd: Path,
        gui: bool,
        image: str | None = None,
        assume_yes: bool = False,
        reason: str,
    ) -> None:
        self.lifecycle.request_recreate(
            state, cwd=cwd, gui=gui, image=image, assume_yes=assume_yes, reason=reason
        )

    def recreate(
        self, state: State, *, cwd: Path, gui: bool, image: str | None = None
    ) -> None:
        self.lifecycle.recreate(state, cwd=cwd, gui=gui, image=image)

    def remove(self, *, purge: bool = False) -> None:
        self.lifecycle.remove(purge=purge)
        if purge and not self.podman.dry_run:
            self.store.clear()
            self.execution.purge_host_state()

    def ensure_volume(self) -> None:
        self.lifecycle.ensure_volume()

    # -- provisioning --------------------------------------------------

    def provision(
        self,
        *,
        force_binary: bool = False,
        force_hooks: bool = False,
        force_mcp: bool = False,
    ) -> str:
        state = self.store.load()
        facts = self.podman.observe()
        return self.provisioning.provision(
            state,
            facts,
            force_binary=force_binary,
            force_hooks=force_hooks,
            force_mcp=force_mcp,
        )

    def ensure_image(self, repo: Path) -> str:
        return self.provisioning.ensure_image(repo)

    def image_hash(self, repo: Path) -> str:
        return self.provisioning.image_hash(repo)

    # -- execution -----------------------------------------------------

    def restart_relays(self, mappings: list[PortMapping]) -> None:
        self.execution.restart_relays(mappings)

    def status(self) -> dict[str, Any]:
        return self.execution.status()

    def check_kali(self) -> str:
        return self.execution.check_kali()

    def podman_exec(
        self,
        args: list[str],
        *,
        check: bool = True,
        detach: bool = False,
        stream: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return self.execution.podman_exec(
            args, check=check, detach=detach, stream=stream
        )

    def root_exec(
        self,
        args: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
        stream: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return self.execution.root_exec(args, check=check, env=env, stream=stream)

    # -- shell ---------------------------------------------------------

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
        return self.shell.enter(
            cwd=cwd,
            gui=gui,
            pass_env=pass_env,
            omp_args=omp_args,
            shell=shell,
            shell_command=shell_command,
        )

    # -- queries -------------------------------------------------------

    def exists(self) -> bool:
        return self.podman.observe().exists

    def running(self) -> bool:
        return self.podman.observe().running

    def managed(self) -> bool:
        return self.podman.observe().managed

    def label(self, name: str) -> str:
        return self.podman.observe().labels.get(name, "")

    def container_id(self) -> str:
        return self.podman.observe().container_id

    def image_name(self) -> str:
        return self.podman.observe().image

    def workspace_source(self) -> str:
        return self.podman.observe().workspace

    def actual_publish_specs(self) -> list[str]:
        return list(self.podman.observe().publish_specs)

    def require_tools(self) -> None:
        self.podman.require_tools()
