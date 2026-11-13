"""Neuer Einrichtungsassistent: Entwuerfe, abgeleitete Wettbewerbe, Vorlagen
und die atomare Uebernahme.

Ein Entwurf ist ein JSON-Dokument in assistent_entwuerfe.daten. Waehrend der
Planung entstehen keine Veranstaltungen, Wettbewerbe oder Spiele - nichts
davon ist vorher in Spielplan, Tagesplan oder Ergebnissen sichtbar. Erst
uebernehmen() legt alles in einer Transaktion an (siehe dort).

Aufbau der Entwurfsdaten (alle Felder optional, siehe neue_daten):

- grunddaten: name, datum, event_type, start, ende (grober Zeitrahmen)
- modus: "schnell" (Schnellplanung) oder "experte" (Expertenmodus)
- jahrgaenge: teilnehmende Jahrgaenge als Text ("7", "11", "GOST")
- teams_abgewaehlt: ids einzelner Klassen, die nicht mitspielen
- sportarten: Liste der Sportarten (Spalten der Matrix)
- matrix: Sportart -> Jahrgaenge, die sie ausueben
- zusammenlegungen: [{"sportart", "jahrgaenge"}] - echte gemeinsame
  Wettbewerbe mit gemeinsamem Sieger
- wettbewerbe: Schluessel -> Einstellungen (Ort, Felder, Modus, Punkte,
  Gleichstandsregeln, Teilmannschaften, Art, Disziplinen)
- orte: Ort -> {"aktiv", "felder": [court ids]}
- zeitbloecke: [{"id", "ort", "start", "ende", "wettbewerbe", "ablauf",
  "spielzeit", "wechselzeit"}]
- programmpunkte: [{"id", "titel", "art", "start", "ende", "jahrgaenge"}]
- showspiele: [{"id", "wettbewerb_a", "wettbewerb_b", "start"}]
- akzeptierte_hinweise: Kennungen bewusst bestaetigter Warnungen
- vorlage: Herkunft ({"quelle", "name"}); abgleich: offener Vorlagenabgleich
- ziel: "veranstaltung" oder "vorlage:<id>" (Vorlage bearbeiten)

Ein Wettbewerb im Assistenten ist "Sportart fuer einen Jahrgang" bzw. fuer
mehrere zusammengelegte Jahrgaenge. Technisch entsteht daraus je
Teilmannschaft (z. B. Tischtennis Jungen/Maedchen) ein Datensatz in
competitions mit expliziter Teamliste (competition_teams)."""
import copy
import json
from datetime import datetime

from app.services.competition_name_service import freier_competition_name
from app.services.jahrgangsfinale_service import create_jahrgangsfinale, parse_final_source_ids
from app.services.programmpunkte_service import create_programmpunkt, list_programmpunkte
from app.services.schedule_conflict_service import (
    find_existing_schedule_conflicts,
    find_internal_conflicts,
)
from app.services.schedule_location_service import (
    COMPETITION_LOCATIONS,
    DEFAULT_COMPETITION_LOCATION,
    NO_SLOT_LOCATION,
    get_court_location,
    natural_sort_key,
    normalize_competition_location,
)
from app.services.schedule_time_service import (
    DEFAULT_CHANGEOVER_DURATION_MINUTES,
    DEFAULT_GAME_DURATION_MINUTES,
)
from app.services.schulpokal_vorlage_service import (
    DEFAULT_SCHULPOKAL_BLOECKE,
    SCHULPOKAL_EVENT_TYPE,
    parse_teilmannschaften,
)
from app.services.tiebreak_service import format_tiebreak_order, parse_tiebreak_order
from app.utils.formatting import jahrgang_sort_key

SCHEMA_VERSION = 1
SCHRITTE = (
    ("grunddaten", "Grunddaten"),
    ("sportarten", "Sportarten"),
    ("orte", "Orte"),
    ("zeitplan", "Zeitplan"),
    ("vorschau", "Vorschau"),
    ("abschluss", "Abschluss"),
)
# Fruehere Schritte, die in einem anderen aufgegangen sind (alte Links und
# gespeicherte Entwuerfe): „Wertung“ steht seit 2026-11-12 im Abschluss.
SCHRITT_ALIASE = {"wertung": "abschluss"}
SCHRITT_KEYS = tuple(key for key, _ in SCHRITTE)
ABLAEUFE = {
    "abwechselnd": "Wettbewerbe abwechselnd",
    "gleichzeitig": "Gleichzeitig auf getrennten Feldern",
}
PROGRAMMPUNKT_ARTEN = {
    "eroeffnung": "Eröffnung",
    "pause": "Pause",
    "puffer": "Puffer",
    "siegerehrung": "Siegerehrung",
    "sonstiges": "Programmpunkt",
}
WETTBEWERB_ARTEN = ("Turnier", "Sechskampf")
SYSTEM_VORLAGE_SCHULPOKAL = "system:schulpokal"
DEFAULT_EVENT_TYPE = "Sonstiges"
DEFAULT_START = "08:00"
DEFAULT_ENDE = "13:00"


def jetzt():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Hilfen
# ---------------------------------------------------------------------------

def jahrgang_text(value):
    text = str(value if value is not None else "").strip()
    return text


def jahrgang_sortierung(value):
    text = jahrgang_text(value)
    return jahrgang_sort_key(int(text) if text.isdigit() else text)


def sortiere_jahrgaenge(values):
    result = []
    for value in values or []:
        text = jahrgang_text(value)
        if text and text not in result:
            result.append(text)
    return sorted(result, key=jahrgang_sortierung)


def wettbewerb_key(sportart, jahrgaenge):
    return f"{sportart}|{'+'.join(sortiere_jahrgaenge(jahrgaenge))}"


def wettbewerb_name(sportart, jahrgaenge, teilmannschaft=None):
    label = f"{sportart} {teilmannschaft}" if teilmannschaft else sportart
    jahrgaenge = sortiere_jahrgaenge(jahrgaenge)
    return f"{label} Jahrgang {'/'.join(jahrgaenge)}"


def uhrzeit(value):
    """'8:05' / '08:05:00' -> '08:05'; ungueltig -> None."""
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        return None
    return f"{hours:02d}:{minutes:02d}"


def minuten(value):
    clock = uhrzeit(value)
    if clock is None:
        return None
    hours, mins = clock.split(":")
    return int(hours) * 60 + int(mins)


def uhr(total):
    return f"{total // 60:02d}:{total % 60:02d}"


def zahl(value, default, minimum=0, maximum=None):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return maximum
    return parsed


def naechste_id(items, prefix):
    used = {str(item.get("id")) for item in items}
    index = 1
    while f"{prefix}{index}" in used:
        index += 1
    return f"{prefix}{index}"


def ist_schulpokal(daten):
    return (daten.get("grunddaten") or {}).get("event_type") == SCHULPOKAL_EVENT_TYPE


def neue_daten():
    return {
        "schema": SCHEMA_VERSION,
        "modus": "schnell",
        "ziel": "veranstaltung",
        "vorlage": None,
        "abgleich": None,
        "grunddaten": {
            "name": "",
            "datum": "",
            "event_type": DEFAULT_EVENT_TYPE,
            "start": DEFAULT_START,
            "ende": DEFAULT_ENDE,
        },
        "jahrgaenge": [],
        "teams_abgewaehlt": [],
        "sportarten": [],
        "matrix": {},
        "zusammenlegungen": [],
        "wettbewerbe": {},
        "orte": {},
        "zeitbloecke": [],
        "programmpunkte": [],
        "showspiele": [],
        "akzeptierte_hinweise": [],
    }


def vervollstaendigen(daten):
    """Fehlende Schluessel (aeltere Entwuerfe/Vorlagen) mit Standardwerten."""
    basis = neue_daten()
    for key, value in basis.items():
        if key not in daten or daten[key] is None and value is not None:
            daten[key] = copy.deepcopy(value)
    for key, value in basis["grunddaten"].items():
        daten["grunddaten"].setdefault(key, value)
    return daten


