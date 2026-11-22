import json
import tempfile
import unittest
from pathlib import Path

from omp_kali.errors import OmpKaliError
from omp_kali.mcp import (
    McpManifest,
    load_config,
    load_manifests,
    merge_servers,
    write_config,
)


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class McpManifestTests(unittest.TestCase):
    def test_load_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "demo.json",
                {
                    "name": "demo",
                    "description": "A demo server",
                    "install": {"apt": ["nodejs", "npm"], "npm": ["demo-mcp"]},
                    "server": {"type": "stdio", "command": "npx", "args": ["-y", "demo-mcp"]},
                },
            )
            manifests = load_manifests(root)
            self.assertEqual(len(manifests), 1)
            manifest = manifests[0]
            self.assertTrue(manifest.enabled)
            self.assertTrue(manifest.has_install)
            self.assertEqual(manifest.server["command"], "npx")
            original = manifest.install_hash

            _write(
                root / "demo.json",
                {
                    "name": "demo",
                    "install": {"apt": ["nodejs"], "npm": ["demo-mcp"]},
                    "server": {"type": "stdio", "command": "npx", "args": ["-y", "demo-mcp"]},
                },
            )
            self.assertNotEqual(original, load_manifests(root)[0].install_hash)

    def test_disabled_and_merge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "one.json",
                {
                    "name": "one",
                    "install": {},
                    "server": {"command": "one-server"},
                },
            )
            _write(
                root / "two.json",
                {
                    "name": "two",
                    "enabled": False,
                    "server": {"url": "http://example.test/mcp"},
                },
            )
            manifests = load_manifests(root)
            config = {"mcpServers": {"manual": {"command": "manual-server"}}}
            merged = merge_servers(config, manifests)
            self.assertIn("one", merged["mcpServers"])
            self.assertNotIn("two", merged["mcpServers"])
            self.assertEqual(merged["mcpServers"]["manual"], {"command": "manual-server"})

    def test_override_manifest_wins(self) -> None:
        manifest = McpManifest(
            name="same",
            server={"command": "new"},
            install={},
            enabled=True,
            description="",
            path=Path("same.json"),
        )
        merged = merge_servers({"mcpServers": {"same": {"command": "old"}}}, [manifest])
        self.assertEqual(merged["mcpServers"]["same"], {"command": "new"})

    def test_invalid_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "bad.json", {"name": "bad", "unknown": True, "server": {"command": "x"}})
            with self.assertRaises(OmpKaliError):
                load_manifests(root)

            root.joinpath("bad.json").unlink()
            _write(root / "bad.json", {"name": "bad", "server": {}})
            with self.assertRaises(OmpKaliError):
                load_manifests(root)

    def test_duplicate_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("a.json", "b.json"):
                _write(root / name, {"name": "dup", "server": {"command": "x"}})
            with self.assertRaises(OmpKaliError):
                load_manifests(root)

    def test_config_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp.json"
            self.assertEqual(load_config(path)["mcpServers"], {})
            config = {"mcpServers": {"one": {"command": "x"}}}
            self.assertTrue(write_config(path, config))
            self.assertEqual(load_config(path), config)
            self.assertFalse(write_config(path, config))


if __name__ == "__main__":
    unittest.main()
