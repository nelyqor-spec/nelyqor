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
# phase 5 : une base par volume (moteur, commandes, affichage, audit API) ; voir compose.yaml
RUN mkdir -p /engine /commands /display /apidata /state && chown nelyqor /engine /commands /display /apidata /state
USER nelyqor
ENV NELYQOR_DB=/engine/engine.db NELYQOR_COMMANDS_DB=/commands/commandes.db NELYQOR_DISPLAY_DB=/display/affichage.db \
    NELYQOR_API_DB=/apidata/api.db