# ---------------------------------------------------------------------------
# Stammdaten
# ---------------------------------------------------------------------------

def lade_stammdaten(conn):
    teams = [
        dict(row) for row in conn.execute(
            "SELECT id, name, jahrgang, active FROM teams WHERE active = 1 ORDER BY name"
        ).fetchall()
    ]
    courts = [
        dict(row) for row in conn.execute(
            "SELECT id, name, sportart, location, active FROM courts ORDER BY name"
        ).fetchall()
    ]
    for court in courts:
        court["ort"] = get_court_location(court)
    courts.sort(key=lambda court: natural_sort_key(court["name"]))
    return {"teams": teams, "courts": courts}


def teams_nach_jahrgang(stamm):
    result = {}
    for team in stamm["teams"]:
        result.setdefault(jahrgang_text(team["jahrgang"]), []).append(team)
    return result


def verfuegbare_jahrgaenge(stamm):
    return sortiere_jahrgaenge(teams_nach_jahrgang(stamm).keys())


def felder_am_ort(stamm, ort, nur_aktive=True):
    return [
        court for court in stamm["courts"]
        if court["ort"] == ort and (court["active"] or not nur_aktive)
    ]


def planbare_orte():
    return [ort for ort in COMPETITION_LOCATIONS if ort != NO_SLOT_LOCATION]


def standard_ort(stamm, sportart):
    key = str(sportart or "").strip().casefold()
    for court in stamm["courts"]:
        if court["active"] and str(court.get("sportart") or "").strip().casefold() == key:
            if court["ort"] in planbare_orte():
                return court["ort"]
    return DEFAULT_COMPETITION_LOCATION


def ort_einstellung(daten, stamm, ort):
    """Ort im Entwurf: aktiv und erlaubte Felder (Standard: alle aktiven
    Felder des Ortes)."""
    eintrag = (daten.get("orte") or {}).get(ort) or {}
    aktive_ids = [court["id"] for court in felder_am_ort(stamm, ort)]
    felder = eintrag.get("felder")
    if felder is None:
        felder = aktive_ids
    else:
        felder = [court_id for court_id in felder if court_id in aktive_ids]
    return {
        "aktiv": eintrag.get("aktiv", True) and bool(aktive_ids),
        "aktiv_gewaehlt": eintrag.get("aktiv", True),
        "felder": felder,
        "hat_aktive_felder": bool(aktive_ids),
    }


# ---------------------------------------------------------------------------
# Wettbewerbe aus Matrix und Zusammenlegungen ableiten
# ---------------------------------------------------------------------------

def standard_einstellungen(daten, stamm, sportart):
    schulpokal = ist_schulpokal(daten)
    return {
        "ort": standard_ort(stamm, sportart),
        "felder": [],
        "modus": None,
        "modus_quelle": None,
        "spiele_pro_team": 2,
        "teilmannschaften": "",
        "punkte_sieg": 2 if schulpokal else 3,
        "punkte_unentschieden": 1,
        "tiebreak": None,
        "art": "Turnier",
        "disziplinen": "",
    }


def wettbewerb_einstellungen(daten, stamm, key, sportart):
    result = standard_einstellungen(daten, stamm, sportart)
    for name, value in ((daten.get("wettbewerbe") or {}).get(key) or {}).items():
        if value is not None or name in ("modus", "modus_quelle", "tiebreak"):
            result[name] = value
    if result["ort"] not in COMPETITION_LOCATIONS:
        result["ort"] = normalize_competition_location(result["ort"]) or DEFAULT_COMPETITION_LOCATION
    if result.get("art") not in WETTBEWERB_ARTEN:
        result["art"] = "Turnier"
    return result


def aktive_jahrgaenge(daten):
    return sortiere_jahrgaenge(daten.get("jahrgaenge") or [])


def aktive_sportarten(daten):
    result = []
    for sportart in daten.get("sportarten") or []:
        sportart = str(sportart).strip()
        if sportart and sportart.casefold() not in {s.casefold() for s in result}:
            result.append(sportart)
    return result


def gewaehlte_teams(daten, stamm, jahrgaenge):
    abgewaehlt = {int(t) for t in daten.get("teams_abgewaehlt") or [] if str(t).isdigit()}
    by_jahrgang = teams_nach_jahrgang(stamm)
    teams = []
    for jahrgang in sortiere_jahrgaenge(jahrgaenge):
        for team in by_jahrgang.get(jahrgang, []):
            if team["id"] not in abgewaehlt:
                teams.append(team)
    return teams


def ableiten_wettbewerbe(daten, stamm):
    """Liste der anzulegenden Wettbewerbe aus Matrix und Zusammenlegungen.
    Nur ausgewaehlte Jahrgaenge und Sportarten zaehlen; was abgewaehlt ist,
    bleibt in den Entwurfsdaten erhalten (Zurueck verliert nichts)."""
    jahrgaenge = set(aktive_jahrgaenge(daten))
    result = []
    for sportart in aktive_sportarten(daten):
        gewaehlt = [
            j for j in sortiere_jahrgaenge((daten.get("matrix") or {}).get(sportart) or [])
            if j in jahrgaenge
        ]
        uebrig = list(gewaehlt)
        gruppen = []
        for merge in daten.get("zusammenlegungen") or []:
            if merge.get("sportart") != sportart:
                continue
            members = [j for j in sortiere_jahrgaenge(merge.get("jahrgaenge")) if j in uebrig]
            if len(members) >= 2:
                gruppen.append(members)
                uebrig = [j for j in uebrig if j not in members]
        gruppen.extend([j] for j in uebrig)
        gruppen.sort(key=lambda members: jahrgang_sortierung(members[0]))
        for members in gruppen:
            key = wettbewerb_key(sportart, members)
            einstellungen = wettbewerb_einstellungen(daten, stamm, key, sportart)
            teilmannschaften = parse_teilmannschaften(einstellungen.get("teilmannschaften")) or [None]
            result.append({
                "key": key,
                "sportart": sportart,
                "jahrgaenge": members,
                "zusammengelegt": len(members) > 1,
                "name": wettbewerb_name(sportart, members),
                "teams": gewaehlte_teams(daten, stamm, members),
                "einstellungen": einstellungen,
                "teilmannschaften": teilmannschaften,
            })
    return result


def zusammenlegen(daten, sportart, keys):
    """Legt die gewaehlten Wettbewerbe einer Sportart zu einem zusammen
    (gemeinsame Begegnungen und Wertung)."""
    members = []
    for key in keys:
        key_sport, _, jahrgaenge = str(key).partition("|")
        if key_sport != sportart:
            raise ValueError("Zusammenlegen geht nur innerhalb derselben Sportart.")
        members.extend(jahrgaenge.split("+"))
    members = sortiere_jahrgaenge(members)
    if len(members) < 2:
        raise ValueError("Bitte mindestens zwei Wettbewerbe derselben Sportart auswählen.")
    merges = [
        merge for merge in daten.get("zusammenlegungen") or []
        if not (merge.get("sportart") == sportart and set(merge.get("jahrgaenge") or []) & set(members))
    ]
    merges.append({"sportart": sportart, "jahrgaenge": members})
    daten["zusammenlegungen"] = merges
    neuer_key = wettbewerb_key(sportart, members)
    einstellungen = daten.setdefault("wettbewerbe", {})
    if neuer_key not in einstellungen:
        for key in keys:
            if key in einstellungen:
                einstellungen[neuer_key] = copy.deepcopy(einstellungen[key])
                break
    _ersetze_in_bloecken(daten, list(keys), [neuer_key])
    return neuer_key


