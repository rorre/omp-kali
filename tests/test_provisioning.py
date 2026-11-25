import tempfile
import unittest
from pathlib import Path

from omp_kali.infra.provisioning import load_scripts, sha256_file


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

    def test_missing_scripts_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_scripts(Path(tmp)), [])


if __name__ == "__main__":
    unittest.main()
