from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional

import typer

from . import __version__, paths
from .container import CONTAINER, Container, Runner, container_workdir
from .errors import OmpKaliError
from .mcp import load_manifests
from .ports import (
    DEFAULT_BIND,
    RELAY_TARGET,
    PortMapping,
    approved_now,
    find_conflict,
    mappings_from_state,
    mappings_to_state,
)
from .util import confirm, eprint, parse_port

app = typer.Typer(
    add_completion=False,
    help="Run an isolated omp instance inside a Kali Linux podman container.",
)
ports_app = typer.Typer(add_completion=False, help="Manage permissioned port forwards.")
app.add_typer(ports_app, name="ports")
mcp_app = typer.Typer(add_completion=False, help="Inspect and install repository MCP server manifests.")
app.add_typer(mcp_app, name="mcp")

COMMANDS = {"run", "shell", "create", "rm", "status", "sync", "update", "ports", "mcp", "provision"}
GLOBAL_FLAGS = {"--gui", "--yes", "-y", "--dry-run", "--verbose", "-v"}
GLOBAL_VALUE_OPTIONS = {"--dir", "-d", "--pass-env"}


class Direction(str, Enum):
    host_to_container = "host-to-container"
    container_to_host = "container-to-host"


@dataclass
class CommonOptions:
    directory: Optional[str] = None
    gui: bool = False
    pass_env: List[str] = field(default_factory=list)
    yes: bool = False
    dry_run: bool = False
    verbose: bool = False


def _merge_common(
    ctx: typer.Context,
    *,
    directory: Optional[str],
    gui: Optional[bool],
    pass_env: Optional[List[str]],
    yes: Optional[bool],
    dry_run: Optional[bool],
    verbose: Optional[bool],
) -> CommonOptions:
    base = ctx.obj if isinstance(ctx.obj, CommonOptions) else CommonOptions()
    return CommonOptions(
        directory=directory if directory is not None else base.directory,
        gui=bool(gui) if gui is not None else base.gui,
        pass_env=list(dict.fromkeys([*base.pass_env, *(pass_env or [])])),
        yes=bool(yes) if yes is not None else base.yes,
        dry_run=bool(dry_run) if dry_run is not None else base.dry_run,
        verbose=bool(verbose) if verbose is not None else base.verbose,
    )


def _port_arg(value: int) -> int:
    try:
        return parse_port(value)
    except OmpKaliError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.callback()
def configure(
    ctx: typer.Context,
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: bool = typer.Option(False, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print commands without executing them."),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print every command."),
    version: bool = typer.Option(False, "--version", help="Show version and exit.", is_eager=True),
) -> None:
    if version:
        typer.echo(f"omp-kali {__version__}")
        raise typer.Exit()
    ctx.obj = CommonOptions(
        directory=directory,
        gui=gui,
        pass_env=list(pass_env or []),
        yes=yes,
        dry_run=dry_run,
        verbose=verbose,
    )