def trennen(daten, key):
    sportart, _, jahrgaenge = str(key).partition("|")
    members = sortiere_jahrgaenge(jahrgaenge.split("+"))
    daten["zusammenlegungen"] = [
        merge for merge in daten.get("zusammenlegungen") or []
        if not (merge.get("sportart") == sportart
                and sortiere_jahrgaenge(merge.get("jahrgaenge")) == members)
    ]
    einzel = [wettbewerb_key(sportart, [j]) for j in members]
    einstellungen = daten.setdefault("wettbewerbe", {})
    for neuer in einzel:
        if neuer not in einstellungen and key in einstellungen:
            einstellungen[neuer] = copy.deepcopy(einstellungen[key])
    _ersetze_in_bloecken(daten, [key], einzel)
    return einzel


def _ersetze_in_bloecken(daten, alte, neue):
    for block in daten.get("zeitbloecke") or []:
        keys = block.get("wettbewerbe") or []
        if any(key in keys for key in alte):
            rest = [key for key in keys if key not in alte]
            block["wettbewerbe"] = rest + [key for key in neue if key not in rest]


# ---------------------------------------------------------------------------
# Vorlagen
# ---------------------------------------------------------------------------

VORLAGEN_SCHLUESSEL = (
    "modus", "grunddaten", "jahrgaenge", "sportarten", "matrix", "zusammenlegungen",
    "wettbewerbe", "orte", "zeitbloecke", "programmpunkte", "showspiele",
)


def system_vorlage_schulpokal():
    """Die bisherige Vorlage Schulpokal (DEFAULT_SCHULPOKAL_BLOECKE) im neuen
    Modell: Bloecke, Jahrgangsfinals als Showspiele, Eroeffnung und
    Siegerehrungen als Programmpunkte."""
    daten = neue_daten()
    daten["grunddaten"].update({"event_type": SCHULPOKAL_EVENT_TYPE, "start": "08:00", "ende": "14:00"})
    jahrgaenge = []
    siegerehrungen = {}
    for index, block in enumerate(DEFAULT_SCHULPOKAL_BLOECKE, start=1):
        sportart = block["sportart"]
        members = sortiere_jahrgaenge(part.strip() for part in block["jahrgaenge"].split(","))
        jahrgaenge.extend(members)
        daten["sportarten"].append(sportart)
        daten["matrix"][sportart] = members
        teilmannschaften = block.get("teilmannschaften") or ""
        keys = []
        for jahrgang in members:
            key = wettbewerb_key(sportart, [jahrgang])
            keys.append(key)
            daten["wettbewerbe"][key] = {
                "ort": block["location"],
                "modus": "jeder_gegen_jeden",
                "modus_quelle": "vorlage",
                "teilmannschaften": teilmannschaften,
                "punkte_sieg": block["points_win"],
                "punkte_unentschieden": block["points_draw"],
            }
        daten["zeitbloecke"].append({
            "id": f"b{index}",
            "ort": block["location"],
            "start": block["start_time"],
            "ende": block["end_time"],
            "wettbewerbe": keys,
            "ablauf": "gleichzeitig" if teilmannschaften else "abwechselnd",
            "spielzeit": DEFAULT_GAME_DURATION_MINUTES,
            "wechselzeit": DEFAULT_CHANGEOVER_DURATION_MINUTES,
        })
        if block.get("finale_time") and len(keys) == 2 and not teilmannschaften:
            daten["showspiele"].append({
                "id": f"s{len(daten['showspiele']) + 1}",
                "wettbewerb_a": keys[0],
                "wettbewerb_b": keys[1],
                "start": block["finale_time"],
            })
        ceremony = siegerehrungen.setdefault(block["end_time"], {"jahrgaenge": [], "finale": []})
        ceremony["jahrgaenge"] = sortiere_jahrgaenge(ceremony["jahrgaenge"] + members)
        if block.get("finale_time"):
            ceremony["finale"].append(block["finale_time"])
    daten["jahrgaenge"] = sortiere_jahrgaenge(jahrgaenge)
    erste = DEFAULT_SCHULPOKAL_BLOECKE[0]
    daten["programmpunkte"].append({
        "id": "p1", "titel": "Eröffnung", "art": "eroeffnung",
        "start": erste["start_time"], "ende": uhr(minuten(erste["start_time"]) + 10),
        "jahrgaenge": sortiere_jahrgaenge(part.strip() for part in erste["jahrgaenge"].split(",")),
    })
    for end_time, ceremony in sorted(siegerehrungen.items()):
        finals = sorted(t for t in ceremony["finale"] if minuten(t) > minuten(end_time))
        daten["programmpunkte"].append({
            "id": f"p{len(daten['programmpunkte']) + 1}",
            "titel": "Siegerehrung", "art": "siegerehrung",
            "start": end_time,
            "ende": finals[0] if finals else uhr(minuten(end_time) + 15),
            "jahrgaenge": ceremony["jahrgaenge"],
        })
    return vorlage_aus_daten(daten, None)


def vorlage_aus_daten(daten, stamm):
    """Vorlage = Parameter und Regeln ohne konkrete ids: Felder werden als
    Namen gespeichert, Klassen gar nicht (nur Jahrgaenge), ebenso weder
    Datum noch Name noch bestaetigte Hinweise."""
    court_names = {court["id"]: court["name"] for court in (stamm or {}).get("courts", [])}
    vorlage = {key: copy.deepcopy(daten.get(key)) for key in VORLAGEN_SCHLUESSEL}
    vorlage["schema"] = SCHEMA_VERSION
    grunddaten = vorlage.get("grunddaten") or {}
    grunddaten.pop("datum", None)
    grunddaten.pop("name", None)
    for einstellung in (vorlage.get("wettbewerbe") or {}).values():
        felder = einstellung.get("felder") or []
        einstellung["felder_namen"] = [court_names[i] for i in felder if i in court_names]
        einstellung.pop("felder", None)
    for ort, eintrag in (vorlage.get("orte") or {}).items():
        felder = eintrag.get("felder")
        if felder is not None:
            eintrag["felder_namen"] = [court_names[i] for i in felder if i in court_names]
        eintrag.pop("felder", None)
    return vorlage


def entwurf_aus_vorlage(vorlage, stamm, quelle, name):
    """Kopie der Vorlage als Entwurf. Feldnamen werden auf die heute aktiven
    Felder abgebildet; was fehlt, landet im Abgleich, statt stillschweigend
    zugeordnet zu werden. Die Vorlage selbst bleibt unveraendert."""
    daten = vervollstaendigen({key: copy.deepcopy(vorlage.get(key)) for key in VORLAGEN_SCHLUESSEL})
    by_name = {}
    for court in stamm["courts"]:
        if court["active"]:
            by_name.setdefault((court["ort"], court["name"]), court["id"])
    fehlende_felder = {}
    for key, einstellung in (daten.get("wettbewerbe") or {}).items():
        namen = einstellung.pop("felder_namen", None) or []
        ort = einstellung.get("ort") or DEFAULT_COMPETITION_LOCATION
        einstellung["felder"] = [by_name[(ort, n)] for n in namen if (ort, n) in by_name]
        for name_ in namen:
            if (ort, name_) not in by_name:
                fehlende_felder.setdefault(ort, [])
                if name_ not in fehlende_felder[ort]:
                    fehlende_felder[ort].append(name_)
        if einstellung.get("modus"):
            einstellung["modus_quelle"] = "vorlage"
    for ort, eintrag in (daten.get("orte") or {}).items():
        namen = eintrag.pop("felder_namen", None)
        if namen is not None:
            eintrag["felder"] = [by_name[(ort, n)] for n in namen if (ort, n) in by_name]
            for name_ in namen:
                if (ort, name_) not in by_name:
                    fehlende_felder.setdefault(ort, [])
                    if name_ not in fehlende_felder[ort]:
                        fehlende_felder[ort].append(name_)
    vorhandene = set(verfuegbare_jahrgaenge(stamm))
    genutzte_orte = {
        (daten["wettbewerbe"].get(key) or {}).get("ort") or DEFAULT_COMPETITION_LOCATION
        for key in daten["wettbewerbe"]
    } | {block.get("ort") for block in daten.get("zeitbloecke") or []}
    daten["vorlage"] = {"quelle": quelle, "name": name}
    daten["abgleich"] = {
        "offen": True,
        "fehlende_jahrgaenge": [j for j in daten.get("jahrgaenge") or [] if j not in vorhandene],
        "inaktive_orte": sorted(
            ort for ort in genutzte_orte if ort and not felder_am_ort(stamm, ort)
        ),
        "fehlende_felder": fehlende_felder,
    }
    daten["grunddaten"]["name"] = ""
    daten["grunddaten"]["datum"] = ""
    daten["akzeptierte_hinweise"] = []
    daten["teams_abgewaehlt"] = []
    return daten


