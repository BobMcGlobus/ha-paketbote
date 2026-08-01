# Changelog

## 0.18.0

- **Das Postfach ist eine zweite Quelle.** Versandbestätigungen aus beliebigen
  Shops werden gelesen und als Sendung übernommen — nicht mehr nur Amazon.
  Zugriff erfolgt ausschließlich lesend
- **Begriffsbibliothek in sieben Sprachen** plus die Adressbestandteile der
  Zusteller-Trackinglinks; ein Zusteller-Link allein genügt zur Erkennung
- **Zusteller wird nicht geraten, sondern erfragt:** die bis zu drei besten
  Kandidaten werden der Reihe nach angefragt, wer die Nummer nicht kennt fällt
  raus. Nötig, weil DPD, Hermes und GLS alle vierzehnstellig nummerieren
- Bestell-, Rechnungs- und Kundennummern werden als Sendungsnummer ausgeschlossen
- **LLM nur als Notnagel**, und sein Ergebnis wird gegengeprüft: die Nummer muss
  wörtlich in der Mail stehen, der Zusteller bekannt sein. Anweisungen im
  Mailtext können damit nichts bewirken
- Testknopf für das Postfach in den Einstellungen

## 0.17.0

- **DPD wird automatisch abgefragt**, ohne Schlüssel. Die Datenschutzseite vor
  der Sendungsverfolgung ist eine Weiterleitung, keine Sperre — in 0.16.0 war
  sie fälschlich als unüberwindbar eingestuft
- Die Stufe wird aus der Symbolnummer gelesen (`status_N.svg`) und ist damit
  sprachunabhängig; die Stufendaten dienen als zweites Signal
- Ein Stufendatum gilt erst ab „Paket in Zustellung" als Zustelldatum —
  vorher benennt es das Ereignis, nicht die Ankunft
- Eigenes Intervall und Prüfknopf für DPD

## 0.16.0

- **DHL funktioniert jetzt ohne Schlüssel.** Der Status kommt von `dhl.de`,
  derselben Adresse, die deren eigene Seite anfragt. Die Stufe steht dort als
  Position, nicht als Wort, ist also sprachunabhängig
- **Ein abgelehnter DHL-Schlüssel lässt die Sendung nicht mehr unbekannt**:
  die Abfrage fällt auf `dhl.de` zurück, statt aufzugeben
- **Hermes wird automatisch abgefragt**, ebenfalls ohne Schlüssel, inklusive
  Zustellfenster
- DPD, GLS, UPS und FedEx sperren automatisierte Zugriffe (Captcha,
  Cloudflare, Akamai) und bleiben beim manuellen Eintragen
- Abrufe über die Zustellerseite werden getrennt gezählt, damit sie DHLs
  Tageskontingent nicht verbrauchen
- Neue Einstellung *Von der Zustellerseite lesen*, und in den Einstellungen
  steht je Zusteller, welcher Weg gerade greift

## 0.15.0

- **UPS und FedEx werden automatisch abgefragt**, sobald ihre Zugangsdaten
  hinterlegt sind. Beide arbeiten mit OAuth: ID und Secret werden gegen ein
  Token getauscht, das zwischengespeichert wird
- **Eigenes Intervall je Zusteller** statt eines gemeinsamen Werts
- **Prüfknopf je Zusteller** in den Einstellungen; die Antwort nennt immer den
  HTTP-Status
- Die Abfrage wählt das Zustellermodul jetzt anhand des Zustellers aus, statt
  DHL fest zu verdrahten

## 0.14.1

- **DHL akzeptiert den Schlüssel auch als Query-Parameter**, nicht nur als
  Header. Wird der Header abgelehnt, wird der zweite Weg versucht und der
  erfolgreiche gemerkt — ein funktionierender Aufbau kostet weiterhin genau
  eine Anfrage

## 0.14.0

- **Alle Artikel einer Sendung untereinander**, mit Produktbild aus Amazons
  CDN. Bilder werden nicht gespeichert, sondern direkt geladen, und ohne
  Referrer angefragt
