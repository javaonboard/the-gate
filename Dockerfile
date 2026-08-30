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

# Footage and face crops are not in the image. Both are written at runtime and
# both have to outlive the container, so /footage and /faces are mount points
# for a bucket. Empty here, filled by the volume.
ENV FOOTAGE_ROOT=/footage     FACES_DIR=/faces
RUN mkdir -p /footage/clips /faces

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

# One worker on purpose. Runs in flight are held in memory and streamed to the
# browser, so a second worker would answer with a run it has never heard of.
# JSON form so the runtime does not wrap this in a shell of its own, with an
# explicit sh because ${PORT} still needs expanding. exec means uvicorn takes
# the process over, so a stop signal reaches it rather than the shell.
CMD ["sh", "-c", "exec uvicorn api.main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
