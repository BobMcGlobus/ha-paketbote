# Paketbote

Amazon-Lieferverfolgung über einen dauerhaft eingeloggten Browser, den du selbst
bedienen kannst.

Amazon hat keine öffentliche API für Bestell- oder Lieferdaten. Die einzige
Quelle für Live-Zustelldaten ist der eingeloggte Progress-Tracker auf
`amazon.de`. Dieses Add-on betreibt dafür einen echten Chrome und stellt ihn als
Panel in der HA-Sidebar bereit — inklusive Maus und Tastatur, damit Login, MFA
und Captchas direkt dort erledigt werden können, auch vom iPhone aus.

**Stand: Phase 2.** Der Browser ist bedienbar und der Scraper liest die
Tracking-Seiten als Rohtext aus. Interpretiert wird noch nichts — LLM-Extraktion
und MQTT folgen in den Phasen 3–5. Entities gibt es also noch keine.

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
| `dump_on_start` | `false` | Beim Start einmal alle Tracking-Seiten nach `/config/dumps/` schreiben |
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

## Sendungen auslesen

Der Abruf läuft zweistufig, und das ist Absicht:

1. **Bestellübersicht** — ein Seitenaufruf auf `?orderFilter=open`, also nur
   offene Bestellungen. Liefert alle Sendungen, ihre Tracking-Links und den
   Text der jeweiligen Bestellkarte. Da steht schon Zustelldatum und Status
   drin. Verschwindet eine Sendung aus dieser Liste, ist sie angekommen.
2. **Progress-Tracker** — ein Aufruf *je Sendung*, teuer. Wird nur für
   Sendungen geöffnet, die die Übersicht nicht als zugestellt meldet.

Ohne diese Vorauswahl würde jeder Zyklus so viele Tracker-Seiten öffnen, wie du
offene Bestellungen hast — bei einem Dutzend Bestellungen ist das Tagesbudget
nach wenigen Zyklen weg. Erkennt die Vorauswahl den Kartentext nicht, gilt die
Sendung als aktiv: ein Request zu viel ist besser als eine verpasste Zustellung.

Die Tracker-Seiten öffnen sich der Reihe nach in sichtbaren Tabs, 2–5 s Pause
dazwischen. Beim Debuggen kannst du im Panel zusehen.

**Ohne SSH:** Option `dump_on_start` auf `true`, Add-on neu starten. Nach dem
Start liegen die Captures unter `/config/dumps/<Zeitstempel>/` — eine `.txt` je
Sendung, erreichbar über File Editor oder Samba. Danach die Option wieder
ausschalten.

**Mit Terminal:** im Add-on-Container steht `paketbote` bereit.

```bash
docker exec addon_local_paketbote paketbote --dump
```

| Aufruf | Wirkung |
|---|---|
| `paketbote --dump` | Übersicht + Rohtext aller aktiven Sendungen nach stdout |
| `paketbote --orders-only` | Nur die Sendungsliste mit aktiv/zugestellt, öffnet keine Tracker-Seiten |
| `paketbote --dump --include-delivered` | Öffnet auch die als zugestellt gemeldeten Sendungen |
| `paketbote --dump --full-history` | Startet auf der kompletten Bestellhistorie statt nur den offenen |
| `paketbote --dump --out DIR` | Dateien in `DIR`, stdout bleibt eine Übersicht |
| `paketbote --dump --html --out DIR` | Zusätzlich das DOM je Seite, für CSS-Selektoren |
| `paketbote --log-level trace` | Zeigt jede Navigation |

Mit `--out` entstehen `_overview.txt` (Rohtext der Bestellübersicht),
`_cards.txt` (Kartentext je Sendung samt Aktiv-Einstufung) und je aktiver
Sendung eine `<shipment_id>.txt`. Mit `--html` zusätzlich die `.html`-Fassung.

**Die Dumps enthalten deine Lieferadresse.** Sie liegen im
Add-on-Config-Verzeichnis, in Studio Code Server unter `/addon_configs/` im
Ordner, der auf `_paketbote` endet. Vor dem Weitergeben anonymisieren.

Exit-Codes: `0` ok, `2` Amazon verlangt Login/MFA/Captcha, `3` Chrome nicht
erreichbar.

Verlangt Amazon eine Interaktion, bricht der Scraper sauber mit `LoginRequired`
ab statt zu crashen. Panel öffnen, Challenge erledigen, erneut laufen lassen.

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
