from __future__ import annotations

import shlex
import sys

from .errors import OmpKaliError


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def shjoin(cmd: list[str]) -> str:
    return shlex.join(str(part) for part in cmd)


def confirm(
    message: str,
    *,
    assume_yes: bool = False,
    default: bool = False,
    require_tty: bool = True,
) -> bool:
    """Ask a yes/no question. Permission is never implied by silence."""
    if assume_yes:
        eprint(f"{message} [auto-approved]")
        return True
    if not sys.stdin.isatty():
        if require_tty:
            raise OmpKaliError(
                f"{message}\n"
                "No terminal is available to ask for permission. Re-run with --yes to approve explicitly."
            )
        eprint(f"{message} [no terminal: {'yes' if default else 'no'}]")
        return default

    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        sys.stderr.write(message + suffix)
        sys.stderr.flush()
        answer = sys.stdin.readline()
        if answer == "":
            return default
        answer = answer.strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        eprint("Please answer yes or no.")


def parse_port(value: str | int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise OmpKaliError(f"Invalid port: {value!r}") from exc
    if not 1 <= port <= 65535:
        raise OmpKaliError(f"Port out of range (1-65535): {port}")
    return port
