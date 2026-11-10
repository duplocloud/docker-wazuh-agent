############################
# Builder stage
############################
FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-pip \
    python3-dev \
    gcc \
    build-essential \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt

# Build wheels including pinned pip/setuptools/wheel for runtime upgrade
RUN pip3 install --no-cache-dir --break-system-packages --ignore-installed wheel && \
    pip3 wheel \
    --no-cache-dir \
    --wheel-dir /tmp/wheels \
    "pip>=26.2.0" \
    "setuptools>=83.0.0" \
    "wheel>=0.46.2" \
    -r /tmp/requirements.txt


############################
# Runtime stage
############################
FROM ubuntu:24.04

LABEL maintainer="support@opennix.ru"
LABEL description="Wazuh Docker Agent (Hardened Ubuntu)"

ENV DEBIAN_FRONTEND=noninteractive

ARG AGENT_VERSION="4.11.1-1"

ENV JOIN_MANAGER_MASTER_HOST=""
ENV JOIN_MANAGER_WORKER_HOST=""
ENV VIRUS_TOTAL_KEY=""
ENV JOIN_MANAGER_PROTOCOL="https"
ENV JOIN_MANAGER_USER=""
ENV JOIN_MANAGER_PASSWORD=""
ENV JOIN_MANAGER_API_PORT="55000"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

#  OS security upgrades + minimal packages
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-recommends \
      curl \
      ca-certificates \
      gnupg \
      inotify-tools \
      procps \
      python3 \
      python3-pip \
      python3-setuptools \
      systemd \
      systemd-sysv \
      libsystemd-dev \
 && rm -rf /var/lib/apt/lists/*

# Eclipse Temurin JRE (patched for CVE-2026-91403 / CVE-2026-114820 — Ubuntu's
# openjdk-17-jre-headless build has not shipped a fix for these as of this build)
RUN curl -fsSL https://packages.adoptium.net/artifactory/api/gpg/key/public \
    | gpg --dearmor -o /usr/share/keyrings/adoptium.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb noble main" \
    > /etc/apt/sources.list.d/adoptium.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends temurin-17-jre && \
    rm -rf /var/lib/apt/lists/*

# Wazuh repo (secure keyring)
RUN curl -fsSL https://packages.wazuh.com/key/GPG-KEY-WAZUH \
    | gpg --dearmor -o /usr/share/keyrings/wazuh.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/wazuh.gpg] https://packages.wazuh.com/4.x/apt/ stable main" \
    > /etc/apt/sources.list.d/wazuh.list && \
    apt-get update && \
    apt-get install -y wazuh-agent=${AGENT_VERSION} && \
    rm -rf /var/lib/apt/lists/*

# App files
COPY *.py *.jinja2 /var/ossec/
WORKDIR /var/ossec/

# Python wheels from builder
COPY --from=builder /tmp/wheels /tmp/wheels

RUN pip3 install \
    --no-cache-dir \
    --no-index \
    --find-links /tmp/wheels \
    --break-system-packages \
    --ignore-installed \
    pip setuptools wheel \
    requests Jinja2 urllib3 certifi idna \
    py-healthcheck psutil loguru markupsafe && \
    chmod +x /var/ossec/register_agent.py && \
    chmod +x /var/ossec/deregister_agent.py && \
    rm -rf /tmp/* && \
    chown -R wazuh:wazuh /var/ossec/ && \
    # Drop the apt-installed python3-pip/python3-setuptools now that the pinned
    # wheel-based copies above are in place — their stale vendored dependencies
    # (msgpack/urllib3/setuptools) otherwise linger and trip vulnerability scans.
    apt-get purge -y --auto-remove python3-pip python3-setuptools && \
    rm -rf /usr/lib/python3/dist-packages/pip* \
           /usr/lib/python3/dist-packages/setuptools* \
           /usr/lib/python3/dist-packages/pkg_resources \
           /var/lib/apt/lists/* && \
    # pip is only needed to install the wheels above; it is never invoked at
    # runtime by the agent. Removing it also drops its bundled SBOM files
    # (vendor.txt / bom.cdx.json) and _vendor/ copies of old msgpack/urllib3,
    # which otherwise show up as phantom vulnerabilities in image scans.
    rm -rf /usr/local/lib/python3.12/dist-packages/pip \
           /usr/local/lib/python3.12/dist-packages/pip-*.dist-info \
           /usr/local/bin/pip3 /usr/local/bin/pip

# SCA policies
RUN mkdir -p /var/ossec/ruleset/sca
COPY sca/*.yml /var/ossec/ruleset/sca/
RUN chown -R root:wazuh /var/ossec/ruleset/sca && \
    chmod 750 /var/ossec/ruleset/sca && \
    chmod 640 /var/ossec/ruleset/sca/*.yml

EXPOSE 5000
ENTRYPOINT ["./register_agent.py"]
