"""Fixtures fuer echte Playwright-Browser-Tests (siehe Issue #119).

Anders als die TestClient/ASGI-basierten Tests im Rest von tests/ braucht ein
echter Browser eine echte HTTP-URL - TestClient spricht die ASGI-App direkt
im selben Prozess an, das kann ein Browser nicht. live_server_url() startet
die App deshalb als echten uvicorn-Server in einem Hintergrund-Thread, immer
gegen eine temporaere DB (niemals data/sportfest.db, siehe CLAUDE.md).

chromium_available() laesst die Tests in diesem Ordner uebersprungen statt
fehlschlagen, solange die Chromium-Browser-Binary lokal/in CI nicht per
`python -m playwright install chromium` installiert ist - der bestehende
`ci`-Workflow (.github/workflows/ci.yml) braucht dafuer noch einen
zusaetzlichen Schritt im `test`-Job, den Claude wegen fehlender Berechtigung
fuer .github/workflows/* nicht selbst einfuegen kann (siehe PR-Beschreibung).
"""
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:
        return False


@pytest.fixture(scope="session")
def live_server_url():
    import httpx
    import uvicorn

    import app.database as database

    tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    db_path = Path(tmpdir.name) / "sportfest-browser-test.db"
    original_db_path = database.DB_PATH
    database.DB_PATH = db_path

    from app.main import app as fastapi_app

    port = _free_port()
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            httpx.get(base_url + "/", timeout=0.5)
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        raise RuntimeError("Live-Testserver ist nicht rechtzeitig gestartet")

    yield base_url

    server.should_exit = True
    thread.join(timeout=5)
    database.DB_PATH = original_db_path
    tmpdir.cleanup()
