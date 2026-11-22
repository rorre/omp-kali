# omp-kali

Run an isolated omp instance inside a Kali Linux container managed by raw podman. The host's `~/.omp` is not used or modified; the container has its own home volume, auth, sessions, and settings. Your repository remains the source of truth for skills, agents, and MCP servers.

Only the selected working directory is host-mounted as project data. Repository capability assets are explicit mounts; optional GUI sockets are the only other host paths, and only with `--gui`.

## Requirements

- Linux with rootless `podman`
- `omp` on the host (its binary is copied into the container on first run)
- Python 3.10+
- `uv` for dependency management

Runtime dependencies are locked in `uv.lock`; the CLI is built with Typer.

## Install

```sh
cd omp-kali
uv sync
uv run omp-kali --help
```

Or use the checkout wrapper, which execs `uv run --project` for you:

```sh
./bin/omp-kali --help
ln -s "$PWD/bin/omp-kali" ~/.local/bin/omp-kali
```

The launcher must find this checkout's `skills/`, `agents/`, and `mcp/`. `uv run` installs from the checkout, so this works directly; set `OMP_KALI_REPO=/path/to/omp-kali` if you relocate the source.

The examples below use `omp-kali`; use `uv run omp-kali` instead if the console script is not on `PATH`.

## Documentation

- [SKILLS.md](SKILLS.md): skill, agent, and MCP repository contract.

## First run

```sh
cd ~/projects/my-project
omp-kali
```

On first use, omp-kali:

1. builds a cached Kali image from the repository `Dockerfile`, installing python3, sudo with passwordless access, and running `provisioning/*.sh` during the build,
2. mounts only the current directory at `/workspace/<directory-name>` and starts omp there,
3. uses a named podman volume (`omp-kali-home`) for the isolated omp home,
4. mounts the repository's `skills/`, `agents/`, and `mcp/mcp.json` into the container's omp config,
5. runs MCP install manifests at container provision time.

The image tag is content-addressed from `Dockerfile` and `provisioning/*.sh`, so unchanged builds reuse the podman cache. After changing either, run `omp-kali create --recreate` to rebuild the image and recreate the container; ordinary starts continue to use the existing image.

omp authentication is container-local. Run omp's normal setup/login inside the container; nothing is copied from the host's omp config. Use `--pass-env NAME` when a specific host variable (for example `HTTPS_PROXY` or `SSH_AUTH_SOCK`) is needed inside the container.

## Commands

| Command | Purpose |
| --- | --- |
| `omp-kali` | Start omp in the container in the current directory. |
| `omp-kali run -- <args>` | Start omp and pass arguments to it. |
| `omp-kali shell` | Drop into a container shell in the current directory. |
| `omp-kali shell -c 'cmd'` | Run one shell command in the container. |
| `omp-kali --gui` | Forward X11 and Wayland so container GUI apps appear on the host. |
| `omp-kali create` | Create/provision the container without starting omp. |
| `omp-kali create --recreate` | Rebuild the cached image if needed, then recreate the container; the home volume and sessions are preserved. |
| `omp-kali status` | Show container, omp, asset, and port state. |
| `omp-kali sync` | Provision assets, changed scripts, and MCP installs. |
| `omp-kali provision` | Run runtime provision steps and changed MCP installs; baked scripts are skipped. |
| `omp-kali provision --force` | Re-run scripts at runtime and all MCP installs. |
| `omp-kali mcp list` | List MCP server manifests. |
| `omp-kali mcp sync` | Install changed manifests and merge `mcp/mcp.json`. |
| `omp-kali mcp sync --force` | Re-run all MCP install steps. |
| `omp-kali update` | Copy the current host omp binary into the container. |
| `omp-kali rm` | Remove the container; add `--purge` to delete its home too. |
| `omp-kali ports list` | List configured port forwards. |
| `omp-kali ports add ...` | Add a forward after an explicit permission prompt. |
| `omp-kali ports remove ID` | Remove a forward. |

Only one `omp-kali` container is managed at a time. Automatic recreate steps (workspace, image, GUI, or published-port change) ask for confirmation and name the container they will remove; use `--yes` for non-interactive runs. Other podman containers are not touched.

Global options may appear before the command or on the command itself: `--dir/-d`, `--gui`, `--pass-env NAME`, `--yes/-y`, `--dry-run`, `--verbose/-v`. Use `--` to pass flags to omp: `omp-kali run -- --model openrouter/...`.

### Examples

```sh
omp-kali --gui
omp-kali run -- --model openrouter/anthropic/claude-sonnet-4.5
omp-kali shell -c 'id; uname -a; pwd'
omp-kali --pass-env OPENROUTER_API_KEY --pass-env HTTPS_PROXY
omp-kali --dir ~/projects/other-repo
```

## Repository assets

```
skills/
  <skill-name>/
    SKILL.md
agents/
  <agent-name>.md
mcp/
  mcp.json
  servers/
    <server-name>.json
provisioning/
  10-packages.sh
  20-config.sh
```

`mcp/servers/*.json` manifests install their packages in the container and merge the declared server into `mcp/mcp.json`; see [mcp/servers/README.md](mcp/servers/README.md). `provisioning/*.sh` scripts run during provisioning in lexical order and are tracked by content hash; see [provisioning/README.md](provisioning/README.md).

These are mounted live into the container:

| Container path | Repository path |
| --- | --- |
| `/home/omp/.omp/agent/skills` | `skills/` |
| `/home/omp/.omp/agent/agents` | `agents/` |
| `/home/omp/.omp/agent/mcp.json` | `mcp/mcp.json` |

Edits on the host are visible immediately. Adding MCP servers from inside omp's `/mcp add` writes through the mount into `mcp/mcp.json`, so the change lands in the repository.

