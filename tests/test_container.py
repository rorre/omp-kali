from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from omp_kali.container import Container, DesiredContainer
from omp_kali.podman import CONTAINER, PODMAN, ContainerFacts
from tests.fakes import FakePodman


class TestContainer(Container):
    """Container with podman-touching side effects replaced by recorders."""

    def __init__(self, podman: FakePodman) -> None:
        super().__init__(podman)
        self.check_kali_calls = 0
        self.provision_calls = 0
        self.ensure_volume_calls = 0
        self.ensure_image_calls = 0
        self.require_tools_calls = 0
        self.request_recreate_calls = 0
        self.restart_relays_calls = 0

    def check_kali(self) -> str:
        self.check_kali_calls += 1
        return "kali"

    def provision(self, **kwargs) -> str:
        self.provision_calls += 1
        return "provisioned"

    def ensure_volume(self) -> None:
        self.ensure_volume_calls += 1

    def ensure_image(self, repo: Path) -> str:
        self.ensure_image_calls += 1
        return "img"

    def require_tools(self) -> None:
        self.require_tools_calls += 1

    def request_recreate(self, *args, **kwargs) -> None:
        self.request_recreate_calls += 1

    def restart_relays(self, mappings) -> None:
        self.restart_relays_calls += 1


class ApplyTests(unittest.TestCase):
    def test_apply_creates_when_absent(self) -> None:
        podman = FakePodman(facts=ContainerFacts())
        container = TestContainer(podman)
        desired = DesiredContainer(
            image="img", workspace="/tmp", gui=False, publish_specs=()
        )
        state: dict = {}

        facts = container._apply(
            state, desired=desired, repo=Path("/repo"), provision=True
        )

        self.assertTrue(facts.exists)
        self.assertTrue(facts.running)
        self.assertEqual(container.check_kali_calls, 1)
        self.assertEqual(container.provision_calls, 1)
        self.assertEqual(len(podman.run_calls), 1)
        self.assertIn("run", podman.run_calls[0])
        self.assertEqual(state["applied_publish"], [])
        self.assertEqual(state["workspace"], "/tmp")
        self.assertEqual(state["gui"], False)

    def test_apply_starts_when_stopped(self) -> None:
        podman = FakePodman(facts=ContainerFacts(exists=True, running=False))
        container = TestContainer(podman)
        desired = DesiredContainer(
            image="img", workspace="/tmp", gui=False, publish_specs=()
        )
        state: dict = {}

        facts = container._apply(
            state, desired=desired, repo=Path("/repo"), provision=True
        )

        self.assertTrue(facts.running)
        self.assertEqual(len(podman.run_calls), 1)
        self.assertIn("start", podman.run_calls[0])
        self.assertEqual(container.check_kali_calls, 1)
        self.assertEqual(container.provision_calls, 1)

    def test_apply_noop_when_running(self) -> None:
        podman = FakePodman(facts=ContainerFacts(exists=True, running=True))
        container = TestContainer(podman)
        desired = DesiredContainer(
            image="img", workspace="/tmp", gui=False, publish_specs=()
        )
        state: dict = {}

        facts = container._apply(
            state, desired=desired, repo=Path("/repo"), provision=True
        )

        self.assertTrue(facts.running)
        self.assertEqual(len(podman.run_calls), 0)
        self.assertEqual(container.check_kali_calls, 1)
        self.assertEqual(container.provision_calls, 1)


class EnsureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_patch = mock.patch(
            "omp_kali.container.paths.ensure_repo_assets",
            return_value=(Path("/repo"),),
        )
        self.repo_patch.start()

    def tearDown(self) -> None:
        self.repo_patch.stop()

    def test_ensure_creates_when_absent(self) -> None:
        podman = FakePodman(facts=ContainerFacts())
        container = TestContainer(podman)
        state: dict = {}

        container.ensure(state, cwd=Path("/tmp"), gui=False, provision=True)

        self.assertEqual(container.require_tools_calls, 1)
        self.assertEqual(container.ensure_volume_calls, 1)
        self.assertEqual(container.ensure_image_calls, 1)
        self.assertEqual(len(podman.run_calls), 1)
        self.assertIn("run", podman.run_calls[0])
        self.assertEqual(container.check_kali_calls, 1)
        self.assertEqual(container.provision_calls, 1)

    def test_ensure_recreates_when_managed_and_drift(self) -> None:
        podman = FakePodman(
            facts=ContainerFacts(
                exists=True,
                running=True,
                image="old",
                labels={"omp-kali.managed": "true"},
            )
        )
        container = TestContainer(podman)
        state: dict = {}

        container.ensure(state, cwd=Path("/tmp"), gui=False, provision=True)

        self.assertEqual(container.request_recreate_calls, 1)

    def test_ensure_raises_when_not_managed(self) -> None:
        podman = FakePodman(
            facts=ContainerFacts(exists=True, running=True, labels={})
        )
        container = TestContainer(podman)
        state: dict = {}

        with self.assertRaises(Exception):
            container.ensure(state, cwd=Path("/tmp"), gui=False, provision=True)


class RecreateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_patch = mock.patch(
            "omp_kali.container.paths.ensure_repo_assets",
            return_value=(Path("/repo"),),
        )
        self.repo_patch.start()

    def tearDown(self) -> None:
        self.repo_patch.stop()

    def test_recreate_removes_and_applies(self) -> None:
        podman = FakePodman(facts=ContainerFacts(exists=True, running=True))
        container = TestContainer(podman)
        state: dict = {}

        container.recreate(state, cwd=Path("/tmp"), gui=False)

        self.assertEqual(len(podman.run_calls), 2)
        self.assertIn("rm", podman.run_calls[0])
        self.assertIn("run", podman.run_calls[1])
        self.assertEqual(container.check_kali_calls, 1)
        self.assertEqual(container.provision_calls, 1)
        self.assertEqual(container.restart_relays_calls, 1)

    def test_recreate_without_existing_container(self) -> None:
        podman = FakePodman(facts=ContainerFacts())
        container = TestContainer(podman)
        state: dict = {}

        container.recreate(state, cwd=Path("/tmp"), gui=False)

        self.assertEqual(len(podman.run_calls), 1)
        self.assertIn("run", podman.run_calls[0])
        self.assertEqual(container.restart_relays_calls, 1)


if __name__ == "__main__":
    unittest.main()
