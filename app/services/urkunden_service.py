"""Urkunden-Generator: ermittelt aus den vorhandenen Wertungen, wer welche
Urkunde (Sieger-, Ehren- oder Teilnehmerurkunde) mit welchem Platz bekommt.

Die Plaetze werden NICHT neu berechnet, sondern 1:1 aus den bestehenden
Wertungen uebernommen (Turnier-Tabelle inkl. KO-Runden, Sechskampf je
Disziplin/gesamt, Gesamtwertung je Jahrgangsgruppe). Gleichstand bedeutet
dort ueberall "gleicher Platz" (z.B. zweimal Platz 2), ein Stechen gibt es
nicht. Falls eine Wertung fuer den Druck korrigiert werden muss, gibt es
manuelle Anpassungen (Tabelle urkunden_anpassungen), die nur die Urkunde
betreffen, nicht die Tabellen.

Empfaenger ist aktuell immer eine Klasse (Team). Die Eintraege fuehren Name
und Klasse aber getrennt (recipient_name / class_name), damit spaeter
Urkunden pro Schueler:in ergaenzt werden koennen, ohne das Format zu aendern.
"""

import base64
import json
from datetime import date, datetime
from typing import Optional

from app.database import get_conn
from app.services.settings_service import get_setting, set_setting
from app.services.ui_view_service import classify_yeargang
from app.utils.formatting import format_points_value


URKUNDE_SIEGER = "Siegerurkunde"
URKUNDE_EHREN = "Ehrenurkunde"
URKUNDE_TEILNEHMER = "Teilnehmerurkunde"
URKUNDENTYPEN = (URKUNDE_SIEGER, URKUNDE_EHREN, URKUNDE_TEILNEHMER)

WERTUNG_WETTBEWERB = "wettbewerb"
WERTUNG_DISZIPLIN = "disziplin"
WERTUNG_GESAMT = "gesamt"
WERTUNGEN = {
    WERTUNG_WETTBEWERB: "Je Wettbewerb",
    WERTUNG_DISZIPLIN: "Je Disziplin (Sechskampf)",
    WERTUNG_GESAMT: "Gesamtwertung je Jahrgang",
}

JAHRGANGSGRUPPEN = ("Jahrgang 7", "Jahrgang 8", "Jahrgang 9", "Oberstufe")

STANDARD_SCHWELLE = {
    # Platz 1 bis einschliesslich sieger_bis_platz -> Siegerurkunde.
    "sieger_bis_platz": 3,
    # Ehrenurkunde ab Platz sieger_bis_platz+1 bis ehren_bis_platz
    # (0 = keine platzbasierte Ehrenurkunde) ...
    "ehren_bis_platz": 0,
    # ... oder ab dieser Punktzahl (None = keine punktbasierte Ehrenurkunde).
    "ehren_ab_punkte": None,
}

LAYOUT_DEFAULTS = {
    "schulname": "",
    "veranstaltungsname": "",
    "ort": "",
    "datum": "",
    "unterschrift_links": "Sportlehrer:in",
    "unterschrift_rechts": "Schulleitung",
}

LOGO_MAX_BYTES = 2 * 1024 * 1024
LOGO_SETTING_KEY = "urkunden_logo"
SCHWELLEN_SETTING_KEY = "urkunden_schwellen"


# ---------------------------------------------------------------------------
# Schwellen und Urkundentyp
# ---------------------------------------------------------------------------

def _parse_optional_int(value, minimum=0):
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= minimum else None


def _parse_optional_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def normalisiere_schwelle(raw, fallback=None):
    """Eine Schwelle aus Formular/JSON lesen. Leere Felder uebernehmen den
    Wert aus fallback (Standard bzw. globale Schwelle)."""
    base = dict(fallback or STANDARD_SCHWELLE)
    raw = raw or {}
    sieger = _parse_optional_int(raw.get("sieger_bis_platz"))
    if sieger is not None:
        base["sieger_bis_platz"] = sieger
    ehren_platz = _parse_optional_int(raw.get("ehren_bis_platz"))
    if ehren_platz is not None:
        base["ehren_bis_platz"] = ehren_platz
    if raw.get("ehren_ab_punkte") not in (None, ""):
        base["ehren_ab_punkte"] = _parse_optional_float(raw.get("ehren_ab_punkte"))
    return base


