# MCP servers

`mcp.json` in this directory is bind-mounted to `/home/omp/.omp/agent/mcp.json` inside the omp-kali container. It uses the standard omp native shape:

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

Remote servers use `type: "http"` with a `url` (or `type: "sse"` for legacy). Stdio `env` values can name host environment variables; omp resolves them from the container environment. Use `omp-kali --pass-env NAME` for values that must come from the host.

Adding a server from omp's `/mcp add` writes through the bind mount into this file.

## Installable server manifests

For servers that need packages installed in the container, add a manifest under `servers/`:

```
mcp/
  mcp.json
  servers/
    filesystem.json
```

A manifest declares installation steps and the omp server object:

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

Provisioning installs dependencies into the container and merges `server` into `mcp.json`. Install steps are tracked by hash; container recreation clears that state so packages are reinstalled.

See [servers/README.md](servers/README.md) for the full manifest contract.

```sh
omp-kali mcp list
omp-kali mcp sync
omp-kali mcp sync --force
```
