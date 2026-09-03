FROM python:3.12-slim
LABEL org.opencontainers.image.title="Obelisk" \
      org.opencontainers.image.description="One container to run and manage an ARK: Survival Ascended cluster"
WORKDIR /app
RUN pip install --no-cache-dir "discord.py>=2.3,<3" "aiohttp>=3.9"
COPY obelisk/ /app/obelisk/
ENV PYTHONUNBUFFERED=1 OBELISK_DATA=/data
VOLUME ["/data"]
CMD ["python", "-m", "obelisk.bot"]
