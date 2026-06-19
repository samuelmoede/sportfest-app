# Changelog

Alle bemerkenswerten �nderungen dieses Projekts werden hier dokumentiert.

## [Unreleased]

- Initiales Changelog erstellt.
- README.md erg�nzt.
- PROJECT_CONTEXT.md erg�nzt.
- Relative Pfade f�r Templates, statische Assets und VERSION eingef�hrt.
- Datenbankinitialisierung und App-Import geprüft.
- Wettbewerbsauswahlfilter auf /tabellen ergänzt.
- Wettbewerbe können als Kopie mit dem Zusatz "(Kopie)" dupliziert werden.
- Wettbewerbe auf /wettbewerbe werden nun als breite Karten untereinander angezeigt; Formulare und Sechskampf-Disziplinzeilen passen sich besser an.
- Teams / Klassenverwaltung auf /teams wurde auf eine kompakte Tabellenansicht umgestellt; Name, Jahrgang und Aktivität lassen sich direkt bearbeiten.
- Fix: /teams lädt jetzt auch auf Datenbanken ohne `discipline_results.team_id` und verhindert falsche Löschrechte.
- Erste Veranstaltungs-Übergreifende Gesamtwertung auf der Event-Detailseite hinzugefügt.
- Gesamtwertung auf der Event-Detailseite als Matrix dargestellt: eigene Spalten je Wettbewerb und Gesamtpunkte.
- Sechskampf-Ergebniseingabe auf `/ergebnisse` für die Klassenansicht verbessert: breitere Karten, horizontale Werteingabe und responsive Darstellung.
- Responsive Layout für Smartphones und Tablets verbessert: Formulare, Tabellen und Seitenleiste angepasst.
- Referee-Timer für Turnierspiele in Spielplan und Ergebnisübersicht ergänzt; Spielstart und Spielende lassen sich jetzt direkt in der App steuern.
- Tagesplanfunktion für Veranstaltungsdetail und Dashboard hinzugefügt; gemeinsame Logik für aktuelle/nächste Zeitblöcke verwendet.
- Gesamtwertung auf der Veranstaltungsdetailseite nach Jahrgängen gruppiert; Platzierung wird pro Jahrgang separat berechnet und Gleichstände überspringen Plätze.
- Sechskampf-Wertung in der Gesamtwertung korrigiert: Klassen ohne echten Ergebniseintrag erhalten fuer diesen Wettbewerb keine Ranglistenwertung (Anzeige "-") und werden nicht als 0-Gleichstand gewertet.
- Darstellung der Gesamtwertung auf der Veranstaltungsdetailseite verbessert: kuerzere Wettbewerbstitel ohne Jahrgang, fehlende Punkte als dezentes "-", Gesamtpunkte klarer hervorgehoben und Tabellen mobil besser horizontal scrollbar.
- Dashboard auf kommende Veranstaltungen ausgerichtet: naechste Veranstaltung mit Datum, Status, Detail-Link und Wettbewerbsanzahl hervorgehoben; bis zu drei weitere kommende Veranstaltungen werden zusaetzlich gelistet.
- Veranstaltungs-Gesamtwertung vervollstaendigt: Platzpunkte pro Wettbewerb konfigurierbar, Gleichstaende erhalten dieselben Platzpunkte, nicht bewertete Wettbewerbe erscheinen als "-", Sortierung erfolgt nach Gesamtpunkten sowie Anzahl erster und zweiter Plaetze.
- Veranstaltungsdetailseite klarer als Ober-Ebene gemacht: Bereich "Wettbewerbe dieser Veranstaltung" mit Hilfetext und "+ Wettbewerb hinzufügen"-Link ergänzt; Sidebar-Gruppierung auf "Allgemein", "Wettbewerbe" und "Verwaltung" geschärft.

## [0.1.1-dev] - 2026-06-18

- Projektstart und erste Entwicklungsstruktur festgelegt.

