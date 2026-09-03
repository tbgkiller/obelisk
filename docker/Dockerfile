FROM python:3.12-slim

LABEL org.opencontainers.image.title="Obelisk" \
      org.opencontainers.image.description="One container to run and manage an ARK: Survival Ascended cluster" \
      org.opencontainers.image.source="https://github.com/tbgkiller/obelisk"

# Obelisk generates a compose file and then runs it, so it needs the Docker CLI and
# the compose plugin - the mounted socket alone isn't enough, since `docker compose`
# is a client-side tool. Installed from Docker's own apt repository.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc; \
    chmod a+r /etc/apt/keyrings/docker.asc; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir "discord.py>=2.3,<3" "aiohttp>=3.9"
COPY obelisk/ /app/obelisk/

ENV PYTHONUNBUFFERED=1 OBELISK_DATA=/data
VOLUME ["/data"]
EXPOSE 8088
CMD ["python", "-m", "obelisk.bot"]
