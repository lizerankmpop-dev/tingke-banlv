FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gcc \
       portaudio19-dev \
       libsndfile1 \
       ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN python -c "import sys; assert sys.version_info[:2] == (3, 11)" \
    && test -f /usr/include/portaudio.h