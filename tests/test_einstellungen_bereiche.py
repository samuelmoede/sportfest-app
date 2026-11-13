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
import app.services.backup_service as backup_service
from app.database import init_db
from app.services.einstellungen_service import (
    BEREICH_SCHLUESSEL,
    baue_beamer_link,
    baue_bereichsleiste,
    beamer_orte,
    bereich_url,
    bestimme_bereich,
)
from app.services.settings_service import (
    get_beamer_refresh_seconds,
    get_dashboard_info_text,
    get_site_theme,
    set_setting,
)
from app.services.urkunden_service import lade_layout, lade_logo, lade_schulname

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class BereichAuswahlTests(unittest.TestCase):
    def test_ohne_angabe_gilt_der_standardbereich(self):
        self.assertEqual(bestimme_bereich(), "anzeige")
        self.assertEqual(bestimme_bereich("gibt-es-nicht"), "anzeige")

    def test_ausdruecklich_gewaehlter_bereich_gewinnt(self):
        for key in BEREICH_SCHLUESSEL:
            self.assertEqual(bestimme_bereich(key), key)
        self.assertEqual(bestimme_bereich(" System ", user_status="ok"), "system")

    def test_alte_status_parameter_fuehren_in_ihren_bereich(self):
        self.assertEqual(bestimme_bereich(user_status="ok"), "benutzer")
        self.assertEqual(bestimme_bereich(security_status="enabled"), "benutzer")
        self.assertEqual(bestimme_bereich(backup_status="ok"), "datensicherung")
        self.assertEqual(bestimme_bereich(restore_status="ok"), "datensicherung")
        self.assertEqual(bestimme_bereich(delete_status="ok"), "datensicherung")
        self.assertEqual(bestimme_bereich(reset_status="ok"), "protokoll")
        self.assertEqual(bestimme_bereich(change_log_username="MOSA"), "protokoll")

    def test_bereich_url_laesst_leere_parameter_weg(self):
        self.assertEqual(bereich_url("system"), "/einstellungen?bereich=system")
        self.assertEqual(
            bereich_url("anzeige", anzeige_status="saved", saved_at=""),
            "/einstellungen?bereich=anzeige&anzeige_status=saved",
        )


class BereichsleisteTests(unittest.TestCase):
    def test_leiste_zeigt_alle_bereiche_und_markiert_den_aktiven(self):
        leiste = baue_bereichsleiste("datensicherung")
        self.assertEqual([e["key"] for e in leiste], list(BEREICH_SCHLUESSEL))
        self.assertEqual([e["key"] for e in leiste if e["aktiv"]], ["datensicherung"])
        self.assertTrue(all(e["href"].startswith("/einstellungen?bereich=") for e in leiste))

    def test_zustandszeilen(self):
        leiste = {
            e["key"]: e
            for e in baue_bereichsleiste(
                "anzeige",
                benutzer_anzahl=3,
                sicherheit_aktiv=True,
                letztes_backup="13.11.2026 18:02",
                aenderungen=1,
                version="1.3",
                system_ok=True,
            )
        }
        self.assertEqual(leiste["benutzer"]["hinweis"], "3 Benutzer · Anmeldung an")
        self.assertEqual(leiste["datensicherung"]["hinweis"], "Letztes Backup: 13.11.2026 18:02")
        self.assertEqual(leiste["protokoll"]["hinweis"], "1 Ergebnisänderung")
        self.assertEqual(leiste["system"]["hinweis"], "Version 1.3 · alles in Ordnung")
        self.assertFalse(leiste["system"]["warnung"])

    def test_zustandszeilen_ohne_backup_und_mit_problem(self):
        leiste = {e["key"]: e for e in baue_bereichsleiste("anzeige", system_ok=False)}
        self.assertEqual(leiste["benutzer"]["hinweis"], "0 Benutzer · Anmeldung aus")
        self.assertEqual(leiste["datensicherung"]["hinweis"], "Noch kein Backup")
        self.assertEqual(leiste["protokoll"]["hinweis"], "0 Ergebnisänderungen")
        self.assertIn("Problem erkannt", leiste["system"]["hinweis"])
        self.assertTrue(leiste["system"]["warnung"])


class BeamerLinkTests(unittest.TestCase):
    def test_standard_ist_der_kurze_link(self):
        self.assertEqual(baue_beamer_link("https://sport.example/"), "https://sport.example/beamer")
        self.assertEqual(
            baue_beamer_link("https://sport.example", sekunden=12), "https://sport.example/beamer"
        )

    def test_optionen_werden_angehaengt(self):
        self.assertEqual(
            baue_beamer_link(
                "https://sport.example/", ort="Mensa", tabellen=False, hell=True, sekunden=20
            ),
            "https://sport.example/beamer?ort=Mensa&tabellen=aus&hell=1&sekunden=20",
        )

    def test_unbekannter_ort_entfaellt_und_sekunden_werden_begrenzt(self):
        self.assertEqual(
            baue_beamer_link("http://x/", ort="Keller", sekunden=999), "http://x/beamer?sekunden=120"
        )
        self.assertEqual(baue_beamer_link("http://x/", sekunden=1), "http://x/beamer?sekunden=5")

    def test_beamer_orte_ohne_ort_ohne_spiele(self):
        orte = beamer_orte()
        self.assertIn("Mensa", orte)
        self.assertIn("Turnhalle", orte)
        self.assertNotIn("Schulhaus", orte)


class EinstellungenBereicheRouteTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp = Path(self._tmpdir.name)
        self._patcher = [
            patch.object(database, "DB_PATH", tmp / "einstellungen-test.db"),
            patch.object(backup_service, "DB_PATH", tmp / "einstellungen-test.db"),
            patch.object(backup_service, "BACKUP_DIR", tmp / "backups"),
        ]
        for patcher in self._patcher:
            patcher.start()
        init_db()
        from app.main import app as fastapi_app

        self.client = TestClient(fastapi_app)

    def tearDown(self):
        self.client.close()
        for patcher in self._patcher:
            patcher.stop()
        self._tmpdir.cleanup()

    def _login_admin(self):
        from app.services.users_service import get_user_by_username, set_user_password

        set_user_password(get_user_by_username("ADMIN")["id"], "geheim123")
        response = self.client.post(
            "/login", data={"username": "admin", "password": "geheim123"}, follow_redirects=False
        )
        self.assertEqual(response.status_code, 303)

    # --- Gliederung ---------------------------------------------------------

    def test_jeder_bereich_zeigt_nur_seine_kaesten(self):
        erwartet = {
            "anzeige": ["<h2>Anzeige</h2>", "<h2>Schule</h2>", "<h2>Beamer-Link</h2>"],
            "benutzer": ["<h2>Sicherheit</h2>", "<h2>Benutzer</h2>"],
            "datensicherung": ["<h2>Backups</h2>"],
            "protokoll": ["<h2>Änderungsprotokoll</h2>"],
            "system": ["<h2>Systeminformationen</h2>", "<h2>Systemstatus</h2>"],
        }
        alle = [titel for titel_liste in erwartet.values() for titel in titel_liste]
        for bereich, titel_liste in erwartet.items():
            with self.subTest(bereich=bereich):
                text = self.client.get(f"/einstellungen?bereich={bereich}").text
                for titel in alle:
                    if titel in titel_liste:
                        self.assertIn(titel, text)
                    else:
                        self.assertNotIn(titel, text)
                # Die Bereichsleiste steht immer da und markiert genau einen Eintrag.
                self.assertEqual(text.count('class="einstellungen-bereich aktiv"'), 1)
                for key in BEREICH_SCHLUESSEL:
                    self.assertIn(f'href="/einstellungen?bereich={key}"', text)

    def test_ohne_bereich_oeffnet_die_anzeige(self):
        text = self.client.get("/einstellungen").text
        self.assertIn("<h2>Anzeige</h2>", text)
        self.assertNotIn("<h2>Backups</h2>", text)

    def test_alte_weiterleitungen_landen_im_richtigen_bereich(self):
        text = self.client.get("/einstellungen?user_status=ok&user_username=MOSA").text
        self.assertIn("<h2>Benutzer</h2>", text)
        self.assertIn("Änderung für MOSA gespeichert.", text)
        text = self.client.get("/einstellungen?delete_status=ok").text
        self.assertIn("Backup gelöscht.", text)

    # --- Anzeige ------------------------------------------------------------

    def test_anzeige_speichert_banner_farbschema_und_intervall_zusammen(self):
        response = self.client.post(
            "/einstellungen/anzeige",
            data={
                "dashboard_info_text": "Treffpunkt 9 Uhr",
                "site_theme": "dunkel",
                "beamer_refresh_seconds": "45",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("bereich=anzeige", response.headers["location"])
        self.assertIn("anzeige_status=saved", response.headers["location"])
        self.assertEqual(get_dashboard_info_text(), "Treffpunkt 9 Uhr")
        self.assertEqual(get_site_theme(), "dunkel")
        self.assertEqual(get_beamer_refresh_seconds(), 45)
        self.assertIn("Gespeichert um", self.client.get(response.headers["location"]).text)

    def test_anzeige_speichert_bei_fehler_nichts(self):
        set_setting("dashboard_info_text", "alt")
        for daten, status in (
            ({"site_theme": "dunkel", "beamer_refresh_seconds": "0"}, "invalid_intervall"),
            ({"site_theme": "dunkel", "beamer_refresh_seconds": "abc"}, "invalid_intervall"),
            ({"site_theme": "neon", "beamer_refresh_seconds": "20"}, "invalid_theme"),
        ):
            with self.subTest(status=status, daten=daten):
                response = self.client.post(
                    "/einstellungen/anzeige",
                    data={"dashboard_info_text": "neu", **daten},
                    follow_redirects=False,
                )
                self.assertIn(f"anzeige_status={status}", response.headers["location"])
                self.assertEqual(get_dashboard_info_text(), "alt")
                self.assertEqual(get_site_theme(), "standard")
                self.assertEqual(get_beamer_refresh_seconds(), 30)

    def test_beamer_baukasten_bietet_orte_und_keine_parameterliste(self):
        text = self.client.get("/einstellungen?bereich=anzeige").text
        self.assertIn("data-beamer-baukasten", text)
        self.assertIn('<option value="Mensa">Mensa</option>', text)
        self.assertNotIn('<option value="Schulhaus">', text)
        self.assertNotIn("?ort=Mensa", text)

    # --- Schule -------------------------------------------------------------

    def test_schulname_wird_zentral_gespeichert_und_gilt_fuer_urkunden(self):
        response = self.client.post(
            "/einstellungen/schule", data={"schulname": "  Gymnasium Süd  "}, follow_redirects=False
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("#schule", response.headers["location"])
        self.assertEqual(lade_schulname(), "Gymnasium Süd")
        self.assertEqual(lade_layout()["schulname"], "Gymnasium Süd")
        self.assertIn('value="Gymnasium Süd"', self.client.get("/einstellungen").text)

    def test_urkunden_formular_ohne_schulname_laesst_ihn_stehen(self):
        self.client.post("/einstellungen/schule", data={"schulname": "Gymnasium Süd"})
        self.client.post(
            "/urkunden/einstellungen",
            data={"ort": "Musterstadt", "unterschrift_links": "Sportlehrer"},
            follow_redirects=False,
        )
        layout = lade_layout()
        self.assertEqual(layout["schulname"], "Gymnasium Süd")
        self.assertEqual(layout["ort"], "Musterstadt")

    def test_urkunden_seite_verweist_auf_die_einstellungen(self):
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO events (name, event_date, status) "
                "VALUES ('Sportfest 2026', '2026-06-30', 'aktiv')"
            )
            conn.commit()
        self.client.post("/einstellungen/schule", data={"schulname": "Gymnasium Süd"})
        text = self.client.get("/urkunden").text
        self.assertIn("Gymnasium Süd", text)
        self.assertIn("/einstellungen?bereich=anzeige#schule", text)
        self.assertNotIn('name="schulname"', text)
        self.assertNotIn('action="/urkunden/logo"', text)

    def test_logo_hochladen_und_entfernen(self):
        response = self.client.post(
            "/einstellungen/schule/logo",
            files={"logo": ("logo.png", PNG_BYTES, "image/png")},
            follow_redirects=False,
        )
        self.assertIn("schule_status=logo_saved", response.headers["location"])
        self.assertEqual(lade_logo(), (PNG_BYTES, "image/png"))
        self.assertIn("Logo entfernen", self.client.get("/einstellungen").text)

        response = self.client.post("/einstellungen/schule/logo/loeschen", follow_redirects=False)
        self.assertIn("schule_status=logo_deleted", response.headers["location"])
        self.assertEqual(lade_logo(), (None, None))

    def test_logo_mit_falschem_format_wird_abgelehnt(self):
        response = self.client.post(
            "/einstellungen/schule/logo",
            files={"logo": ("logo.gif", b"GIF89a", "image/gif")},
            follow_redirects=False,
        )
        self.assertIn("schule_status=logo_format", response.headers["location"])
        self.assertEqual(lade_logo(), (None, None))
        self.assertIn("PNG- oder JPG-Bild", self.client.get(response.headers["location"]).text)

    # --- Backup herunterladen -----------------------------------------------

    def _backup_anlegen(self):
        name = backup_service.create_database_backup()
        self.assertIsNotNone(name)
        return name

    def test_download_nur_mit_admin_anmeldung(self):
        name = self._backup_anlegen()
        response = self.client.get(
            f"/einstellungen/backup/download?datei={name}", follow_redirects=False
        )
        self.assertEqual(response.status_code, 303)
        self.assertIn("backup_status=download_login", response.headers["location"])

    def test_download_liefert_die_backup_datei(self):
        name = self._backup_anlegen()
        self._login_admin()
        response = self.client.get(f"/einstellungen/backup/download?datei={name}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(name, response.headers["content-disposition"])
        self.assertTrue(response.content.startswith(b"SQLite format 3"))

    def test_download_lehnt_fremde_pfade_ab(self):
        self._backup_anlegen()
        self._login_admin()
        for datei in ("../einstellungen-test.db", "gibt-es-nicht.db", "", "/etc/passwd"):
            with self.subTest(datei=datei):
                response = self.client.get(
                    "/einstellungen/backup/download", params={"datei": datei}, follow_redirects=False
                )
                self.assertEqual(response.status_code, 303)
                self.assertIn("backup_status=download_not_found", response.headers["location"])

    def test_backup_liste_hat_herunterladen_knopf(self):
        name = self._backup_anlegen()
        text = self.client.get("/einstellungen?bereich=datensicherung").text
        self.assertIn(f"/einstellungen/backup/download?datei={name}", text)
        self.assertIn("Herunterladen", text)


if __name__ == "__main__":
    unittest.main()