Skills are discovered one level under `skills/`; each needs `SKILL.md` with a `description`. Agents are frontmatter Markdown files; see `agents/README` and omp's agent contract.

## Provisioning

### Extra setup scripts

Drop `*.sh` files into `provisioning/`. They run as root inside the container, sorted by filename, after base provisioning and before MCP installs. Each script runs when its content changes; use `--force` to re-run.

```sh
# provisioning/10-packages.sh
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nmap sqlite3 jq
```

```sh
omp-kali provision
omp-kali provision --force
```

### Installable MCP servers

Add a manifest under `mcp/servers/` when a server needs packages installed in the container:

```json
{
  "name": "filesystem",
  "install": {
    "apt": ["nodejs", "npm"],
    "npm": ["@modelcontextprotocol/server-filesystem"]
  },
  "server": {
    "type": "stdio",
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "${HOME}"]
  }
}
```

Provisioning installs the dependencies, records the install hash, and merges `server` into `mcp/mcp.json`. A container recreate clears the hash state so installs run again.

```sh
omp-kali mcp list
omp-kali mcp sync
omp-kali mcp sync --force
```

## Port forwarding

The container runs in its own pasta network namespace, so ports are not shared implicitly. Each mapping is printed and confirmed before it is activated.

### Container to host (publish)

```sh
omp-kali ports add container-to-host --host 8080 --container 80
```

Publishes container port `80/tcp` on `127.0.0.1:8080` of the host. Requires recreating the container (its home and sessions are preserved). Use `--bind 0.0.0.0` to expose it on all host interfaces.

### Host to container (relay)

```sh
omp-kali ports add host-to-container --host 5432 --container 5432
```

Makes the host service on port `5432` reachable inside the container at `127.0.0.1:5432`. The relay inside the container targets `host.containers.internal:5432`. No container recreation is needed.

**Limitation:** podman's pasta network can only reach host services bound to a non-loopback address. If the host service listens on `127.0.0.1` only, the command warns you; rebind the service to `0.0.0.0` (or use `--target-host` with a reachable address).

Use `omp-kali ports list` to see stable ids, for example `h2c:127.0.0.1:5432:5432` or `c2h:127.0.0.1:8080:80`.

## GUI forwarding

GUI forwarding is opt-in. With `--gui`, the launcher mounts the X11 socket, Xauthority file, Wayland socket, and D-Bus socket read/write as needed, and passes `DISPLAY`, `WAYLAND_DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, and the D-Bus address into the container. Without `--gui`, no display sockets are mounted and those variables are absent.

## State and isolation

| Path | Contents |
| --- | --- |
| `omp-kali-home` podman volume | Container home: isolated `~/.omp`, auth, sessions, settings |
| `~/.local/state/omp-kali/state.json` | Port mappings, selected directory, GUI flag, applied publish set |
| `~/.local/state/omp-kali/provision-state.json` | Provisioning script/MCP install hashes and container ID |
| `~/.local/state/omp-kali/provision.log` | Streamed provisioning log (commands and output, mode `0600`) |
| `~/.local/share/omp-kali/relay.json` | Generated relay configuration (host copy) |

Provisioning streams every step to the terminal and appends it to `~/.local/state/omp-kali/provision.log`; no `--verbose` flag is needed. The log includes the exact commands, apt/pip/npm/script output, and container IDs, and is chmod `0600`.

Override host state with `OMP_KALI_DATA_DIR`, `OMP_KALI_STATE_DIR`, or `OMP_KALI_STATE_FILE`. Override the asset source repository with `OMP_KALI_REPO`.

omp's own config, credentials, and sessions stay in the `omp-kali-home` volume. The host's omp instance is never mounted or reused. The container is created with a minimal environment; `podman exec` passes only the fixed launch variables plus explicit `--pass-env` values.

Host path visibility:

- only the selected directory is mounted, at `/workspace/<directory-name>`;
- `skills/`, `agents/`, and `mcp/mcp.json` are mounted at the container's omp config paths;
- GUI sockets are mounted only with `--gui`;
- host home, `/tmp`, and `/run/host` are not mounted.

Changing the selected directory, toggling `--gui`, or changing published ports recreates the container, because podman cannot alter a running container's mounts. The home volume persists; container-layer packages are reinstalled by provisioning on the next run.

## Development

```sh
uv sync
uv run python -m unittest discover -s tests
uv lock          # after changing pyproject dependencies
uv run omp-kali --help
```

The `bin/omp-kali` wrapper and the console script share the same uv environment. The examples above assume `omp-kali` is on `PATH` (installed via the wrapper symlink or `uv run omp-kali`).

## Notes

- The base image is `docker.io/kalilinux/kali-rolling:latest`; `Dockerfile` adds python3 and bakes `provisioning/*.sh` into a content-addressed local image. The launcher verifies `ID=kali` before provisioning.
- The image installs `sudo` and writes `/etc/sudoers.d/omp-kali` with `NOPASSWD: ALL` for the container user, so `sudo` works non-interactively inside the container.
- `kali-rolling` is a minimal image. Add tools through `provisioning/*.sh`; changing either those scripts or `Dockerfile` requires `omp-kali create --recreate` to build the new image. MCP manifest installs still run at container provisioning time.
- Only one `omp-kali` container is managed at a time. Automatic recreates ask for confirmation and do not touch other podman containers.
- Container removal uses `podman rm -f -t 0`, so `sleep infinity` does not add a stop timeout.
- The `omp-kali-home` podman volume is isolated from the host omp instance, but it is not encrypted.
- `omp-kali rm --purge` deletes the container, the `omp-kali-home` volume, and provisioning state.
