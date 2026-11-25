"""Container identity and layout constants.

Single source of truth for the container's fixed facts: name, volume, labels,
home directory, workspace root, base image, and image repository.
"""

PODMAN = "podman"
CONTAINER = "omp-kali"
VOLUME = "omp-kali-home"
MANAGED_LABEL = "omp-kali.managed"
WORKSPACE_LABEL = "omp-kali.workspace"
GUI_LABEL = "omp-kali.gui"

CONTAINER_HOME = "/home/omp"
WORKSPACE_ROOT = "/workspace"
BASE_IMAGE = "docker.io/kalilinux/kali-rolling:latest"
IMAGE_REPO = "localhost/omp-kali"