def parse_schwellen(raw_json):
    """Liest die gespeicherten Schwellen: {"standard": {...},
    "Jahrgang 7": {...}, ...}. Fehlende/kaputte Werte fallen auf den
    Standard zurueck. Gruppen-Eintraege bleiben roh (nur gesetzte Felder),
    damit "leer = wie Standard" auch nach einer Aenderung des Standards gilt."""
    try:
        data = json.loads(raw_json) if raw_json else {}
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    result = {"standard": normalisiere_schwelle(data.get("standard"))}
    for gruppe in JAHRGANGSGRUPPEN:
        eintrag = data.get(gruppe)
        result[gruppe] = eintrag if isinstance(eintrag, dict) else {}
    return result


def schwelle_fuer_gruppe(schwellen, jahrgangsgruppe: Optional[str]):
    standard = schwellen.get("standard") or dict(STANDARD_SCHWELLE)
    if not jahrgangsgruppe:
        return standard
    return normalisiere_schwelle(schwellen.get(jahrgangsgruppe), standard)


def bestimme_urkundentyp(platz: Optional[int], punkte, schwelle) -> str:
    """Siegerurkunde fuer Platz 1..sieger_bis_platz, Ehrenurkunde bis
    ehren_bis_platz oder ab ehren_ab_punkte, sonst Teilnehmerurkunde. Bei
    Gleichstand haben alle Beteiligten denselben Platz und bekommen damit
    auch denselben Urkundentyp."""
    sieger_bis = schwelle.get("sieger_bis_platz") or 0
    ehren_bis = schwelle.get("ehren_bis_platz") or 0
    ehren_ab_punkte = schwelle.get("ehren_ab_punkte")

    if platz is not None and platz >= 1:
        if platz <= sieger_bis:
            return URKUNDE_SIEGER
        if ehren_bis and platz <= ehren_bis:
            return URKUNDE_EHREN
    if ehren_ab_punkte is not None and punkte is not None:
        try:
            if float(punkte) >= float(ehren_ab_punkte):
                return URKUNDE_EHREN
        except (TypeError, ValueError):
            pass
    return URKUNDE_TEILNEHMER


# ---------------------------------------------------------------------------
# Eintraege aus den bestehenden Wertungen
# ---------------------------------------------------------------------------

def formatiere_datum(value) -> str:
    if not value:
        return ""
    if isinstance(value, (date, datetime)):
        return value.strftime("%d.%m.%Y")
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return text


def _row_value(row, key, default=None):
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def _jahrgang_label(jahrgang):
    if jahrgang in (None, ""):
        return ""
    return f"Jahrgang {jahrgang}"


def _eintrag(
    *,
    key: str,
    wertung: str,
    quelle_id: str,
    quelle_label: str,
    quelle_praefix: str,
    team_id,
    team_name: str,
    jahrgang,
    platz,
    punkte,
    leistung: str,
    datum: str,
    competition_id=None,
    discipline_id=None,
):
    return {
        "key": key,
        "wertung": wertung,
        "quelle_id": quelle_id,
        "quelle_label": quelle_label,
        "quelle_praefix": quelle_praefix,
        "competition_id": competition_id,
        "discipline_id": discipline_id,
        "team_id": team_id,
        "recipient_name": team_name,
        "class_name": team_name,
        "jahrgang": jahrgang,
        "jahrgang_label": _jahrgang_label(jahrgang),
        "jahrgangsgruppe": classify_yeargang(jahrgang),
        "berechneter_platz": platz,
        "platz": platz,
        "punkte": punkte,
        "berechnete_leistung": leistung,
        "leistung": leistung,
        "datum": datum,
    }


