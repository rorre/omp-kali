from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from omp_kali.domain.desired import DesiredContainer
from omp_kali.app.lifecycle import Lifecycle
from omp_kali.domain.facts import ContainerFacts
from omp_kali.domain.state import State
from tests.fakes import FakePodman


class FakeProvisioning:
    def __init__(self) -> None:
        self.ensure_image_calls = 0
        self.provision_calls = 0

    def ensure_image(self, repo: Path) -> str:
        self.ensure_image_calls += 1
        return "img"

    def provision(self, state: State, facts: ContainerFacts) -> str:
        self.provision_calls += 1
        return "provisioned"


class FakeExecution:
    def __init__(self) -> None:
        self.check_kali_calls = 0
        self.restart_relays_calls = 0

    def check_kali(self) -> str:
        self.check_kali_calls += 1
        return "kali"

    def restart_relays(self, mappings) -> None:
        self.restart_relays_calls += 1


def make_lifecycle(podman: FakePodman) -> tuple[Lifecycle, FakeProvisioning, FakeExecution]:
    provisioning = FakeProvisioning()
    execution = FakeExecution()
    lifecycle = Lifecycle(podman, provisioning, execution)
    return lifecycle, provisioning, execution


class ApplyTests(unittest.TestCase):
    def test_apply_creates_when_absent(self) -> None:
        podman = FakePodman(facts=ContainerFacts())
        lifecycle, provisioning, execution = make_lifecycle(podman)
        desired = DesiredContainer(
            image="img", workspace="/tmp", gui=False, publish_specs=()
        )
        state = State()

        facts = lifecycle.apply(
            state, desired=desired, repo=Path("/repo"), provision=True
        )

        self.assertTrue(facts.exists)
        self.assertTrue(facts.running)
        self.assertEqual(execution.check_kali_calls, 1)
        self.assertEqual(provisioning.provision_calls, 1)
        self.assertEqual(len(podman.run_calls), 1)
        self.assertIn("run", podman.run_calls[0])
        self.assertEqual(state.applied_publish, [])
        self.assertEqual(state.workspace, "/tmp")
        self.assertEqual(state.gui, False)

    def test_apply_starts_when_stopped(self) -> None:
        podman = FakePodman(facts=ContainerFacts(exists=True, running=False))
        lifecycle, provisioning, execution = make_lifecycle(podman)
        desired = DesiredContainer(
            image="img", workspace="/tmp", gui=False, publish_specs=()
        )
        state = State()

        facts = lifecycle.apply(
            state, desired=desired, repo=Path("/repo"), provision=True
        )

        self.assertTrue(facts.running)
        self.assertEqual(len(podman.run_calls), 1)
        self.assertIn("start", podman.run_calls[0])
        self.assertEqual(execution.check_kali_calls, 1)
        self.assertEqual(provisioning.provision_calls, 1)

    def test_apply_noop_when_running(self) -> None:
        podman = FakePodman(facts=ContainerFacts(exists=True, running=True))
        lifecycle, provisioning, execution = make_lifecycle(podman)
        desired = DesiredContainer(
            image="img", workspace="/tmp", gui=False, publish_specs=()
        )
        state = State()

        facts = lifecycle.apply(
            state, desired=desired, repo=Path("/repo"), provision=True
        )

        self.assertTrue(facts.running)
        self.assertEqual(len(podman.run_calls), 0)
        self.assertEqual(execution.check_kali_calls, 1)
        self.assertEqual(provisioning.provision_calls, 1)


class EnsureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_patch = mock.patch(
            "omp_kali.app.lifecycle.paths.ensure_repo_assets",
            return_value=(Path("/repo"),),
        )
        self.repo_patch.start()

    def tearDown(self) -> None:
        self.repo_patch.stop()

    def test_ensure_creates_when_absent(self) -> None:
        podman = FakePodman(facts=ContainerFacts())
        lifecycle, provisioning, execution = make_lifecycle(podman)
        state = State()

        lifecycle.ensure(state, cwd=Path("/tmp"), gui=False, provision=True)

        self.assertEqual(podman.require_tools_calls, 1)
        self.assertEqual(provisioning.ensure_image_calls, 1)
        self.assertEqual(len(podman.run_calls), 2)
        self.assertIn("volume", podman.run_calls[0])
        self.assertIn("run", podman.run_calls[1])
        self.assertEqual(execution.check_kali_calls, 1)
        self.assertEqual(provisioning.provision_calls, 1)

    def test_ensure_recreates_when_managed_and_drift(self) -> None:
        podman = FakePodman(
            facts=ContainerFacts(
                exists=True,
                running=True,
                image="old",
                labels={"omp-kali.managed": "true"},
            )
        )
        lifecycle, provisioning, execution = make_lifecycle(podman)
        state = State()

        with mock.patch.object(lifecycle, "request_recreate") as request_recreate:
            lifecycle.ensure(state, cwd=Path("/tmp"), gui=False, provision=True)
            request_recreate.assert_called_once()

    def test_ensure_raises_when_not_managed(self) -> None:
        podman = FakePodman(
            facts=ContainerFacts(exists=True, running=True, labels={})
        )
        lifecycle, provisioning, execution = make_lifecycle(podman)
        state = State()

        with self.assertRaises(Exception):
            lifecycle.ensure(state, cwd=Path("/tmp"), gui=False, provision=True)


class RecreateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_patch = mock.patch(
            "omp_kali.app.lifecycle.paths.ensure_repo_assets",
            return_value=(Path("/repo"),),
        )
        self.repo_patch.start()

    def tearDown(self) -> None:
        self.repo_patch.stop()

    def test_recreate_removes_and_applies(self) -> None:
        podman = FakePodman(facts=ContainerFacts(exists=True, running=True))
        lifecycle, provisioning, execution = make_lifecycle(podman)
        state = State()

        lifecycle.recreate(state, cwd=Path("/tmp"), gui=False)

        self.assertEqual(len(podman.run_calls), 2)
        self.assertIn("rm", podman.run_calls[0])
        self.assertIn("run", podman.run_calls[1])
        self.assertEqual(execution.check_kali_calls, 1)
        self.assertEqual(provisioning.provision_calls, 1)
        self.assertEqual(execution.restart_relays_calls, 1)

    def test_recreate_without_existing_container(self) -> None:
        podman = FakePodman(facts=ContainerFacts())
        lifecycle, provisioning, execution = make_lifecycle(podman)
        state = State()

        lifecycle.recreate(state, cwd=Path("/tmp"), gui=False)

        self.assertEqual(len(podman.run_calls), 1)
        self.assertIn("run", podman.run_calls[0])
        self.assertEqual(execution.restart_relays_calls, 1)


if __name__ == "__main__":
    unittest.main()
