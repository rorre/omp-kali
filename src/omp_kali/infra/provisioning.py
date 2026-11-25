from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from ..core import paths
from ..core.constants import BASE_IMAGE, CONTAINER_HOME, IMAGE_REPO
from ..core.errors import OmpKaliError
from .execution import Execution
from .mcp import McpManifest, load_config as load_mcp_config
from .mcp import load_manifests, merge_servers, write_config as write_mcp_config
from ..core.constants import CONTAINER, PODMAN
from ..domain.facts import ContainerFacts
from .podman import Podman
from ..domain.state import State, StateStore

SCRIPT_DIR = "provisioning"


@dataclass(frozen=True)
class ProvisionScript:
    name: str
    path: Path
    sha256: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_scripts(repo_root: Path) -> list[ProvisionScript]:
    directory = repo_root / SCRIPT_DIR
    if not directory.is_dir():
        return []
    return [
        ProvisionScript(path.name, path, sha256_file(path))
        for path in sorted(directory.glob("*.sh"))
        if path.is_file()
    ]


class Provisioning:
    """Builds the image and provisions the container's tools and assets."""

    def __init__(self, podman: Podman, execution: Execution, store: StateStore) -> None:
        self.podman = podman
        self.execution = execution
        self.store = store
        self.user = execution.user
        self.uid = execution.uid
        self.gid = execution.gid
        self.home = CONTAINER_HOME

    # -- image ---------------------------------------------------------

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
        return self.podman.image_exists(tag)

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
        if self.podman.dry_run:
            self.podman.run(build)
            return tag
        if self.image_exists(tag):
            return tag
        log_path = paths.STATE_DIR / paths.PROVISION_LOG
        self.podman.open_log(log_path)
        try:
            self.podman.log(f"\n=== {time.strftime('%Y-%m-%dT%H:%M:%S%z')} building image {tag} ===\n")
            self.podman.note(f"image: building {tag}")
            self.podman.run(build, capture=False, stream=True)
        finally:
            self.podman.close_log()
        return tag

    # -- provisioning --------------------------------------------------

    def provision(
        self,
        state: State,
        facts: ContainerFacts,
        *,
        force_binary: bool = False,
        force_hooks: bool = False,
        force_mcp: bool = False,
    ) -> str:
        if self.podman.dry_run:
            print(f"[dry-run] would provision {CONTAINER}")
            return "(dry run)"
        if not facts.exists:
            raise OmpKaliError(f"Container {CONTAINER} does not exist; run `omp-kali create` first.")
        if not facts.running:
            self.podman.run([PODMAN, "start", CONTAINER], capture=True)

        if state.container_id != facts.container_id:
            state.container_id = facts.container_id
            state.scripts = {}
            state.mcp = {}

        log_path = paths.STATE_DIR / paths.PROVISION_LOG
        header = (
            f"\n=== {time.strftime('%Y-%m-%dT%H:%M:%S%z')} provisioning "
            f"{CONTAINER} ({facts.container_id}) ===\n"
        )
        self.podman.open_log(log_path)
        try:
            self.podman.log(header)
            self.podman.note(f"provisioning: log {log_path}")
            self.podman.note(
                f"provisioning: binary={force_binary} hooks={force_hooks} mcp={force_mcp}"
            )
            repo, _skills, _agents, _mcp_file = paths.ensure_repo_assets()
            self.ensure_home_dirs()
            self.install_binary(force=force_binary)
            self.ensure_python()
            self.install_relay()
            self.verify_asset_mounts()
            self.ensure_path()

            baked_hash = facts.labels.get("omp-kali.provision-hash", "")
            if baked_hash and baked_hash == self.image_hash(repo) and not force_hooks:
                self.podman.note("provisioning: scripts baked into image; skipping")
                for script in load_scripts(repo):
                    state.scripts[script.name] = script.sha256
            else:
                self.run_provision_scripts(repo, state, force=force_hooks)
            self.sync_mcp(repo, state, force=force_mcp)
            self.store.save(state)

            version = self.verify_omp()
            self.podman.note(f"omp in container: {version}")
            return version
        finally:
            self.podman.close_log()

    def verify_asset_mounts(self) -> None:
        for path in (
            f"{CONTAINER_HOME}/.omp/agent/skills",
            f"{CONTAINER_HOME}/.omp/agent/agents",
            f"{CONTAINER_HOME}/.omp/agent/mcp.json",
        ):
            result = self.execution.podman_exec(["test", "-e", path], check=False)
            if result.returncode != 0:
                raise OmpKaliError(
                    f"Asset mount missing in container: {path}. Recreate with `omp-kali create --recreate`."
                )

    def ensure_home_dirs(self) -> None:
        script = (
            f"mkdir -p {shlex.quote(CONTAINER_HOME + '/.omp/agent')} {shlex.quote(CONTAINER_HOME + '/.local/bin')} {shlex.quote(CONTAINER_HOME + '/.omp-kali')} && "
            f"chown {self.uid}:{self.gid} {shlex.quote(CONTAINER_HOME + '/.omp')} {shlex.quote(CONTAINER_HOME + '/.omp/agent')} {shlex.quote(CONTAINER_HOME + '/.local')} {shlex.quote(CONTAINER_HOME + '/.omp-kali')}"
        )
        self.podman.run(
            [PODMAN, "exec", "-u", "0", CONTAINER, "sh", "-c", script],
            capture=True,
            stream=True,
        )

    def install_binary(self, *, force: bool = False) -> None:
        destination = f"{CONTAINER_HOME}/.local/bin/omp"
        if not force:
            probe = self.execution.podman_exec(["test", "-x", destination], check=False)
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
        self.podman.note(f"provisioning: copying host omp binary ({host_omp})")
        temp = f"/tmp/omp-kali-omp-{os.getpid()}"
        self.podman.run([PODMAN, "cp", host_omp, f"{CONTAINER}:{temp}"], capture=True, stream=True)
        script = (
            f"mkdir -p {shlex.quote(CONTAINER_HOME + '/.local/bin')} && "
            f"chown {self.uid}:{self.gid} {shlex.quote(CONTAINER_HOME + '/.local')} {shlex.quote(CONTAINER_HOME + '/.local/bin')} && "
            f"install -o {self.uid} -g {self.gid} -m 0755 {shlex.quote(temp)} {shlex.quote(destination)} && "
            f"rm -f {shlex.quote(temp)}"
        )
        self.podman.run(
            [PODMAN, "exec", "-u", "0", CONTAINER, "sh", "-c", script],
            capture=True,
            stream=True,
        )

    def ensure_python(self) -> None:
        def has_python() -> bool:
            return (
                self.execution.root_exec(["sh", "-c", "command -v python3"], check=False).returncode
                == 0
            )

        if has_python():
            return
        self.podman.note("provisioning: installing python3 for port relays")
        self.podman.run(
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
        self.podman.note("provisioning: installing port relay")
        self.podman.run([PODMAN, "cp", str(source), f"{CONTAINER}:{temp}"], capture=True, stream=True)
        relay, _config, pid, status, _log = self.execution.relay_paths()
        relay_dir = str(Path(relay).parent)
        script = (
            f"mkdir -p {shlex.quote(relay_dir)} && "
            f"rm -f {shlex.quote(pid)} {shlex.quote(status)} && "
            f"chown -R {self.uid}:{self.gid} {shlex.quote(relay_dir)} && "
            f"install -o {self.uid} -g {self.gid} -m 0755 {shlex.quote(temp)} {shlex.quote(relay)} && "
            f"rm -f {shlex.quote(temp)}"
        )
        self.podman.run(
            [PODMAN, "exec", "-u", "0", CONTAINER, "sh", "-c", script],
            capture=True,
            stream=True,
        )

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
        self.podman.note("provisioning: ensuring ~/.local/bin is on PATH")
        self.execution.podman_exec(["sh", "-c", script], stream=True)

    def _provision_env(self) -> dict[str, str]:
        return {
            "HOME": CONTAINER_HOME,
            "CONTAINER_HOME": CONTAINER_HOME,
            "DEBIAN_FRONTEND": "noninteractive",
        }

    def run_provision_scripts(self, repo: Path, state: State, *, force: bool = False) -> None:
        scripts = load_scripts(repo)
        if not scripts:
            return
        if self.podman.dry_run:
            for script in scripts:
                if force or state.scripts.get(script.name) != script.sha256:
                    print(f"[dry-run] would run provisioning script {script.name}")
            return
        for index, script in enumerate(scripts):
            if not force and state.scripts.get(script.name) == script.sha256:
                continue
            self.podman.note(f"provisioning: running {script.name}")
            temp = f"/tmp/omp-kali-provision-{os.getpid()}-{index}"
            self.podman.run([PODMAN, "cp", str(script.path), f"{CONTAINER}:{temp}"], capture=True, stream=True)
            try:
                result = self.execution.root_exec(
                    ["bash", temp], check=False, env=self._provision_env(), stream=True
                )
                if result.returncode != 0:
                    raise OmpKaliError(
                        f"Provisioning script {script.name} failed with exit code {result.returncode}"
                    )
            finally:
                self.execution.root_exec(["rm", "-f", temp], check=False)
            state.scripts[script.name] = script.sha256
            self.store.save(state)

    def sync_mcp(self, repo: Path, state: State, *, force: bool = False) -> None:
        manifests = load_manifests(repo / paths.MCP_DIR / paths.MCP_SERVERS_DIR)
        if not manifests:
            return
        if self.podman.dry_run:
            for manifest in manifests:
                if manifest.enabled:
                    print(f"[dry-run] would install MCP server {manifest.name}")
            return
        for manifest in manifests:
            if not manifest.enabled:
                continue
            if manifest.has_install and (force or state.mcp.get(manifest.name) != manifest.install_hash):
                self.podman.note(f"mcp: installing {manifest.name}")
                self._install_mcp(manifest)
                state.mcp[manifest.name] = manifest.install_hash
                self.store.save(state)
        config_path = repo / paths.MCP_DIR / paths.MCP_FILE
        merged = merge_servers(load_mcp_config(config_path), manifests)
        if write_mcp_config(config_path, merged):
            self.podman.note("mcp: configuration written")

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
            if self.execution.root_exec(["python3", "-m", "pip", "--version"], check=False).returncode != 0:
                raise OmpKaliError(
                    f"MCP server {manifest.name}: pip is not available. Add python3-pip to install.apt."
                )
            packages = " ".join(shlex.quote(package) for package in pip)
            self._run_mcp_step(
                manifest.name, "pip", f"python3 -m pip install --break-system-packages {packages}"
            )
        npm = list(manifest.install.get("npm", ()))
        if npm:
            if self.execution.root_exec(["sh", "-c", "command -v npm"], check=False).returncode != 0:
                raise OmpKaliError(
                    f"MCP server {manifest.name}: npm is not available. Add nodejs to install.apt."
                )
            packages = " ".join(shlex.quote(package) for package in npm)
            self._run_mcp_step(manifest.name, "npm", f"npm install -g {packages}")
        for command in manifest.install.get("commands", ()):
            self._run_mcp_step(manifest.name, "command", command)

    def _run_mcp_step(self, name: str, label: str, script: str) -> None:
        self.podman.note(f"mcp: {name} {label}")
        result = self.execution.root_exec(
            ["bash", "-lc", script], check=False, env=self._provision_env(), stream=True
        )
        if result.returncode != 0:
            raise OmpKaliError(
                f"MCP server {name}: {label} step failed with exit code {result.returncode}"
            )

    def verify_omp(self) -> str:
        result = self.execution.podman_exec([f"{CONTAINER_HOME}/.local/bin/omp", "--version"])
        version = (result.stdout or "").strip().splitlines()
        if not version:
            raise OmpKaliError("The container omp binary did not report a version.")
        return version[0]