def eintraege_aus_turnier(competition, rows, datum: str, team_jahrgaenge=None):
    """rows = Ergebnis von calculate_tournament_points (bereits inkl.
    direktem Vergleich und KO-Platzierungen). team_jahrgaenge (team_id ->
    jahrgang) ergaenzt den Jahrgang, den die Tabellenzeilen nicht fuehren."""
    team_jahrgaenge = team_jahrgaenge or {}
    eintraege = []
    competition_id = _row_value(competition, "id")
    for row in rows:
        pkt = _row_value(row, "pkt", 0)
        pkt_display = _row_value(row, "pkt_display") or format_points_value(pkt)
        leistung = f"{pkt_display} Punkte ({_row_value(row, 'plus', 0)}:{_row_value(row, 'minus', 0)})"
        eintraege.append(_eintrag(
            key=f"w:{competition_id}:{row['team_id']}",
            wertung=WERTUNG_WETTBEWERB,
            quelle_id=f"w:{competition_id}",
            quelle_label=_row_value(competition, "name", ""),
            quelle_praefix="im Wettbewerb",
            competition_id=competition_id,
            team_id=row["team_id"],
            team_name=row["team"],
            jahrgang=team_jahrgaenge.get(row["team_id"], _row_value(competition, "jahrgang")),
            platz=_row_value(row, "placement"),
            punkte=pkt,
            leistung=leistung,
            datum=datum,
        ))
    return eintraege


def eintraege_aus_sechskampf(competition, ranking, datum: str):
    """Eine Urkunde je Klasse fuer den gesamten Sechskampf ("Gesamturkunde"
    ueber alle Disziplinen)."""
    eintraege = []
    competition_id = _row_value(competition, "id")
    for row in ranking:
        team = row["team"]
        eintraege.append(_eintrag(
            key=f"w:{competition_id}:{team['id']}",
            wertung=WERTUNG_WETTBEWERB,
            quelle_id=f"w:{competition_id}",
            quelle_label=_row_value(competition, "name", ""),
            quelle_praefix="im Wettbewerb",
            competition_id=competition_id,
            team_id=team["id"],
            team_name=team["name"],
            jahrgang=_row_value(team, "jahrgang", _row_value(competition, "jahrgang")),
            platz=row.get("placement"),
            punkte=row.get("intermediate_total"),
            leistung=f"{row.get('intermediate_total_display', '')} Punkte",
            datum=datum,
        ))
    return eintraege


def eintraege_aus_disziplinen(competition, disciplines, ranking, datum: str):
    """Eine Urkunde je Klasse und Sechskampf-Disziplin. Leistung ist der
    Messwert (z.B. "4,35 m"), Punkte die Disziplinpunkte."""
    eintraege = []
    competition_id = _row_value(competition, "id")
    competition_name = _row_value(competition, "name", "")
    for discipline in disciplines:
        discipline_id = discipline["id"]
        for row in ranking:
            placements = row.get("discipline_placements") or {}
            if discipline_id not in placements:
                continue
            team = row["team"]
            eintraege.append(_eintrag(
                key=f"d:{discipline_id}:{team['id']}",
                wertung=WERTUNG_DISZIPLIN,
                quelle_id=f"d:{discipline_id}",
                quelle_label=f"{discipline['name']} ({competition_name})",
                quelle_praefix="in der Disziplin",
                competition_id=competition_id,
                discipline_id=discipline_id,
                team_id=team["id"],
                team_name=team["name"],
                jahrgang=_row_value(team, "jahrgang", _row_value(competition, "jahrgang")),
                platz=placements[discipline_id],
                punkte=(row.get("discipline_points") or {}).get(discipline_id),
                leistung=(row.get("discipline_results_display") or {}).get(discipline_id) or "",
                datum=datum,
            ))
    return eintraege


def eintraege_aus_gesamtwertung(overall_groups, datum: str):
    """overall_groups = Ergebnis von calculate_event_overall_ranking."""
    eintraege = []
    for group in overall_groups:
        label = group["year_group"]
        for row in group["rows"]:
            eintraege.append(_eintrag(
                key=f"g:{label}:{row['team_id']}",
                wertung=WERTUNG_GESAMT,
                quelle_id=f"g:{label}",
                quelle_label=label,
                quelle_praefix="in der",
                team_id=row["team_id"],
                team_name=row["team"],
                jahrgang=row.get("jahrgang"),
                platz=row.get("place"),
                punkte=row.get("total_points"),
                leistung=f"{row.get('total_points_display', '')} Punkte",
                datum=datum,
            ))
    return eintraege


