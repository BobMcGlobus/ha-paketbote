# Changelog

## 0.2.0

Phase 2 — Scraper-Kern. Liest, interpretiert aber nichts.

- Playwright hängt sich per `connect_over_cdp` an den laufenden Chrome und
  benutzt dessen Profil-Context, also die eingeloggte Session
- Sendungserkennung auf der Bestellübersicht über Tracking-URLs statt über
  CSS-Klassen
- Rohtext-Extraktion je Tracking-Seite, 2–5 s Pause zwischen den Seiten
- Login-/MFA-/Captcha-Erkennung: sauberes `LoginRequired` statt Crash
- CLI `paketbote --dump`, plus Option `dump_on_start` für den Weg ohne SSH
- Unit-Tests für Sendungs-IDs, Textnormalisierung und Optionen

## 0.1.0

Phase 1 — add-on skeleton with a usable browser. No scraping yet.

- Add-on structure on `ghcr.io/hassio-addons/debian-base` (Debian 13)
- s6 services: Xvfb `:99` → Openbox → Google Chrome (headful, CDP on 9222)
- x11vnc and noVNC, both bound to loopback
- nginx on port 6080 as the ingress entry point
- Chrome profile persisted in `/config/chromium-profile`
