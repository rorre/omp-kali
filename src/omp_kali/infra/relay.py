#!/usr/bin/env python3
"""TCP relay used inside the omp-kali container.

Listens on container-side loopback ports and forwards to host endpoints
(default ``host.containers.internal``).  Runs detached via ``podman exec -d``.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


LogFn = Callable[[str], None]
USAGE = (
    "usage: relay.py --config PATH [--pid-file PATH] "
    "[--log-file PATH] [--status-file PATH]"
)


@dataclass
class RelayArgs:
    config: str
    pid_file: str | None = None
    log_file: str | None = None
    status_file: str | None = None


def _make_logger(log_file: str | None) -> LogFn:
    def log(message: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
        print(line, file=sys.stderr, flush=True)
        if log_file:
            try:
                with open(log_file, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                pass

    return log


def _write_status(path: str | None, status: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionError, RuntimeError):
            pass


def _handler(target_host: str, target_port: int, log: LogFn):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            remote_reader, remote_writer = await asyncio.open_connection(target_host, target_port)
        except OSError as exc:
            log(f"connect {target_host}:{target_port} failed for {peer}: {exc}")
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, RuntimeError):
                pass
            return

        log(f"forward {peer} -> {target_host}:{target_port}")
        try:
            await asyncio.gather(
                _pipe(reader, remote_writer),
                _pipe(remote_reader, writer),
            )
        finally:
            for stream in (remote_writer, writer):
                try:
                    stream.close()
                except RuntimeError:
                    pass

    return handle


async def _serve(args: RelayArgs, status: dict[str, Any], log: LogFn) -> int:
    config_path = Path(args.config)
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log(f"cannot read relay config {config_path}: {exc}")
        return 2

    mappings = config.get("mappings", [])
    if not isinstance(mappings, list):
        log("relay config: mappings must be a list")
        return 2

    servers: list[asyncio.AbstractServer] = []
    for mapping in mappings:
        try:
            listen_host = str(mapping.get("listen_host", "127.0.0.1"))
            listen_port = int(mapping["container"])
            target_host = str(mapping.get("target_host", "host.containers.internal"))
            target_port = int(mapping["target_port"])
            mapping_id = str(mapping.get("id", f"{listen_host}:{listen_port}"))
        except (KeyError, TypeError, ValueError) as exc:
            message = f"invalid mapping {mapping!r}: {exc}"
            status["errors"].append(message)
            log(message)
            continue

        try:
            server = await asyncio.start_server(
                _handler(target_host, target_port, log),
                listen_host,
                listen_port,
                reuse_address=True,
            )
        except OSError as exc:
            message = f"listen {listen_host}:{listen_port} failed: {exc}"
            status["errors"].append(message)
            log(message)
            continue

        servers.append(server)
        status["listeners"].append(
            {
                "id": mapping_id,
                "listen": f"{listen_host}:{listen_port}",
                "target": f"{target_host}:{target_port}",
                "ok": True,
            }
        )
        log(f"listening {listen_host}:{listen_port} -> {target_host}:{target_port}")

    _write_status(args.status_file, status)
    if not servers:
        log("no relay listeners started")
        return 1

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    log("relay ready")
    await stop.wait()
    log("relay stopping")
    for server in servers:
        server.close()
    await asyncio.gather(*(server.wait_closed() for server in servers), return_exceptions=True)
    return 0


def _parse_argv(argv: list[str]) -> RelayArgs:
    values: dict[str, str | None] = {
        "config": None,
        "pid_file": None,
        "log_file": None,
        "status_file": None,
    }
    names = {
        "--config": "config",
        "--pid-file": "pid_file",
        "--log-file": "log_file",
        "--status-file": "status_file",
    }
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in {"-h", "--help"}:
            print(USAGE)
            raise SystemExit(0)
        if token in names:
            if i + 1 >= len(argv):
                raise SystemExit(f"{USAGE}\nmissing value for {token}")
            values[names[token]] = argv[i + 1]
            i += 2
            continue
        if token.startswith("--") and "=" in token:
            key, value = token.split("=", 1)
            if key in names:
                values[names[key]] = value
                i += 1
                continue
        raise SystemExit(f"{USAGE}\nunknown argument: {token}")
    if not values["config"]:
        raise SystemExit(f"{USAGE}\n--config is required")
    return RelayArgs(**values)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> int:
    args = _parse_argv(list(sys.argv[1:] if argv is None else argv))

    status: dict[str, Any] = {"listeners": [], "errors": []}
    log = _make_logger(args.log_file)

    if args.pid_file:
        Path(args.pid_file).write_text(f"{os.getpid()}\n", encoding="utf-8")

        def _cleanup_pid() -> None:
            try:
                if Path(args.pid_file).read_text(encoding="utf-8").strip() == str(os.getpid()):
                    Path(args.pid_file).unlink(missing_ok=True)
            except OSError:
                pass

        atexit.register(_cleanup_pid)

    try:
        return asyncio.run(_serve(args, status, log))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
