# Einrichtungsassistent (erste vollständige Umsetzung)

Grundlage: `ASSISTENT_SPECS_FINAL.md`. Dieses Dokument beschreibt Plan,
Architektur, Migration, bekannte Grenzen und eine manuelle Testanleitung.

## Plan und Architektur

| Baustein | Datei | Aufgabe |
| --- | --- | --- |
| Entwurfsmodell, Vorlagen, Übernehmen | `app/services/assistent_entwurf_service.py` | Entwurf als JSON, Matrix → Wettbewerbe, Zusammenlegen/Trennen, Vorlagen (System-Vorlage Schulpokal, eigene, aus Veranstaltung), Abgleich, Lösungsvorschläge anwenden, atomares Übernehmen |
| Planungskern | `app/services/assistent_planung_service.py` | rechnet aus einem Entwurf den kompletten Vorschlag: Runden je Turniermodus, Taktung je Zeitblock (abwechselnd/gleichzeitig), Programmpunkte und Puffer überspringen, Showspiele, Machbarkeitsprüfung, Tagesplan für die Vorschau |
| Gleichstandsregeln | `app/services/tiebreak_service.py` | einstellbare Reihenfolge (direkter Vergleich, Tordifferenz, Tore), von `sort_table_rows` genutzt |
| Routen | `app/routes/assistent.py` | Einstieg, Schritte, Vorschau-Aktionen, Übernehmen, Vorlagen verwalten |
| Oberfläche | `app/templates/assistent_entwurf.html`, `partials/assistent_vorschau.html`, `assistent_fertig.html`, `assistent_vorlagen.html` | Schritte, Matrix mit Live-Liste, Vorschau mit Zeitleiste und Karussell, Bearbeiten-Dialog |

Wichtige Entscheidungen:

- **Nichts wird vor „Veranstaltung erstellen“ angelegt.** Der Entwurf liegt als
  JSON in `assistent_entwuerfe`. Der Planungskern ist rein rechnend und liest die
  Datenbank nur für Stammdaten und die Belegung anderer Veranstaltungen am
  selben Tag.
- **Ein Planungskern für beide Modi.** Schnellplanung und Expertenmodus sind
  nur unterschiedliche Ansichten desselben Entwurfs; der Wechsel verliert nichts.
- **Bestehende Bausteine wiederverwendet:** Paarungen und Runden aus dem
  Spielplan-Generator (`_build_pairings`, `_schedule_pairings_into_rounds`,
  Hin-/Rückrunde, `_split_courts`), K.-o.-Runden aus `build_ko_rounds`,
  Showspiele über `create_jahrgangsfinale` (Jahrgang `mixed`, zählt nicht in
  Wertungen), Programmpunkte in `event_programmpunkte`, Konfliktprüfung
  `find_internal_conflicts`/`find_existing_schedule_conflicts` als letzte
  Absicherung beim Übernehmen.
- **Machbarkeits-Abzeichen in jedem Schritt** (seit 2026-11-12): oben rechts
  „✔ Machbar“, „⚠ Machbar · n Warnungen offen“ oder „✖ Noch nicht machbar“,
  ein Klick führt zur Vorschau. Der Plan wird dafür bei jedem Aufruf neu
  berechnet. Die frühere Seite „Wertung“ steht im Abschluss; dort geänderte
  Punkte und Gleichstandsregeln gelten beim Erstellen mit (nur auf der
  angezeigten Entwurfsversion), alte Links auf `/wertung` leiten um.
- **Machbarkeitsprüfung in drei Stufen:** blockierend (z. B. Feld doppelt
  belegt, Plan passt nicht ins Zeitfenster), Warnung zum Bestätigen (z. B.
  Klasse gleichzeitig in zwei Sportarten), Info. Vorschläge (Spielzeit kürzen,
  Wechselzeit kürzen, Block verlängern/verschieben, anderer Turniermodus)
  werden nur auf Klick angewendet, ein Turniermodus aus einer Vorlage bleibt
  bis dahin erhalten. Eine bestätigte Warnung gilt nur für genau diese Zeiten;
  ändert sich der Plan, muss sie neu bestätigt werden.
- **Übernehmen atomar und idempotent:** eine Transaktion (`BEGIN IMMEDIATE`),
  Status `uebernommen` verhindert Duplikate bei Doppelklick oder Reload, die
  Version des Entwurfs schützt vor Übernahmen eines inzwischen geänderten Plans.