def wende_anpassungen_an(eintraege, anpassungen, schwellen):
    """Setzt Urkundentyp (automatisch aus Platz/Punkten) und uebernimmt
    manuelle Anpassungen. Ein manuell geaenderter Platz fuehrt zu einem neu
    bestimmten Typ, ausser der Typ ist selbst manuell festgelegt."""
    for eintrag in eintraege:
        anpassung = anpassungen.get(eintrag["key"]) or {}
        schwelle = schwelle_fuer_gruppe(schwellen, eintrag.get("jahrgangsgruppe"))
        eintrag["berechneter_typ"] = bestimme_urkundentyp(
            eintrag["berechneter_platz"], eintrag["punkte"], schwelle
        )

        if anpassung.get("platz") is not None:
            eintrag["platz"] = anpassung["platz"]
        if anpassung.get("name"):
            eintrag["recipient_name"] = anpassung["name"]
        if anpassung.get("leistung") is not None and anpassung.get("leistung") != "":
            eintrag["leistung"] = anpassung["leistung"]

        if anpassung.get("urkundentyp") in URKUNDENTYPEN:
            eintrag["urkundentyp"] = anpassung["urkundentyp"]
            eintrag["typ_manuell"] = True
        else:
            eintrag["urkundentyp"] = bestimme_urkundentyp(
                eintrag["platz"], eintrag["punkte"], schwelle
            )
            eintrag["typ_manuell"] = False

        eintrag["ausgeblendet"] = bool(anpassung.get("ausgeblendet"))
        eintrag["angepasst"] = bool(anpassung)
    return eintraege


def filtere_eintraege(
    eintraege,
    *,
    team_id=None,
    jahrgangsgruppe=None,
    quelle_id=None,
    platz_bis=None,
    urkundentyp=None,
    nur_druck=False,
):
    result = []
    for eintrag in eintraege:
        if team_id is not None and eintrag["team_id"] != team_id:
            continue
        if jahrgangsgruppe and eintrag.get("jahrgangsgruppe") != jahrgangsgruppe:
            continue
        if quelle_id and eintrag["quelle_id"] != quelle_id:
            continue
        if platz_bis is not None and (eintrag["platz"] is None or eintrag["platz"] > platz_bis):
            continue
        if urkundentyp and eintrag.get("urkundentyp") != urkundentyp:
            continue
        if nur_druck and eintrag.get("ausgeblendet"):
            continue
        result.append(eintrag)
    return result


def sortiere_eintraege(eintraege, sortierung: str = "wertung"):
    """"wertung": je Wettbewerb/Disziplin nach Platz (Sammel-PDF pro
    Disziplin); "klasse": je Klasse alle ihre Urkunden (Sammel-PDF pro
    Klasse). Wettbewerbe/Disziplinen behalten dabei die Reihenfolge, in der
    sie erzeugt wurden (Jahrgang, Wettbewerb, Disziplin-Reihenfolge)."""
    quelle_reihenfolge = {}
    for eintrag in eintraege:
        quelle_reihenfolge.setdefault(eintrag["quelle_id"], len(quelle_reihenfolge))

    def platz_key(eintrag):
        return eintrag["platz"] if eintrag["platz"] is not None else 10**6

    if sortierung == "klasse":
        return sorted(eintraege, key=lambda e: (
            str(e.get("jahrgang") or ""), e["class_name"].lower(),
            quelle_reihenfolge[e["quelle_id"]], platz_key(e),
        ))
    return sorted(eintraege, key=lambda e: (
        quelle_reihenfolge[e["quelle_id"]], platz_key(e), e["recipient_name"].lower(),
    ))


# ---------------------------------------------------------------------------
# Persistenz: Anpassungen, Layout, Schwellen, Logo
# ---------------------------------------------------------------------------