- **Zusteller-Plakette** oben rechts auf jeder Karte, in den Hausfarben von
  DHL, Amazon, DPD, Hermes, GLS, UPS, FedEx und Deutscher Post. Selbst
  gezeichnete Wortmarken statt fremder Logodateien
- **Zurückholen** für Sendungen im Archiv: setzt den Zustellzeitpunkt zurück,
  danach ermittelt der nächste Abruf den Zustand neu
- Der DHL-Schlüsseltest nennt jetzt **DHLs eigene Fehlermeldung**, nicht nur
  den Status — bei einem 401 steht dort, was DHL bemängelt

## 0.13.1

- **Der DHL-Testknopf meldete gültige Schlüssel als abgelehnt.** Er fragte mit
  einer syntaktisch ungültigen Sendungsnummer, worauf DHL mit einem Formfehler
  antwortet — und alles außer „nicht gefunden" galt als Ablehnung. Jetzt zählt
  nur noch eine echte Zurückweisung (401/403) als Fehler; eine Beschwerde über
  die Nummer beweist im Gegenteil, dass der Schlüssel akzeptiert wurde
- Die Meldung nennt immer den HTTP-Status, damit der nächste Fall nicht wieder
  geraten werden muss
- Leerzeichen und Zeilenumbrüche um einen eingefügten Schlüssel werden entfernt

## 0.13.0

- **Deutlich ruhigerer Standardrhythmus:** untätig alle 3 h statt 1 h, heute
  erwartet 60 min, Zustellfenster 20 min, kommt näher 10 min, gleich da 3 min.
  Ein normaler Liefertag kostet damit rund 28 statt 98 Abrufe
- **Budget-Reserve:** es wird nur so schnell abgerufen, wie genug übrig bleibt,
  um bis zur Nachtruhe im 3-Stunden-Takt weiterzumachen. Damit hört das Add-on
  nicht mitten am Nachmittag auf, während Pakete unterwegs sind
- **Bei erreichtem Tageslimit wird alle 10 Minuten nachgesehen**, ob das Limit
  angehoben wurde — vorher wartete es das volle Ruheintervall ab, weshalb eine
  Anhebung scheinbar wirkungslos blieb
- **Testknopf für den DHL-Schlüssel** in den Einstellungen: fragt DHL nach
  einer unmöglichen Sendungsnummer, ein „nicht gefunden" beweist, dass der
  Schlüssel akzeptiert wird
- **Auf Standard zurücksetzen** in den Einstellungen; Schlüssel und
  Empfängerfilter bleiben erhalten
- Der Status zeigt jetzt, welche Werte beim letzten Abruf **tatsächlich
  wirksam** waren — damit ist nachprüfbar, ob Änderungen angekommen sind

## 0.12.0

- **Die Stopp-Zahl wird gelesen — die Eskalationsstufen funktionieren.** Sie
  steht nicht im sichtbaren Text, sondern im eingebetteten JSON der Seite
  (`calloutMessage`); die Kartenblase entsteht daraus erst per JavaScript.
  Deshalb war sie in allen bisherigen Text-Dumps unsichtbar, obwohl sie in
  denselben Seiten längst enthalten war. Funktioniert in beiden Sprachen
- **Zustellfenster wurden nie erkannt.** Amazon schreibt sie als
  „5:30 pm - 8:30 pm" — das am/pm steht zwischen Minuten und Bindestrich, was
  keines der bisherigen Muster überstand. Alle Schreibweisen laufen jetzt über
  ein gemeinsames Muster, und ein Zeitraum, der vor seinem Beginn endet, wird
  verworfen statt als Fenster durchgereicht
- Das Fenster wird aus der Ankunftszeile gelesen, nicht mehr aus der ganzen
  Karte — dort standen sonst beliebige Zahlenpaare mit Bindestrich

## 0.11.1

- **Frisch aufgegebene Bestellungen landeten im Archiv.** Eine Sendung, die in
  *einem* Abruf nicht auf der Bestellliste stand, galt sofort als zugestellt —
  eine neue Bestellung erscheint dort aber verzögert, und eine Seite, die nicht
  fertig rendert, sieht genauso aus. Jetzt muss sie dreimal in Folge fehlen,
  und eine Liste, die unvollständig gelesen wurde, entfernt gar nichts
