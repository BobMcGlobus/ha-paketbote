# Paketbote — Home Assistant Add-on Repository

Amazon-Lieferverfolgung für Home Assistant über einen dauerhaft eingeloggten,
bedienbaren Browser.

## Installation

**Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories**, dann diese URL
hinzufügen:

```
https://github.com/BobMcGlobus/hassio-addon-paketbote
```

## Add-ons

| Add-on | Beschreibung |
|---|---|
| [Paketbote](paketbote) | Amazon-Lieferverfolgung via persistentem Browser |

## Warum ein Add-on

Amazon bietet keine öffentliche API für Bestell- oder Lieferdaten. Die einzige
Quelle für Live-Zustelldaten — „noch X Stopps entfernt", aktives Zustellfenster
— ist der eingeloggte Progress-Tracker auf `amazon.de`.

Das Add-on betreibt dafür einen echten, headful laufenden Chrome und liest die
Tracking-Seiten aus. Verlangt Amazon Login, MFA oder ein Captcha, lässt sich das
direkt im Panel erledigen — über HA-Ingress authentifiziert, auch aus der
Companion App heraus. Kein VNC-Port, kein Reverse-Proxy-Eintrag, kein SSH.

Ergebnis geht per MQTT Discovery nach Home Assistant.

## Stand

| Phase | Inhalt | Status |
|---|---|---|
| 0 | Vorbedingungen (HA OS in VM, amd64) | ✅ |
| 1 | Add-on-Skelett + bedienbarer Browser | ✅ gebaut, Abnahme offen |
| 2 | Scraper-Kern (Playwright über CDP) | offen |
| 3 | LLM-Extraktion | offen |
| 4 | Scheduler / Zustandsmaschine | offen |
| 5 | MQTT Discovery | offen |
| 6 | Härtung (WAF-Erkennung, Backoff, Cap) | offen |
| 7 | DHL über die offizielle API | offen |
