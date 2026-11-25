import unittest
from pathlib import Path

from omp_kali.infra.create import create_command


class CreateCommandTests(unittest.TestCase):
    def test_create_command_is_raw_podman_with_minimal_mounts(self) -> None:
        command = create_command(
            Path("/home/user/project"),
            False,
            ["127.0.0.1:8080:80"],
            Path("/home/user/omp-kali"),
            "localhost/omp-kali:test",
        )
        self.assertEqual(command[0], "podman")
        self.assertIn("localhost/omp-kali:test", command)
        self.assertIn("--userns=keep-id", command)
        self.assertIn("--security-opt", command)
        self.assertIn("label=disable", command)
        joined = " ".join(str(part) for part in command)
        self.assertIn("omp-kali-home:/home/omp", joined)
        self.assertIn("/home/user/project:/workspace/project", joined)
        self.assertIn("omp-kali.workspace=/home/user/project", joined)
        self.assertNotIn("/home/user/project:/home/user/project", joined)
        self.assertIn("/home/user/omp-kali/skills:/home/omp/.omp/agent/skills", joined)
        self.assertIn("/home/user/omp-kali/agents:/home/omp/.omp/agent/agents", joined)
        self.assertIn("/home/user/omp-kali/mcp/mcp.json:/home/omp/.omp/agent/mcp.json", joined)
        self.assertIn("--publish 127.0.0.1:8080:80", joined)
        self.assertNotIn("/run/host", joined)
        self.assertNotIn("/tmp:/tmp", joined)
        self.assertNotIn("distrobox", joined)

    def test_gui_mounts_are_opt_in(self) -> None:
        plain = create_command(Path("/p"), False, [], Path("/r"), "img")
        gui = create_command(Path("/p"), True, [], Path("/r"), "img")
        self.assertNotIn("omp-kali.gui=true", " ".join(plain))
        self.assertIn("omp-kali.gui=true", " ".join(gui))


if __name__ == "__main__":
    unittest.main()
