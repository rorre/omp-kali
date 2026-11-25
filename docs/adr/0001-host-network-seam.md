# 1. Host-network queries stay a separate seam

Date: 2026-11-25

## Status

Accepted

## Context

`cli._listening_addresses` runs `ss -ltnH` on the host to check whether a port
is bound and, if so, whether it is loopback-only. This backs the `_warn_loopback`
warning shown by `ports add` for host-to-container forwards.

An architecture review flagged this as a leaked exec seam: `cli.py` calls
`subprocess.run` directly while `container.py` routes podman calls through the
`Podman` adapter. The speculative candidate was to route the `ss` call through
that adapter, or through a new `HostNetwork` adapter.

## Decision

The `ss` call is a host-network query, not a podman call. Routing it through the
`Podman` adapter would be a false seam: `Podman`'s interface is the podman CLI,
and `ss` is unrelated to it. There is exactly one call site, and it only feeds a
best-effort warning.

We keep the direct `subprocess.run`. If host-network queries grow past this
single call, introduce a dedicated `HostNetwork` adapter rather than overloading
`Podman`.

## Consequences

- The `Podman` interface stays honest: it wraps podman, not arbitrary host commands.
- `_listening_addresses` is not injectable; the warning path is not unit-tested.
  Acceptable while it is a single, best-effort call.
- Future architecture reviews should not re-suggest folding `ss` into `Podman`;
  a separate `HostNetwork` adapter is the path if this grows.
