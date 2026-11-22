# SKILLS.MD

Repository contract for omp capabilities: **skills**, **agents**, **MCP servers**, and container provisioning hooks.

This file is intentionally uppercase and at the repository root. It is documentation, not a discoverable skill; omp only scans `skills/<name>/SKILL.md`.

## Directory layout

```
omp-kali/
  skills/
    README.md                 # documentation, ignored by omp discovery
    <skill-name>/
      SKILL.md                # required
      references/             # optional assets
  agents/
    README                    # documentation, ignored (*.md is scanned)
    <agent-name>.md           # frontmatter + system prompt
  mcp/
    README.md
    mcp.json                  # omp-native MCP config
    servers/
      <server-name>.json      # installable MCP server manifest
  provisioning/
    README.md
    *.sh                      # run during provisioning
```

These host paths are bind-mounted into the container at creation:

| Host repository | Container path |
| --- | --- |
| `skills/` | `/home/omp/.omp/agent/skills` |
| `agents/` | `/home/omp/.omp/agent/agents` |
| `mcp/mcp.json` | `/home/omp/.omp/agent/mcp.json` |

Edits on the host are visible immediately. A write through the bind mount (for example omp's `/mcp add`) lands in this repository.

The container home is isolated from the host's `~/.omp`. Only these three asset paths are shared. `mcp/servers/` manifests and `provisioning/` scripts are consumed by the host launcher during provisioning; they are not mounted into the container.

## Skills

Place each skill in its own directory, one level under `skills/`:

```
skills/<skill-name>/SKILL.md
```

Nested paths such as `skills/group/<skill>/SKILL.md` are not discovered by the native scanner. Point `skills.customDirectories` at the nested parent if you need a taxonomy.

`SKILL.md` frontmatter:

```markdown
---
name: my-skill
description: Use when doing a specific task.
---

Instructions, commands, and workflows.
```

- `name` defaults to the directory name.
- `description` is what the model matches against; keep it specific.
- `hide: true` keeps the skill loaded but out of the system-prompt list.
- `enabled: false` skips the skill.
- Assets referenced from the body should live beside `SKILL.md`; read them with `skill://my-skill/<path>`.

Example:

```markdown
---
name: recon-notes
description: Capture and organize reconnaissance notes for an authorized engagement.
---

Create `notes/` in the current project, one Markdown file per host, and keep a
single `notes/index.md` with scope, timestamps, and open questions.
```

## Agents

Agents are frontmatter Markdown files directly under `agents/`:

```
agents/<agent-name>.md
```

Required fields are `name`, `description`, and a non-empty prompt body:

```markdown
---
name: reviewer
description: Review a change for correctness and risk.
model: "@review"
---

Review the assigned change. Report concrete findings with file and line references.
```

Optional fields supported by omp include `tools`, `spawns`, `model`, `thinking-level`, `structured`, `blocking`, `autoloadSkills`, `prewalk`, and `advisor`. See omp's agent discovery documentation for the full contract.

`agents/README` deliberately has no `.md` extension, so it is documentation rather than an agent definition. OMP loads `*.md` files from this directory.

## MCP servers

`mcp/mcp.json` uses the omp-native shape:

```json
{
  "$schema": "https://raw.githubusercontent.com/can1357/oh-my-pi/main/packages/coding-agent/src/config/mcp-schema.json",
  "mcpServers": {
    "server-name": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "some-mcp-server"]
    }
  }
}
```

Remote transports use `"type": "http"` with a `url` (or `"type": "sse"` for legacy servers).

Environment values in a stdio server can name host variables. The container receives only the fixed launch environment plus variables passed explicitly with `omp-kali --pass-env NAME`:

```json
{
  "mcpServers": {
    "example": {
      "command": "some-server",
      "env": {
        "API_KEY": "API_KEY"
      }
    }
  }
}
```

Then run:

```sh
omp-kali --pass-env API_KEY
```

### Installable server manifests

Servers that need container packages can declare them in `mcp/servers/<name>.json`. Provisioning installs dependencies, then merges the declared server into `mcp/mcp.json`:

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

Install actions support `apt`, `pip`, `npm`, and arbitrary `commands`, all executed as root in the container. Install blocks are hash-tracked; container recreation clears the tracking because container-layer packages are lost. See `mcp/servers/README.md`.

```sh
omp-kali mcp list
omp-kali mcp sync --force
```

## Sync behavior

`omp-kali sync` (and every `run`/`create`) verifies the asset mounts and provisions changed scripts/MCP installs. The assets are bind mounts, so host edits are live and no symlink backup or target probing is involved.

```sh
omp-kali sync
omp-kali status     # shows container, workspace, and asset state
```

## Provisioning scripts

Extra setup lives in `provisioning/*.sh`. Scripts are sorted, run as root inside the container, and tracked by SHA-256 so only new or changed scripts execute:

```sh
omp-kali provision
omp-kali provision --force
```

They run after base provisioning and before MCP installs, so they can install tools an MCP manifest depends on. See `provisioning/README.md`.

## Validation

```sh
uv run omp-kali status
uv run python -m unittest discover -s tests
```

`status` prints the resolved container-side targets. Full skill/agent loading happens when omp starts; the paths above are omp's native user-level discovery roots.