def _daten_aus_veranstaltung(conn, event_id):
    """Liest eine bestehende Veranstaltung in Entwurfsdaten zurueck.
    Gibt (event, daten, bestand) zurueck; bestand ordnet Eintrags-ids
    ("Schluessel#Teilmannschaft") und Showspiel-ids den competition-ids zu.
    Feldnamen stehen noch als felder_namen in den Wettbewerbseinstellungen."""
    event = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        raise ValueError("Veranstaltung nicht gefunden.")
    bestand = {"event_id": event["id"], "wettbewerbe": {}, "showspiele": {}}
    daten = neue_daten()
    daten["grunddaten"]["event_type"] = event["event_type"] or DEFAULT_EVENT_TYPE
    competitions = conn.execute(
        """
        SELECT * FROM competitions
        WHERE event_id = ? AND COALESCE(status, '') != 'archiviert'
        ORDER BY start_time, sportart, jahrgang, name
        """,
        (event_id,),
    ).fetchall()
    explicit = {}
    for row in conn.execute(
        """
        SELECT ct.competition_id, t.jahrgang FROM competition_teams ct
        JOIN teams t ON t.id = ct.team_id
        """
    ).fetchall():
        explicit.setdefault(row["competition_id"], set()).add(jahrgang_text(row["jahrgang"]))
    court_names = {row["id"]: row["name"] for row in conn.execute("SELECT id, name FROM courts")}
    key_by_competition = {}
    bloecke = {}
    ort_felder = {}
    for competition in competitions:
        if (competition["final_source_ids"] or "").strip():
            continue
        sportart = competition["sportart"]
        members = sortiere_jahrgaenge(
            explicit.get(competition["id"]) or [jahrgang_text(competition["jahrgang"])]
        )
        members = [j for j in members if j and j != "mixed"]
        if not members:
            continue
        key = wettbewerb_key(sportart, members)
        key_by_competition[competition["id"]] = key
        bestand["wettbewerbe"][f'{key}#{competition["teilmannschaft"] or ""}'] = competition["id"]
        if sportart not in daten["sportarten"]:
            daten["sportarten"].append(sportart)
        matrix = daten["matrix"].setdefault(sportart, [])
        for jahrgang in members:
            if jahrgang not in matrix:
                matrix.append(jahrgang)
            if jahrgang not in daten["jahrgaenge"]:
                daten["jahrgaenge"].append(jahrgang)
        if len(members) > 1 and not any(
            m["sportart"] == sportart and m["jahrgaenge"] == members for m in daten["zusammenlegungen"]
        ):
            daten["zusammenlegungen"].append({"sportart": sportart, "jahrgaenge": members})
        einstellung = daten["wettbewerbe"].setdefault(key, {
            "ort": normalize_competition_location(competition["location"]) or DEFAULT_COMPETITION_LOCATION,
            "modus": _wizard_modus_aus_db(competition),
            "modus_quelle": "vorlage",
            "teilmannschaften": "",
            "punkte_sieg": competition["points_win"],
            "punkte_unentschieden": competition["points_draw"],
            "tiebreak": parse_tiebreak_order(competition["tiebreak_order"]),
            "art": "Sechskampf" if competition["competition_type"] == "Sechskampf" else "Turnier",
            "disziplinen": "",
        })
        if competition["teilmannschaft"]:
            teile = parse_teilmannschaften(einstellung.get("teilmannschaften"))
            if competition["teilmannschaft"] not in teile:
                teile.append(competition["teilmannschaft"])
            einstellung["teilmannschaften"] = ", ".join(teile)
        if competition["competition_type"] == "Sechskampf":
            disziplinen = conn.execute(
                "SELECT name FROM competition_disciplines WHERE competition_id = ? ORDER BY sort_order, id",
                (competition["id"],),
            ).fetchall()
            einstellung["disziplinen"] = ", ".join(row["name"] for row in disziplinen)
        felder = {
            court_names.get(row["court_id"]) for row in conn.execute(
                "SELECT DISTINCT court_id FROM slots WHERE competition_id = ? AND court_id IS NOT NULL",
                (competition["id"],),
            ).fetchall()
        } - {None}
        if felder:
            # Teilmannschaften teilen sich einen Schlüssel: Felder vereinigen.
            felder |= set(einstellung.get("felder_namen") or [])
            einstellung["felder_namen"] = sorted(felder, key=natural_sort_key)
            ort_felder.setdefault(einstellung["ort"], set()).update(felder)
        start, ende = uhrzeit(competition["start_time"]), uhrzeit(competition["end_time"])
        if start and ende:
            ort = einstellung["ort"]
            block = bloecke.setdefault((ort, start, ende), {
                "id": f"b{len(bloecke) + 1}", "ort": ort, "start": start, "ende": ende,
                "wettbewerbe": [], "ablauf": "abwechselnd",
                "spielzeit": competition["game_duration_minutes"] or DEFAULT_GAME_DURATION_MINUTES,
                "wechselzeit": competition["changeover_duration_minutes"],
            })
            if key not in block["wettbewerbe"]:
                block["wettbewerbe"].append(key)
            if competition["teilmannschaft"]:
                block["ablauf"] = "gleichzeitig"
    daten["jahrgaenge"] = sortiere_jahrgaenge(daten["jahrgaenge"])
    daten["zeitbloecke"] = list(bloecke.values())
    # Damit der Plan beim erneuten Rechnen derselbe wird: Ort = genutzte
    # Felder; eigene Felder je Wettbewerb nur, wo sie davon abweichen. Bei
    # „gleichzeitig“ teilt der Planungskern die Felder wieder selbst auf.
    for ort, namen in ort_felder.items():
        daten["orte"][ort] = {"aktiv": True, "felder_namen": sorted(namen, key=natural_sort_key)}
    gleichzeitig = {
        key for block in daten["zeitbloecke"] if block["ablauf"] == "gleichzeitig" for key in block["wettbewerbe"]
    }
    for key, einstellung in daten["wettbewerbe"].items():
        namen = einstellung.get("felder_namen")
        if namen is not None and (
            key in gleichzeitig or set(namen) == ort_felder.get(einstellung["ort"], set())
        ):
            einstellung.pop("felder_namen")
    starts = [minuten(b["start"]) for b in daten["zeitbloecke"]]
    ends = [minuten(b["ende"]) for b in daten["zeitbloecke"]]
    for punkt in list_programmpunkte(conn, event_id):
        daten["programmpunkte"].append({
            "id": f"p{len(daten['programmpunkte']) + 1}",
            "titel": punkt["titel"],
            "art": _programm_art(punkt["titel"]),
            "start": punkt["start_time"], "ende": punkt["end_time"],
            "jahrgaenge": punkt["jahrgaenge_liste"],
        })
        starts.append(minuten(punkt["start_time"]))
        ends.append(minuten(punkt["end_time"]))
    for competition in competitions:
        sources = parse_final_source_ids(competition["final_source_ids"])
        if len(sources) == 2 and all(s in key_by_competition for s in sources):
            show_id = f"s{len(daten['showspiele']) + 1}"
            bestand["showspiele"][show_id] = competition["id"]
            daten["showspiele"].append({
                "id": show_id,
                "wettbewerb_a": key_by_competition[sources[0]],
                "wettbewerb_b": key_by_competition[sources[1]],
                "start": uhrzeit(competition["start_time"]) or "",
            })
    starts = [s for s in starts if s is not None]
    ends = [e for e in ends if e is not None]
    if starts:
        daten["grunddaten"]["start"] = uhr(min(starts))
    if ends:
        daten["grunddaten"]["ende"] = uhr(max(ends))
    return event, daten, bestand


