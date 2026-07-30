# Changelog

## 0.2.1

Die Bestellübersicht trifft jetzt die Vorauswahl, statt jede Sendung zu öffnen.

- **Zweistufig wie im Plan:** die Übersicht liefert Kartentext je Sendung,
  zugestellte Sendungen werden übersprungen. Vorher wurden alle 9 gefundenen
  Sendungen einzeln geöffnet — das sprengt das Request-Budget
- Übersichtstext wird selbst als `_overview.txt` und `_cards.txt` gedumpt
- `--all` öffnet auf Wunsch trotzdem alles
- **Titel-Bug behoben:** die Kartensuche brach nach 8 DOM-Ebenen ab, Amazon
  verschachtelt tiefer. Alle Sendungen hießen `untitled`
- Kartengrenze läuft jetzt bis über den Status-Header hinaus, sonst fehlte
  genau das „Zugestellt am …", nach dem gefiltert wird
- `/etc/machine-id` wird befüllt und auf dem Config-Volume stabil gehalten
- WebGL per Software-Rendering statt blocklisted — leiser und unauffälliger
- Kein irreführendes „restarting" mehr beim Herunterfahren

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
