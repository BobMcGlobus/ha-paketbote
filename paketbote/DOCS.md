# Paketbote

Amazon-Lieferverfolgung über einen dauerhaft eingeloggten Browser, den du selbst
bedienen kannst.

Amazon hat keine öffentliche API für Bestell- oder Lieferdaten. Die einzige
Quelle für Live-Zustelldaten ist der eingeloggte Progress-Tracker auf
`amazon.de`. Dieses Add-on betreibt dafür einen echten Chrome und stellt ihn als
Panel in der HA-Sidebar bereit — inklusive Maus und Tastatur, damit Login, MFA
und Captchas direkt dort erledigt werden können, auch vom iPhone aus.

## Installation

1. Repository in Home Assistant hinzufügen —
   **Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories**:
   `https://github.com/BobMcGlobus/ha-paketbote`
2. `Paketbote` installieren und starten.
3. **Paketbote** erscheint in der Sidebar.

Der erste Start dauert länger, weil das Image gebaut wird.

**Voraussetzung für die Entities:** ein MQTT-Broker. Das Mosquitto-Add-on
genügt; die Zugangsdaten holt sich Paketbote selbst über den Supervisor.

## Erste Inbetriebnahme

1. Add-on starten, Panel in der Sidebar öffnen.
2. Bei Amazon anmelden, MFA durchführen, „Angemeldet bleiben" bestätigen.
3. Add-on einmal neu starten und das Panel erneut öffnen — du solltest **immer
   noch angemeldet** sein.

Vor dem Produktivbetrieb: **1-Click-Bestellung im Amazon-Konto deaktivieren.**
Im Panel sitzt ein voll funktionsfähiger, eingeloggter Browser.

## Die Oberfläche

Das Panel in der Sidebar zeigt jetzt die Sendungsübersicht: was unterwegs ist,
für wen, wann erwartet, mit welchem Zusteller. Dazu Warnungen, wenn Amazon eine
Anmeldung verlangt, das Tageslimit erreicht ist oder die Selektoren nicht mehr
greifen.

- **Jetzt abrufen** stößt einen Zyklus sofort an, statt auf das nächste
  Intervall zu warten.
- **Browser öffnen** führt zum bisherigen Browser-Panel — für Login, MFA und
  Captchas.
- **Sendung manuell hinzufügen** nimmt eine Sendungsnummer samt Zusteller auf,
  für Pakete, die keine Quelle kennt. Bei DHL wird der Status danach automatisch
  geholt, sofern ein Schlüssel hinterlegt ist; bei den übrigen Zustellern
  entsteht ein Link zur jeweiligen Sendungsverfolgung.

Die Oberfläche läuft als eigener Prozess und liest nur mit. Ein Fehler dort
kann die Abrufschleife nicht anhalten.

## Entities

**Aggregate** — immer vorhanden, stabile IDs. Das ist, was Automationen nutzen.

| Entity | Bedeutung |
|---|---|
| `sensor.paketbote_pakete_heute` | Für heute erwartete Sendungen |
| `sensor.paketbote_pakete_aktiv` | Sendungen, die noch unterwegs sind |
| `sensor.paketbote_naechste_stopps` | Kleinste Stopp-Zahl über alle Sendungen |
| `sensor.paketbote_gesamtstatus` | Dringendster Zustand aller Sendungen |
| `sensor.paketbote_naechstes_fenster` | Frühester Fensterbeginn |
| `sensor.paketbote_letzter_abruf` | Watchdog-Basis |
| `sensor.paketbote_extraktionsmethode` | `css`, `llm`, `mixed` |
| `sensor.paketbote_sendungen` | Anzahl aktiver Sendungen; alle Details im Attribut `shipments` |
| `binary_sensor.paketbote_zustellfenster_aktiv` | Mindestens eine Sendung im Fenster |
| `binary_sensor.paketbote_zustellung_unmittelbar` | Mindestens eine Sendung kurz davor |
| `binary_sensor.paketbote_login_erforderlich` | Amazon verlangt Interaktion |
| `binary_sensor.paketbote_gedrosselt` | Request-Cap erreicht |
| `binary_sensor.paketbote_selektoren_defekt` | CSS-Selektoren tragen nicht mehr |

**Pro Sendung** — dynamisch, verschwinden nach Zustellung: je ein Gerät mit
Status, Stopps, Fenster ab, Erwartet, Titel, Zusteller, **Empfänger** und
**Lieferadresse**.

Empfänger und Adresse gibt es, weil ein Amazon-Konto einen ganzen Haushalt
bedienen kann. Automationen lassen sich damit auf die eigenen Pakete
einschränken — über den Empfängernamen oder den Ort in der Adresse.

`sensor.paketbote_sendungen` trägt die komplette Liste als Attribut — das ist
der Sensor, den eine Lovelace-Karte auslesen sollte, statt sich durch die
dynamischen Geräte zu hangeln.

Die Aggregate werden vom Add-on berechnet, nicht in HA nachgebaut. Ein
Template-Sensor über eine wechselnde Entity-Liste bricht bei jeder neuen
Sendung. In Automationen deshalb `entity_id` verwenden, nie `device_id`.