def vorlage_aus_veranstaltung(conn, event_id):
    """Macht eine bestehende (fertig geplante) Veranstaltung als Vorlage
    wiederverwendbar: Sportarten, Jahrgaenge, Zusammenlegungen, Orte,
    Zeitbloecke, Modi, Punkte, Programmpunkte und Showspiele."""
    _event, daten, _bestand = _daten_aus_veranstaltung(conn, event_id)
    stamm = {"courts": []}
    vorlage = vorlage_aus_daten(daten, stamm)
    for key, einstellung in daten["wettbewerbe"].items():
        if "felder_namen" in einstellung:
            vorlage["wettbewerbe"][key]["felder_namen"] = einstellung["felder_namen"]
    return vorlage


def entwurf_aus_vorjahr(conn, event_id, stamm):
    """„Vorjahr übernehmen“: neue Veranstaltung nach dem Muster einer
    bestehenden. Wie ein Start aus einer Vorlage, nur ohne Umweg über das
    Speichern einer Vorlage; Name, Datum und Klassen kommen in den Abgleich."""
    event = conn.execute("SELECT name FROM events WHERE id = ?", (event_id,)).fetchone()
    if event is None:
        raise ValueError("Veranstaltung nicht gefunden.")
    vorlage = vorlage_aus_veranstaltung(conn, event_id)
    return entwurf_aus_vorlage(vorlage, stamm, f"veranstaltung:{event_id}", event["name"])


def entwurf_aus_veranstaltung(conn, event_id, stamm):
    """Bestehende Veranstaltung im Assistenten bearbeiten. Der Entwurf
    behält Name, Datum und Klassenauswahl und merkt sich in daten["bestand"],
    welche Wettbewerbe schon existieren. Übernehmen ändert dann diese
    Veranstaltung, statt eine neue anzulegen (siehe uebernehmen)."""
    event, roh, bestand = _daten_aus_veranstaltung(conn, event_id)
    vorlage = vorlage_aus_daten(roh, {"courts": []})
    for key, einstellung in roh["wettbewerbe"].items():
        if "felder_namen" in einstellung:
            vorlage["wettbewerbe"][key]["felder_namen"] = einstellung["felder_namen"]
    daten = entwurf_aus_vorlage(vorlage, stamm, f"bearbeiten:{event_id}", event["name"])
    daten["grunddaten"]["name"] = event["name"] or ""
    daten["grunddaten"]["datum"] = event["event_date"] or ""
    daten["grunddaten"]["event_type"] = event["event_type"] or DEFAULT_EVENT_TYPE
    for einstellung in daten["wettbewerbe"].values():
        if einstellung.get("modus_quelle") == "vorlage":
            einstellung["modus_quelle"] = "bestand"
    # Klassen, die in keinem expliziten Wettbewerb mitspielen, bleiben abgewählt.
    explizit = {
        row["team_id"] for row in conn.execute(
            """
            SELECT ct.team_id FROM competition_teams ct
            JOIN competitions c ON c.id = ct.competition_id
            WHERE c.event_id = ? AND COALESCE(c.status, '') != 'archiviert'
            """,
            (event_id,),
        ).fetchall()
    }
    if explizit:
        gewaehlt = set(daten.get("jahrgaenge") or [])
        daten["teams_abgewaehlt"] = sorted(
            team["id"] for team in stamm["teams"]
            if jahrgang_text(team["jahrgang"]) in gewaehlt and team["id"] not in explizit
        )
    abgleich = daten.get("abgleich") or {}
    offen = bool(
        abgleich.get("fehlende_jahrgaenge") or abgleich.get("inaktive_orte") or abgleich.get("fehlende_felder")
    )
    daten["abgleich"] = dict(abgleich, offen=offen) if offen else None
    daten["vorlage"] = None
    daten["bestand"] = bestand
    return daten


def bestand_event_id(daten):
    bestand = daten.get("bestand") or {}
    return bestand.get("event_id")


def offener_entwurf_fuer(conn, event_id):
    """Offener Bearbeiten-Entwurf zu einer Veranstaltung (sonst None), damit
    ein zweiter Klick denselben Entwurf öffnet statt einen neuen."""
    for row in conn.execute(
        "SELECT id, daten FROM assistent_entwuerfe WHERE status = 'offen' ORDER BY id DESC"
    ).fetchall():
        try:
            if bestand_event_id(json.loads(row["daten"])) == event_id:
                return row["id"]
        except (TypeError, ValueError):
            continue
    return None


def _programm_art(titel):
    text = str(titel or "").casefold()
    if "eröffnung" in text or "eroeffnung" in text:
        return "eroeffnung"
    if "siegerehrung" in text:
        return "siegerehrung"
    if "puffer" in text:
        return "puffer"
    if "pause" in text:
        return "pause"
    return "sonstiges"


def _wizard_modus_aus_db(competition):
    mode = competition["tournament_mode"]
    if competition["competition_type"] == "Schulpokal":
        return mode if mode in ("jeder_gegen_jeden", "hin_rueckrunde") else "jeder_gegen_jeden"
    if competition["competition_type"] == "Turnier":
        return {"punkterunde": "jeder_gegen_jeden", "ko_runde": "ko_runde"}.get(mode, "gruppenphase_ko")
    return None


def liste_vorlagen(conn):
    eigene = [
        dict(row) for row in conn.execute(
            "SELECT id, name, created_at, updated_at FROM assistent_vorlagen ORDER BY name"
        ).fetchall()
    ]
    return {
        "system": [{"key": SYSTEM_VORLAGE_SCHULPOKAL, "name": "Schulpokal (Systemvorlage)"}],
        "eigene": eigene,
    }


def lade_vorlage(conn, key):
    """key: 'system:schulpokal' oder 'vorlage:<id>' -> (daten, name)."""
    if key == SYSTEM_VORLAGE_SCHULPOKAL:
        return system_vorlage_schulpokal(), "Schulpokal"
    prefix, _, raw_id = str(key).partition(":")
    if prefix == "vorlage" and raw_id.isdigit():
        row = conn.execute("SELECT * FROM assistent_vorlagen WHERE id = ?", (int(raw_id),)).fetchone()
        if row is not None:
            return json.loads(row["daten"]), row["name"]
    raise ValueError("Vorlage nicht gefunden.")


def speichere_vorlage(conn, name, vorlage, vorlage_id=None):
    name = (name or "").strip()
    if not name:
        raise ValueError("Bitte einen Namen für die Vorlage angeben.")
    clash = conn.execute(
        "SELECT id FROM assistent_vorlagen WHERE name = ?", (name,)
    ).fetchone()
    if clash is not None and clash["id"] != vorlage_id:
        raise ValueError("Eine Vorlage mit diesem Namen gibt es schon.")
    payload = json.dumps(vorlage, ensure_ascii=False)
    if vorlage_id is None:
        return conn.execute(
            "INSERT INTO assistent_vorlagen (name, daten, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, payload, jetzt(), jetzt()),
        ).lastrowid
    conn.execute(
        "UPDATE assistent_vorlagen SET name = ?, daten = ?, updated_at = ? WHERE id = ?",
        (name, payload, jetzt(), vorlage_id),
    )
    return vorlage_id


# ---------------------------------------------------------------------------
# Entwuerfe speichern
# ---------------------------------------------------------------------------

