# Changelog

Alle bemerkenswerten �nderungen dieses Projekts werden hier dokumentiert.

## [Unreleased]

- Nicht angemeldete Nutzer erhalten automatisch die Rolle viewer und können die öffentlichen Seiten ohne vorgeschalteten Login öffnen.
- /login dient jetzt als „Rechte erweitern“-Seite für Helfer (referee) und Admins; die Navigation zeigt die aktuelle Rolle sowie je nach Zustand Rechteerweiterung oder Logout.
- Optionales Helfer-Passwort über SPORTFEST_REFEREE_PASSWORD oder den Settings-Key referee_password vorbereitet; Ergebnisrouten bleiben in dieser Phase unverändert und erhalten noch keinen neuen Schutz.
- `/einstellungen` erhält einen Admin-Kennwort-geschützten Schalter zum Aktivieren und Deaktivieren der Sicherheit; ein Environment-Override bleibt vorrangig und sperrt den UI-Schalter.
- Sicherheit auf zentralen Bereichsschutz umgestellt: `/einstellungen`, `/teams`, `/spielfelder` und `/wettbewerbe` werden bei aktiver Sicherheit einschließlich ihrer Verwaltungsaktionen durch eine gemeinsame Middleware geschützt; redundante Einzel-Guards wurden entfernt.
- Erste optionale Sicherheitsschicht vorbereitet: neue globale Einstellung `security_enabled` mit Standardwert `false`, Admin-Passwort per Setting oder Umgebungsvariable und automatisch deaktivierter Schutz ohne Passwort.
- SessionMiddleware mit Umgebungs-Secret (und temporärem Entwicklungsschlüssel als Fallback), einfache Routen `/login` und `/logout` sowie die Helper `is_logged_in()` und `require_admin()` ergänzt.
- `/einstellungen` zeigt jetzt „Sicherheit aktiv“ und „Login vorbereitet“; Docker Compose reicht die neuen Sicherheits-Umgebungsvariablen durch.
- Formular-Redirects behalten jetzt global die ungefaehre Scrollposition: Vor normalen POST-Submits wird die aktuelle Position in `sessionStorage` gespeichert und nach dem Reload auf derselben Seite wiederhergestellt; der zuletzt gedrueckte Submit-Button wird kurz hervorgehoben.
- Dashboard mobil optimiert: zu breite Karten/Zeilen wurden fuer Smartphone-Breiten kompakter gemacht (insbesondere laufende Spiele und weitere kommende Veranstaltungen), damit Inhalte ohne seitliches Herausragen nutzbar bleiben.
- Mobiles Navigationslayout ueberarbeitet: Auf Smartphone-Breite wird die Desktop-Sidebar ausgeblendet und durch eine kompakte Kopfzeile mit "☰ Menü" ersetzt; Navigation oeffnet als schliessbares Overlay-Drawer, waehrend der Seiteninhalt die volle Breite nutzt.
- Mobile/Responsive Sechskampf-Ergebniseingabe auf /ergebnisse ueberarbeitet: kompakte Klassenkarten, Kopfbereich mit Klasse/Gesamt/Speichern plus kleine Speicherzeit (HH:MM), direkte Eingabefelder ohne grosse Luecke, dichter responsiver Werte-Grid (Handy/Tablet/Laptop), 16px-Inputs gegen Browser-Zoom sowie sticky Stationskopf beim Scrollen.
- Einstellungen-Seite als zentrale Verwaltung erweitert: Systeminformationen enthalten jetzt auch Spielfeldanzahl; Backups zeigen letztes Backup und Gesamtanzahl als kompakte Karten; neuer Systemstatus-Bereich zeigt Datenbank-Erreichbarkeit und Schreibzugriff (Ja/Nein).
- Projektpflege/Stabilisierung: ROADMAP.md auf den aktuellen Umsetzungsstand abgeglichen (erledigte Punkte abgehakt, offene naechste Schritte aktualisiert) und .gitignore um weitere IDE-Dateien erweitert.
- CSV-Export fuer Auswertungen auf /tabellen hinzugefuegt: Per Button "CSV exportieren" werden die aktuell gefilterten Daten (Turniertabellen, Sechskampf-Ranglisten und bei Veranstaltungsfilter auch Gesamtwertungen) als UTF-8-CSV mit Semikolon-Trennzeichen exportiert.
- Tabellen-Seite erweitert: kombinierbare GET-Filter fuer Veranstaltung, Jahrgang und Wettbewerb ergaenzt (`/tabellen?event_id=...&jahrgang=...&competition_id=...`); ausgewaehlte Filter bleiben sichtbar und bei leerem Ergebnis erscheint der Hinweis "Keine Tabellen für diese Auswahl vorhanden.".
- Veranstaltungsdetailseite als zentraler Einstieg ausgebaut: Zugehoerige Wettbewerbe werden jetzt als kompakte Liste mit Name, Sportart, Jahrgang und Status angezeigt; pro Wettbewerb stehen direkte Aktionen fuer Bearbeiten, Spielplan und Ergebnisse zur Verfuegung.
- Spielfelder-Loeschschutz ergaenzt: Loeschen wird verhindert, wenn das Spielfeld in Slots verwendet wird (Spiele werden separat gezaehlt); stattdessen erscheint auf /spielfelder eine klare Fehlermeldung mit Nutzungsanzahl. Inaktive Spielfelder bleiben bestehenden Zuordnungen erhalten und werden weiterhin nicht fuer neue Planungen vorgeschlagen.
- Spielfelder-Seite auf kompakte Tabellenansicht umgestellt: Name und Sportart sind direkt in der Zeile bearbeitbar, Aktiv/Inaktiv ist pro Zeile umschaltbar, und bestehende Speichern-/Loeschen-Aktionen bleiben erhalten (inklusive horizontalem Scrollen auf kleinen Bildschirmen).
- Veranstaltungsduplikate wurden vervollstaendigt: Neue Veranstaltungen erhalten automatisch den Namenszusatz `(Kopie)`, uebernehmen den Veranstaltungstyp, lassen das Datum leer und kopieren zugehoerige Wettbewerbe inklusive Punkte-Einstellungen und Sechskampf-Disziplinen ohne Spielplaene oder Ergebnisse.
- Wettbewerbe zeigen nach dem Speichern jetzt direkt an der betroffenen Karte eine dezente Rueckmeldung "Gespeichert um HH:MM"; die soeben gespeicherte Karte bleibt nach dem Redirect geoeffnet.
- Layout-Fix fuer aufgeklappte Wettbewerbskarten: Detailbereiche liegen jetzt stets unterhalb der kompakten Zusammenfassung; auf Mobilgeraeten werden Formularfelder in eine Spalte umgestellt, damit nichts seitlich herausragt.
- Wettbewerbe-Seite auf kompakte Aufklapp-Karten umgestellt: Jeder Wettbewerb zeigt zuerst nur Kerndaten, waehrend Bearbeitungsformular, Zeiten, Punktefelder, Aktionen und Sechskampf-Disziplinen erst nach Klick per nativen `details/summary` sichtbar werden.
- Darstellung von Wettbewerben und Sechskampf-Disziplinen auf Mobilgeraeten verbessert: volle Breite, keine herausragenden Eingabefelder und sauberes Umbrechen in den Detailbereichen.
- Veranstaltungen haben jetzt einen einfachen `Veranstaltungstyp` (`Bewegungsfest`, `Einzelturnier`, `Käthelauf`, `Sonstiges`) mit Auswahl in Anlegen/Bearbeiten sowie Anzeige in Übersicht und Detailseite.
- Datenbank: `events.event_type` wird beim Start bei Bedarf ergänzt; bestehende Veranstaltungen ohne Typ werden automatisch auf `Sonstiges` gesetzt.
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
- Seite /einstellungen um globale Systemfunktionen erweitert: Systeminfos (Version, Datenbankgroesse, Anzahl Datensaetze), Backup-Erstellung mit Backup-Liste und speicherbares Beamer-Refresh-Intervall.
- Neue settings-Tabelle (key/value) fuer globale App-Einstellungen; Standardwert beamer_refresh_seconds=30 wird automatisch angelegt.
- Beamerseite nutzt jetzt das globale Refresh-Intervall aus den Einstellungen statt festem Wert.
- Stabilisierung der Einstellungs-/Backupfunktion: Backup-Erstellung, Dateiliste (Name/Groesse/Erstellungszeit), DB-Groessenanzeige und Intervallspeicherung verifiziert.
- Warnhinweis auf /einstellungen ergänzt: Wiederherstellung ist noch nicht über die Oberfläche möglich.
- README um kurze manuelle Restore-Anleitung ergänzt (Container stoppen, DB sichern, Backup nach data/sportfest.db kopieren, Container starten).

## [0.1.1-dev] - 2026-06-18

- Projektstart und erste Entwicklungsstruktur festgelegt.

