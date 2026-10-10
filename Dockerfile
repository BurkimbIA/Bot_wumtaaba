FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN groupadd --gid 10001 bot && useradd --uid 10001 --gid bot --home-dir /app bot
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
RUN pip install -r requirements.lock && pip install --no-deps . && mkdir -p /app/data && chown -R bot:bot /app/data
COPY scripts/container_start.py /app/container_start.py
EXPOSE 8000
ENTRYPOINT ["python", "/app/container_start.py"]
CMD ["uvicorn", "bot_wumtaaba.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