def titel_fuer(daten):
    name = (daten.get("grunddaten") or {}).get("name") or ""
    if bestand_event_id(daten):
        return f"{name.strip() or 'Veranstaltung'} (bearbeiten)"
    if name.strip():
        return name.strip()
    vorlage = daten.get("vorlage") or {}
    if vorlage.get("name"):
        return f"Neue Veranstaltung aus Vorlage {vorlage['name']}"
    return "Neue Veranstaltung"


def erstelle_entwurf(conn, daten):
    daten = vervollstaendigen(daten)
    return conn.execute(
        """
        INSERT INTO assistent_entwuerfe (titel, daten, status, version, created_at, updated_at)
        VALUES (?, ?, 'offen', 1, ?, ?)
        """,
        (titel_fuer(daten), json.dumps(daten, ensure_ascii=False), jetzt(), jetzt()),
    ).lastrowid


def lade_entwurf(conn, entwurf_id):
    row = conn.execute("SELECT * FROM assistent_entwuerfe WHERE id = ?", (entwurf_id,)).fetchone()
    if row is None:
        return None
    entwurf = dict(row)
    entwurf["daten"] = vervollstaendigen(json.loads(row["daten"]))
    return entwurf


def speichere_entwurf(conn, entwurf_id, daten):
    """Speichert die Entwurfsdaten und erhoeht die Version (Grundlage der
    Aenderungspruefung beim Uebernehmen)."""
    conn.execute(
        """
        UPDATE assistent_entwuerfe
        SET daten = ?, titel = ?, version = version + 1, updated_at = ?
        WHERE id = ? AND status = 'offen'
        """,
        (json.dumps(daten, ensure_ascii=False), titel_fuer(daten), jetzt(), entwurf_id),
    )


def offene_entwuerfe(conn):
    """Offene Entwürfe, neueste zuerst; bestand_event_id ist bei einem
    Bearbeiten-Entwurf die Veranstaltung, sonst None."""
    result = []
    for row in conn.execute(
        """
        SELECT id, titel, updated_at, version, daten FROM assistent_entwuerfe
        WHERE status = 'offen' ORDER BY updated_at DESC, id DESC
        """
    ).fetchall():
        eintrag = dict(row)
        try:
            eintrag["bestand_event_id"] = bestand_event_id(json.loads(eintrag.pop("daten")))
        except (TypeError, ValueError):
            eintrag["bestand_event_id"] = None
        result.append(eintrag)
    return result


# ---------------------------------------------------------------------------
# Uebernehmen
# ---------------------------------------------------------------------------

def db_typ_und_modus(daten, modus):
    """Wizard-Modus -> (competition_type, tournament_mode). "Jeder gegen
    jeden" ist beim Schulpokal ein Schulpokal-Wettbewerb (Wertung je
    Jahrgang), sonst eine Punkterunde; Hin- und Rueckrunde gibt es nur als
    Schulpokal-Modus."""
    if modus == "hin_rueckrunde":
        return "Schulpokal", "hin_rueckrunde"
    if modus == "jeder_gegen_jeden":
        if ist_schulpokal(daten):
            return "Schulpokal", "jeder_gegen_jeden"
        return "Turnier", "punkterunde"
    if modus == "ko_runde":
        return "Turnier", "ko_runde"
    return "Turnier", "gruppenphase_ko"