def lade_anpassungen(event_id: int):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM urkunden_anpassungen WHERE event_id = ?", (event_id,)
        ).fetchall()
    return {row["eintrag_key"]: dict(row) for row in rows}


def speichere_anpassung(
    event_id: int,
    eintrag_key: str,
    *,
    platz=None,
    urkundentyp=None,
    name=None,
    leistung=None,
    ausgeblendet=False,
    now: str = "",
):
    platz_value = _parse_optional_int(platz, minimum=1)
    typ_value = urkundentyp if urkundentyp in URKUNDENTYPEN else None
    name_value = (name or "").strip() or None
    leistung_value = (leistung or "").strip() or None
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO urkunden_anpassungen (
                event_id, eintrag_key, platz, urkundentyp, name, leistung,
                ausgeblendet, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id, eintrag_key) DO UPDATE SET
                platz = excluded.platz,
                urkundentyp = excluded.urkundentyp,
                name = excluded.name,
                leistung = excluded.leistung,
                ausgeblendet = excluded.ausgeblendet,
                updated_at = excluded.updated_at
            """,
            (
                event_id, eintrag_key, platz_value, typ_value, name_value,
                leistung_value, 1 if ausgeblendet else 0, now,
            ),
        )
        conn.commit()


def loesche_anpassung(event_id: int, eintrag_key: Optional[str] = None):
    """Ohne eintrag_key werden alle Anpassungen der Veranstaltung entfernt."""
    with get_conn() as conn:
        if eintrag_key is None:
            conn.execute("DELETE FROM urkunden_anpassungen WHERE event_id = ?", (event_id,))
        else:
            conn.execute(
                "DELETE FROM urkunden_anpassungen WHERE event_id = ? AND eintrag_key = ?",
                (event_id, eintrag_key),
            )
        conn.commit()


def lade_layout():
    return {
        key: get_setting(f"urkunden_{key}", default) or default
        for key, default in LAYOUT_DEFAULTS.items()
    }


def speichere_layout(values):
    for key in LAYOUT_DEFAULTS:
        set_setting(f"urkunden_{key}", (values.get(key) or "").strip())


def lade_schwellen():
    return parse_schwellen(get_setting(SCHWELLEN_SETTING_KEY, ""))


def speichere_schwellen(form_values):
    """form_values: {"standard": {...}, "Jahrgang 7": {...}} mit rohen
    Formularwerten. Gruppen speichern nur gesetzte Felder."""
    data = {"standard": normalisiere_schwelle(form_values.get("standard"))}
    for gruppe in JAHRGANGSGRUPPEN:
        raw = form_values.get(gruppe) or {}
        data[gruppe] = {
            key: str(value).strip()
            for key, value in raw.items()
            if key in STANDARD_SCHWELLE and str(value or "").strip() != ""
        }
    set_setting(SCHWELLEN_SETTING_KEY, json.dumps(data))


def erkenne_bildformat(data: bytes) -> Optional[str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def speichere_logo(data: bytes):
    """Das Logo liegt (base64) in der settings-Tabelle: so ueberlebt es
    Updates und ist automatisch in jedem Datenbank-Backup enthalten."""
    if not data:
        raise ValueError("Keine Datei ausgewählt.")
    if len(data) > LOGO_MAX_BYTES:
        raise ValueError("Das Logo ist größer als 2 MB.")
    mime = erkenne_bildformat(data)
    if mime is None:
        raise ValueError("Bitte ein PNG- oder JPG-Bild hochladen.")
    payload = json.dumps({"mime": mime, "data": base64.b64encode(data).decode("ascii")})
    set_setting(LOGO_SETTING_KEY, payload)


def lade_logo():
    """(bytes, mime) oder (None, None)."""
    raw = get_setting(LOGO_SETTING_KEY, "")
    if not raw:
        return None, None
    try:
        payload = json.loads(raw)
        return base64.b64decode(payload["data"]), payload["mime"]
    except (TypeError, ValueError, KeyError):
        return None, None


def loesche_logo():
    set_setting(LOGO_SETTING_KEY, "")