## Konfiguration

| Option | Default | Bedeutung |
|---|---|---|
| `display_width` / `display_height` | 1280 / 1024 | Auflösung des virtuellen Bildschirms |
| `amazon_domain` | `amazon.de` | Bestimmt Übersicht und Tracker-URLs |
| `llm_provider` / `llm_model` | `gemini` / `gemini-2.5-flash` | Nur für den Fallback |
| `llm_api_key` | leer | **Optional.** Ohne Key läuft alles weiter, nur ohne Fallback |
| `dhl_api_key` | leer | **Optional.** Schaltet die DHL-Abfrage frei |
| `poll_idle_minutes` | 60 | Keine aktive Sendung |
| `poll_pending_minutes` | 15 | Zustellung heute, Fenster noch zu |
| `poll_window_minutes` | 10 | Fenster offen |
| `poll_approaching_minutes` | 3 | Weniger Stopps als die Schwelle |
| `poll_imminent_minutes` | 1 | Kurz davor |
| `approaching_stops_threshold` | 7 | |
| `imminent_stops_threshold` | 2 | |
| `quiet_hours_start` / `_end` | 22 / 6 | Nachtruhe: nur `IDLE`-Intervall |
| `daily_request_cap` | 300 | Harter Stopp bis Mitternacht |
| `jitter_percent` | 20 | Zufallsstreuung auf jedes Intervall |
| `dump_on_start` | `false` | Beim Start alle Seiten nach `/config/dumps/` schreiben |
| `developer_mode` | `false` | Siehe unten |
| `language` | `auto` | Sprache der Oberfläche: `auto`, `de`, `en` |
| `log_level` | `info` | `trace` zeigt jeden Schritt |

MQTT-Zugangsdaten kommen über die Supervisor-Services-API und stehen bewusst
**nicht** in den Optionen.

## Wie gelesen wird

Der Abruf läuft zweistufig:

1. **Bestellübersicht** — ein Seitenaufruf auf die normale Bestellliste.
   Meldet sie eine Sendung als zugestellt, wird das einmal veröffentlicht und
   das Gerät danach aus HA entfernt.

   *Nicht* `?orderFilter=open`: das ist Amazons Reiter „Nicht versendet“ und
   blendet ausgerechnet die Pakete aus, die schon unterwegs sind.
2. **Progress-Tracker** — ein Aufruf *je Sendung*, teuer. Wird nur für
   Sendungen geöffnet, die die Übersicht nicht als zugestellt meldet.

Ohne diese Vorauswahl würde jeder Zyklus so viele Tracker-Seiten öffnen, wie du
offene Bestellungen hast. Erkennt die Vorauswahl den Kartentext nicht, gilt die
Sendung als aktiv — ein Request zu viel ist besser als eine verpasste Zustellung.

### Zusteller statt Shop fragen

Quelle und Zusteller sind getrennt: die Quelle (Amazon) sagt, *was* bestellt
ist und liefert die Sendungsnummer — der Zusteller sagt, *wo* das Paket ist.

Ist `dhl_api_key` gesetzt und meldet Amazon DHL als Zusteller, wird der Status
direkt bei DHL geholt. Das ist genauer als Amazons Trackingseite und kostet
keinen Amazon-Abruf. Zustellfenster gibt DHL nur heraus, wenn die Postleitzahl
des Empfängers mitgeschickt wird; die kommt aus der Lieferadresse.

Kostenlos, 250 Abrufe pro Tag, höchstens einer alle fünf Sekunden — beides wird
eingehalten und separat vom Amazon-Budget gezählt. Einen Schlüssel gibt es auf
`developer.dhl.com` unter *Shipment Tracking — Unified*.

AMZL-Sendungen bleiben beim Amazon-Weg; dafür gibt es keine Alternative.

### CSS zuerst, LLM nur im Notfall

Die Seiten werden über Amazons eigenes Markup gelesen, nicht über ein
Sprachmodell. Das ist kostenlos, deterministisch und **sprachunabhängig**: die
Fortschrittsleiste trägt `data-`Attribute, sodass der aktuelle Schritt an seiner
Position erkannt wird und nicht am Wort „Dispatched" oder „Versandt".

Erst wenn die Selektoren keinen Status mehr liefern — also wenn Amazon umgebaut
hat — springt das LLM ein. Ohne API-Key passiert das nicht; dann wird der letzte
bekannte Zustand behalten und geloggt.

Beide Fälle sind sichtbar: `sensor.paketbote_extraktionsmethode` sagt, was
gerade benutzt wurde, und `binary_sensor.paketbote_selektoren_defekt` geht an,
sobald CSS nicht mehr trägt. So merkst du, dass ein Update fällig ist, bevor
irgendwo etwas still kaputtgeht.

### Zustandsmaschine

```
IDLE ──▶ PENDING ──▶ WINDOW ──▶ APPROACHING ──▶ IMMINENT ──▶ DELIVERED
 60min     15min      10min        3min           1min
```