- **Ein leeres Passwortfeld löschte den gespeicherten Schlüssel.** Das
  Einstellungsformular schickt alle Felder mit; einmal Speichern hat damit den
  DHL-Schlüssel aus den Add-on-Optionen überschrieben. Leer heißt jetzt
  „unverändert", und gespeicherte Schlüssel werden nicht mehr an den Browser
  zurückgegeben
- **Einstellungen wirkten erst nach einem Neustart**, entgegen der Ansage. Der
  Scheduler liest die Konfiguration jetzt bei jedem Zyklus neu und baut die
  DHL-Anbindung um, wenn sich der Schlüssel ändert
- Zusteller und Abrufrhythmus sind in den Einstellungen getrennt benannt, neu
  ist **Frühestens erneut fragen** je Zusteller (Standard 30 min) — das schont
  das DHL-Tageslimit
- Wird ein Zusteller nicht gefragt, steht der Grund im Log

## 0.11.0

- **Alle Einstellungen in der Oberfläche**, gruppiert unter *Mehr*. Die
  Add-on-Optionen setzen nur noch die Startwerte; danach gilt, was in der
  Oberfläche steht. Bildschirmgröße und Protokollstufe sind als „wirkt nach
  Neustart" gekennzeichnet
- **Empfänger werden zusammengeführt.** „Jonas Althoff", „jonas althoff",
  „Herr Jonas Althoff" und „Althoff, Jonas" gelten als eine Person: Anreden,
  Titel und Zweitnamen fallen vor dem Vergleich weg
- **Adressen ebenso**, und je Empfänger werden die bekannten Postleitzahlen
  gemerkt. DHL wird damit bis zu dreimal mit unterschiedlichen Postleitzahlen
  gefragt, bis das Zustellfenster kommt — die erfolgreiche rückt nach vorn
- **Empfänger dauerhaft ausblenden:** ein Tipp auf den Namen filtert dessen
  Pakete aus der Hauptansicht
- Beim manuellen Anlegen werden bekannte Empfänger vorgeschlagen
- **Ankunft ist jetzt die Schlagzeile** jeder Karte — Datum, Fenster oder
  verbleibende Stopps stehen groß, der Artikelname darunter
- Helles Design in Karton-Orange auf Briefpapier-Weiß

## 0.10.0

- **Fehlalarm behoben:** „Selektoren greifen nicht" schlug schon an, wenn eine
  einzige Seite nicht sauber gelesen wurde — meist nur eine, die noch nicht
  fertig gerendert war. Jetzt zählt nur ein Zyklus, in dem **keine** Seite
  gelesen werden konnte, und das muss zweimal hintereinander passieren
- **Zugestellte Sendungen bleiben drei Tage sichtbar** und wandern dann ins
  Archiv. Nach 90 Tagen werden sie verworfen. Bisher verschwanden sie sofort
- **Manuelle Sendungen bearbeiten:** Bezeichnung und Empfänger nachträglich
  änderbar
- **Oberfläche neu strukturiert:** vier Bereiche (Sendungen, Archiv,
  Hinzufügen, Mehr) mit App-Navigation am unteren Rand auf dem Handy und oben
  auf dem Desktop. Einstellungen und Diagnose haben eine eigene Seite
- Statusring je Sendung nach Vorbild der DHL-App: Füllstand zeigt die Stufe,
  Farbe die Dringlichkeit, Symbol den Status. Dazu ein Key Visual im Kopf

## 0.9.0

- **Sendungen manuell hinzufügen.** Sendungsnummer und Zusteller genügen; bei
  DHL wird der Status danach automatisch geholt, bei DPD, Hermes, GLS, UPS,
  FedEx und Deutscher Post entsteht ein Link zur Sendungsverfolgung. Manuelle
  Einträge überstehen Abrufzyklen, in denen keine Quelle sie sieht, und lassen
  sich in der Oberfläche wieder entfernen
- **Mehrsprachigkeit vorbereitet.** Oberfläche auf Deutsch und Englisch, neue
  Option `language` (`auto`, `de`, `en`); `auto` folgt dem Browser. Die
  Add-on-Optionen sind in beiden Sprachen beschriftet
