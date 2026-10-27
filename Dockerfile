FROM python:3.12-slim-bookworm

# Calibre ebook-convert az EPUB -> AZW3 konverzióhoz (WITH_CALIBRE=false esetén kimarad, kisebb image)
ARG WITH_CALIBRE=true
RUN if [ "$WITH_CALIBRE" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends calibre fonts-dejavu-core && \
      rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

RUN useradd -u 1000 -m polc && mkdir -p /data && chown polc /data
USER polc

ENV DATA_DIR=/data PORT=8080 QT_QPA_PLATFORM=offscreen PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8080 8181
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/egeszseg' % os.environ.get('PORT','8080'))" || exit 1
CMD ["sh", "-c", "exec uvicorn --factory app.main:create_app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips '*'"]
