# omp-kali

A CLI that runs an isolated omp instance inside a Kali Linux podman container, with its own home volume, auth, sessions, and settings.

## Language

**Container**:
The single podman container named `omp-kali` that this tool manages. It runs the Kali image and hosts one omp instance.
_Avoid_: box, sandbox, VM

**Container facts**:
The observed state of the Container: whether it exists, runs, and is managed; its image, workspace, GUI flag, published ports, and id.
_Avoid_: status, info, snapshot

**Desired container**:
The state the user's configuration asks for: image, workspace, GUI flag, and published ports.
_Avoid_: config, spec, target

**Apply**:
The action that brings the Container to the Desired container state: create if absent, start if stopped, then check and provision. It is the single place where the create/start/check/provision sequence lives.
_Avoid_: reconcile, enforce

**Recreate decision**:
The comparison of Container facts against the Desired container that decides whether the Container must be removed and recreated.
_Avoid_: drift check, diff

**Home volume**:
The named podman volume `omp-kali-home` that persists the Container's omp home across recreations.
_Avoid_: data volume, state volume

**Workspace**:
The single host directory mounted into the Container at `/workspace/<name>`.
_Avoid_: project, mount, volume