- **Behoben:** „Browser öffnen" landete auf einem Verzeichnislisting — beim
  Umbau auf die Oberfläche war der Redirect auf die noVNC-Seite verlorengegangen
- Zustandsdatenbank läuft im WAL-Modus, damit Oberfläche und Abrufschleife
  gleichzeitig zugreifen können

## 0.8.0

- **Eigene Oberfläche im Add-on-Panel.** Sendungsübersicht mit Empfänger,
  Zustelldatum, Fenster, Stopps und Zusteller; Warnungen bei nötiger Anmeldung,
  erreichtem Tageslimit oder nicht mehr greifenden Selektoren. Responsive,
  hell/dunkel je nach System
- **Jetzt abrufen** stößt einen Zyklus sofort an
- **Browser öffnen** führt zum bisherigen Panel, jetzt unter `/browser/`
- Die Oberfläche läuft als eigener Prozess und liest nur mit — ein Fehler dort
  hält die Abrufschleife nicht an
- **Neuer Sensor `sensor.paketbote_sendungen`**: Anzahl aktiver Sendungen als
  Zustand, die vollständige Liste im Attribut `shipments`. Grundlage für die
  Lovelace-Karte

## 0.7.0

- **DHL-Anbindung über die offizielle API.** Meldet Amazon DHL als Zusteller
  und ist `dhl_api_key` gesetzt, kommt der Status direkt von DHL: genauer als
  Amazons Trackingseite und ohne Amazon-Abruf. Zustellfenster inklusive, sofern
  die Empfänger-PLZ aus der Lieferadresse ermittelt werden konnte
- Rate-Limits der kostenlosen Stufe werden eingehalten (250/Tag, 1 alle 5 s)
  und getrennt vom Amazon-Budget gezählt
- Neue Struktur `app/carriers/`: Quellen (was ist bestellt) und Zusteller (wo
  ist es) sind getrennt. Grundlage für weitere Zusteller und Quellen
- Sendungsnummer wird gespeichert; bestehende Datenbanken werden migriert

## 0.6.3

- **Google Chrome war seit 0.6.0 gar nicht mehr im Image.** Beim KasmVNC-Rückbau
  habe ich den Dockerfile-Block über einen Textbereich entfernt — und der
  Chrome-Installationsblock lag genau dazwischen. Geprüft hatte ich nur, dass
  KasmVNC weg ist, nicht was übrig blieb. Das erklärt die schwarzen Desktops
  seit v0.6.0
- **Der Build prüft das jetzt selbst.** Fehlt eines der nötigen Programme
  (Chrome, Xvfb, x11vnc, websockify, nginx, …) oder eine Python-Abhängigkeit,
  schlägt der Build fehl, statt ein Image auszuliefern, das erst auf der
  Instanz auffällt
- **Neustart-Backoff wächst.** Ein Dienst, der überhaupt nicht startet, hat
  sich im Sekundentakt neu gestartet und den Log geflutet. Jetzt 2 s, ab dem
  fünften Fehlschlag 10 s, ab dem fünfzehnten 30 s — mit einer klaren
  Fehlermeldung statt zwanzig gleichen Warnungen

## 0.6.2

- **Gespeicherte Fensterposition wird beim Start verworfen.** Chrome merkt sich
  seine Fenstergeometrie im Profil und stellt sie wieder her — das sticht
  `--window-position` und `--window-size`. Eine Position, die bei anderer
  Bildschirmgröße gespeichert wurde, bringt den Browser damit für immer
  außerhalb des Sichtbaren hoch. Das erklärt, warum der Rückbau auf die alten
  Flags nichts geändert hat: der Unterschied lag nicht in der Konfiguration,
  sondern im Profil auf dem Config-Volume
- `Default/Sessions` wird ebenfalls geräumt, dort stehen Fenstergrenzen auch
- Die Preferences werden jetzt als JSON bearbeitet statt per `sed`
- `window-check` unterscheidet „kein Fenster" von „Fenster existiert, ist aber
  nicht sichtbar" und protokolliert im zweiten Fall Map-State und Größe

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
