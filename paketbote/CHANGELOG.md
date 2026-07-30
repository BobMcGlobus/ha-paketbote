# Changelog

## 0.6.1

Chromes Umgebung exakt zurück auf den Stand, in dem das Browserfenster zuletzt
nachweislich sichtbar war (v0.2.3).

- `--disable-gpu` raus (kam in 0.5.4 gegen KasmVNCs sterbenden GPU-Prozess)
- `--start-maximized` raus (kam in 0.5.2 gegen einen vermuteten
  Platzierungsfehler)
- `XDG_RUNTIME_DIR` raus (kam in 0.3.0, weil KasmVNC eins wollte)

Alle drei entstanden als Umgehungen KasmVNC-spezifischer Probleme, die es ohne
KasmVNC nicht gibt — und alle drei kamen nach der letzten Version, in der der
Browser zu sehen war.

## 0.6.0

- **Zurück auf Xvfb + x11vnc + noVNC.** KasmVNC lieferte ein Panel, in dem das
  Browserfenster nachweislich existiert, gemappt und bildschirmfüllend ist —
  aber nichts malt. Vier Ansätze, vier Fehlschläge; der bewährte Stack aus
  v0.2.x kommt zurück, damit das Panel wieder tut, was es soll
- **Fehlalarm beim Selektoren-Sensor behoben.** Eine Seite, die noch nicht
  fertig gerendert war, sah aus wie eine mit geändertem Markup und löste
  „Selektoren defekt" aus. Es wird jetzt einmal nachgefasst, bevor Alarm
  geschlagen wird — sonst meldet der Sensor bei jeder langsamen Seite und
  bedeutet damit nichts mehr
- `--disable-gpu` bleibt: spart den GPU-Prozess und damit Lograuschen

## 0.5.4

Panel zeigte nur den Hintergrund, obwohl das Fenster nachweislich da war.

- Die Diagnose aus 0.5.3 hat es entschieden: `Browser window 4194307 found;
  sizing it to the display`. Das Fenster existiert, ist gemappt und
  bildschirmfüllend — es malt nur nichts
- Passend dazu eine Logzeile, die es mit Xvfb nie gab: *Exiting GPU process
  due to errors during initialization*. Das ist Chromes viz-Prozess, der
  Display-Compositor. Stirbt der, bleibt das Fenster leer
- `--disable-gpu` dazu: KasmVNCs X-Server bietet kein DRI, also wird gar nicht
  erst versucht, einen GPU-Prozess zu starten
- `window-check` protokolliert jetzt zusätzlich Position, Größe und Map-State
  des Fensters — ein leeres Vollbildfenster und ein falsch platziertes
  brauchen gegensätzliche Korrekturen

## 0.5.3

Das Add-on startete nicht mehr — mein Diagnose-Dienst aus 0.5.2 hat es
umgebracht.

- bashio führt Skripte unter `errexit` und `pipefail` aus, und ein
  fehlschlagender s6-Oneshot **stoppt den ganzen Container**. `xdotool search`
  ohne Treffer liefert Exit 1 und riss damit alles mit
- `window-check` steigt jetzt explizit aus der Strictness aus und endet immer
  mit 0. Eine Diagnose darf das Add-on nie beenden können
- **Derselbe Fehler steckte längst in `dump_on_start`:** der Scraper wurde
  ungeschützt aufgerufen. Hätte Amazon beim Start einen Login verlangt
  (Exit 2), wäre der Container ebenfalls gestoppt worden. Beides gegen einen
  nachgebauten Fehlschlag verifiziert
- `init-kasmvnc` und `init-browser` an denselben Stellen gehärtet: die
  Passwort-Pipeline konnte per SIGPIPE fehlschlagen, `dbus-uuidgen` und `sed`
  waren ungeschützt
- Doppelter `wasm`-MIME-Typ entfernt, den Debians `mime.types` schon kennt

## 0.5.2

Panel zeigte nur den Desktop-Hintergrund, keinen Browser.

- Chrome wartete bisher nur auf den **X-Server**, nicht auf den
  **Window-Manager**. Ein Fenster, das gemappt wird bevor ein WM da ist, wird
  von keinem platziert oder dimensioniert
- `--start-maximized` dazu, damit Openbox das Fenster aufzieht
- Neuer Dienst `window-check`: wartet auf das Browserfenster, protokolliert
  Bildschirmgröße und tatsächlich gemappte Fenster, und zieht das Fenster auf
  Bildschirmgröße. Findet er keins, steht das mitsamt Fensterliste im Log

## 0.5.1

- **401 im Panel behoben.** `-DisableBasicAuth` gilt laut KasmVNC-Doku nur für
  Websocket-Verbindungen — die HTTP-Auslieferung des Web-Clients verlangt
  immer Basic-Auth. Deshalb half kein Zugangsdaten-Basteln: die Seite selbst
  ging nie ohne Auth durch. nginx liefert den Client jetzt direkt vom
  Dateisystem aus, nur der Websocket geht noch an KasmVNC. So machen es die
  Referenz-Container auch

## 0.5.0

- **Es wurden zu wenige Bestellungen gefunden.** `?orderFilter=open` ist
  Amazons Reiter „Nicht versendet“, nicht „noch unterwegs“ — er blendet genau
  die Pakete aus, die schon auf dem Weg sind. Die Übersicht liest wieder die
  normale Bestellliste, die Vorauswahl über den Kartentext filtert
- Als Folge: eine zugestellte Sendung verschwindet nicht mehr aus der Liste,
  sondern wird als zugestellt gemeldet — die Aufräumlogik richtet sich danach
- **Empfänger und Lieferadresse** je Sendung als eigene Entities. Ein
  Amazon-Konto kann einen ganzen Haushalt an mehreren Adressen bedienen
