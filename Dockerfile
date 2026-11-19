FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    BACKINFRAME_MEDIA_DIR=/media \
    BACKINFRAME_DATA_DIR=/data

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install . && rm -rf /app/src

# Runs as an unprivileged user; override with `user:` in compose (Unraid: 99:100).
RUN useradd --uid 1000 --user-group --no-create-home backinframe \
 && mkdir -p /data /media && chown backinframe:backinframe /data
USER backinframe

VOLUME ["/data"]
EXPOSE 8080 8081
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4)" || exit 1

CMD ["backinframe"]
