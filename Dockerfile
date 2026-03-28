FROM python:3.11-slim

ARG DNGLAB_DOWNLOAD_URL

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CSI_WEB_ENV=production \
    CSI_WEB_HOST=0.0.0.0 \
    CSI_WEB_PORT=8080

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

RUN set -eux; \
    if [ -z "${DNGLAB_DOWNLOAD_URL}" ]; then \
        echo "DNGLAB_DOWNLOAD_URL build arg is required"; \
        exit 1; \
    fi; \
    curl -fsSL "${DNGLAB_DOWNLOAD_URL}" -o /tmp/dnglab-download; \
    mkdir -p /tmp/dnglab-extract; \
    case "${DNGLAB_DOWNLOAD_URL}" in \
        *.tar.gz|*.tgz) tar -xzf /tmp/dnglab-download -C /tmp/dnglab-extract ;; \
        *.tar.xz|*.txz|*.xz) tar -xJf /tmp/dnglab-download -C /tmp/dnglab-extract ;; \
        *) install -m 0755 /tmp/dnglab-download /usr/local/bin/dnglab ;; \
    esac; \
    if [ ! -x /usr/local/bin/dnglab ]; then \
        install -m 0755 "$(find /tmp/dnglab-extract -type f -name dnglab | head -n 1)" /usr/local/bin/dnglab; \
    fi; \
    /usr/local/bin/dnglab --version; \
    rm -rf /tmp/dnglab-download /tmp/dnglab-extract

COPY csi_tool ./csi_tool
COPY README.md ./README.md

EXPOSE 8080

CMD ["python", "-m", "csi_tool.web"]
