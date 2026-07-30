# Changelog

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