def uebernehmen(conn, entwurf_id, erwartete_version, plane, statistik=None):
    """Legt Veranstaltung, Wettbewerbe, Teams, Spiele, Programmpunkte und
    Showspiele in EINER Transaktion an. Aufrufer startet BEGIN IMMEDIATE und
    committet bei Erfolg bzw. rollt bei Fehlern zurueck.

    Gibt (event_id, fehler) zurueck. Ist der Entwurf schon uebernommen,
    kommt die bestehende event_id ohne neue Datensaetze zurueck (kein
    Duplikat bei Doppelklick/Reload)."""
    entwurf = lade_entwurf(conn, entwurf_id)
    if entwurf is None:
        return None, ["Entwurf nicht gefunden."]
    if entwurf["status"] == "uebernommen":
        return entwurf["event_id"], []
    if str(entwurf["version"]) != str(erwartete_version):
        return None, [
            "Der Entwurf wurde inzwischen geändert. Bitte die Vorschau neu prüfen und dann übernehmen."
        ]
    daten = entwurf["daten"]
    stamm = lade_stammdaten(conn)
    plan = plane(conn, daten, stamm)
    if not plan["uebernahme_moeglich"]:
        fehler = [h["titel"] for h in plan["hinweise"] if h["stufe"] == "blockierend"]
        fehler += [
            f'Noch nicht bestätigt: {h["titel"]}'
            for h in plan["hinweise"] if h["stufe"] == "warnung" and not h["bestaetigt"]
        ]
        return None, fehler or ["Der Plan ist noch nicht vollständig."]

    grunddaten = daten["grunddaten"]
    name = grunddaten["name"].strip()
    bestand = plan.get("bestand")
    if bestand:
        event_id = bestand["event_id"]
        conn.execute(
            "UPDATE events SET name = ?, event_date = ?, event_type = ? WHERE id = ?",
            (name, grunddaten.get("datum") or None, grunddaten.get("event_type") or DEFAULT_EVENT_TYPE,
             event_id),
        )
        # Ohne Ergebnisse: entfernte Wettbewerbe verschwinden ganz, wie beim
        # Löschen eines Wettbewerbs. Gesperrte bleiben unberührt; Showspiele
        # werden weiter unten nur ersetzt, wenn sie sich geändert haben.
        for competition_id in bestand["entfernen_ids"]:
            _loesche_wettbewerb(conn, competition_id)
    else:
        event_id = conn.execute(
            """
            INSERT INTO events (name, description, details, event_date, status, event_type)
            VALUES (?, '', '', ?, 'geplant', ?)
            """,
            (name, grunddaten.get("datum") or None, grunddaten.get("event_type") or DEFAULT_EVENT_TYPE),
        ).lastrowid

    zaehler = {"neu": 0, "geaendert": 0, "unveraendert": 0, "gesperrt": 0,
               "geloescht": len((bestand or {}).get("entfernen_ids") or [])}
    unveraendert = set()
    competition_ids = {}
    for eintrag in plan["eintraege"]:
        wb = eintrag["wettbewerb"]
        einstellungen = wb["einstellungen"]
        block = eintrag["block"]
        vorhanden = (bestand or {}).get("wettbewerbe", {}).get(eintrag["id"])
        if eintrag.get("gesperrt"):
            competition_ids[eintrag["id"]] = vorhanden
            zaehler["gesperrt"] += 1
            continue
        if einstellungen["art"] == "Sechskampf":
            competition_type, tournament_mode = "Sechskampf", None
        else:
            competition_type, tournament_mode = db_typ_und_modus(daten, eintrag["modus"])
        jahrgang = wb["jahrgaenge"][0]
        werte = (
            freier_competition_name(conn, eintrag["name"], event_id, ausser_id=vorhanden), wb["sportart"],
            int(jahrgang) if jahrgang.isdigit() else jahrgang,
            einstellungen["punkte_sieg"], einstellungen["punkte_unentschieden"],
            max(len(wb["teams"]), 1), event_id, competition_type, tournament_mode,
            block["spielzeit"], block["wechselzeit"], einstellungen["ort"],
            block["start"], block["ende"], eintrag["teilmannschaft"],
            format_tiebreak_order(einstellungen.get("tiebreak")),
        )
        if vorhanden and _wettbewerb_unveraendert(conn, vorhanden, werte, wb, eintrag, plan["slots"]):
            # Gleicher Plan wie in der Datenbank: nichts anfassen (Spiele
            # behalten ihre ids, eine per „Zeitplan aufholen“ gekürzte
            # Spielzeit bleibt).
            competition_ids[eintrag["id"]] = vorhanden
            unveraendert.add(eintrag["id"])
            zaehler["unveraendert"] += 1
            continue
        if vorhanden:
            # Noch ohne Ergebnisse (sonst gesperrt): Einstellungen
            # überschreiben, Spiele, Klassen und Disziplinen neu anlegen.
            zaehler["geaendert"] += 1
            conn.execute(
                """
                UPDATE competitions SET
                    name = ?, sportart = ?, jahrgang = ?, points_win = ?, points_draw = ?,
                    points_first_place = ?, event_id = ?, competition_type = ?, tournament_mode = ?,
                    game_duration_minutes = ?, game_duration_seconds = NULL,
                    changeover_duration_minutes = ?, location = ?, start_time = ?, end_time = ?,
                    teilmannschaft = ?, tiebreak_order = ?
                WHERE id = ?
                """,
                werte + (vorhanden,),
            )
            for tabelle in ("slots", "competition_teams", "competition_disciplines"):
                conn.execute(f"DELETE FROM {tabelle} WHERE competition_id = ?", (vorhanden,))
            competition_id = vorhanden
        else:
            zaehler["neu"] += 1
            competition_id = conn.execute(
                """
                INSERT INTO competitions (
                    name, sportart, jahrgang, status, points_win, points_draw, points_loss,
                    points_first_place, event_id, competition_type, tournament_mode,
                    game_duration_minutes, changeover_duration_minutes, location,
                    start_time, end_time, teilmannschaft, tiebreak_order
                ) VALUES (?, ?, ?, 'geplant', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                werte,
            ).lastrowid
        competition_ids[eintrag["id"]] = competition_id
        for team in wb["teams"]:
            conn.execute(
                "INSERT OR IGNORE INTO competition_teams (competition_id, team_id) VALUES (?, ?)",
                (competition_id, team["id"]),
            )
        if competition_type == "Sechskampf":
            for index, disziplin in enumerate(eintrag["disziplinen"]):
                conn.execute(
                    "INSERT INTO competition_disciplines (competition_id, name, sort_order) VALUES (?, ?, ?)",
                    (competition_id, disziplin, index),
                )

    neue_slots = []
    for slot in plan["slots"]:
        if slot.get("showspiel") or slot.get("bestehend") or slot["eintrag_id"] in unveraendert:
            continue
        competition_id = competition_ids[slot["eintrag_id"]]
        conn.execute(
            """
            INSERT INTO slots (
                competition_id, court_id, startzeit, slot_typ, phase, gruppe,
                team_a_id, team_b_id, status, note
            ) VALUES (?, ?, ?, 'Spiel', ?, ?, ?, ?, 'geplant', ?)
            """,
            (
                competition_id, slot["court_id"], slot["startzeit"], slot["phase"],
                slot.get("gruppe") or None, slot.get("team_a_id"), slot.get("team_b_id"),
                slot.get("note") or "",
            ),
        )
        neue_slots.append({
            **slot, "competition_id": competition_id, "slot_typ": "Spiel",
            "game_duration_minutes": slot["spielzeit"],
        })

    programm = [
        (punkt.get("titel") or PROGRAMMPUNKT_ARTEN.get(punkt.get("art"), "Programmpunkt"),
         uhrzeit(punkt.get("start")), uhrzeit(punkt.get("ende")),
         sorted(jahrgang_text(j) for j in punkt.get("jahrgaenge") or []))
        for punkt in daten.get("programmpunkte") or []
    ]
    bisher = [
        (p["titel"], uhrzeit(p["start_time"]), uhrzeit(p["end_time"]),
         sorted(jahrgang_text(j) for j in p["jahrgaenge_liste"] or []))
        for p in list_programmpunkte(conn, event_id)
    ] if bestand else []
    if sorted(programm) != sorted(bisher):
        if bestand:
            conn.execute("DELETE FROM event_programmpunkte WHERE event_id = ?", (event_id,))
        for titel, start, ende, jahrgaenge in programm:
            create_programmpunkt(conn, event_id, titel, start, ende, jahrgaenge)

    # Showspiele: ein unverändertes bleibt stehen, sonst neu anlegen.
    behalten = set()
    for show in plan["showspiele"]:
        final_id = (bestand or {}).get("showspiele", {}).get(show["id"])
        if show["gueltig"] and final_id in (bestand or {}).get("finals_loeschen", []) and \
                _showspiel_unveraendert(conn, final_id, show, competition_ids):
            behalten.add(final_id)
    for final_id in (bestand or {}).get("finals_loeschen", []):
        if final_id not in behalten:
            _loesche_wettbewerb(conn, final_id)
    for show in plan["showspiele"]:
        if not show["gueltig"]:
            continue
        if (bestand or {}).get("showspiele", {}).get(show["id"]) in behalten:
            continue
        sources = [competition_ids[eid] for eid in show["quellen"]]
        final_id = create_jahrgangsfinale(conn, sources, show["start"], show["court_id"])
        neue_slots.append({
            "competition_id": final_id, "slot_typ": "Spiel", "startzeit": show["start"],
            "court_id": show["court_id"], "team_a_id": None, "team_b_id": None,
            "game_duration_minutes": show["spielzeit"],
        })

    # Letzte Absicherung mit der bestehenden Konfliktpruefung des
    # Spielplan-Generators, jetzt mit den echten Datensaetzen.
    guard = [
        w["message"]
        for w in find_internal_conflicts(conn, neue_slots) + find_existing_schedule_conflicts(conn, neue_slots)
        if w["level"] == "error"
    ]
    if guard:
        return None, guard

    conn.execute(
        """
        UPDATE assistent_entwuerfe
        SET status = 'uebernommen', event_id = ?, updated_at = ?
        WHERE id = ? AND status = 'offen'
        """,
        (event_id, jetzt(), entwurf_id),
    )
    if statistik is not None:
        statistik.update(zaehler)
    return event_id, []


def _wettbewerb_unveraendert(conn, competition_id, werte, wb, eintrag, slots):
    """True, wenn Einstellungen, Klassen, Disziplinen und Spiele des
    bestehenden Wettbewerbs genau dem neuen Plan entsprechen."""
    row = conn.execute("SELECT * FROM competitions WHERE id = ?", (competition_id,)).fetchone()
    if row is None:
        return False
    spalten = (
        "name", "sportart", "jahrgang", "points_win", "points_draw", "points_first_place", "event_id",
        "competition_type", "tournament_mode", "game_duration_minutes", "changeover_duration_minutes",
        "location", "start_time", "end_time", "teilmannschaft", "tiebreak_order",
    )
    def norm(value):
        return "" if value is None else str(value)
    if [norm(row[s]) for s in spalten] != [norm(v) for v in werte]:
        return False
    teams = {r["team_id"] for r in conn.execute(
        "SELECT team_id FROM competition_teams WHERE competition_id = ?", (competition_id,))}
    if teams != {team["id"] for team in wb["teams"]}:
        return False
    disziplinen = [r["name"] for r in conn.execute(
        "SELECT name FROM competition_disciplines WHERE competition_id = ? ORDER BY sort_order, id",
        (competition_id,))]
    if disziplinen != list(eintrag.get("disziplinen") or []):
        return False
    def spiel(startzeit, court_id, phase, gruppe, a, b, note):
        return (uhrzeit(startzeit), court_id, phase or "", gruppe or "", a, b, note or "")
    alt = sorted(
        (spiel(r["startzeit"], r["court_id"], r["phase"], r["gruppe"], r["team_a_id"], r["team_b_id"], r["note"])
         for r in conn.execute(
             "SELECT * FROM slots WHERE competition_id = ? AND slot_typ = 'Spiel'", (competition_id,))),
        key=repr,
    )
    neu = sorted(
        (spiel(sl["startzeit"], sl["court_id"], sl["phase"], sl.get("gruppe"), sl.get("team_a_id"),
               sl.get("team_b_id"), sl.get("note"))
         for sl in slots if sl.get("eintrag_id") == eintrag["id"] and not sl.get("showspiel")),
        key=repr,
    )
    andere = conn.execute(
        "SELECT COUNT(*) FROM slots WHERE competition_id = ? AND slot_typ != 'Spiel'", (competition_id,)
    ).fetchone()[0]
    return alt == neu and not andere


def _showspiel_unveraendert(conn, final_id, show, competition_ids):
    row = conn.execute("SELECT final_source_ids FROM competitions WHERE id = ?", (final_id,)).fetchone()
    if row is None:
        return False
    quellen = [competition_ids.get(eid) for eid in show["quellen"]]
    if None in quellen or sorted(parse_final_source_ids(row["final_source_ids"])) != sorted(quellen):
        return False
    slots = conn.execute(
        "SELECT startzeit, court_id FROM slots WHERE competition_id = ? AND slot_typ = 'Spiel'", (final_id,)
    ).fetchall()
    return len(slots) == 1 and uhrzeit(slots[0]["startzeit"]) == show["start"] and \
        slots[0]["court_id"] == show["court_id"]


def _loesche_wettbewerb(conn, competition_id):
    """Wie „Wettbewerb löschen“ in der Wettbewerbsverwaltung. Nur für
    Wettbewerbe ohne Ergebnisse (das prüft der Planungskern)."""
    conn.execute(
        """
        DELETE FROM discipline_results WHERE discipline_id IN (
            SELECT id FROM competition_disciplines WHERE competition_id = ?
        )
        """,
        (competition_id,),
    )
    for tabelle in (
        "sixkampf_team_results", "sixkampf_participants", "slots",
        "competition_disciplines", "competition_teams",
    ):
        conn.execute(f"DELETE FROM {tabelle} WHERE competition_id = ?", (competition_id,))
    conn.execute("DELETE FROM competitions WHERE id = ?", (competition_id,))


# ---------------------------------------------------------------------------
# Vorschlaege und Bearbeitung aus der Vorschau
# ---------------------------------------------------------------------------

def _block(daten, block_id):
    for block in daten.get("zeitbloecke") or []:
        if str(block.get("id")) == str(block_id):
            return block
    raise ValueError("Zeitblock nicht gefunden.")


def _wb_einstellung(daten, key):
    return daten.setdefault("wettbewerbe", {}).setdefault(key, {})


def neuer_block(daten, ort, wettbewerbe=None, start=None, ende=None):
    grunddaten = daten.get("grunddaten") or {}
    block = {
        "id": naechste_id(daten.get("zeitbloecke") or [], "b"),
        "ort": ort,
        "start": start or grunddaten.get("start") or DEFAULT_START,
        "ende": ende or grunddaten.get("ende") or DEFAULT_ENDE,
        "wettbewerbe": list(wettbewerbe or []),
        "ablauf": "abwechselnd",
        "spielzeit": DEFAULT_GAME_DURATION_MINUTES,
        "wechselzeit": DEFAULT_CHANGEOVER_DURATION_MINUTES,
    }
    daten.setdefault("zeitbloecke", []).append(block)
    return block


def bloecke_vorschlagen(daten, stamm):
    """Schnellplanung ohne Vorlage: je Ort ein Zeitblock ueber den groben
    Zeitrahmen mit allen Wettbewerben dieses Ortes (abwechselnd)."""
    if daten.get("zeitbloecke"):
        return False
    by_ort = {}
    for wb in ableiten_wettbewerbe(daten, stamm):
        by_ort.setdefault(wb["einstellungen"]["ort"], []).append(wb["key"])
    for ort, keys in by_ort.items():
        neuer_block(daten, ort, keys)
    return bool(by_ort)


def entferne_aus_bloecken(daten, key):
    for block in daten.get("zeitbloecke") or []:
        block["wettbewerbe"] = [k for k in block.get("wettbewerbe") or [] if k != key]


def wende_vorschlag_an(daten, aktion, werte):
    """Wendet einen Loesungsvorschlag der Machbarkeitspruefung an - nur auf
    ausdrueckliche Auswahl des Nutzers. Gibt eine kurze Beschreibung zurueck."""
    werte = werte or {}
    if aktion == "block_spielzeit":
        block = _block(daten, werte.get("block"))
        block["spielzeit"] = zahl(werte.get("spielzeit"), block.get("spielzeit"), 1, 240)
        return f"Spielzeit im Zeitblock {block['ort']} auf {block['spielzeit']} Min. gesetzt."
    if aktion == "block_wechselzeit":
        block = _block(daten, werte.get("block"))
        block["wechselzeit"] = zahl(werte.get("wechselzeit"), block.get("wechselzeit"), 0, 120)
        return f"Wechselzeit im Zeitblock {block['ort']} auf {block['wechselzeit']} Min. gesetzt."
    if aktion == "block_ende":
        block = _block(daten, werte.get("block"))
        block["ende"] = uhrzeit(werte.get("ende")) or block["ende"]
        return f"Zeitblock {block['ort']} endet jetzt um {block['ende']}."
    if aktion == "block_start":
        block = _block(daten, werte.get("block"))
        neu = minuten(werte.get("start"))
        alt_start, alt_ende = minuten(block["start"]), minuten(block["ende"])
        if neu is not None and alt_start is not None and alt_ende is not None:
            block["start"] = uhr(neu)
            block["ende"] = uhr(neu + (alt_ende - alt_start))
        return f"Zeitblock {block['ort']} beginnt jetzt um {block['start']}."
    if aktion == "block_ablauf":
        block = _block(daten, werte.get("block"))
        if werte.get("ablauf") in ABLAEUFE:
            block["ablauf"] = werte["ablauf"]
        return f"Zeitblock {block['ort']}: {ABLAEUFE[block['ablauf']]}."
    if aktion == "wb_modus":
        einstellung = _wb_einstellung(daten, werte.get("wb"))
        einstellung["modus"] = werte.get("modus")
        einstellung["modus_quelle"] = "nutzer"
        return "Turniermodus geändert."
    if aktion == "wb_ort":
        key = werte.get("wb")
        ort = normalize_competition_location(werte.get("ort"))
        if not ort:
            raise ValueError("Unbekannter Ort.")
        einstellung = _wb_einstellung(daten, key)
        alter_block = next(
            (b for b in daten.get("zeitbloecke") or [] if key in (b.get("wettbewerbe") or [])), None
        )
        einstellung["ort"] = ort
        einstellung["felder"] = []
        entferne_aus_bloecken(daten, key)
        ziel = next(
            (b for b in daten.get("zeitbloecke") or []
             if b.get("ort") == ort and alter_block is not None
             and (b.get("start"), b.get("ende")) == (alter_block.get("start"), alter_block.get("ende"))),
            None,
        )
        if ziel is None:
            neuer_block(
                daten, ort, [key],
                alter_block.get("start") if alter_block else None,
                alter_block.get("ende") if alter_block else None,
            )
        else:
            ziel["wettbewerbe"].append(key)
        return f"Wettbewerb nach {ort} verlegt."
    if aktion == "ort_felder_alle":
        daten.setdefault("orte", {}).setdefault(werte.get("ort"), {})["felder"] = None
        daten["orte"][werte.get("ort")]["aktiv"] = True
        return f"Alle Felder in {werte.get('ort')} werden genutzt."
    if aktion == "block_zuordnen":
        key = werte.get("wb")
        entferne_aus_bloecken(daten, key)
        _block(daten, werte.get("block"))["wettbewerbe"].append(key)
        return "Wettbewerb dem Zeitblock zugeordnet."
    if aktion == "block_anlegen":
        key = werte.get("wb")
        ort = (daten.get("wettbewerbe", {}).get(key) or {}).get("ort") or DEFAULT_COMPETITION_LOCATION
        entferne_aus_bloecken(daten, key)
        neuer_block(daten, ort, [key])
        return f"Neuer Zeitblock {ort} angelegt."
    raise ValueError("Unbekannter Vorschlag.")
