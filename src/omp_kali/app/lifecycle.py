from __future__ import annotations

from pathlib import Path

from ..core import paths
from ..domain.desired import DesiredContainer, recreate_reason
from ..infra.create import create_command
from ..core.errors import OmpKaliError
from ..core.constants import CONTAINER, PODMAN, VOLUME
from ..domain.facts import ContainerFacts
from ..infra.podman import Podman
from ..domain.ports import desired_publish_specs, mappings_from_state
from ..domain.state import State
from ..core.util import confirm, eprint


class Lifecycle:
    """Ensures the container exists, runs, and is recreated when desired state drifts."""

    def __init__(self, podman: Podman, provisioning, execution) -> None:
        self.podman = podman
        self.provisioning = provisioning
        self.execution = execution

    def ensure(
        self,
        state: State,
        *,
        assume_yes: bool = False,
        cwd: Path,
        gui: bool,
        provision: bool = True,
    ) -> None:
        self.podman.require_tools()
        self.ensure_volume()
        repo = paths.ensure_repo_assets()[0]
        image = self.provisioning.ensure_image(repo)
        cwd = cwd.resolve()
        desired = DesiredContainer(
            image=image,
            workspace=str(cwd),
            gui=gui,
            publish_specs=tuple(
                desired_publish_specs(mappings_from_state({"ports": state.ports}))
            ),
        )
        facts = self.podman.observe()
        if self.podman.dry_run:
            self.podman.run(
                create_command(
                    Path(desired.workspace), desired.gui, list(desired.publish_specs), repo, image
                )
            )
            return
        if facts.exists:
            if not facts.managed:
                raise OmpKaliError(
                    f"A container named {CONTAINER} exists and was not created by omp-kali."
                )
            reason = recreate_reason(facts, desired)
            if reason:
                self.request_recreate(
                    state,
                    cwd=cwd,
                    gui=gui,
                    image=image,
                    assume_yes=assume_yes,
                    reason=reason,
                )
                return
        self.apply(state, desired=desired, repo=repo, provision=provision, facts=facts)

    def apply(
        self,
        state: State,
        *,
        desired: DesiredContainer,
        repo: Path,
        provision: bool = True,
        facts: ContainerFacts | None = None,
    ) -> ContainerFacts:
        """Create the container if absent, start it if stopped, then check and provision."""
        if facts is None:
            facts = self.podman.observe()
        if not facts.exists:
            eprint(f"Creating podman container {CONTAINER} from {desired.image}...")
            self.podman.run(
                create_command(
                    Path(desired.workspace),
                    desired.gui,
                    list(desired.publish_specs),
                    repo,
                    desired.image,
                ),
                capture=False,
            )
            state.applied_publish = list(desired.publish_specs)
            facts = self.podman.observe()
        if not facts.running:
            eprint(f"Starting container {CONTAINER}...")
            self.podman.run([PODMAN, "start", CONTAINER], capture=True)
            facts = self.podman.observe()
        self.execution.check_kali()
        state.workspace = desired.workspace
        state.gui = desired.gui
        if provision:
            self.provisioning.provision(state, facts)
        return facts

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
        message = (
            f"{reason}\n"
            f"Remove and recreate container {CONTAINER}? "
            "Other containers are not affected. The home volume and omp sessions are preserved."
        )
        if not confirm(message, assume_yes=assume_yes, default=False):
            raise OmpKaliError("Cancelled; container left unchanged.")
        self.recreate(state, cwd=cwd, gui=gui, image=image)

    def recreate(
        self,
        state: State,
        *,
        cwd: Path,
        gui: bool,
        image: str | None = None,
    ) -> None:
        if self.podman.observe().exists:
            eprint(f"Removing container {CONTAINER}...")
            self.podman.run([PODMAN, "rm", "-f", "-t", "0", CONTAINER], capture=True)
        repo = paths.ensure_repo_assets()[0]
        if image is None:
            image = self.provisioning.ensure_image(repo)
        cwd = cwd.resolve()
        desired = DesiredContainer(
            image=image,
            workspace=str(cwd),
            gui=gui,
            publish_specs=tuple(
                desired_publish_specs(mappings_from_state({"ports": state.ports}))
            ),
        )
        eprint(f"Recreating podman container {CONTAINER}...")
        self.apply(state, desired=desired, repo=repo, provision=True)
        self.execution.restart_relays(mappings_from_state({"ports": state.ports}))

    def ensure_volume(self) -> None:
        if not self.podman.volume_exists(VOLUME):
            self.podman.run([PODMAN, "volume", "create", VOLUME], capture=True)

    def remove(self, *, purge: bool = False) -> None:
        if self.podman.dry_run:
            return
        if self.podman.observe().exists:
            self.podman.run([PODMAN, "rm", "-f", "-t", "0", CONTAINER], capture=True)
        if purge:
            if self.podman.volume_exists(VOLUME):
                self.podman.run([PODMAN, "volume", "rm", "-f", VOLUME], capture=True)