- **Vorlagen bleiben unabhängig:** Vorlagen speichern Parameter und Regeln
  (Jahrgänge, Sportarten, Feldnamen statt IDs), aber weder Name noch Datum
  noch Klassen. Ein Start aus einer Vorlage erzwingt den Abgleich von Datum,
  Klassen, Sportarten und Orten; fehlende Jahrgänge, inaktive Orte und nicht
  mehr vorhandene Felder werden dort gemeldet statt stillschweigend ersetzt.
- **Alte Wege entfernt:** Die bisherigen Formulare der Assistent-Startseite
  (Veranstaltung wählen/anlegen, Vorlage Schulpokal, Turnier-Schnellstart)
  waren seit 2026-11-06 nicht mehr verlinkt (Samuel: „verwirrt nur“). Seit
  2026-11-12, nachdem der Assistent bestehende Veranstaltungen bearbeiten kann,
  sind sie samt `schulpokal_gesamtplan_service.py` entfernt. Alte Lesezeichen
  leiten um: `/assistent/{event_id}` und `/assistent/{event_id}/schulpokal-plan`
  auf die Veranstaltungsseite (Knopf „Plan ändern“), `/turnier-schnellstart`
  auf `/assistent`. Die Blöcke der System-Vorlage „Schulpokal“ stehen weiter
  in `schulpokal_vorlage_service.py` (`DEFAULT_SCHULPOKAL_BLOECKE`).

## Bestehende Veranstaltung bearbeiten und „Vorjahr übernehmen“

- **Bearbeiten** (`POST /assistent/entwurf/bearbeiten/{event_id}`, Knopf „Im Assistenten
  planen“ auf der Veranstaltungsseite, Kachel „Bestehende Veranstaltung“ auf `/assistent`):
  `entwurf_aus_veranstaltung()` liest die Veranstaltung in einen Entwurf zurück und merkt
  sich in `daten["bestand"]` die Zuordnung Eintrag → `competition_id` und Showspiel →
  Finale. Name, Datum, Art und Klassenauswahl bleiben, ein Abgleich erscheint nur, wenn
  Felder, Orte oder Jahrgänge fehlen. Ein offener Bearbeiten-Entwurf derselben
  Veranstaltung wird wiederverwendet.
- **Planungskern** (`_lade_bestand`): Ein Zeitblock ist gesperrt, sobald ein Wettbewerb
  darin begonnene/beendete Spiele, eingetragene Tore oder Sechskampf-Ergebnisse hat. Seine
  Spiele kommen unverändert aus der Datenbank in die Vorschau (`bestehend`), Showspiele
  dieser Wettbewerbe bleiben. Nicht mehr ausgewählte Wettbewerbe ohne Ergebnisse werden
  gelöscht (Info), mit Ergebnissen blockiert. Die eigenen alten Spiele zählen nicht als
  fremde Belegung, der Namensabgleich ignoriert die eigene Veranstaltung.
- **Übernehmen**: aktualisiert `events`, überschreibt Einstellungen nicht gesperrter
  Wettbewerbe (Spielzeit in Sekunden wird zurückgesetzt) und legt Spiele, Klassen und
  Disziplinen neu an; entfernte Wettbewerbe und nicht gesperrte Showspiele werden gelöscht
  und neu erzeugt, Programmpunkte ersetzt. Alles in einer Transaktion, danach die übliche
  Konfliktprüfung gegen die echten Datensätze.
- **Vorjahr übernehmen** (`POST /assistent/entwurf/bestehend`, `aktion=vorjahr`):
  `entwurf_aus_vorjahr()` = Vorlage aus der Veranstaltung + Start aus Vorlage, ohne die
  Vorlage zu speichern.

## Migration

Wie gewohnt in `init_db()`, idempotent und ohne Migrationsdateien:

- neue Tabelle `assistent_entwuerfe` (`id`, `titel`, `daten` JSON, `status`
  `offen`/`uebernommen`, `version`, `event_id`, Zeitstempel)
- neue Tabelle `assistent_vorlagen` (`id`, `name` eindeutig, `daten` JSON,
  Zeitstempel)
- neue Spalte `competitions.tiebreak_order TEXT` (im `CREATE TABLE` und als
  geschütztes `ALTER TABLE`). `NULL` = bisherige Gleichstandsregel, unverändertes
  Verhalten für alle bestehenden Wettbewerbe.

## Bekannte Grenzen

- Orte sind weiter die festen Standorte der App (`COMPETITION_LOCATIONS`); neue
  Ortsarten legt der Assistent nicht an, wohl aber neue Felder an einem Ort.
