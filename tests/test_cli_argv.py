import unittest

from omp_kali.cli import normalize_argv


class NormalizeArgvTests(unittest.TestCase):
    def test_default_run(self) -> None:
        self.assertEqual(normalize_argv([]), ["run"])

    def test_global_flag_defaults_to_run(self) -> None:
        self.assertEqual(normalize_argv(["--gui"]), ["run", "--gui"])
        self.assertEqual(normalize_argv(["--dry-run", "--verbose"]), ["run", "--dry-run", "--verbose"])

    def test_passthrough_omp_args(self) -> None:
        self.assertEqual(normalize_argv(["--model", "x"]), ["run", "--model", "x"])
        self.assertEqual(normalize_argv(["--", "--version"]), ["run", "--", "--version"])
        self.assertEqual(normalize_argv(["prompt"]), ["run", "prompt"])

    def test_explicit_run_is_preserved(self) -> None:
        self.assertEqual(normalize_argv(["run", "--", "--version"]), ["run", "--", "--version"])
        self.assertEqual(normalize_argv(["--gui", "run", "--model", "x"]), ["--gui", "run", "--model", "x"])

    def test_commands_survive_globals(self) -> None:
        self.assertEqual(normalize_argv(["--gui", "shell", "-c", "pwd"]), ["--gui", "shell", "-c", "pwd"])
        self.assertEqual(normalize_argv(["--dir", "/tmp", "ports", "list"]), ["--dir", "/tmp", "ports", "list"])
        self.assertEqual(normalize_argv(["shell", "--gui"]), ["shell", "--gui"])

    def test_provisioning_commands(self) -> None:
        self.assertEqual(normalize_argv(["provision", "--force"]), ["provision", "--force"])
        self.assertEqual(normalize_argv(["mcp", "list"]), ["mcp", "list"])
        self.assertEqual(normalize_argv(["mcp", "sync", "--force"]), ["mcp", "sync", "--force"])

    def test_help_and_version(self) -> None:
        self.assertEqual(normalize_argv(["--help"]), ["--help"])
        self.assertEqual(normalize_argv(["ports", "--help"]), ["ports", "--help"])
        self.assertEqual(normalize_argv(["--version"]), ["--version"])
        self.assertEqual(normalize_argv(["--", "--version"]), ["run", "--", "--version"])


if __name__ == "__main__":
    unittest.main()
