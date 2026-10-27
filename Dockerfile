FROM python:3.12-slim

# Tectonic (LaTeX engine) for Linux. It downloads TeX packages on first use, so the
# container needs outbound network access (or pre-warm the cache at build time below).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates fontconfig libgraphite2-3 libharfbuzz0b libssl3 \
    && rm -rf /var/lib/apt/lists/* \
    && cd /usr/local/bin \
    && curl --proto '=https' --tlsv1.2 -fsSL https://drop-sh.fullyjustified.net | sh

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# Pre-warm Tectonic's package cache so the first user request isn't slow.
RUN printf '\documentclass{article}\begin{document}warm\end{document}' > /tmp/w.tex \
    && tectonic /tmp/w.tex || true

ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
