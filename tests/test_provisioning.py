import json
import tempfile
import unittest
from pathlib import Path

from omp_kali.provisioning import (
    default_state,
    load_scripts,
    load_state,
    save_state,
    sha256_file,
    state_path,
)


class ProvisioningTests(unittest.TestCase):
    def test_scripts_sorted_and_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            directory = root / "provisioning"
            directory.mkdir()
            (directory / "20-second.sh").write_text("echo 2\n", encoding="utf-8")
            (directory / "10-first.sh").write_text("echo 1\n", encoding="utf-8")
            (directory / "notes.txt").write_text("ignore\n", encoding="utf-8")
            scripts = load_scripts(root)
            self.assertEqual([script.name for script in scripts], ["10-first.sh", "20-second.sh"])
            self.assertEqual(scripts[0].sha256, sha256_file(directory / "10-first.sh"))

    def test_state_resets_for_new_container(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state = default_state("old-container")
            state["scripts"]["a.sh"] = "hash"
            state["mcp"]["demo"] = "hash"
            save_state(home, state)
            self.assertEqual(load_state(home, "old-container")["scripts"]["a.sh"], "hash")
            reset = load_state(home, "new-container")
            self.assertEqual(reset["scripts"], {})
            self.assertEqual(reset["mcp"], {})
            self.assertEqual(reset["container_id"], "new-container")

    def test_state_roundtrip_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            state = default_state("cid")
            state["scripts"]["x.sh"] = "abc"
            save_state(home, state)
            path = state_path(home)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(path.read_text())["scripts"]["x.sh"], "abc")

    def test_missing_scripts_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_scripts(Path(tmp)), [])


if __name__ == "__main__":
    unittest.main()
