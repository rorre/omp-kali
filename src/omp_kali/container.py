from __future__ import annotations

import hashlib
import json
import os
import pwd
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import paths, provisioning
from .errors import OmpKaliError
from .mcp import McpManifest, load_config as load_mcp_config
from .mcp import load_manifests, merge_servers, write_config as write_mcp_config
from .ports import PortMapping, desired_publish_specs, mappings_from_state, relay_payloads
from .util import confirm, eprint, shjoin

CONTAINER = "omp-kali"
BASE_IMAGE = "docker.io/kalilinux/kali-rolling:latest"
IMAGE_REPO = "localhost/omp-kali"
VOLUME = "omp-kali-home"
CONTAINER_HOME = "/home/omp"
WORKSPACE_ROOT = "/workspace"
MANAGED_LABEL = "omp-kali.managed"
WORKSPACE_LABEL = "omp-kali.workspace"
GUI_LABEL = "omp-kali.gui"
PODMAN = "podman"


def container_workdir(cwd: Path) -> Path:
    """Deterministic in-container path for one host directory."""
    return Path(WORKSPACE_ROOT) / (cwd.name or "root")


def create_command(cwd: Path, gui: bool, publish_specs: list[str], repo: Path, image: str) -> list[str]:
    """Build the raw podman create-and-start command.

    Only the selected directory is mounted as project data, at
    /workspace/<directory-name>. Changing that directory requires recreating
    the container because podman cannot alter a running container's mounts.
    """
    uid = os.getuid()
    gid = os.getgid()
    cmd = [
        PODMAN,
        "run",
        "-d",
        "--name",
        CONTAINER,
        "--hostname",
        CONTAINER,
        "--label",
        f"{MANAGED_LABEL}=true",
        "--label",
        f"{WORKSPACE_LABEL}={cwd}",
        "--label",
        f"{GUI_LABEL}={'true' if gui else 'false'}",
        "--userns=keep-id",
        "--security-opt",
        "label=disable",
        "--env",
        f"HOME={CONTAINER_HOME}",
        "--env",
        f"USER={pwd.getpwuid(uid).pw_name}",
        "--env",
        "LANG=C.UTF-8",
        "--volume",
        f"{VOLUME}:{CONTAINER_HOME}:rw",
        "--volume",
        f"{cwd}:{container_workdir(cwd)}:rw",
        "--volume",
        f"{repo / 'skills'}:{CONTAINER_HOME}/.omp/agent/skills:rw",
        "--volume",
        f"{repo / 'agents'}:{CONTAINER_HOME}/.omp/agent/agents:rw",
        "--volume",
        f"{repo / 'mcp' / 'mcp.json'}:{CONTAINER_HOME}/.omp/agent/mcp.json:rw",
    ]
    for spec in publish_specs:
        cmd.extend(["--publish", spec])
    if gui:
        cmd.extend(_gui_mounts())
    cmd.extend([image, "sleep", "infinity"])
    return cmd


def _gui_mounts() -> list[str]:
    mounts: list[str] = []
    x11 = Path("/tmp/.X11-unix") / f"X{os.environ.get('DISPLAY', ':0').lstrip(':')}"
    if x11.exists():
        mounts.extend(["--volume", f"{x11}:{x11}:rw"])
    xauth = os.environ.get("XAUTHORITY")
    if xauth and Path(xauth).exists():
        mounts.extend(["--volume", f"{xauth}:/run/omp-kali/Xauthority:ro"])
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        for name in (os.environ.get("WAYLAND_DISPLAY"), "bus"):
            if not name:
                continue
            source = Path(runtime) / name
            if source.exists():
                mounts.extend(["--volume", f"{source}:{source}:rw"])
    return mounts


