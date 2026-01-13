FROM bitnami/minideb@sha256:bce8004f7da6547bc568e92895e1b3a3835e6dba48283fbbf9b3f66c1d166c6d
LABEL maintainer="support@opennix.ru"
LABEL description="Wazuh Docker Agent"
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
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      procps \
      curl \
      apt-transport-https \
      gnupg2 \
      inotify-tools \
      python3-docker \
      python3-setuptools \
      python3-pip \
      openjdk-17-jdk && \
    curl -s https://packages.wazuh.com/key/GPG-KEY-WAZUH | apt-key add - && \
    echo "deb https://packages.wazuh.com/4.x/apt/ stable main" > /etc/apt/sources.list.d/wazuh.list && \
    apt-get update && \    apt-get install -y wazuh-agent=${AGENT_VERSION} && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /tmp/requirements.txt

COPY *.py *.jinja2  /var/ossec/
WORKDIR /var/ossec/
RUN chmod +x /var/ossec/deregister_agent.py && \
    chmod +x /var/ossec/register_agent.py && \
    apt-get clean autoclean && \
    apt-get autoremove -y && \
    rm -rf /var/lib/{apt,dpkg,cache,log}/ && \
    rm -rf /tmp/* /var/tmp/* /var/log/* && \
    chown -R wazuh:wazuh /var/ossec/

# Ensure SCA ruleset directory exists
RUN mkdir -p /var/ossec/ruleset/sca

# Copy SCA policies into agent ruleset
COPY sca/*.yml /var/ossec/ruleset/sca/

# Permissions
RUN chown -R root:wazuh /var/ossec/ruleset/sca && \
    chmod 750 /var/ossec/ruleset/sca && \
    chmod 640 /var/ossec/ruleset/sca/*.yml

EXPOSE 5000
ENTRYPOINT ["./register_agent.py"]