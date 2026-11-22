ARG BASE_IMAGE=docker.io/kalilinux/kali-rolling:latest
FROM ${BASE_IMAGE}

ARG CONTAINER_USER=omp
ARG PROVISION_HASH=unknown

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    HOME=/home/omp \
    CONTAINER_HOME=/home/omp \
    CONTAINER_USER=${CONTAINER_USER}

LABEL omp-kali.image="true" \
      omp-kali.provision-hash="${PROVISION_HASH}"

RUN set -eu; \
    mkdir -p /home/omp/.omp/agent /home/omp/.local/bin /home/omp/.omp-kali /etc/sudoers.d && \
    apt-get update && \
    apt-get install -y --no-install-recommends python3 ca-certificates sudo && \
    case "$CONTAINER_USER" in *[!A-Za-z0-9_.-]*) echo "invalid CONTAINER_USER: $CONTAINER_USER" >&2; exit 1;; esac && \
    printf '%s ALL=(ALL) NOPASSWD: ALL\n' "$CONTAINER_USER" > /etc/sudoers.d/omp-kali && \
    chmod 0440 /etc/sudoers.d/omp-kali && \
    visudo -cf /etc/sudoers.d/omp-kali && \
    rm -rf /var/lib/apt/lists/*

COPY provisioning/ /tmp/omp-kali/provisioning/

RUN set -eu; \
    for script in /tmp/omp-kali/provisioning/*.sh; do \
        [ -e "$script" ] || continue; \
        echo ">>> running $script"; \
        bash -e "$script"; \
    done; \
    rm -rf /tmp/omp-kali /var/lib/apt/lists/*

CMD ["sleep", "infinity"]