@app.command("run", help="Start omp in the container (default).", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run_command(
    ctx: typer.Context,
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    omp_args = list(ctx.args)
    if omp_args and omp_args[0] == "--":
        omp_args = omp_args[1:]
    raise typer.Exit(_ensure_and_run(opts, omp_args=omp_args, shell=False, shell_command=None))


@app.command("shell", help="Drop into a shell in the container.")
def shell_command(
    ctx: typer.Context,
    command: Optional[str] = typer.Option(None, "-c", "--command", help="Run one shell command instead of an interactive shell."),
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(_ensure_and_run(opts, omp_args=[], shell=True, shell_command=command))


@app.command("create", help="Create/provision the container.")
def create_command(
    ctx: typer.Context,
    recreate: bool = typer.Option(False, "--recreate", help="Remove and recreate the container."),
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(_cmd_create(opts, recreate=recreate))


@app.command("rm", help="Remove the container.")
def rm_command(
    ctx: typer.Context,
    purge: bool = typer.Option(False, "--purge", help="Also delete the container home (sessions/auth)."),
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(_cmd_rm(opts, purge=purge))


@app.command("status", help="Show container and asset status.")
def status_command(
    ctx: typer.Context,
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(_cmd_status(opts))


@app.command("sync", help="Re-link skills/agents/MCP into the container.")
def sync_command(
    ctx: typer.Context,
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(_cmd_sync(opts))


@app.command("update", help="Copy the host omp binary into the container.")
def update_command(
    ctx: typer.Context,
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(_cmd_update(opts))


@ports_app.command("list", help="List configured port forwards.")
def ports_list(
    ctx: typer.Context,
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(_cmd_ports_list(opts))


@ports_app.command("add", help="Add a port forward (asks for permission).")
def ports_add(
    ctx: typer.Context,
    direction: Direction = typer.Argument(..., help="host-to-container or container-to-host."),
    host: int = typer.Option(..., "--host", help="Host port.", callback=_port_arg),
    container: int = typer.Option(..., "--container", help="Container port.", callback=_port_arg),
    bind: str = typer.Option(DEFAULT_BIND, "--bind", help=f"Bind address (default: {DEFAULT_BIND})."),
    target_host: str = typer.Option(
        RELAY_TARGET, "--target-host", help=f"host-to-container relay target (default: {RELAY_TARGET})."
    ),
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(
        _cmd_ports_add(
            opts,
            direction=direction.value,
            host=host,
            container=container,
            bind=bind,
            target_host=target_host,
        )
    )


@ports_app.command("remove", help="Remove a port forward by id.")
def ports_remove(
    ctx: typer.Context,
    mapping_id: str = typer.Argument(..., metavar="ID"),
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(_cmd_ports_remove(opts, mapping_id))


@app.command("provision", help="Run changed provisioning scripts and MCP installs.")
def provision_command(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", help="Re-run all scripts and MCP installs."),
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(_cmd_provision(opts, force=force))


@mcp_app.command("list", help="List MCP server manifests.")
def mcp_list_command(
    ctx: typer.Context,
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(_cmd_mcp_list(opts))


@mcp_app.command("sync", help="Install and configure manifest MCP servers.")
def mcp_sync_command(
    ctx: typer.Context,
    force: bool = typer.Option(False, "--force", help="Re-run MCP install steps."),
    directory: Optional[str] = typer.Option(None, "--dir", "-d", help="Directory to run in (default: cwd)."),
    gui: Optional[bool] = typer.Option(None, "--gui", help="Forward X11/Wayland from the host."),
    pass_env: Optional[List[str]] = typer.Option(
        None, "--pass-env", metavar="NAME", help="Pass one host environment variable (repeatable)."
    ),
    yes: Optional[bool] = typer.Option(None, "--yes", "-y", help="Approve permission prompts without asking."),
    dry_run: Optional[bool] = typer.Option(None, "--dry-run", help="Print commands without executing them."),
    verbose: Optional[bool] = typer.Option(None, "--verbose", "-v", help="Print every command."),
) -> None:
    opts = _merge_common(
        ctx, directory=directory, gui=gui, pass_env=pass_env, yes=yes, dry_run=dry_run, verbose=verbose
    )
    raise typer.Exit(_cmd_mcp_sync(opts, force=force))


def _resolve_dir(value: Optional[str]) -> Path:
    path = Path(value).expanduser() if value else Path.cwd()
    path = path.resolve()
    if not path.is_dir():
        raise OmpKaliError(f"Directory does not exist: {path}")
    return path


def _state_cwd(state: dict[str, Any], opts: CommonOptions | None = None) -> Path:
    value = state.get("workspace")
    if value:
        return Path(value).expanduser().resolve()
    return _resolve_dir(opts.directory if opts else None)


def _state_gui(state: dict[str, Any], opts: CommonOptions | None = None) -> bool:
    if "gui" in state:
        return bool(state["gui"])
    return bool(opts.gui) if opts else False


def _pass_env(names: List[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name in names:
        if name not in os.environ:
            raise OmpKaliError(f"Cannot pass unset host environment variable: {name}")
        values[name] = os.environ[name]
    return values


def _listening_addresses(port: int) -> List[str]:
    try:
        result = subprocess.run(["ss", "-ltnH"], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return []
    addresses: List[str] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        local = fields[3]
        if local.endswith(f":{port}"):
            address = local.rsplit(":", 1)[0].strip("[]")
            addresses.append(address)
    return addresses


def _warn_loopback(mapping: PortMapping) -> None:
    if mapping.direction != "host-to-container":
        return
    addresses = _listening_addresses(mapping.host)
    if not addresses:
        eprint(
            f"warning: no host listener currently found on port {mapping.host}; "
            "the relay will work once the host service is running and reachable."
        )
        return
    loopback_only = all(address in {"127.0.0.1", "::1", "localhost"} for address in addresses)
    if loopback_only:
        eprint(
            f"warning: host port {mapping.host} listens only on loopback "
            f"({', '.join(addresses)}). The in-container relay reaches the host through "
            "host.containers.internal and cannot reach loopback-only services; bind the host "
            "service to 0.0.0.0 (or a non-loopback address) instead."
        )


def _load_container(opts: CommonOptions) -> tuple[Container, dict[str, Any]]:
    runner = Runner(dry_run=opts.dry_run, verbose=opts.verbose)
    return Container(runner), paths.load_state()


def _ensure_and_run(
    opts: CommonOptions,
    *,
    omp_args: List[str],
    shell: bool,
    shell_command: Optional[str],
) -> int:
    container, state = _load_container(opts)
    cwd = _resolve_dir(opts.directory)
    container.ensure(state, assume_yes=opts.yes, cwd=cwd, gui=opts.gui)
    if not opts.dry_run:
        paths.save_state(state)
    container.restart_relays(mappings_from_state(state))

    if opts.gui and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        eprint("warning: --gui requested but no host DISPLAY or WAYLAND_DISPLAY is set.")

    return container.enter(
        cwd=cwd,
        gui=opts.gui,
        pass_env=_pass_env(opts.pass_env),
        omp_args=[] if shell else omp_args,
        shell=shell,
        shell_command=shell_command,
    )


def _cmd_create(opts: CommonOptions, *, recreate: bool) -> int:
    container, state = _load_container(opts)
    cwd = _resolve_dir(opts.directory)
    if recreate and container.exists() and not opts.dry_run:
        container.request_recreate(
            state,
            cwd=cwd,
            gui=opts.gui,
            assume_yes=opts.yes,
            reason="Recreate requested.",
        )
    else:
        container.ensure(state, assume_yes=opts.yes, cwd=cwd, gui=opts.gui)
    if not opts.dry_run:
        paths.save_state(state)
    container.restart_relays(mappings_from_state(state))
    return 0


def _cmd_rm(opts: CommonOptions, *, purge: bool) -> int:
    container, state = _load_container(opts)
    if opts.dry_run:
        eprint(f"[dry-run] would remove container {CONTAINER}" + (" and its home" if purge else ""))
        return 0
    message = f"Remove container {CONTAINER}?"
    if purge:
        message += "\nThis also deletes the podman volume omp-kali-home (container omp sessions and auth)."
    if not confirm(message, assume_yes=opts.yes, default=False):
        eprint("Cancelled.")
        return 0
    container.remove(purge=purge)
    state["applied_publish"] = []
    paths.save_state(state)
    return 0


def _cmd_sync(opts: CommonOptions) -> int:
    container, state = _load_container(opts)
    container.provision()
    container.restart_relays(mappings_from_state(state))
    return 0


def _cmd_update(opts: CommonOptions) -> int:
    container, _state = _load_container(opts)
    container.provision(force_binary=True)
    return 0


def _cmd_status(opts: CommonOptions) -> int:
    container, state = _load_container(opts)
    info = container.status()
    print(f"container: {CONTAINER}")
    print(f"exists:    {'yes' if info['exists'] else 'no'}")
    if info["exists"]:
        print(f"running:   {'yes' if info['running'] else 'no'}")
        print(f"managed:   {'yes' if info['managed'] else 'no'}")
        print(f"image:     {info['image'] or '(unknown)'}")
        print(f"kali:      {info['kali'] or '(not running)'}")
    print(f"home:      {info['home']}")
    print(f"volume:    {info['volume']}")
    if info['exists']:
        source = info['workspace']
        container_path = container_workdir(Path(source)) if source else None
        print(f"workspace: {container_path or '(none)'} (host: {source or '(none)'})")
        print(f"gui:       {'yes' if info['gui'] else 'no'}")
    if info.get("omp_version"):
        print(f"omp:       {info['omp_version']}")
    mappings = mappings_from_state(state)
    print("ports:")
    if not mappings:
        print("  (none)")
    for mapping in mappings:
        print(f"  {mapping.id}  {mapping.direction}  {mapping.describe()}  [{mapping.method}]")
    return 0


def _cmd_ports_list(opts: CommonOptions) -> int:
    _container, state = _load_container(opts)
    mappings = mappings_from_state(state)
    if not mappings:
        print("No port mappings configured.")
        return 0
    for mapping in mappings:
        approved = mapping.approved_at or "unknown"
        print(f"{mapping.id}")
        print(f"  direction: {mapping.direction}")
        print(f"  mapping:   {mapping.describe()}")
        print(f"  method:    {mapping.method}")
        print(f"  approved:  {approved}")
    return 0


def _cmd_ports_add(
    opts: CommonOptions,
    *,
    direction: str,
    host: int,
    container: int,
    bind: str,
    target_host: str,
) -> int:
    container_, state = _load_container(opts)
    mappings = mappings_from_state(state)
    candidate = PortMapping(
        direction=direction,
        host=host,
        container=container,
        bind=bind,
        target_host=target_host,
    )
    conflict = find_conflict(mappings, candidate)
    if conflict is not None:
        raise OmpKaliError(f"Port mapping conflicts with existing {conflict.id}")

    if opts.dry_run:
        eprint(f"[dry-run] would add {candidate.id}: {candidate.describe()}")
        return 0

    _warn_loopback(candidate)
    if not confirm(candidate.permission_message(), assume_yes=opts.yes, default=False):
        eprint("Not added.")
        return 0

    approved = approved_now(candidate)
    mappings.append(approved)
    state["ports"] = mappings_to_state(mappings)
    paths.save_state(state)
    eprint(f"Added {approved.id}")

    if container_.exists():
        if approved.direction == "container-to-host":
            if confirm(
                f"Recreate {CONTAINER} now to publish the port? "
                "Running container processes stop; the container home is preserved.",
                assume_yes=opts.yes,
                default=True,
            ):
                container_.recreate(state, cwd=_state_cwd(state, opts), gui=_state_gui(state, opts))
                paths.save_state(state)
        else:
            container_.provision()
            container_.restart_relays(mappings)
    return 0


def _cmd_ports_remove(opts: CommonOptions, mapping_id: str) -> int:
    container_, state = _load_container(opts)
    mappings = mappings_from_state(state)
    match = next((mapping for mapping in mappings if mapping.id == mapping_id), None)
    if match is None:
        raise OmpKaliError(f"No port mapping with id {mapping_id!r}")

    if opts.dry_run:
        eprint(f"[dry-run] would remove {match.id}")
        return 0

    mappings = [mapping for mapping in mappings if mapping.id != mapping_id]
    state["ports"] = mappings_to_state(mappings)
    paths.save_state(state)
    eprint(f"Removed {match.id}")

    if container_.exists():
        if match.direction == "container-to-host":
            if confirm(
                f"Recreate {CONTAINER} now to stop publishing {match.bind}:{match.host}?",
                assume_yes=opts.yes,
                default=True,
            ):
                container_.recreate(state, cwd=_state_cwd(state, opts), gui=_state_gui(state, opts))
                paths.save_state(state)
            else:
                eprint("warning: the port remains published until the container is recreated.")
        else:
            container_.restart_relays(mappings)
    return 0


def _cmd_provision(opts: CommonOptions, *, force: bool) -> int:
    container, state = _load_container(opts)
    cwd = _state_cwd(state, opts)
    container.ensure(state, assume_yes=opts.yes, cwd=cwd, gui=_state_gui(state, opts), provision=not force)
    if not opts.dry_run:
        paths.save_state(state)
    if force:
        container.provision(force_hooks=True, force_mcp=True)
    return 0


def _cmd_mcp_sync(opts: CommonOptions, *, force: bool) -> int:
    container, state = _load_container(opts)
    cwd = _state_cwd(state, opts)
    container.ensure(state, assume_yes=opts.yes, cwd=cwd, gui=_state_gui(state, opts), provision=not force)
    if not opts.dry_run:
        paths.save_state(state)
    if force:
        container.provision(force_mcp=True)
    return 0


def _cmd_mcp_list(opts: CommonOptions) -> int:
    repo = paths.repo_root()
    manifests = load_manifests(repo / paths.MCP_DIR / paths.MCP_SERVERS_DIR)
    if not manifests:
        print("No MCP server manifests. Add JSON files under mcp/servers/.")
        return 0
    print(f"{'NAME':<24} {'ENABLED':<8} {'SERVER':<40} INSTALL")
    for manifest in manifests:
        enabled = "yes" if manifest.enabled else "no"
        print(
            f"{manifest.name:<24} {enabled:<8} {manifest.server_summary():<40} "
            f"{manifest.install_summary()}"
        )
        if manifest.description:
            print(f"  {manifest.description}")
    return 0


def _before_double_dash(argv: List[str]) -> List[str]:
    try:
        return argv[: argv.index("--")]
    except ValueError:
        return argv


def _version_requested(argv: List[str]) -> bool:
    for token in _before_double_dash(argv):
        if token in COMMANDS:
            return False
        if token == "--version":
            return True
    return False


def _help_requested(argv: List[str]) -> bool:
    return any(token in {"--help", "-h"} for token in _before_double_dash(argv))


def _find_command(argv: List[str]) -> Optional[str]:
    tokens = _before_double_dash(argv)
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in GLOBAL_FLAGS or token in {"--version", "--help", "-h"}:
            i += 1
            continue
        if token in GLOBAL_VALUE_OPTIONS:
            i += 2
            continue
        if token.startswith(("--dir=", "--pass-env=", "-d=")):
            i += 1
            continue
        if token in COMMANDS:
            return token
        return None
    return None


def normalize_argv(argv: List[str]) -> List[str]:
    """Insert the default `run` command and leave omp arguments untouched."""
    if _version_requested(argv):
        return ["--version"]
    if _help_requested(argv):
        return list(argv)
    if _find_command(argv) is None:
        return ["run", *argv]
    return list(argv)


def main(argv: Optional[List[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if _version_requested(raw):
        typer.echo(f"omp-kali {__version__}")
        return 0
    try:
        app(args=normalize_argv(raw), prog_name="omp-kali")
    except OmpKaliError as exc:
        eprint(f"omp-kali: error: {exc}")
        return 1
    except KeyboardInterrupt:
        eprint("interrupted")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