- Gewichtung einzelner Sportarten in der Gesamtwertung gibt es nicht, weil das
  bestehende Wertungssystem sie nicht kennt; alle Wettbewerbe zählen gleich.
- Zusammengelegte Wettbewerbe (z. B. Volleyball 9+10) stehen im echten
  Tagesplan in der Spalte des ersten Jahrgangs; die Teams beider Jahrgänge
  sind über `competition_teams` zugeordnet.
- Halbfinale 2 und Spiel um Platz 3 brauchen wie im bestehenden Generator
  mindestens zwei Felder.
- Die K.-o.-Platzhalter bei „Gruppenphase + K.-o.“ sind dem bestehenden
  `generate_group_plan` nachgebaut.
- Feinjustierung über den Bearbeiten-Dialog mit Minutenwerten, bewusst ohne
  Drag & Drop.

## Manuelle Testanleitung (DEV)

Vorbereitung: auf DEV als Admin anmelden (bei aktiver Sicherheit), Klassen für
die Jahrgänge 7–10 und Felder in Turnhalle und Mensa sollten vorhanden sein.

### Beispiel 1: Schulpokal aus der Vorlage

1. `/assistent` öffnen → „Aus Vorlage starten“ → „Schulpokal“.
2. Abgleich: Name „Schulpokal Test“, Datum setzen, Jahrgänge 7–10, alle drei
   Sportarten und beide Orte bestätigen. Fehlen Klassen oder Felder, steht das
   hier.
3. Schritte Grunddaten, Sportarten (Matrix: Zweifelderball 7/8, Volleyball 9/10,
   Tischtennis 9/10 mit Jungen/Mädchen), Orte und Zeitplan durchklicken.
   Zeitplan zeigt die Eröffnung 08:00–08:10, die Siegerehrungen und die beiden
   Showspiele.
4. Vorschau: oben der Tagesplan, darunter genau ein Wettbewerbsplan, mit ◀ ▶
   bzw. den Pfeiltasten blättern („x von y“). Erwartet: zwei Warnungen
   „Klassen gleichzeitig in Volleyball und Tischtennis“ → bewusst bestätigen.
5. „Bearbeiten“ an einem Wettbewerb: Spielzeit z. B. auf 12 Minuten setzen →
   Plan wird neu berechnet, ggf. erscheint „passt nicht ins Zeitfenster“ mit
   Vorschlägen. Einen Vorschlag anklicken, Auswirkungen erscheinen als Meldung.
6. Abschluss (mit der Wertung) → „Veranstaltung erstellen“, optional „Als
   Vorlage speichern“. Danach Tagesplan, Spielplan, Tabellen und Ergebnisse der neuen
   Veranstaltung prüfen. Der Zurück-Knopf und ein zweites Absenden legen nichts
   doppelt an.

### Beispiel 2: Oberstufe frei planen (ohne Vorlage)

1. `/assistent` → „Neue Veranstaltung“, Expertenmodus wählen.
2. Grunddaten: „Oberstufen-Sportfest“, Datum, 09:00–12:00, Jahrgänge 10 und
   (falls vorhanden) 11/12.
3. Sportarten: „Volleyball“ und „Basketball“ hinzufügen, in der Matrix
   ankreuzen. Die Live-Liste zeigt die entstehenden Wettbewerbe. Volleyball 10
   und 11 auswählen → „Zusammenlegen“ (ein gemeinsamer Sieger), danach einmal
   „Trennen“ und wieder zusammenlegen.
4. Orte: Turnhalle aktiv, Basketball nur auf einem Feld erlauben.
5. Zeitplan: die vorgeschlagenen Blöcke prüfen, einen Puffer (Programmpunkt
   „Pause“ 10:30–10:45) eintragen. In der Vorschau darf in dieser Zeit kein
   Spiel liegen.
6. Abschluss, Abschnitt Wertung: bei Volleyball unter „Anpassen“ die
   Reihenfolge der Gleichstandsregeln ändern
   (z. B. Tordifferenz vor direktem Vergleich). Nach dem Erstellen und dem
   Eintragen von Ergebnissen mit Punktgleichstand nutzt die Tabelle diese
   Reihenfolge.
7. Entwurf zwischendurch verlassen und über „Entwurf fortsetzen“ wieder
   öffnen: man landet im zuletzt bearbeiteten Schritt.
8. Optional: auf der Seite der fertigen Veranstaltung „Als Vorlage speichern“.
