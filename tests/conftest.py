"""Gemeinsame Test-Einstellungen.

Fester Session-Schluessel, damit das Importieren von app.main in Tests keine
data/.session_secret im Repo-Ordner anlegt (siehe session_secret_service.py;
der Repo-Ordner kann der Live-Produktivordner sein).
"""
import os

os.environ.setdefault("SPORTFEST_SESSION_SECRET_KEY", "test-session-secret")
