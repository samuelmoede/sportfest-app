# Codex aus GitHub-Issues starten

Die Automation `.github/workflows/codex.yml` ergaenzt den Claude-Workflow.
Sie ist unabhaengig von den Codex-Webeinstellungen fuer automatische PR-Reviews.
Claude-Dateien und Deployment-Workflows bleiben unveraendert.

## Einmalige Einrichtung durch den Repository-Owner

1. Zuerst PR #127 mit den Codex-Regeln in `AGENTS.md` nach `develop` mergen.
   Der neue Workflow verweigert die Implementierung, solange diese Regeln fehlen.
2. Den PR mit dieser Automation pruefen und nach gruenen Checks nach `develop`
   mergen. Codex fuehrt keinen Merge aus.
3. Die Workflow-Datei und den Helper `.github/scripts/codex_issue.py` ueber den
   normalen menschlich geprueften Release-PR von `develop` nach `main` uebernehmen.
   `main` ist derzeit der GitHub-Standardbranch. `issues` und `issue_comment`
   starten nur Workflows, die auf dem Standardbranch existieren. Den Standardbranch
   nicht nur fuer diese Automation umstellen. Ein Merge nach main startet den
   vorhandenen Prod-Workflow, dessen Freigabe im Environment `production` weiterhin
   ausschliesslich der Mensch erteilt. Nicht fuer diesen Test Produktion freigeben.
4. Auf der OpenAI-API-Plattform ein eigenes Projekt fuer die Automation waehlen
   oder anlegen, dessen API-Abrechnung bewusst konfigurieren und einen API-Schluessel
   erstellen. API-Nutzung ist separat vom ChatGPT-Abonnement. Budget-Benachrichtigungen
   nicht als garantiertes hartes Ausgabenlimit verstehen.
5. GitHub: Repository > Settings > Secrets and variables > Actions >
   New repository secret. Name: `OPENAI_API_KEY`; Wert: der API-Schluessel.
   Schluessel niemals in Issues, Chat, Dateien oder Workflow-YAML einfuegen.
6. GitHub-Kontoeinstellungen > Developer settings > Personal access tokens >
   Fine-grained tokens > Generate new token:
   - Name: `sportfest-codex-pr`.
   - Resource owner: `samuelmoede`.
   - Ablaufdatum bewusst waehlen, z. B. 90 Tage; rechtzeitig erneuern.
   - Repository access: Only select repositories > `sportfest-app`.
   - Repository permissions: Contents = Read and write;
     Pull requests = Read and write. Metadata wird automatisch lesbar.
   - Keine Administration-, Actions-, Workflows- oder Environments-Schreibrechte.
   - Generate token; sofort als Repository-Secret `CODEX_PR_TOKEN` speichern.
     Den Token nicht im Chat teilen. Er wird nur im separaten Publish-Job verwendet.
   Der separate Token sorgt dafuer, dass PR-Ereignisse die normale CI automatisch
   starten. Kein GitHub-Token wird an den implementierenden Agenten weitergegeben;
   dessen Job hat nur `contents: read`, und Checkout speichert keine Zugangsdaten.
7. Falls GitHub Actions auf ausgewaehlte Actions beschraenkt ist: unter Repository >
   Settings > Actions > General die verwendeten offiziellen `actions/*` und
   `openai/codex-action` erlauben, ohne die bestehenden Schutzregeln abzuschwaechen.

Die Cloud-Umgebung aus der Codex-Weboberflaeche wird von dieser GitHub Action
nicht verwendet. Python 3.12, die Requirements und Chromium werden im Workflow
auf einem frischen `ubuntu-latest`-Runner eingerichtet, niemals auf dem NAS.

## Ausloesen und kontrollieren

1. Als `samuelmoede` ein Issue mit einer klaren, kleinen Aufgabe erstellen.
2. Im Titel/Text des neuen Issues oder in einem neuen Kommentar schreiben:
   `@codex bitte umsetzen`.
   Beispiel fuer den ersten Test: `Ergaenze in README.md einen kurzen Hinweis,
   dass die Tests mit python -m pytest tests -v gestartet werden. Aendere nur
   README.md. @codex bitte umsetzen`.
3. Unter Actions den Lauf `codex-issue` oeffnen. Die Jobs heissen `gate`,
   `implement` und `publish`. Der Link zum PR steht in der Zusammenfassung des
   Publish-Jobs; bei einer Bearbeitung ohne Dateiaenderungen entsteht kein PR.
4. Unter Pull requests erscheint ein **Draft-PR gegen develop** mit einem Branch
   `codex/issue-<Nummer>-run-<Lauf>-<Versuch>`. Die Pflichtchecks `test` und `docker`
   laufen unabhaengig. Entwurf und Testprotokoll pruefen, bei Bedarf auf
   Ready for review stellen und selbst ueber einen Merge entscheiden.

Nur frische, explizite Mentions des Repository-Owners starten einen Auftrag.
Fremde Nutzer, Bots, geschlossene Issues, editierte Kommentare und PR-Kommentare
starten diese Automation nicht. Auf PRs bleibt die native Codex-Integration
zustaendig. Ein weiterer Issue-Kommentar mit Mention erzeugt einen neuen Vorschlag
auf Basis von develop, keine Fortsetzung des bestehenden PR-Branches. Parallele
Auftraege zum selben Issue werden serialisiert; GitHub kann aeltere wartende Laeufe
durch neuere ersetzen. Daher jeweils einen Auftrag abwarten.

## Grenzen und Fehlerbehebung

- Erlaubt sind maximal 50 UTF-8-Textdateien mit insgesamt 1 MB unter `app/`,
  `tests/`, `docs/` sowie `README.md`, `CHANGELOG.md`, `VERSION`, `requirements.txt`
  und `requirements-dev.txt`. Keine Symlinks, Submodule, versteckten Pfade,
  Agent-Regeln, Datenbanken, Schluessel- oder Deployment-Dateien. Solche Aufgaben
  gehoeren in einen separat betreuten PR.
- Der Publish-Job validiert die Daten erneut auf einem frischen Runner und
  fuehrt keinen erzeugten Code aus. Er erstellt nur einen neuen Feature-Branch
  und einen PR mit festem Ziel develop. Kein Merge, keine Aktualisierung von
  main/develop, keine Produktivfreigabe.
- Schlaegt pytest fehl, wird kein PR veroeffentlicht. Details stehen im Actions-Log.
  Ein gruener Agent-Job ersetzt die unabhaengige PR-CI nicht.
- Kein Lauf sichtbar: Workflow/Helper auf dem Standardbranch pruefen, dann als
  Owner einen **neuen** Kommentar senden; alte Kommentare werden nicht nachgeholt.
- `OPENAI_API_KEY fehlt`, HTTP 401 oder Quotenfehler: Repository-Secret sowie
  API-Projekt, Abrechnung und Modellzugang pruefen.
- `CODEX_PR_TOKEN fehlt` oder GitHub 403: Secret, Ablaufdatum, Repository-Auswahl
  und Contents-/Pull-requests-Rechte pruefen. Keine Bypass-Rechte erteilen.
- Fuer Pause oder Kostenstopp: Actions > codex-issue > Menue > Disable workflow.
  Bereits laufende Jobs gegebenenfalls separat abbrechen.
- Lokal offline pruefen: `python -m unittest discover -s tests -p test_codex_issue_workflow.py -v`.

## Quellen

- [Offizielle Codex Action](https://learn.chatgpt.com/docs/github-action)
- [GitHub Issue-Ereignisse](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [GitHub-Token und Folge-Workflows](https://docs.github.com/en/actions/concepts/security/github_token)
