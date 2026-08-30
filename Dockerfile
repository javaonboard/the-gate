# THE GATE
#
# Built in two stages: the interface, then the service that serves it.
#
# ffmpeg is why this is a Dockerfile and not a buildpack. Cutting a film into
# shots, pulling frames for the model and probing durations are all shelling
# out to ffmpeg, and no Python buildpack ships it.

FROM node:22-slim AS web

WORKDIR /web
COPY web/package*.json ./
RUN npm ci
COPY web/ ./
RUN npm run build


FROM python:3.12-slim

# ffmpeg for cutting and probing; ca-certificates so ClickHouse and the model
# endpoints can be reached over TLS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/
COPY api/ ./api/
COPY core/ ./core/
COPY data/ ./data/
COPY --from=web /web/dist ./web/dist

# Footage lives outside the source tree so it can never be committed. In the
# image it sits beside the app; a deployment that needs it to survive a restart
# should mount a volume or point FOOTAGE_ROOT at a bucket.
ENV FOOTAGE_ROOT=/footage
RUN mkdir -p /footage/clips

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

# One worker on purpose. Runs in flight are held in memory and streamed to the
# browser, so a second worker would answer with a run it has never heard of.
CMD exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 1
