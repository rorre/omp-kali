import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from omp_kali.core.errors import OmpKaliError
from omp_kali.domain.state import (
    FileStatePersistence,
    State,
    StatePersistence,
    StateStore,
)


class FakeStatePersistence(StatePersistence):
    """In-memory adapter proving the seam needs no filesystem."""

    def __init__(self) -> None:
        self.main: dict[str, Any] = {}
        self.provision: dict[str, Any] = {}

    def load_main(self) -> dict[str, Any]:
        return self.main

    def load_provision(self) -> dict[str, Any]:
        return self.provision

    def save_main(self, payload: dict[str, Any]) -> None:
        self.main = payload

    def save_provision(self, payload: dict[str, Any]) -> None:
        self.provision = payload

    def clear(self) -> None:
        self.main = {}
        self.provision = {}


class StateStoreTests(unittest.TestCase):
    def test_roundtrip_through_fake_persistence(self) -> None:
        store = StateStore(FakeStatePersistence())
        state = State(workspace="/tmp/ws", gui=True)
        state.ports = [{"host": 8080, "container": 80}]
        store.save(state)
        loaded = store.load()
        self.assertEqual(loaded.workspace, "/tmp/ws")
        self.assertTrue(loaded.gui)
        self.assertEqual(loaded.ports, state.ports)


class FileStatePersistenceTests(unittest.TestCase):
    def test_roundtrip_and_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            persistence = FileStatePersistence(Path(tmp))
            store = StateStore(persistence)
            state = State()
            state.ports = [
                {
                    "direction": "container-to-host",
                    "host": 8080,
                    "container": 80,
                    "bind": "127.0.0.1",
                }
            ]
            state.applied_publish = ["127.0.0.1:8080:80"]
            store.save(state)
            loaded = store.load()
            self.assertEqual(loaded.ports, state.ports)
            self.assertEqual(loaded.applied_publish, state.applied_publish)
            self.assertEqual(persistence.main_path.stat().st_mode & 0o777, 0o600)

    def test_missing_file_returns_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(FileStatePersistence(Path(tmp)))
            self.assertEqual(store.load(), State())

    def test_corrupt_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            persistence = FileStatePersistence(Path(tmp))
            persistence.main_path.write_text("not json", encoding="utf-8")
            store = StateStore(persistence)
            with self.assertRaises(OmpKaliError):
                store.load()

    def test_unknown_version_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            persistence = FileStatePersistence(Path(tmp))
            persistence.main_path.write_text(json.dumps({"version": 99}), encoding="utf-8")
            store = StateStore(persistence)
            with self.assertRaises(OmpKaliError):
                store.load()

    def test_invalid_ports_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            persistence = FileStatePersistence(Path(tmp))
            persistence.main_path.write_text(
                json.dumps({"version": 1, "ports": "not-a-list"}), encoding="utf-8"
            )
            store = StateStore(persistence)
            with self.assertRaises(OmpKaliError):
                store.load()

    def test_invalid_applied_publish_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            persistence = FileStatePersistence(Path(tmp))
            persistence.main_path.write_text(
                json.dumps({"version": 1, "applied_publish": "not-a-list"}),
                encoding="utf-8",
            )
            store = StateStore(persistence)
            with self.assertRaises(OmpKaliError):
                store.load()

    def test_clear_removes_both_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            persistence = FileStatePersistence(Path(tmp))
            store = StateStore(persistence)
            store.save(State(workspace="/tmp/ws"))
            self.assertTrue(persistence.main_path.exists())
            self.assertTrue(persistence.provision_path.exists())
            store.clear()
            self.assertFalse(persistence.main_path.exists())
            self.assertFalse(persistence.provision_path.exists())
            self.assertEqual(store.load(), State())


if __name__ == "__main__":
    unittest.main()