- Bestehende Zustandsdatenbanken werden beim Öffnen um die neuen Spalten
  ergänzt
- Version kommt aus dem Build statt aus einer zweiten Stelle im Code — Log und
  Geräteinfo zeigten noch 0.2.0
- Fehlende Tracking-ID vor dem Versand ist normal und wird nicht mehr als
  unvollständige Extraktion gemeldet
- Weiterer Anlauf gegen den 401: `HOME` wird festgenagelt und die Passwortdatei
  an beiden Stellen abgelegt, an denen KasmVNC sie sucht. `log_level: debug`
  macht KasmVNC gesprächiger, falls es weiter klemmt

## 0.4.1

- **Panel antwortete nur mit 401.** Das ausgelieferte KasmVNC-Paket honoriert
  `-DisableBasicAuth` nicht. Statt auf das Flag zu bauen, bekommt KasmVNC jetzt
  ein bei jedem Start neu erzeugtes Zufallspasswort in der Datei, die es
  ohnehin von sich aus liest, und nginx schickt es bei jedem Request mit. Kein
  zusätzlicher Kommandozeilenparameter — ein ungültiger hätte den X-Server gar
  nicht mehr starten lassen
- Die Zugangsdaten liegen nur in tmpfs und in `/etc` im Container, nie auf dem
  Config-Volume und nie im Log. Abgesichert wird damit nichts: der Port bleibt
  auf Loopback, die eigentliche Tür ist HA-Ingress

## 0.4.0

Phasen 3 bis 5: aus gelesenen Seiten werden Entities.

- **Extraktion: CSS zuerst, LLM nur als Fallback.** Die Fortschrittsleiste
  trägt `data-`Attribute, der aktuelle Schritt wird also an seiner Position
  erkannt statt am Wort — damit ist die Kontosprache egal. Gegen die echten
  Seiten verifiziert: Status, Zustelldatum, Tracking-ID und Zusteller stimmen
- **Der LLM-Key ist optional.** Ohne Key läuft alles weiter, es wird nur
  geloggt, dass die Selektoren nicht mehr tragen
- **Zustandsmaschine** vollständig, inklusive der Stopp-Stufen. Ohne Stopp-Zahl
  endet die Leiter bei `WINDOW`; die Stufen schalten sich frei, sobald Amazon
  eine liefert. Zustand wird neu berechnet, nie fortgeschrieben —
  Rückwärtsübergänge funktionieren
- Nachtruhe, Jitter, Request-Cap mit Tagesreset, exponentielles Backoff bei
  Login-Wänden
- **MQTT Discovery** mit 12 Aggregat-Entities und einem Gerät je Sendung.
  Aggregate werden vom Add-on berechnet, nicht in HA nachgebaut
- Zustandsdatenbank in `/config/state.db`, überlebt Neustarts
- `developer_mode`: Trefferquote je Feld als Attribute, plus Dump jeder Seite,
  die die Selektoren nicht lesen konnten
- Eine Sendung, die aus den offenen Bestellungen verschwindet, gilt als
  zugestellt und wird aus HA entfernt
- Lücke geschlossen, die die Simulation zeigte: ohne Zustellfenster griff die
  Abbruchregel des Plans nie, und eine bei „1 Stopp" hängende Sendung hätte bis
  zur Nachtruhe im Minutentakt gepollt

## 0.3.0

Fernzugriff neu gebaut. Am Scraper ändert sich nichts.

- **Xvfb + x11vnc + websockify durch KasmVNC ersetzt.** Ein Prozess statt drei:
  `Xkasmvnc` ist X-Server und Web-Client zugleich. Regionsbasierte Kompression
  statt roher Framebuffer-Updates — spürbar flüssiger, besonders über Mobilfunk
- Drei s6-Dienste weniger, kürzere Startkette
- nginx entscheidet den Websocket-Upgrade jetzt pro Request statt an einer
  fest verdrahteten Stelle
- `XDG_RUNTIME_DIR` wird gesetzt, das nimmt Chrome und KasmVNC das Raten ab

Chrome bleibt Chrome: Playwrights `connect_over_cdp` ist laut Doku
Chromium-only, und ohne CDP-Anhängen an einen fremdgestarteten Browser fällt
die persistente Session — der ganze Sinn der Übung.

## 0.2.3

Aus den echten Seiten-Dumps: Amazon hat brauchbare semantische Container.

- **Textextraktion auf `.pt-card` / `.order-card` verengt.** Eine Tracker-Seite
  schrumpft damit von ~7.600 Zeichen Navigation, Empfehlungen und Footer auf
  ~130 Zeichen, die vollständig Nutzlast sind. Das war vorher 3,6 % Signal
- Nebeneffekt: die Lieferadresse steckt nicht mehr im Text-Dump
- Greift kein Amazon-Container, wird auf `#pageContainer`/`main`/`body`
  zurückgefallen **und eine Warnung geloggt** — die Vorstufe des
  Selektoren-Gesundheitssensors
- Der verwendete Container steht jetzt in der Dump-Ausgabe

## 0.2.2

- Einstieg auf `?orderFilter=open` statt der kompletten Historie — zugestellte
  Bestellungen tauchen gar nicht erst auf. `--full-history` schaltet zurück
- **Titel-Bug endgültig behoben:** Amazon verlinkt jedes Produkt zweimal, Bild
  zuerst. `querySelector` nahm den Bild-Link, dessen Text leer ist. Jetzt wird
  der erste Produktlink mit echtem Text genommen
- `--html` legt das DOM je Seite mit ab, Grundlage für die CSS-Selektoren in
  Phase 3. `dump_on_start` nutzt das automatisch
- Hinweis im Log, dass die Dumps die Lieferadresse enthalten
- `--all` heißt jetzt `--include-delivered`

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
