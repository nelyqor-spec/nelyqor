FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN useradd --uid 10001 --create-home nelyqor
WORKDIR /app
COPY requirements.lock .
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* \
    && pip install -r requirements.lock
COPY backend ./backend
COPY config ./config
COPY frontend ./frontend
COPY scripts ./scripts
COPY pyproject.toml .
RUN mkdir -p /data /state && chown nelyqor /data /state
USER nelyqor
ENV NELYQOR_DB=/data/nelyqor.db
