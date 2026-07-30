ARG BUILD_FROM=ghcr.io/hassio-addons/debian-base:9.3.0
# hadolint ignore=DL3006
FROM ${BUILD_FROM}

# shellcheck shell=bash
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV LANG=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99 \
    CHROME_PROFILE=/config/chromium-profile \
    PYTHONUNBUFFERED=1

# X stack, VNC bridge, ingress proxy.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        dbus-x11 \
        fonts-liberation \
        fonts-noto-color-emoji \
        gnupg \
        nginx-light \
        openbox \
        tzdata \
        x11-utils \
    ; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# KasmVNC is the X server *and* the web client in one process, replacing
# Xvfb + x11vnc + websockify. Its region-based compression is what makes the
# panel usable over mobile data; plain VNC framebuffer updates were not.
ARG KASMVNC_VERSION=1.5.0
RUN set -eux; \
    curl -fsSL -o /tmp/kasmvnc.deb \
        "https://github.com/kasmtech/KasmVNC/releases/download/v${KASMVNC_VERSION}/kasmvncserver_trixie_${KASMVNC_VERSION}_amd64.deb"; \
    apt-get update; \
    apt-get install -y --no-install-recommends /tmp/kasmvnc.deb; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /tmp/kasmvnc.deb

# Google Chrome rather than Debian's chromium: this browser talks to Amazon
# while logged in, and a stock Chrome build is the least remarkable client
# there is. Playwright attaches over CDP later, so Chrome is never launched
# with --enable-automation and navigator.webdriver stays undefined.
RUN set -eux; \
    echo 'repo_add_once=false' > /etc/default/google-chrome; \
    install -d -m 0755 /etc/apt/keyrings; \
    curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
        | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg; \
    echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends google-chrome-stable; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Python side. `playwright install` is deliberately NOT run: the scraper
# attaches over CDP to the Chrome that already lives in this image, so the
# bundled browser downloads would be ~500 MB of dead weight.
COPY app/requirements.txt /opt/paketbote/app/requirements.txt
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends python3 python3-venv; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*; \
    python3 -m venv /opt/venv; \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip; \
    /opt/venv/bin/pip install --no-cache-dir -r /opt/paketbote/app/requirements.txt

COPY app /opt/paketbote/app
COPY rootfs /

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# The app reports this, so the log and the device info cannot disagree with
# the add-on version again.
ENV PAKETBOTE_VERSION=${BUILD_VERSION}

LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="Bob McGlobus <42802973+BobMcGlobus@users.noreply.github.com>" \
    org.opencontainers.image.title="${BUILD_NAME}" \
    org.opencontainers.image.description="${BUILD_DESCRIPTION}" \
    org.opencontainers.image.source="https://github.com/${BUILD_REPOSITORY}" \
    org.opencontainers.image.created=${BUILD_DATE} \
    org.opencontainers.image.revision=${BUILD_REF} \
    org.opencontainers.image.version=${BUILD_VERSION}
