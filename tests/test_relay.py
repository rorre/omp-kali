import json
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


class _EchoHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        while True:
            data = self.request.recv(65536)
            if not data:
                return
            self.request.sendall(data)


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class RelayTests(unittest.TestCase):
    def test_forwards_tcp(self) -> None:
        target = _Server(("127.0.0.1", 0), _EchoHandler)
        target_thread = threading.Thread(target=target.serve_forever, daemon=True)
        target_thread.start()
        listen_port = _free_port()
        relay_script = Path(__file__).resolve().parents[1] / "src" / "omp_kali" / "infra" / "relay.py"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config = tmp_path / "relay.json"
            status = tmp_path / "status.json"
            pid = tmp_path / "relay.pid"
            log = tmp_path / "relay.log"
            config.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {
                                "id": "test",
                                "listen_host": "127.0.0.1",
                                "container": listen_port,
                                "target_host": "127.0.0.1",
                                "target_port": target.server_address[1],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            process = subprocess.Popen(
                [
                    sys.executable,
                    str(relay_script),
                    "--config",
                    str(config),
                    "--pid-file",
                    str(pid),
                    "--log-file",
                    str(log),
                    "--status-file",
                    str(status),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.time() + 5
                while time.time() < deadline and not status.exists():
                    if process.poll() is not None:
                        self.fail("relay exited early:" + (log.read_text() if log.exists() else ""))
                    time.sleep(0.02)
                self.assertTrue(status.exists(), "relay did not report status")

                with socket.create_connection(("127.0.0.1", listen_port), timeout=2) as client:
                    client.sendall(b"omp-kali")
                    self.assertEqual(client.recv(64), b"omp-kali")
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                target.shutdown()
                target.server_close()


if __name__ == "__main__":
    unittest.main()
