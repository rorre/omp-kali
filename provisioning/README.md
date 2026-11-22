# Provisioning scripts

Shell `*.sh` files placed directly in this directory run inside the cached image during `podman build`. Use them to install apps, configure services, generate config files, or prepare tooling that the image lacks.

Only direct `*.sh` files are considered, sorted lexicographically so numeric prefixes control order:

```
provisioning/
  10-packages.sh
  20-config.sh
  30-user-tools.sh
```

## Execution model

- Runs as root while building `localhost/omp-kali:local-<hash>`.
- Runs before the container is created; the built image is reused while the Dockerfile and scripts are unchanged.
- Runs before runtime MCP manifest installation.
- Environment:
  - `HOME` and `CONTAINER_HOME`: isolated container home
  - `CONTAINER_USER`: container user (normally your host username)
  - `DEBIAN_FRONTEND=noninteractive`
- Output is streamed live to the terminal and appended to `~/.local/state/omp-kali/provision.log`.
- A failing script aborts the image build; fix it and run `omp-kali create --recreate`.

At runtime, matching scripts are skipped when the image label `omp-kali.provision-hash` matches the current Dockerfile/script hash. `omp-kali provision --force` runs them at runtime as a fallback.

Treat provisioning scripts as trusted code: they run as root during the image build.

## Change tracking

The image tag and `omp-kali.provision-hash` label are SHA-256-derived from the Dockerfile and every script. A change to either produces a new tag; `omp-kali create --recreate` builds it. MCP install hashes remain in `<state dir>/provision-state.json`. `omp-kali provision --force` re-runs scripts at runtime as a fallback.

Scripts should still be idempotent; that makes `--force` and recreated containers safe.

## Examples

`10-packages.sh`:

```sh
#!/bin/sh
set -eu
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq nmap sqlite3 jq
```

`20-config.sh`:

```sh
#!/bin/sh
set -eu
install -d -o "$CONTAINER_USER" -g "$CONTAINER_USER" "$CONTAINER_HOME/.config/my-tool"
cat > "$CONTAINER_HOME/.config/my-tool/config.ini" <<EOF
[main]
color=auto
EOF
chown "$CONTAINER_USER:$CONTAINER_USER" "$CONTAINER_HOME/.config/my-tool/config.ini"
```

Because scripts run as root, explicitly set ownership for files the container user must modify. Do not put secrets in this repository; use `omp-kali --pass-env NAME` for runtime secrets.

## Commands

```sh
omp-kali create --recreate # rebuild image and recreate container
omp-kali provision          # runtime MCP installs; baked scripts skipped
omp-kali provision --force  # run scripts at runtime plus all MCP installs
```

Runtime provisioning also runs automatically on `omp-kali run`, `shell`, `create`, and `sync`. Matching baked scripts are skipped; MCP installs are hashed.
