# MCP server manifests

Each `*.json` file in this directory describes how to install an MCP server inside the `omp-kali` container and how omp should launch it. Provisioning installs missing dependencies, then merges the `server` object into `../mcp.json` (bind-mounted to the container's `/home/omp/.omp/agent/mcp.json`).

Only direct `*.json` files are read. Hidden files and other extensions are ignored.

## Manifest shape

```json
{
  "name": "filesystem",
  "description": "Filesystem MCP server",
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

Fields:

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Unique server name; becomes the `mcpServers` key. |
| `description` | no | Human-readable summary shown by `omp-kali mcp list`. |
| `enabled` | no | Default `true`. A disabled manifest is not installed or merged. |
| `install` | no | Install actions; omit when the server needs no installation. |
| `server` | yes | omp MCP server object. Must contain `command` or `url`. |

`name` may contain letters, digits, `_`, `-`, `.`, and `:`. Duplicate names are rejected.

## Install actions

All actions run as root inside the container via `bash -lc`. Actions run only when the manifest's install block changes, or with `--force`.

| Key | Action |
| --- | --- |
| `apt` | `apt-get update -qq && apt-get install -y -qq <packages>` |
| `pip` | `python3 -m pip install --break-system-packages <packages>`; requires pip (`python3-pip` can be added to `apt`). |
| `npm` | `npm install -g <packages>`; requires npm (`nodejs`/`npm` can be added to `apt`). |
| `commands` | Arbitrary shell commands, one per list entry. |

Available environment for `commands`:

- `HOME` and `CONTAINER_HOME`: the isolated container home
- `CONTAINER_USER`: the container user (normally your host username)
- `DEBIAN_FRONTEND=noninteractive`

The install block is hashed and recorded in `<container home>/.omp-kali/provision-state.json`. A container recreate clears that state because container-layer packages are gone, so the next provision reinstalls them.

Treat manifests as trusted code: every install action runs as root inside the container.

## Merge semantics

- Manifest servers are merged into `mcp/mcp.json`; other manual entries are preserved.
- A manifest with the same name as a manual entry wins.
- Removing a manifest does not remove its previously merged server entry; remove it from `mcp/mcp.json` manually.
- `mcp.json` is bind-mounted into the container, so omp sees the result on its next start (or after `/mcp reload`).

## Commands

```sh
omp-kali mcp list             # inspect manifests
omp-kali mcp sync             # install changed manifests and merge config
omp-kali mcp sync --force     # re-run all install steps
omp-kali provision --force    # run provisioning scripts and MCP installs
```

Provisioning runs automatically on `omp-kali run`, `shell`, `create`, and `sync`, but only changed install blocks execute.
