FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.4 /uv /usr/local/bin/uv
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN groupadd --gid 10001 bot && useradd --uid 10001 --gid bot --home-dir /app bot

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable && mkdir -p /app/data && chown -R bot:bot /app/data
COPY scripts/container_start.py /app/container_start.py
EXPOSE 8000
ENTRYPOINT ["python", "/app/container_start.py"]
CMD ["uvicorn", "bot_wumtaaba.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
