import json
import tempfile
import unittest
from pathlib import Path

from omp_kali.errors import OmpKaliError
from omp_kali.paths import default_state, load_state, save_state


class StateTests(unittest.TestCase):
    def test_roundtrip_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = default_state()
            state["ports"] = [{"direction": "container-to-host", "host": 8080, "container": 80, "bind": "127.0.0.1"}]
            state["applied_publish"] = ["127.0.0.1:8080:80"]
            save_state(state, path)
            loaded = load_state(path)
            self.assertEqual(loaded, state)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_missing_file_returns_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_state(Path(tmp) / "missing.json"), default_state())

    def test_corrupt_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(OmpKaliError):
                load_state(path)

    def test_unknown_version_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps({"version": 99}), encoding="utf-8")
            with self.assertRaises(OmpKaliError):
                load_state(path)


if __name__ == "__main__":
    unittest.main()
