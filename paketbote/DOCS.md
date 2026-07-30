# Paketbote

Amazon-Lieferverfolgung über einen dauerhaft eingeloggten Browser, den du selbst
bedienen kannst.

Amazon hat keine öffentliche API für Bestell- oder Lieferdaten. Die einzige
Quelle für Live-Zustelldaten ist der eingeloggte Progress-Tracker auf
`amazon.de`. Dieses Add-on betreibt dafür einen echten Chrome und stellt ihn als
Panel in der HA-Sidebar bereit — inklusive Maus und Tastatur, damit Login, MFA
und Captchas direkt dort erledigt werden können, auch vom iPhone aus.

**Stand: Phase 1.** Bisher gibt es nur den bedienbaren Browser. Scraping,
LLM-Extraktion und MQTT folgen in den Phasen 2–5.

## Installation

1. Repository in Home Assistant hinzufügen —
   **Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories**:
   `https://github.com/BobMcGlobus/ha-paketbote`
2. `Paketbote` installieren und starten.
3. Das Panel erscheint als **Paketbote** in der Sidebar.

Der erste Start dauert länger, weil Chrome im Image gebaut wird.

## Konfiguration

| Option | Default | Bedeutung |
|---|---|---|
| `display_width` / `display_height` | 1280 / 1024 | Auflösung des virtuellen Bildschirms. Kleiner = flüssiger auf dem iPhone |
| `amazon_domain` | `amazon.de` | Startseite und Ziel des Scrapers |
| `llm_provider` | `gemini` | Ab Phase 3 relevant |
| `llm_model` | `gemini-2.5-flash` | Freies Textfeld, damit ein Modellwechsel keinen Rebuild braucht |
| `llm_api_key` | leer | Ab Phase 3 relevant |
| `poll_*_minutes` | s. Plan | Polling-Leiter der Zustandsmaschine, ab Phase 4 |
| `quiet_hours_start` / `_end` | 22 / 6 | Nachtruhe: dazwischen nur `IDLE`-Intervall |
| `daily_request_cap` | 300 | Harter Stopp bis Mitternacht, ab Phase 6 |
| `jitter_percent` | 20 | Zufallsstreuung auf jedes Intervall |
| `log_level` | `info` | `trace` zeigt jeden Schritt |

MQTT-Zugangsdaten kommen über die Supervisor-Services-API und stehen bewusst
**nicht** in den Optionen.

## Erste Inbetriebnahme

1. Add-on starten, Panel in der Sidebar öffnen.
2. Im Browser bei Amazon anmelden, MFA durchführen, „Angemeldet bleiben"
   bestätigen.
3. Add-on einmal neu starten und das Panel erneut öffnen — du solltest **immer
   noch angemeldet** sein. Das ist der eigentliche Test von Phase 1.

Vor dem Produktivbetrieb: **1-Click-Bestellung im Amazon-Konto deaktivieren.**
Im Panel sitzt ein voll funktionsfähiger, eingeloggter Browser.

## Wie das intern läuft

```
Xvfb :99  ──▶  Openbox  ──▶  Google Chrome (headful, CDP :9222)
   │
   └──▶  x11vnc :5900  ──▶  websockify/noVNC :6081  ──▶  nginx :6080  ──▶  Ingress
```

Alle Ports außer 6080 sind an `127.0.0.1` gebunden, und 6080 nimmt nur
Verbindungen vom Supervisor an. Nach außen existiert kein offener VNC-Port.

**Chrome läuft als eigener, langlebiger Prozess** — nicht von Playwright
gestartet. Der Scraper hängt sich ab Phase 2 per CDP an. Dadurch überlebt die
Amazon-Session jeden Scraper-Neustart und jeden Scraper-Crash. Nebeneffekt: weil
Chrome nie mit `--enable-automation` startet, bleibt `navigator.webdriver`
undefiniert.

Statt Debian-Chromium wird **Google Chrome Stable** installiert. Der Browser
redet eingeloggt mit Amazon; ein Standard-Chrome ist dabei der unauffälligste
Client.

Das Profil liegt in `/config/chromium-profile` (Add-on-Config-Volume) und
überlebt Neustarts und Updates. Es enthält Amazon-Session- und
Device-Trust-Cookies — dieses Volume gehört **nicht** in einen Backup-Sync im
Klartext.

## Fehlersuche

**Panel bleibt grau oder schwarz**
Xvfb oder Chrome sind noch nicht oben. Ein paar Sekunden warten, dann neu laden.
Bleibt es dabei: Add-on-Log prüfen.

**Panel zeigt „Failed to connect to server"**
Die Websocket-Verbindung kam nicht durch. Log auf `x11vnc`- oder
`novnc`-Fehler prüfen.

**Chrome startet nicht, Log nennt ein gesperrtes Profil**
Sollte nicht passieren — `init-browser` räumt `SingletonLock` bei jedem Start
weg. Falls doch: Add-on stoppen, Datei aus `/config/chromium-profile` löschen.

**Nach Neustart wieder ausgeloggt**
Prüfen, ob `addon_config` wirklich gemappt ist und ob beim Login „Angemeldet
bleiben" aktiv war. Amazon setzt Device-Trust-Cookies nur dann dauerhaft.

**Panel reagiert träge auf dem iPhone**
`display_width`/`display_height` reduzieren, z. B. auf 1024×768.
