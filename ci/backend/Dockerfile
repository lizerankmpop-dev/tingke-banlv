FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       portaudio19-dev \
       libsndfile1 \
       ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN python -c "import sys; assert sys.version_info[:2] == (3, 11)" \
    && test -f /usr/include/portaudio.h
RUN printf '#include <stdlib.h>\n#include <portaudio.h>\nint main(void) { return Pa_GetVersion() > 0 ? EXIT_SUCCESS : EXIT_FAILURE; }\n' \
    | gcc -x c - -o /tmp/ci-portaudio-check -lportaudio \
    && /tmp/ci-portaudio-check \
    && rm /tmp/ci-portaudio-check