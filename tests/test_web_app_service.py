import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from fastapi.testclient import TestClient

import app.database as database
from app.services.web_app_service import SYMBOLE, build_manifest, manifest_version

STATIC_DIR = ROOT_DIR / "app" / "static"


def png_groesse(pfad):
    daten = pfad.read_bytes()[:24]
    assert daten[:8] == b"\x89PNG\r\n\x1a\n", f"{pfad} ist kein PNG"
    breite, hoehe = struct.unpack(">II", daten[16:24])
    return f"{breite}x{hoehe}"


class WebAppManifestTests(unittest.TestCase):
    def test_manifest_ist_installierbar(self):
        manifest = build_manifest()
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["short_name"], "Sportfest")
        groessen = {icon["sizes"] for icon in manifest["icons"] if icon["purpose"] == "any"}
        # Chrome/Android verlangt mindestens 192 und 512 Pixel.
        self.assertIn("192x192", groessen)
        self.assertIn("512x512", groessen)
        self.assertTrue(any(icon["purpose"] == "maskable" for icon in manifest["icons"]))

    def test_symbole_existieren_in_passender_groesse(self):
        for pfad, groesse, _purpose in SYMBOLE:
            self.assertEqual(png_groesse(STATIC_DIR / pfad), groesse, pfad)
        self.assertEqual(png_groesse(STATIC_DIR / "icons" / "apple-touch-icon.png"), "180x180")
        self.assertTrue((STATIC_DIR / "icons" / "app-icon.svg").exists())

    def test_symbol_urls_tragen_versionsnummer(self):
        # Ohne ?v= liefert der Cloudflare-Edge-Cache nach Aenderungen alte Dateien.
        for icon in build_manifest()["icons"]:
            self.assertRegex(icon["src"], r"^/static/icons/[\w-]+\.png\?v=[0-9a-f]{12}$")
        self.assertRegex(manifest_version(), r"^[0-9a-f]{12}$")


class WebAppRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self._db_path_patcher = patch.object(
            database, "DB_PATH", Path(self._tmpdir.name) / "sportfest-test.db"
        )
        self._db_path_patcher.start()

    def tearDown(self):
        self._db_path_patcher.stop()
        self._tmpdir.cleanup()

    def test_manifest_route_und_einbindung(self):
        from app.main import app as fastapi_app

        with TestClient(fastapi_app) as client:
            response = client.get("/manifest.webmanifest")
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("application/manifest+json"))
            self.assertEqual(json.loads(response.text)["name"], "Sportfest Manager")

            for icon in build_manifest()["icons"]:
                self.assertEqual(client.get(icon["src"]).status_code, 200, icon["src"])

            seite = client.get("/").text
            self.assertIn(f'<link rel="manifest" href="/manifest.webmanifest?v={manifest_version()}">', seite)
            self.assertIn('rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png?v=', seite)
            self.assertIn('name="apple-mobile-web-app-capable" content="yes"', seite)


if __name__ == "__main__":
    unittest.main()