class Runner:
    def __init__(self, *, dry_run: bool = False, verbose: bool = False) -> None:
        self.dry_run = dry_run
        self.verbose = verbose
        self.log_file: Any | None = None

    def log(self, text: str) -> None:
        if self.log_file is None:
            return
        self.log_file.write(text)
        self.log_file.flush()

    def run(
        self,
        cmd: list[str],
        *,
        capture: bool = False,
        check: bool = True,
        quiet: bool = False,
        input_text: str | None = None,
        stream: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if self.dry_run:
            eprint("+ " + shjoin(cmd))
            self.log(f"$ {shjoin(cmd)}\n")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        self.log(f"\n[{time.strftime('%Y-%m-%dT%H:%M:%S%z')}] $ {shjoin(cmd)}\n")
        if self.verbose and not quiet:
            eprint("+ " + shjoin(cmd))
        streamed = stream and input_text is None
        try:
            if streamed:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                chunks: list[str] = []
                assert process.stdout is not None
                try:
                    for line in process.stdout:
                        chunks.append(line)
                        sys.stdout.write(line)
                        sys.stdout.flush()
                        self.log(line)
                finally:
                    process.stdout.close()
                result = subprocess.CompletedProcess(cmd, process.wait(), "".join(chunks), "")
            else:
                result = subprocess.run(
                    cmd, text=True, capture_output=capture, input=input_text, check=False
                )
        except FileNotFoundError as exc:
            raise OmpKaliError(
                f"Required command not found: {cmd[0]}. Install podman, then retry."
            ) from exc
        if check and result.returncode != 0:
            detail = ""
            if capture and not streamed:
                detail = ((result.stdout or "") + (result.stderr or "")).strip()
            if detail:
                raise OmpKaliError(f"Command failed: {shjoin(cmd)}\n{detail}")
            raise OmpKaliError(f"Command failed ({result.returncode}): {shjoin(cmd)}")
        return result


class Container:
    def __init__(self, runner: Runner | None = None) -> None:
        self.runner = runner or Runner()
        self.user = pwd.getpwuid(os.getuid()).pw_name
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.home = CONTAINER_HOME

    # -- low level -----------------------------------------------------

    def _note(self, message: str) -> None:
        eprint(message)
        self.runner.log(message + "\n")

    def _inspect(self, fmt: str) -> str:
        if self.runner.dry_run:
            return ""
        result = self.runner.run(
            [PODMAN, "inspect", "-f", fmt, CONTAINER],
            capture=True,
            check=False,
            quiet=True,
        )
        return (result.stdout or "").strip()

    def podman_exec(
        self,
        args: list[str],
        *,
        check: bool = True,
        detach: bool = False,
        stream: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [PODMAN, "exec"]
        if detach:
            cmd.append("-d")
        cmd.extend([
            "-u", f"{self.uid}:{self.gid}",
            "-e", f"HOME={CONTAINER_HOME}",
            "-e", f"USER={self.user}",
            CONTAINER,
            *args,
        ])
        return self.runner.run(cmd, capture=not stream, check=check, quiet=not detach, stream=stream)

    def root_exec(
        self,
        args: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
        stream: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [PODMAN, "exec", "-u", "0"]
        for key, value in (env or {}).items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.extend([CONTAINER, *args])
        return self.runner.run(cmd, capture=not stream, check=check, quiet=True, stream=stream)

    def require_tools(self) -> None:
        if shutil.which(PODMAN) is None:
            raise OmpKaliError("podman is required. Install podman, then retry.")

    def image_hash(self, repo: Path) -> str:
        dockerfile = repo / "Dockerfile"
        if not dockerfile.is_file():
            raise OmpKaliError(f"Dockerfile not found: {dockerfile}")
        digest = hashlib.sha256()
        for path in [dockerfile, *sorted((repo / paths.PROVISIONING_DIR).glob("*.sh"))]:
            digest.update(path.name.encode("utf-8") + b"\0")
            digest.update(path.read_bytes())
        digest.update(BASE_IMAGE.encode("utf-8") + b"\0" + self.user.encode("utf-8"))
        return digest.hexdigest()

    def image_tag(self, repo: Path) -> str:
        return f"{IMAGE_REPO}:local-{self.image_hash(repo)[:16]}"

    def image_exists(self, tag: str) -> bool:
        if self.runner.dry_run:
            return False
        result = self.runner.run(
            [PODMAN, "image", "exists", tag], capture=True, check=False, quiet=True
        )
        return result.returncode == 0

    def ensure_image(self, repo: Path) -> str:
        tag = self.image_tag(repo)
        build = [
            PODMAN,
            "build",
            "-f",
            str(repo / "Dockerfile"),
            "-t",
            tag,
            "--build-arg",
            f"BASE_IMAGE={BASE_IMAGE}",
            "--build-arg",
            f"CONTAINER_USER={self.user}",
            "--build-arg",
            f"PROVISION_HASH={self.image_hash(repo)}",
            str(repo),
        ]
        if self.runner.dry_run:
            self.runner.run(build)
            return tag
        if self.image_exists(tag):
            return tag
        log_path = paths.STATE_DIR / paths.PROVISION_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.runner.log_file = log_path.open("a", encoding="utf-8")
        os.chmod(log_path, 0o600)
        try:
            self.runner.log(f"\n=== {time.strftime('%Y-%m-%dT%H:%M:%S%z')} building image {tag} ===\n")
            self._note(f"image: building {tag}")
            self.runner.run(build, capture=False, stream=True)
        finally:
            self.runner.log_file.close()
            self.runner.log_file = None
        return tag

    def exists(self) -> bool:
        if self.runner.dry_run:
            return False
        result = self.runner.run(
            [PODMAN, "container", "exists", CONTAINER], capture=True, check=False, quiet=True
        )
        return result.returncode == 0

    def running(self) -> bool:
        if self.runner.dry_run:
            return False
        return self._inspect("{{.State.Running}}").lower() == "true"

    def managed(self) -> bool:
        if self.runner.dry_run:
            return True
        return self._inspect('{{index .Config.Labels "' + MANAGED_LABEL + '"}}') == "true"

    def label(self, name: str) -> str:
        return self._inspect('{{index .Config.Labels "' + name + '"}}')

    def container_id(self) -> str:
        return self._inspect("{{.Id}}")

    def image_name(self) -> str:
        return self._inspect("{{.Config.Image}}")

    def workspace_source(self) -> str:
        return self.label(WORKSPACE_LABEL)

    def ensure_volume(self) -> None:
        if self.runner.dry_run:
            return
        result = self.runner.run(
            [PODMAN, "volume", "exists", VOLUME], capture=True, check=False, quiet=True
        )
        if result.returncode != 0:
            self.runner.run([PODMAN, "volume", "create", VOLUME], capture=True)

    def check_kali(self) -> str:
        if self.runner.dry_run:
            return "Kali (dry run)"
        result = self.root_exec(["cat", "/etc/os-release"], check=False)
        text = (result.stdout or "").strip()
        if result.returncode != 0 or ("ID=kali" not in text and "Kali" not in text):
            raise OmpKaliError(f"Container {CONTAINER} is not a Kali Linux container.")
        for line in text.splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
        return "Kali Linux"

    def actual_publish_specs(self) -> list[str]:
        if self.runner.dry_run:
            return []
        result = self.runner.run(
            [PODMAN, "inspect", "-f", "{{json .HostConfig.PortBindings}}", CONTAINER],
            capture=True,
            check=False,
            quiet=True,
        )
        raw = (result.stdout or "").strip()
        if not raw:
            return []
        try:
            bindings = json.loads(raw)
        except json.JSONDecodeError:
            return []
        specs: list[str] = []
        for container_port, entries in (bindings or {}).items():
            if not container_port.endswith("/tcp"):
                continue
            port = container_port.split("/", 1)[0]
            for entry in entries or []:
                host_ip = entry.get("HostIp") or "0.0.0.0"
                host_port = entry.get("HostPort")
                if host_port:
                    specs.append(f"{host_ip}:{host_port}:{port}")
        return sorted(specs)

    # -- lifecycle -----------------------------------------------------

    def ensure(
        self,
        state: dict[str, Any],
        *,
        assume_yes: bool = False,
        cwd: Path,
        gui: bool,
        provision: bool = True,
    ) -> None:
        self.require_tools()
        self.ensure_volume()
        repo = paths.ensure_repo_assets()[0]
        image = self.ensure_image(repo)
        desired = desired_publish_specs(mappings_from_state(state))
        cwd = cwd.resolve()

        if self.runner.dry_run:
            self.runner.run(create_command(cwd, gui, desired, repo, image))
            if provision:
                self.provision()
            return

        if not self.exists():
            eprint(f"Creating podman container {CONTAINER} from {image}...")
            self.runner.run(create_command(cwd, gui, desired, repo, image), capture=False)
            state["applied_publish"] = desired
        else:
            if not self.managed():
                raise OmpKaliError(
                    f"A container named {CONTAINER} exists and was not created by omp-kali."
                )
            existing_ws = self.workspace_source()
            existing_gui = self.label(GUI_LABEL) == "true"
            existing_image = self.image_name()
            if existing_image and existing_image != image:
                self.request_recreate(
                    state,
                    cwd=cwd,
                    gui=gui,
                    image=image,
                    assume_yes=assume_yes,
                    reason=f"Container image changed ({existing_image} -> {image}).",
                )
                return
            if existing_ws != str(cwd) or existing_gui != gui:
                self.request_recreate(
                    state,
                    cwd=cwd,
                    gui=gui,
                    image=image,
                    assume_yes=assume_yes,
                    reason=f"Container workspace/GUI changed ({existing_ws or '(none)'} -> {cwd}, gui={gui}).",
                )
                return
            if not self.running():
                eprint(f"Starting container {CONTAINER}...")
                self.runner.run([PODMAN, "start", CONTAINER], capture=True)
            actual = self.actual_publish_specs()
            if set(actual) != set(desired):
                self.request_recreate(
                    state,
                    cwd=cwd,
                    gui=gui,
                    image=image,
                    assume_yes=assume_yes,
                    reason=(
                        "Published port set differs from the configured set:\n"
                        f"  container: {', '.join(actual) or '(none)'}\n"
                        f"  requested: {', '.join(desired) or '(none)'}"
                    ),
                )
                return

        if not self.running():
            self.runner.run([PODMAN, "start", CONTAINER], capture=True)
        self.check_kali()
        state["workspace"] = str(cwd)
        state["gui"] = gui
        if provision:
            self.provision()

    def request_recreate(
        self,
        state: dict[str, Any],
        *,
        cwd: Path,
        gui: bool,
        image: str | None = None,
        assume_yes: bool = False,
        reason: str,
    ) -> None:
        message = (
            f"{reason}\n"
            f"Remove and recreate container {CONTAINER}? "
            "Other containers are not affected. The home volume and omp sessions are preserved."
        )
        if not confirm(message, assume_yes=assume_yes, default=False):
            raise OmpKaliError("Cancelled; container left unchanged.")
        self.recreate(state, cwd=cwd, gui=gui, image=image)

    def recreate(self, state: dict[str, Any], *, cwd: Path, gui: bool, image: str | None = None) -> None:
        if self.exists():
            eprint(f"Removing container {CONTAINER}...")
            self.runner.run([PODMAN, "rm", "-f", "-t", "0", CONTAINER], capture=True)
        desired = desired_publish_specs(mappings_from_state(state))
        repo = paths.ensure_repo_assets()[0]
        if image is None:
            image = self.ensure_image(repo)
        cwd = cwd.resolve()
        eprint(f"Recreating podman container {CONTAINER}...")
        self.runner.run(create_command(cwd, gui, desired, repo, image), capture=False)
        state["applied_publish"] = desired
        state["workspace"] = str(cwd)
        state["gui"] = gui
        if not self.running():
            self.runner.run([PODMAN, "start", CONTAINER], capture=True)
        self.check_kali()
        self.provision()
        self.restart_relays(mappings_from_state(state))

    def remove(self, *, purge: bool = False) -> None:
        if self.exists():
            self.runner.run([PODMAN, "rm", "-f", "-t", "0", CONTAINER], capture=True)
        if purge and not self.runner.dry_run:
            self.runner.run([PODMAN, "volume", "rm", "-f", VOLUME], capture=True, check=False)
            for path in (
                paths.STATE_DIR / "provision-state.json",
                paths.DATA_DIR / "relay.json",
            ):
                path.unlink(missing_ok=True)

    # -- provisioning --------------------------------------------------

    def provision(self, *, force_binary: bool = False, force_hooks: bool = False, force_mcp: bool = False) -> str:
        if self.runner.dry_run:
            eprint(f"[dry-run] would provision {CONTAINER}")
            return "(dry run)"
        if not self.exists():
            raise OmpKaliError(f"Container {CONTAINER} does not exist; run `omp-kali create` first.")
        if not self.running():
            self.runner.run([PODMAN, "start", CONTAINER], capture=True)

        log_path = paths.STATE_DIR / paths.PROVISION_LOG
        log_path.parent.mkdir(parents=True, exist_ok=True)
        header = f"\n=== {time.strftime('%Y-%m-%dT%H:%M:%S%z')} provisioning {CONTAINER} ({self.container_id()}) ===\n"
        self.runner.log_file = log_path.open("a", encoding="utf-8")
        os.chmod(log_path, 0o600)
        try:
            self.runner.log(header)
            self._note(f"provisioning: log {log_path}")
            self._note(f"provisioning: binary={force_binary} hooks={force_hooks} mcp={force_mcp}")
            repo, _skills, _agents, _mcp_file = paths.ensure_repo_assets()
            self.ensure_home_dirs()
            self.install_binary(force=force_binary)
            self.ensure_python()
            self.install_relay()
            self.verify_asset_mounts()
            self.ensure_path()

            state = provisioning.load_state(paths.STATE_DIR, self.container_id())
            baked_hash = self.label("omp-kali.provision-hash")
            if baked_hash and baked_hash == self.image_hash(repo) and not force_hooks:
                self._note("provisioning: scripts baked into image; skipping")
                for script in provisioning.load_scripts(repo):
                    state["scripts"][script.name] = script.sha256
            else:
                self.run_provision_scripts(repo, state, force=force_hooks)
            self.sync_mcp(repo, state, force=force_mcp)
            provisioning.save_state(paths.STATE_DIR, state)

            version = self.verify_omp()
            self._note(f"omp in container: {version}")
            return version
        finally:
            self.runner.log_file.close()
            self.runner.log_file = None

    def verify_asset_mounts(self) -> None:
        for path in (
            f"{CONTAINER_HOME}/.omp/agent/skills",
            f"{CONTAINER_HOME}/.omp/agent/agents",
            f"{CONTAINER_HOME}/.omp/agent/mcp.json",
        ):
            result = self.podman_exec(["test", "-e", path], check=False)
            if result.returncode != 0:
                raise OmpKaliError(
                    f"Asset mount missing in container: {path}. Recreate with `omp-kali create --recreate`."
                )

    def ensure_home_dirs(self) -> None:
        script = (
            f"mkdir -p {shlex.quote(CONTAINER_HOME + '/.omp/agent')} {shlex.quote(CONTAINER_HOME + '/.local/bin')} {shlex.quote(CONTAINER_HOME + '/.omp-kali')} && "
            f"chown {self.uid}:{self.gid} {shlex.quote(CONTAINER_HOME + '/.omp')} {shlex.quote(CONTAINER_HOME + '/.omp/agent')} {shlex.quote(CONTAINER_HOME + '/.local')} {shlex.quote(CONTAINER_HOME + '/.omp-kali')}"
        )
        self.runner.run([PODMAN, "exec", "-u", "0", CONTAINER, "sh", "-c", script], capture=True, stream=True)

    def install_binary(self, *, force: bool = False) -> None:
        destination = f"{CONTAINER_HOME}/.local/bin/omp"
        if not force:
            probe = self.podman_exec(["test", "-x", destination], check=False)
            if probe.returncode == 0:
                return
        host_omp = shutil.which("omp")
        if host_omp is None:
            candidate = Path.home() / ".local/bin/omp"
            if candidate.is_file():
                host_omp = str(candidate)
        if not host_omp or not Path(host_omp).is_file():
            raise OmpKaliError(
                "No host `omp` binary found. Install it with:\n  curl -fsSL https://omp.sh/install | sh"
            )
        self._note(f"provisioning: copying host omp binary ({host_omp})")
        temp = f"/tmp/omp-kali-omp-{os.getpid()}"
        self.runner.run([PODMAN, "cp", host_omp, f"{CONTAINER}:{temp}"], capture=True, stream=True)
        script = (
            f"mkdir -p {shlex.quote(CONTAINER_HOME + '/.local/bin')} && "
            f"chown {self.uid}:{self.gid} {shlex.quote(CONTAINER_HOME + '/.local')} {shlex.quote(CONTAINER_HOME + '/.local/bin')} && "
            f"install -o {self.uid} -g {self.gid} -m 0755 {shlex.quote(temp)} {shlex.quote(destination)} && "
            f"rm -f {shlex.quote(temp)}"
        )
        self.runner.run([PODMAN, "exec", "-u", "0", CONTAINER, "sh", "-c", script], capture=True, stream=True)

    def ensure_python(self) -> None:
        def has_python() -> bool:
            return self.root_exec(["sh", "-c", "command -v python3"], check=False).returncode == 0

        if has_python():
            return
        self._note("provisioning: installing python3 for port relays")
        self.runner.run(
            [
                PODMAN,
                "exec",
                "-u",
                "0",
                CONTAINER,
                "sh",
                "-c",
                "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3",
            ],
            capture=True,
            check=False,
            stream=True,
        )
        if not has_python():
            raise OmpKaliError(
                "python3 is not available in the container. Run `apt-get update && apt-get install -y python3` inside it and retry."
            )

    def install_relay(self) -> None:
        source = Path(__file__).with_name("relay.py")
        temp = f"/tmp/omp-kali-relay-{os.getpid()}.py"
        self._note("provisioning: installing port relay")
        self.runner.run([PODMAN, "cp", str(source), f"{CONTAINER}:{temp}"], capture=True, stream=True)
        destination = f"{CONTAINER_HOME}/.omp-kali/relay.py"
        script = (
            f"mkdir -p {shlex.quote(CONTAINER_HOME + '/.omp-kali')} && "
            f"rm -f {shlex.quote(CONTAINER_HOME + '/.omp-kali/relay.pid')} {shlex.quote(CONTAINER_HOME + '/.omp-kali/relay.status.json')} && "
            f"chown -R {self.uid}:{self.gid} {shlex.quote(CONTAINER_HOME + '/.omp-kali')} && "
            f"install -o {self.uid} -g {self.gid} -m 0755 {shlex.quote(temp)} {shlex.quote(destination)} && "
            f"rm -f {shlex.quote(temp)}"
        )
        self.runner.run([PODMAN, "exec", "-u", "0", CONTAINER, "sh", "-c", script], capture=True, stream=True)

    def ensure_path(self) -> None:
        start = "# >>> omp-kali path >>>"
        end = "# <<< omp-kali path <<<"
        script = """for f in "$HOME/.profile" "$HOME/.bashrc"; do
  [ -e "$f" ] || : > "$f"
  if ! grep -Fq __START__ "$f"; then
    printf '\\n%s\\n%s\\n%s\\n' __START__ 'case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac' __END__ >> "$f"
  fi
done
"""
        script = script.replace("__START__", shlex.quote(start)).replace("__END__", shlex.quote(end))
        self._note("provisioning: ensuring ~/.local/bin is on PATH")
        self.podman_exec(["sh", "-c", script], stream=True)

    def _provision_env(self) -> dict[str, str]:
        return {
            "HOME": CONTAINER_HOME,
            "CONTAINER_HOME": CONTAINER_HOME,
            "CONTAINER_USER": self.user,
            "DEBIAN_FRONTEND": "noninteractive",
        }

    def run_provision_scripts(self, repo: Path, state: dict[str, Any], *, force: bool = False) -> None:
        scripts = provisioning.load_scripts(repo)
        if not scripts:
            return
        if self.runner.dry_run:
            for script in scripts:
                if force or state["scripts"].get(script.name) != script.sha256:
                    eprint(f"[dry-run] would run provisioning script {script.name}")
            return
        for index, script in enumerate(scripts):
            if not force and state["scripts"].get(script.name) == script.sha256:
                continue
            self._note(f"provisioning: running {script.name}")
            temp = f"/tmp/omp-kali-provision-{os.getpid()}-{index}.sh"
            self.runner.run([PODMAN, "cp", str(script.path), f"{CONTAINER}:{temp}"], capture=True, stream=True)
            try:
                result = self.root_exec(["bash", temp], check=False, env=self._provision_env(), stream=True)
                if result.returncode != 0:
                    raise OmpKaliError(f"Provisioning script {script.name} failed with exit code {result.returncode}")
            finally:
                self.root_exec(["rm", "-f", temp], check=False, stream=True)
            state["scripts"][script.name] = script.sha256
            provisioning.save_state(paths.STATE_DIR, state)

    def sync_mcp(self, repo: Path, state: dict[str, Any], *, force: bool = False) -> None:
        manifests = load_manifests(repo / paths.MCP_DIR / paths.MCP_SERVERS_DIR)
        if not manifests:
            return
        if self.runner.dry_run:
            for manifest in manifests:
                if manifest.enabled:
                    eprint(f"[dry-run] would configure MCP server {manifest.name}")
            return
        for manifest in manifests:
            if not manifest.enabled:
                continue
            pending = manifest.has_install and (force or state["mcp"].get(manifest.name) != manifest.install_hash)
            if pending:
                self._note(f"mcp: installing {manifest.name}: {manifest.install_summary()}")
                self._install_mcp(manifest)
                state["mcp"][manifest.name] = manifest.install_hash
                provisioning.save_state(paths.STATE_DIR, state)
        config_path = repo / paths.MCP_DIR / paths.MCP_FILE
        config = load_mcp_config(config_path)
        merged = merge_servers(config, manifests)
        if write_mcp_config(config_path, merged):
            names = ", ".join(manifest.name for manifest in manifests if manifest.enabled)
            self._note(f"mcp: configured {names}")

    def _install_mcp(self, manifest: McpManifest) -> None:
        apt = list(manifest.install.get("apt", ()))
        if apt:
            packages = " ".join(shlex.quote(package) for package in apt)
            self._run_mcp_step(
                manifest.name,
                "apt",
                f"apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq {packages}",
            )
        pip = list(manifest.install.get("pip", ()))
        if pip:
            if self.root_exec(["python3", "-m", "pip", "--version"], check=False).returncode != 0:
                raise OmpKaliError(
                    f"MCP server {manifest.name}: pip is not available. Add python3-pip to install.apt."
                )
            packages = " ".join(shlex.quote(package) for package in pip)
            self._run_mcp_step(manifest.name, "pip", f"python3 -m pip install --break-system-packages {packages}")
        npm = list(manifest.install.get("npm", ()))
        if npm:
            if self.root_exec(["sh", "-c", "command -v npm"], check=False).returncode != 0:
                raise OmpKaliError(
                    f"MCP server {manifest.name}: npm is not available. Add nodejs and npm to install.apt."
                )
            packages = " ".join(shlex.quote(package) for package in npm)
            self._run_mcp_step(manifest.name, "npm", f"npm install -g {packages}")
        for command in manifest.install.get("commands", ()):
            self._run_mcp_step(manifest.name, "command", command)

    def _run_mcp_step(self, name: str, label: str, script: str) -> None:
        self._note(f"mcp: {name}: {label}")
        result = self.root_exec(["bash", "-lc", script], check=False, env=self._provision_env(), stream=True)
        if result.returncode != 0:
            raise OmpKaliError(f"MCP server {name}: {label} step failed with exit code {result.returncode}")

    def verify_omp(self) -> str:
        result = self.podman_exec([f"{CONTAINER_HOME}/.local/bin/omp", "--version"])
        version = (result.stdout or "").strip().splitlines()
        if not version:
            raise OmpKaliError("The container omp binary did not report a version.")
        return version[0]

    # -- relays --------------------------------------------------------

    def _relay_paths(self) -> tuple[str, str, str, str]:
        base = f"{CONTAINER_HOME}/.omp-kali"
        return (f"{base}/relay.py", f"{base}/relay.json", f"{base}/relay.pid", f"{base}/relay.status.json")

    def _relay_alive(self, pid_path: str) -> bool:
        script = (
            f'pid=$(cat {shlex.quote(pid_path)} 2>/dev/null || true); '
            '[ -n "$pid" ] && kill -0 "$pid" 2>/dev/null'
        )
        return self.podman_exec(["sh", "-c", script], check=False).returncode == 0

    def restart_relays(self, mappings: list[PortMapping]) -> None:
        if not self.exists() or not self.running():
            return
        _relay, config_path, pid_path, status_path = self._relay_paths()
        payloads = relay_payloads(mappings)
        desired = {"mappings": payloads}
        current: dict[str, Any] | None = None
        result = self.podman_exec(["cat", config_path], check=False)
        if result.returncode == 0 and (result.stdout or "").strip():
            try:
                current = json.loads(result.stdout)
            except json.JSONDecodeError:
                current = None
        if not payloads:
            if current is not None or self._relay_alive(pid_path):
                script = (
                    f'if [ -f {shlex.quote(pid_path)} ]; then '
                    f'pid=$(cat {shlex.quote(pid_path)} 2>/dev/null || true); '
                    '[ -n "$pid" ] && kill "$pid" 2>/dev/null || true; '
                    f'rm -f {shlex.quote(pid_path)}; fi; '
                    f'rm -f {shlex.quote(status_path)}'
                )
                self.podman_exec(["sh", "-c", script], check=False)
            return
        if current == desired and self._relay_alive(pid_path):
            return
        kill_script = (
            f'if [ -f {shlex.quote(pid_path)} ]; then '
            f'pid=$(cat {shlex.quote(pid_path)} 2>/dev/null || true); '
            '[ -n "$pid" ] && kill "$pid" 2>/dev/null || true; '
            f'rm -f {shlex.quote(pid_path)}; fi; '
            f'rm -f {shlex.quote(status_path)}'
        )
        self.podman_exec(["sh", "-c", kill_script], check=False)
        time.sleep(0.3)
        host_config = paths.DATA_DIR / "relay.json"
        host_config.parent.mkdir(parents=True, exist_ok=True)
        host_config.write_text(json.dumps(desired, indent=2) + "\n", encoding="utf-8")
        self.runner.run([PODMAN, "cp", str(host_config), f"{CONTAINER}:{config_path}"], capture=True)
        self.runner.run(
            [PODMAN, "exec", "-u", "0", CONTAINER, "chown", f"{self.uid}:{self.gid}", config_path],
            capture=True,
            check=False,
        )
        self.podman_exec(
            [
                "python3",
                f"{CONTAINER_HOME}/.omp-kali/relay.py",
                "--config",
                config_path,
                "--pid-file",
                pid_path,
                "--log-file",
                f"{CONTAINER_HOME}/.omp-kali/relay.log",
                "--status-file",
                status_path,
            ],
            detach=True,
        )
        time.sleep(0.4)
        status = self.podman_exec(["cat", status_path], check=False)
        raw = (status.stdout or "").strip()
        if not raw:
            eprint("warning: relay did not report readiness; see ~/.omp-kali/relay.log in the container")
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return
        for listener in data.get("listeners", []):
            eprint(f"relay: {listener['listen']} -> {listener['target']}")
        for error in data.get("errors", []):
            eprint(f"relay warning: {error}")

    # -- launching -----------------------------------------------------

    def enter(
        self,
        *,
        cwd: Path,
        gui: bool,
        pass_env: dict[str, str],
        omp_args: list[str],
        shell: bool,
        shell_command: str | None,
    ) -> int:
        command = "exec bash -i" if shell and not shell_command else (
            "exec bash -c " + shlex.quote(shell_command or "") if shell else "exec " + shjoin([f"{CONTAINER_HOME}/.local/bin/omp", *omp_args])
        )
        workdir = container_workdir(cwd)
        inner = f"cd {shlex.quote(str(workdir))} || exit 1\n{command}\n"
        env = {
            "HOME": CONTAINER_HOME,
            "USER": self.user,
            "SHELL": "/bin/bash",
            "PATH": f"{CONTAINER_HOME}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "TERM": os.environ.get("TERM", "xterm-256color"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
        }
        if gui:
            for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
                value = os.environ.get(key)
                if value:
                    env[key] = value
            if os.environ.get("XAUTHORITY"):
                env["XAUTHORITY"] = "/run/omp-kali/Xauthority"
        env.update(pass_env)
        cmd = [PODMAN, "exec", "-i"]
        if sys.stdin.isatty():
            cmd.append("-t")
        cmd.extend(["-u", f"{self.uid}:{self.gid}"])
        for key, value in env.items():
            cmd.extend(["-e", f"{key}={value}"])
        cmd.extend(["-w", str(workdir), CONTAINER, "bash", "-c", inner])
        if self.runner.dry_run:
            eprint("+ " + shjoin(cmd))
            return 0
        result = self.runner.run(cmd, capture=False, check=False)
        return result.returncode

    # -- status --------------------------------------------------------

    def status(self) -> dict[str, Any]:
        info: dict[str, Any] = {
            "exists": self.exists(),
            "running": False,
            "managed": False,
            "image": "",
            "kali": "",
            "home": CONTAINER_HOME,
            "volume": VOLUME,
            "workspace": "",
            "gui": False,
            "omp_version": "",
        }
        if not info["exists"]:
            return info
        info["running"] = self.running()
        info["managed"] = self.managed()
        info["image"] = self.image_name()
        info["workspace"] = self.workspace_source()
        info["gui"] = self.label(GUI_LABEL) == "true"
        if not info["running"]:
            return info
        info["kali"] = self.check_kali()
        version = self.podman_exec([f"{CONTAINER_HOME}/.local/bin/omp", "--version"], check=False)
        if version.stdout:
            info["omp_version"] = version.stdout.strip().splitlines()[0]
        return info
