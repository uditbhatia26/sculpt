# Debian 12 (bookworm) pinned, so a new Debian release can't change the image under us
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Tectonic (the LaTeX engine for resume PDFs): the fully static Linux build, pinned and
# checksum-verified. The static build needs no system libraries, unlike the one the
# drop-sh installer picks. Same version as backend/bin/tectonic.exe used locally.
ARG TECTONIC_VERSION=0.17.0
ARG TECTONIC_SHA256=8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 -fsSL -o /tmp/tectonic.tar.gz \
        "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
    && echo "${TECTONIC_SHA256}  /tmp/tectonic.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/tectonic.tar.gz -C /usr/local/bin tectonic \
    && rm /tmp/tectonic.tar.gz \
    && tectonic --version

# Run as an unprivileged user, not root. It owns /app so it can write app.log there.
RUN useradd --create-home --uid 10001 app \
    && mkdir /app && chown app:app /app
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .
USER app

# Compile every template once, so the LaTeX packages are cached in the image (in the app
# user's home) and the first resume after a deploy isn't slow. Fails the build if Tectonic
# can't produce a PDF, instead of shipping PDFs that silently fall back to ReportLab.
RUN python scripts/warm_tectonic.py

# Render sets PORT; 8000 is the local default
ENV PORT=8000
EXPOSE 8000
# Apply any pending migrations, then start the API
CMD ["sh", "-c", "python init_db.py && uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
