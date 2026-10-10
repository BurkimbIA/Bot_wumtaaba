FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app

RUN groupadd --system bot && useradd --system --gid bot --home-dir /app bot
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
RUN pip install -r requirements.lock && pip install --no-deps . && mkdir -p /app/data && chown -R bot:bot /app/data
USER bot
EXPOSE 8000
CMD ["uvicorn", "bot_wumtaaba.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
