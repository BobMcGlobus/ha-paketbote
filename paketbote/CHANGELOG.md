# Changelog

## 0.1.0

Phase 1 — add-on skeleton with a usable browser. No scraping yet.

- Add-on structure on `ghcr.io/hassio-addons/debian-base` (Debian 13)
- s6 services: Xvfb `:99` → Openbox → Google Chrome (headful, CDP on 9222)
- x11vnc and noVNC, both bound to loopback
- nginx on port 6080 as the ingress entry point
- Chrome profile persisted in `/config/chromium-profile`
