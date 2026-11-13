# Öffentlicher Zugriff für viele Zuschauer

Wenn die ganze Schule den Tagesplan, den Spielplan oder die Tabellen aufruft,
soll nicht jede Anfrage bis zur App auf der Synology durchlaufen. Die App
erlaubt deshalb einem vorgeschalteten Cache (Cloudflare), öffentliche
Leseseiten kurz zwischenzuspeichern.

## Was die App macht

Für Besucher **ohne Anmeldung** tragen diese Seiten die Kopfzeile
`Cache-Control: public, max-age=0, s-maxage=15`:

- `/` (Startseite mit Tagesplan)
- `/spielplan`, `/spielplan/aushang`
- `/tabellen`
- `/beamer`
- `/siegerehrung/…` (nur wenn freigegeben)
- `/durchsage/aktuell` (Abfrage alle 15 Sekunden von jeder offenen Seite)

Angemeldete Benutzer (Cookie `sportfest_session`) bekommen `private`. Ihre
Seiten werden nie geteilt, sie sehen immer den aktuellen Stand.

Die Dauer lässt sich mit der Umgebungsvariable
`SPORTFEST_PUBLIC_CACHE_SECONDS` ändern (Standard 15, höchstens 300,
`0` schaltet die Kopfzeilen ab).

**Die Kopfzeile allein ändert nichts.** Cloudflare speichert HTML-Seiten erst
mit der folgenden Regel.

## Regel bei Cloudflare einrichten

Im Cloudflare-Dashboard die Domain wählen, dann **Caching → Cache Rules →
Create rule**:

- Name: `Sportfest öffentliche Leseseiten`
- „Custom filter expression“, über **Edit expression** einfügen:

  ```
  (http.host eq "sportfest.moede-digital.org"
   and http.request.method eq "GET"
   and not http.cookie contains "sportfest_session"
   and (http.request.uri.path in {"/" "/spielplan" "/spielplan/aushang" "/tabellen" "/beamer" "/durchsage/aktuell"}
        or starts_with(http.request.uri.path, "/siegerehrung/")))
  ```

- Cache eligibility: **Eligible for cache**
- Edge TTL: **Use cache-control header if present, bypass cache if not**
- Browser TTL: **Respect origin TTL**

Wichtig ist die Zeile mit `sportfest_session`: Wer angemeldet ist, geht immer
am Cache vorbei.

## Prüfen

In einem privaten Browserfenster (ohne Anmeldung) `/spielplan` zweimal kurz
nacheinander laden und in den Entwicklerwerkzeugen (Netzwerk → Antwort-
Kopfzeilen) nachsehen: `cf-cache-status` zeigt beim zweiten Mal `HIT`.
Angemeldet steht dort `BYPASS` oder `DYNAMIC`.

## Was sich dadurch ändert

- Zuschauer sehen Ergebnisse, Tabellen und Durchsagen bis zu 15 Sekunden
  später.
- Bei der Synology kommen je Seite höchstens rund vier Anfragen pro Minute
  an, egal wie viele Schüler zuschauen.
- Schiedsrichter, Turnierleitung und Admins merken nichts, weil sie
  angemeldet sind.

## Vor der Freigabe an die Schule

- Die Anmeldung muss in Produktion eingeschaltet sein (Einstellungen →
  Sicherheit). Ohne sie kann jeder mit der Adresse Ergebnisse ändern.
- Die Regel zuerst ohne Publikum prüfen (siehe oben).