Der Zustand wird pro Sendung aus den aktuellen Daten **neu berechnet**, nicht
fortgeschrieben — Amazon korrigiert Stopp-Zahlen gelegentlich nach oben, und
Rückwärtsübergänge müssen funktionieren. Das effektive Intervall ist das
Minimum über alle Sendungen, plus Jitter.

Ohne Stopp-Zahl endet die Leiter bei `WINDOW`; die beiden schnellsten Stufen
entfallen dann ersatzlos. Zwischen `quiet_hours_start` und `_end` gilt immer das
`IDLE`-Intervall.

## developer_mode

Schaltet zusätzliche Diagnose ein:

- `binary_sensor.paketbote_selektoren_defekt` bekommt Attribute mit der
  Trefferquote **je Feld** über alle bisherigen Abrufe — daran siehst du, ob nur
  ein Selektor bröckelt oder Amazon komplett umgebaut hat
- jede Seite, die die Selektoren nicht lesen konnten, landet als `.html` und
  `.txt` unter `/config/dumps/selector-misses/`

Im Normalbetrieb aus lassen: die Dumps enthalten deine Lieferadresse.

## Sendungen manuell auslesen

**Ohne SSH:** `dump_on_start` auf `true`, Add-on neu starten. Danach liegen die
Captures unter `/config/dumps/<Zeitstempel>/`. In Studio Code Server über
`/addon_configs/` erreichbar, im Ordner der auf `_paketbote` endet.

**Mit Terminal:**

```bash
docker exec addon_local_paketbote paketbote --dump
```

| Aufruf | Wirkung |
|---|---|
| `paketbote --dump` | Übersicht + Rohtext aller aktiven Sendungen |
| `paketbote --orders-only` | Nur die Sendungsliste, öffnet keine Tracker-Seiten |
| `paketbote --dump --include-delivered` | Auch die als zugestellt gemeldeten |
| `paketbote --dump --undispatched-only` | Amazons Reiter „Nicht versendet“ statt der Bestellliste |
| `paketbote --dump --html --out DIR` | Zusätzlich das DOM, für CSS-Selektoren |

Exit-Codes: `0` ok, `2` Amazon verlangt Login/MFA/Captcha, `3` Chrome nicht
erreichbar.

## Wie das intern läuft

```
Xvfb :99  ──▶  Openbox  ──▶  Google Chrome (headful, CDP :9222)
   │                                   ▲
   └──▶  x11vnc :5900  ──▶  noVNC :6081  ──▶  nginx :6080  ──▶  Ingress
                                       │
                     Scheduler ────────┘  ──▶  MQTT
```

Alle Ports außer 6080 sind an `127.0.0.1` gebunden, und 6080 nimmt nur
Verbindungen vom Supervisor an. Nach außen existiert kein offener VNC-Port.

**Chrome läuft als eigener, langlebiger Prozess** — nicht von Playwright
gestartet. Der Scheduler hängt sich per CDP an. Dadurch überlebt die
Amazon-Session jeden Scheduler-Neustart und jeden Crash. Nebeneffekt: weil
Chrome nie mit `--enable-automation` startet, bleibt `navigator.webdriver`
undefiniert.

Statt Debian-Chromium wird **Google Chrome Stable** installiert — der
unauffälligste Client für eine eingeloggte Amazon-Sitzung.

Profil und Zustandsdatenbank liegen in `/config` (Add-on-Config-Volume) und
überleben Neustarts und Updates. Das Profil enthält Session- und
Device-Trust-Cookies und gehört **nicht** in einen Klartext-Backup-Sync.

## Fehlersuche

**Panel bleibt grau oder schwarz**
Xvfb oder Chrome sind noch nicht oben. Ein paar Sekunden warten, dann neu
laden. Bleibt es dabei: Add-on-Log prüfen.

**Panel verbindet sich nicht**
Die Websocket-Verbindung kam nicht durch. Im Log nach `x11vnc`- oder
`novnc`-Fehlern suchen.

**Keine Entities in HA**
Log auf `No MQTT broker` prüfen. Mosquitto-Add-on installieren, MQTT-Integration
hinzufügen, Paketbote neu starten.

**`binary_sensor.paketbote_login_erforderlich` ist an**
Amazon will Login, MFA oder ein Captcha. Panel öffnen, erledigen — das Add-on
versucht es von selbst wieder, mit wachsendem Abstand.

**`binary_sensor.paketbote_selektoren_defekt` ist an**
Amazon hat sein Markup geändert. Mit `developer_mode` liegen die betroffenen
Seiten unter `/config/dumps/selector-misses/`; damit lässt sich ein Issue
aufmachen. Mit hinterlegtem LLM-Key läuft es derweil weiter.

**Nach Neustart wieder ausgeloggt**
Prüfen, ob `addon_config` gemappt ist und beim Login „Angemeldet bleiben" aktiv
war. Amazon setzt Device-Trust-Cookies nur dann dauerhaft.

**Panel reagiert träge auf dem iPhone**
`display_width`/`display_height` reduzieren, z. B. auf 1024×768.
