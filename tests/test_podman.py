from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from omp_kali.errors import OmpKaliError
from omp_kali.podman import CONTAINER, PODMAN, MANAGED_LABEL, RealPodman
from tests.fakes import FakeRunner


class RealPodmanObserveTests(unittest.TestCase):
    def test_observe_not_exists(self) -> None:
        runner = FakeRunner()
        runner.responses = [subprocess.CompletedProcess([], 1, "", "")]
        podman = RealPodman(runner)

        facts = podman.observe()

        self.assertFalse(facts.exists)
        self.assertFalse(facts.running)
        self.assertEqual(facts.image, "")
        self.assertEqual(facts.labels, {})
        self.assertEqual(facts.publish_specs, ())
        self.assertEqual(facts.container_id, "")
        self.assertEqual(runner.calls, [[PODMAN, "container", "exists", CONTAINER]])

    def test_observe_exists(self) -> None:
        runner = FakeRunner()
        runner.responses = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "true", ""),
            subprocess.CompletedProcess([], 0, "localhost/omp-kali:test", ""),
            subprocess.CompletedProcess(
                [],
                0,
                '{"omp-kali.managed":"true","omp-kali.workspace":"/tmp/proj","omp-kali.gui":"true"}',
                "",
            ),
            subprocess.CompletedProcess(
                [], 0, '{"80/tcp":[{"HostIp":"127.0.0.1","HostPort":"8080"}]}', ""
            ),
            subprocess.CompletedProcess([], 0, "abc123", ""),
        ]
        podman = RealPodman(runner)

        facts = podman.observe()

        self.assertTrue(facts.exists)
        self.assertTrue(facts.running)
        self.assertEqual(facts.image, "localhost/omp-kali:test")
        self.assertEqual(
            facts.labels,
            {
                "omp-kali.managed": "true",
                "omp-kali.workspace": "/tmp/proj",
                "omp-kali.gui": "true",
            },
        )
        self.assertEqual(facts.publish_specs, ("127.0.0.1:8080:80",))
        self.assertEqual(facts.container_id, "abc123")
        self.assertEqual(len(runner.calls), 6)
        self.assertEqual(runner.calls[0], [PODMAN, "container", "exists", CONTAINER])
        self.assertEqual(
            runner.calls[1], [PODMAN, "inspect", "-f", "{{.State.Running}}", CONTAINER]
        )
        self.assertEqual(
            runner.calls[2], [PODMAN, "inspect", "-f", "{{.Config.Image}}", CONTAINER]
        )
        self.assertEqual(
            runner.calls[3],
            [PODMAN, "inspect", "-f", "{{json .Config.Labels}}", CONTAINER],
        )
        self.assertEqual(
            runner.calls[4],
            [PODMAN, "inspect", "-f", "{{json .HostConfig.PortBindings}}", CONTAINER],
        )
        self.assertEqual(runner.calls[5], [PODMAN, "inspect", "-f", "{{.Id}}", CONTAINER])

    def test_observe_dry_run(self) -> None:
        runner = FakeRunner(dry_run=True)
        podman = RealPodman(runner)

        facts = podman.observe()

        self.assertTrue(facts.managed)
        self.assertEqual(facts.labels.get(MANAGED_LABEL), "true")
        self.assertEqual(runner.calls, [])


class RealPodmanExecTests(unittest.TestCase):
    def test_exec_constructs_argv(self) -> None:
        runner = FakeRunner()
        podman = RealPodman(runner)

        podman.exec(
            ["echo", "hi"],
            user="1000:1000",
            env={"A": "B"},
            interactive=True,
            tty=True,
            workdir="/w",
            capture=False,
            quiet=False,
        )

        expected = [
            PODMAN,
            "exec",
            "-i",
            "-t",
            "-u",
            "1000:1000",
            "-e",
            "A=B",
            "-w",
            "/w",
            CONTAINER,
            "echo",
            "hi",
        ]
        self.assertEqual(runner.calls, [expected])

    def test_exec_detach_omits_capture(self) -> None:
        runner = FakeRunner()
        podman = RealPodman(runner)

        podman.exec(["true"], detach=True)

        self.assertEqual(runner.calls, [[PODMAN, "exec", "-d", CONTAINER, "true"]])


class RealPodmanRunTests(unittest.TestCase):
    def test_run_constructs_argv(self) -> None:
        runner = FakeRunner()
        podman = RealPodman(runner)

        podman.run([PODMAN, "ps", "-a"], capture=True, check=False, quiet=True)

        self.assertEqual(runner.calls, [[PODMAN, "ps", "-a"]])


class RealPodmanExistenceTests(unittest.TestCase):
    def test_volume_exists(self) -> None:
        runner = FakeRunner()
        runner.responses = [subprocess.CompletedProcess([], 0, "", "")]
        podman = RealPodman(runner)

        self.assertTrue(podman.volume_exists("test-volume"))
        self.assertEqual(runner.calls, [[PODMAN, "volume", "exists", "test-volume"]])

    def test_volume_absent(self) -> None:
        runner = FakeRunner()
        runner.responses = [subprocess.CompletedProcess([], 1, "", "")]
        podman = RealPodman(runner)

        self.assertFalse(podman.volume_exists("test-volume"))

    def test_image_exists(self) -> None:
        runner = FakeRunner()
        runner.responses = [subprocess.CompletedProcess([], 0, "", "")]
        podman = RealPodman(runner)

        self.assertTrue(podman.image_exists("test-image"))
        self.assertEqual(runner.calls, [[PODMAN, "image", "exists", "test-image"]])

    def test_volume_exists_dry_run(self) -> None:
        runner = FakeRunner(dry_run=True)
        podman = RealPodman(runner)

        self.assertFalse(podman.volume_exists("test-volume"))
        self.assertEqual(runner.calls, [])

    def test_image_exists_dry_run(self) -> None:
        runner = FakeRunner(dry_run=True)
        podman = RealPodman(runner)

        self.assertFalse(podman.image_exists("test-image"))
        self.assertEqual(runner.calls, [])


class RealPodmanToolsTests(unittest.TestCase):
    def test_require_tools_ok(self) -> None:
        podman = RealPodman(FakeRunner())

        with mock.patch("shutil.which", return_value="/usr/bin/podman"):
            podman.require_tools()

    def test_require_tools_missing(self) -> None:
        podman = RealPodman(FakeRunner())

        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(OmpKaliError):
                podman.require_tools()


if __name__ == "__main__":
    unittest.main()
