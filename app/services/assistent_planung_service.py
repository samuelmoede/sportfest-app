"""Planungskern des neuen Einrichtungsassistenten: rechnet aus Entwurfsdaten
und aktuellen Stammdaten den kompletten Tagesablauf (ohne zu speichern) und
prueft ihn auf Machbarkeit.

Wiederverwendet werden die Bausteine des bestehenden Spielplan-Generators:
Paarungen (_jeder_gegen_jeden_pairings, _hin_rueckrunde_pairings,
_build_pairings), Rundenbildung (_schedule_pairings_into_rounds),
Feldaufteilung (_split_courts), der KO-Baum (build_ko_rounds) und fuer die
Vorschau die Kalenderansicht des Dashboards (build_day_timeline). Neu ist
nur die Taktung mehrerer Wettbewerbe in einem Zeitblock mit Programmpunkten
und Puffern, die nicht ueberplant werden.

Ein Zeitblock ist ein Ort mit Zeitraum und den Wettbewerben, die dort
spielen. "abwechselnd": je Zeitschritt ist einer der Wettbewerbe an der
Reihe (Schulpokal J7/J8 auf denselben Feldern), "gleichzeitig": alle
spielen zugleich auf aufgeteilten Feldern. Ein Feld wird nie doppelt
vergeben; getrennte Wettbewerbe spielen nie gegeneinander."""
import hashlib

from app.services.assistent_entwurf_service import (
    ABLAEUFE,
    PROGRAMMPUNKT_ARTEN,
    ableiten_wettbewerbe,
    felder_am_ort,
    ist_schulpokal,
    minuten,
    ort_einstellung,
    planbare_orte,
    sortiere_jahrgaenge,
    uhr,
    uhrzeit,
    zahl,
)
from app.services.schedule_generator_service import (
    _build_pairings,
    _hin_rueckrunde_pairings,
    _jeder_gegen_jeden_pairings,
    _schedule_pairings_into_rounds,
    _split_courts,
)
from app.services.schedule_location_service import natural_sort_key
from app.services.schedule_time_service import (
    DEFAULT_CHANGEOVER_DURATION_MINUTES,
    DEFAULT_GAME_DURATION_MINUTES,
)
from app.services.tournament_modes.ko_runde import build_ko_rounds
from app.services.ui_view_service import build_day_timeline

MODI = {
    "jeder_gegen_jeden": "Jeder gegen jeden",
    "hin_rueckrunde": "Hin- und Rückrunde",
    "gruppenphase_ko": "Gruppenphase mit Halbfinale/Finale",
    "ko_runde": "K.-o.-System",
}
AUTO_MODUS_REIHENFOLGE = ("jeder_gegen_jeden", "gruppenphase_ko", "ko_runde")
STUFEN = ("blockierend", "warnung", "info")
MIN_SPIELZEIT = 3


def _kennung(*parts):
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _hinweis(stufe, titel, text="", *, kennung=None, wb=None, block=None,
             bestaetigbar=False, vorschlaege=None):
    return {
        "id": kennung or _kennung(stufe, titel, text),
        "stufe": stufe,
        "titel": titel,
        "text": text,
        "wb": wb,
        "block": block,
        "bestaetigbar": bestaetigbar,
        "bestaetigt": False,
        "vorschlaege": vorschlaege or [],
    }


# ---------------------------------------------------------------------------
# Runden je Turniermodus (bestehende Generator-Bausteine)
# ---------------------------------------------------------------------------

def _rr_runden(pairings, team_names, court_ids):
    runden = []
    for runde in _schedule_pairings_into_rounds(pairings, team_names, court_ids):
        runden.append([
            {"team_a": a, "team_b": b, "gruppe": gruppe or "", "phase": "Gruppenphase",
             "note": "", "feld": court_id}
            for court_id, a, b, gruppe in runde
        ])
    return runden


def baue_runden(modus, team_names, court_ids, spiele_pro_team=2):
    """Runden (Liste von Spielen) fuer einen Wettbewerb. Platzhalter-Spiele
    der K.-o.-Phase haben team_a/team_b None und eine erklaerende Notiz -
    genau wie im Spielplan-Generator."""
    if len(team_names) < 2 or not court_ids:
        return []
    if modus == "hin_rueckrunde":
        return _rr_runden(_hin_rueckrunde_pairings(team_names), team_names, court_ids)
    if modus == "gruppenphase_ko":
        pairings = _build_pairings(team_names, max(1, spiele_pro_team))
        runden = _rr_runden(pairings, team_names, court_ids)
        mehrere_felder = len(court_ids) > 1
        if any(gruppe for _a, _b, gruppe in pairings):
            hf = [{"team_a": None, "team_b": None, "gruppe": "", "phase": "Halbfinale",
                   "note": "HF1: 1. Gruppe A gegen 2. Gruppe B", "feld": None}]
            if mehrere_felder:
                hf.append({"team_a": None, "team_b": None, "gruppe": "", "phase": "Halbfinale",
                           "note": "HF2: 1. Gruppe B gegen 2. Gruppe A", "feld": None})
            runden.append(hf)
            finale = [{"team_a": None, "team_b": None, "gruppe": "", "phase": "Finale",
                       "note": "Finale: Sieger HF1 gegen Sieger HF2", "feld": None}]
            if mehrere_felder:
                finale.append({"team_a": None, "team_b": None, "gruppe": "", "phase": "Spiel um Platz 3",
                               "note": "Spiel um Platz 3: Verlierer HF1 gegen Verlierer HF2", "feld": None})
        else:
            finale = [{"team_a": None, "team_b": None, "gruppe": "", "phase": "Finale",
                       "note": "Finale: Platz 1 gegen Platz 2 der Tabelle", "feld": None}]
            if mehrere_felder:
                finale.append({"team_a": None, "team_b": None, "gruppe": "", "phase": "Spiel um Platz 3",
                               "note": "Spiel um Platz 3: Platz 3 gegen Platz 4 der Tabelle", "feld": None})
        runden.append(finale)
        return runden
    if modus == "ko_runde":
        rounds = build_ko_rounds(team_names)
        runden = []
        for round_matches in rounds:
            runde = []
            for match in round_matches:
                both = match["team_a"] is not None and match["team_b"] is not None
                note = "" if both else (
                    f'{match["phase"]}: {_beschreibe(match["team_a"], match["source_a"])} gegen '
                    f'{_beschreibe(match["team_b"], match["source_b"])}'
                )
                runde.append({"team_a": match["team_a"], "team_b": match["team_b"], "gruppe": "",
                              "phase": match["phase"], "note": note, "feld": None})
            runden.append(runde)
        final_match = rounds[-1][0] if rounds else None
        if final_match and final_match["source_a"] and final_match["source_b"] and len(court_ids) > 1:
            runden[-1].append({
                "team_a": None, "team_b": None, "gruppe": "", "phase": "Spiel um Platz 3", "feld": None,
                "note": (
                    f'Spiel um Platz 3: Verlierer {final_match["source_a"]["phase"]} Spiel '
                    f'{final_match["source_a"]["position"] + 1} gegen Verlierer '
                    f'{final_match["source_b"]["phase"]} Spiel {final_match["source_b"]["position"] + 1}'
                ),
            })
        return runden
    return _rr_runden(_jeder_gegen_jeden_pairings(team_names), team_names, court_ids)


def _beschreibe(team, source):
    if team is not None:
        return team
    return f'Sieger {source["phase"]} Spiel {source["position"] + 1}'


# ---------------------------------------------------------------------------
# Taktung eines Zeitblocks
# ---------------------------------------------------------------------------

def _blocker_fuer(blocker, jahrgaenge):
    return [
        b for b in blocker
        if not b["jahrgaenge"] or set(b["jahrgaenge"]) & set(jahrgaenge)
    ]


def _naechster_freier_start(t, dauer, blocker):
    changed = True
    while changed:
        changed = False
        for b in blocker:
            if t < b["ende"] and b["start"] < t + dauer:
                t = b["ende"]
                changed = True
    return t


def takte_block(start, spielzeit, wechselzeit, gruppen, blocker):
    """gruppen: Liste von Taktgruppen, je Gruppe eine Liste von Eintraegen
    (dict mit id, jahrgaenge, felder, runden). Je Zeitschritt spielt eine
    Gruppe (abwechselnd) - bei nur einer Gruppe alle zugleich. Ein Feld wird
    je Zeitschritt nur einmal vergeben; Spiele ohne freies Feld rutschen in
    den naechsten Schritt dieser Gruppe. Programmpunkte/Puffer (blocker), die
    fuer die Jahrgaenge der Gruppe gelten, werden uebersprungen.

    Gibt (slots, schritte) zurueck; schritte = [(gruppen_index, start)]."""
    zustand = {}
    for gruppe in gruppen:
        for eintrag in gruppe:
            zustand[eintrag["id"]] = {"runde": 0, "offen": None}

    def hat_rest(eintrag):
        z = zustand[eintrag["id"]]
        return z["runde"] < len(eintrag["runden"])

    slots = []
    schritte = []
    t = start
    zeiger = 0
    sicherung = 0
    while any(hat_rest(e) for gruppe in gruppen for e in gruppe):
        sicherung += 1
        if sicherung > 2000:
            break
        for offset in range(len(gruppen)):
            index = (zeiger + offset) % len(gruppen)
            if any(hat_rest(e) for e in gruppen[index]):
                break
        gruppe = gruppen[index]
        zeiger = index + 1
        jahrgaenge = sorted({j for e in gruppe for j in e["jahrgaenge"]})
        t = _naechster_freier_start(t, spielzeit, _blocker_fuer(blocker, jahrgaenge))
        belegt = set()
        for eintrag in gruppe:
            if not hat_rest(eintrag):
                continue
            z = zustand[eintrag["id"]]
            if z["offen"] is None:
                z["offen"] = list(eintrag["runden"][z["runde"]])
            frei = [f for f in eintrag["felder"] if f not in belegt]
            for spiel in list(z["offen"]):
                if not frei:
                    break
                feld = spiel["feld"] if spiel["feld"] in frei else frei[0]
                frei.remove(feld)
                belegt.add(feld)
                z["offen"].remove(spiel)
                slots.append({
                    **spiel, "eintrag_id": eintrag["id"], "court_id": feld,
                    "start_min": t, "ende_min": t + spielzeit,
                })
            if not z["offen"]:
                z["runde"] += 1
                z["offen"] = None
        schritte.append((index, t))
        t += spielzeit + wechselzeit
    return slots, schritte


# ---------------------------------------------------------------------------
# Gesamtplan
# ---------------------------------------------------------------------------

def _block_daten(block, grunddaten):
    start = minuten(block.get("start"))
    ende = minuten(block.get("ende"))
    return {
        "id": block.get("id"),
        "ort": block.get("ort"),
        "start": uhrzeit(block.get("start")),
        "ende": uhrzeit(block.get("ende")),
        "start_min": start,
        "ende_min": ende,
        "wettbewerbe": list(block.get("wettbewerbe") or []),
        "ablauf": block.get("ablauf") if block.get("ablauf") in ABLAEUFE else "abwechselnd",
        "spielzeit": zahl(block.get("spielzeit"), DEFAULT_GAME_DURATION_MINUTES, 1, 240),
        "wechselzeit": zahl(block.get("wechselzeit"), DEFAULT_CHANGEOVER_DURATION_MINUTES, 0, 120),
    }


def _blocker_liste(daten):
    result = []
    for punkt in daten.get("programmpunkte") or []:
        start, ende = minuten(punkt.get("start")), minuten(punkt.get("ende"))
        if start is None or ende is None or ende <= start:
            continue
        result.append({
            "id": punkt.get("id"), "start": start, "ende": ende,
            "jahrgaenge": sortiere_jahrgaenge(punkt.get("jahrgaenge") or []),
            "titel": punkt.get("titel") or PROGRAMMPUNKT_ARTEN.get(punkt.get("art"), "Programmpunkt"),
            "art": punkt.get("art") or "sonstiges",
        })
    return result


def _eintraege_fuer(wb, block, felder, modus, eintrag_ids):
    result = []
    team_names = [team["name"] for team in wb["teams"]]
    eigene_felder = [f for f in (wb["einstellungen"].get("felder") or []) if f in felder]
    for teil in wb["teilmannschaften"]:
        eintrag_id = f'{wb["key"]}#{teil or ""}'
        eintrag_ids.append(eintrag_id)
        result.append({
            "id": eintrag_id,
            "wettbewerb": wb,
            "teilmannschaft": teil,
            "name": _db_name(wb, teil),
            "jahrgaenge": wb["jahrgaenge"],
            "felder": eigene_felder or list(felder),
            "eigene_felder": bool(eigene_felder),
            "team_names": team_names,
            "team_ids": {team["name"]: team["id"] for team in wb["teams"]},
            "modus": modus,
            "block": block,
        })
    return result


def _db_name(wb, teil):
    from app.services.assistent_entwurf_service import wettbewerb_name
    return wettbewerb_name(wb["sportart"], wb["jahrgaenge"], teil)


def plane_block(block, eintraege, blocker):
    """Taktet einen Zeitblock. Gibt dict mit slots, schritte, ende_min,
    fehler zurueck (fehler: Texte fuer blockierende Hinweise)."""
    fehler = []
    turnier = [e for e in eintraege if e["wettbewerb"]["einstellungen"]["art"] != "Sechskampf"]
    turnier = [e for e in turnier if len(e["team_names"]) >= 2 and e["felder"]]
    if not turnier or block["start_min"] is None:
        return {"slots": [], "schritte": [], "ende_min": block["start_min"], "fehler": fehler, "gruppen": []}
    if block["ablauf"] == "gleichzeitig":
        gruppen = [turnier]
        gleiche_felder = len({tuple(e["felder"]) for e in turnier}) == 1 and not any(
            e["eigene_felder"] for e in turnier
        )
        if gleiche_felder and len(turnier) > 1:
            chunks = _split_courts(list(turnier[0]["felder"]), [len(e["team_names"]) // 2 for e in turnier])
            if chunks is None:
                fehler.append(
                    f"Zu wenige Felder: {len(turnier)} Wettbewerbe sollen gleichzeitig spielen, "
                    f"es gibt aber nur {len(turnier[0]['felder'])} Felder."
                )
                return {"slots": [], "schritte": [], "ende_min": block["start_min"], "fehler": fehler,
                        "gruppen": []}
            for eintrag, chunk in zip(turnier, chunks):
                eintrag["felder"] = chunk
    else:
        by_wb = {}
        for eintrag in turnier:
            by_wb.setdefault(eintrag["wettbewerb"]["key"], []).append(eintrag)
        gruppen = list(by_wb.values())
    for eintrag in turnier:
        eintrag["runden"] = baue_runden(
            eintrag["modus"], eintrag["team_names"], eintrag["felder"],
            zahl(eintrag["wettbewerb"]["einstellungen"].get("spiele_pro_team"), 2, 1, 20),
        )
    slots, schritte = takte_block(
        block["start_min"], block["spielzeit"], block["wechselzeit"], gruppen, blocker,
    )
    ende_min = max((s["ende_min"] for s in slots), default=block["start_min"])
    return {"slots": slots, "schritte": schritte, "ende_min": ende_min, "fehler": fehler, "gruppen": gruppen}


def _passt(block, eintraege, blocker, **aenderung):
    test_block = {**block, **aenderung}
    kopien = [dict(e, felder=list(e["felder"])) for e in eintraege]
    if "modi" in aenderung:
        for e in kopien:
            e["modus"] = aenderung["modi"].get(e["wettbewerb"]["key"], e["modus"])
    result = plane_block(test_block, kopien, blocker)
    if result["fehler"] or block["ende_min"] is None:
        return False, result
    return result["ende_min"] <= block["ende_min"], result


def _team_anzahl_text(wb):
    return f"{len(wb['teams'])} Klassen"


def plane(conn, daten, stamm, belegung=None):
    """Rechnet den kompletten Vorschlag. conn wird nur fuer die Belegung
    anderer Veranstaltungen am selben Tag genutzt (lesend)."""
    grunddaten = daten.get("grunddaten") or {}
    wettbewerbe = ableiten_wettbewerbe(daten, stamm)
    wb_by_key = {wb["key"]: wb for wb in wettbewerbe}
    court_names = {court["id"]: court["name"] for court in stamm["courts"]}
    blocker = _blocker_liste(daten)
    hinweise = []

    # Grunddaten und Abgleich
    if (daten.get("abgleich") or {}).get("offen"):
        hinweise.append(_hinweis(
            "blockierend", "Abgleich mit der Vorlage fehlt",
            "Bitte Datum, Klassen, Sportarten und Orte der Vorlage bestätigen.",
            kennung="abgleich",
        ))
    if not (grunddaten.get("name") or "").strip():
        hinweise.append(_hinweis("blockierend", "Name der Veranstaltung fehlt", kennung="name"))
    elif conn is not None and conn.execute(
        "SELECT 1 FROM events WHERE name = ? AND id != ?",
        (grunddaten["name"].strip(), ((daten.get("bestand") or {}).get("event_id") or -1)),
    ).fetchone():
        hinweise.append(_hinweis(
            "blockierend", "Es gibt schon eine Veranstaltung mit diesem Namen",
            "Bitte in den Grunddaten einen anderen Namen wählen.", kennung="name-doppelt",
        ))
    if not grunddaten.get("datum"):
        hinweise.append(_hinweis(
            "info", "Kein Datum angegeben",
            "Ohne Datum lässt sich nicht gegen andere Veranstaltungen am selben Tag prüfen.",
            kennung="datum",
        ))
    if not wettbewerbe:
        hinweise.append(_hinweis(
            "blockierend", "Noch keine Wettbewerbe",
            "Bitte bei Sportarten mindestens einen Jahrgang einer Sportart zuordnen.",
            kennung="keine-wettbewerbe",
        ))

    # Bloecke
    bloecke = [_block_daten(block, grunddaten) for block in daten.get("zeitbloecke") or []]
    block_by_wb = {}
    for block in bloecke:
        if block["start_min"] is None or block["ende_min"] is None or block["ende_min"] <= block["start_min"]:
            hinweise.append(_hinweis(
                "blockierend", f"Zeitblock {block['ort']}: ungültiger Zeitraum",
                "Das Ende muss nach dem Beginn liegen.", kennung=f"block-zeit-{block['id']}",
                block=block["id"],
            ))
        for key in block["wettbewerbe"]:
            if key in wb_by_key and key not in block_by_wb:
                block_by_wb[key] = block

    for punkt in daten.get("programmpunkte") or []:
        start, ende = minuten(punkt.get("start")), minuten(punkt.get("ende"))
        if start is None or ende is None or ende <= start:
            hinweise.append(_hinweis(
                "blockierend", f"Programmpunkt „{punkt.get('titel') or 'ohne Titel'}“: ungültige Zeit",
                "Bitte Uhrzeit von und bis prüfen.", kennung=f"punkt-{punkt.get('id')}",
            ))

    # Modus je Wettbewerb
    for wb in wettbewerbe:
        modus = wb["einstellungen"].get("modus")
        wb["modus"] = modus if modus in MODI else None
        wb["modus_quelle"] = wb["einstellungen"].get("modus_quelle") if wb["modus"] else "vorschlag"

    eintraege = []
    eintrag_ids = []
    block_ergebnisse = {}
    for wb in wettbewerbe:
        einstellungen = wb["einstellungen"]
        ort = einstellungen["ort"]
        ort_info = ort_einstellung(daten, stamm, ort)
        block = block_by_wb.get(wb["key"])
        andere_orte = [o for o in planbare_orte() if o != ort and ort_einstellung(daten, stamm, o)["aktiv"]]
        if len(wb["teams"]) < 2 and einstellungen["art"] != "Sechskampf":
            hinweise.append(_hinweis(
                "blockierend", f"{wb['name']}: weniger als zwei Klassen",
                "Bitte in den Grunddaten Klassen ergänzen oder den Jahrgang in der Matrix abwählen.",
                kennung=f"teams-{wb['key']}", wb=wb["key"],
            ))
        if einstellungen["art"] == "Sechskampf" and not _disziplinen(einstellungen):
            hinweise.append(_hinweis(
                "blockierend", f"{wb['name']}: keine Disziplinen",
                "Bitte unter Orte/Erweiterte Einstellungen die Disziplinen eintragen.",
                kennung=f"disziplinen-{wb['key']}", wb=wb["key"],
            ))
        if einstellungen["art"] != "Sechskampf" and not ort_info["aktiv"]:
            grund = "ist im Entwurf deaktiviert" if ort_info["hat_aktive_felder"] else "hat keine aktiven Felder"
            hinweise.append(_hinweis(
                "blockierend", f"{wb['name']}: Ort {ort} {grund}",
                "Bitte einen anderen Ort wählen oder Felder für diesen Ort anlegen bzw. aktivieren.",
                kennung=f"ort-{wb['key']}-{ort}", wb=wb["key"],
                vorschlaege=[
                    {"label": f"Nach {o} verlegen", "aktion": "wb_ort", "werte": {"wb": wb["key"], "ort": o}}
                    for o in andere_orte
                ],
            ))
            continue
        if block is None:
            passende = [b for b in bloecke if b["ort"] == ort]
            hinweise.append(_hinweis(
                "blockierend", f"{wb['name']}: kein Zeitblock",
                f"Bitte den Wettbewerb einem Zeitblock am Ort {ort} zuordnen.",
                kennung=f"ohne-block-{wb['key']}", wb=wb["key"],
                vorschlaege=[
                    {"label": f"Zu Block {b['start']}–{b['ende']} hinzufügen", "aktion": "block_zuordnen",
                     "werte": {"wb": wb["key"], "block": b["id"]}}
                    for b in passende
                ] + [{"label": f"Neuen Zeitblock {ort} anlegen", "aktion": "block_anlegen",
                      "werte": {"wb": wb["key"]}}],
            ))
            continue
        if block["ort"] != ort:
            hinweise.append(_hinweis(
                "blockierend", f"{wb['name']}: Zeitblock liegt an einem anderen Ort",
                f"Der Wettbewerb ist für {ort} eingestellt, der Zeitblock für {block['ort']}.",
                kennung=f"block-ort-{wb['key']}", wb=wb["key"],
                vorschlaege=[{"label": f"Wettbewerb nach {block['ort']} verlegen", "aktion": "wb_ort",
                              "werte": {"wb": wb["key"], "ort": block["ort"]}}],
            ))
            continue
        felder = [f for f in ort_info["felder"]]
        if not felder and einstellungen["art"] != "Sechskampf":
            hinweise.append(_hinweis(
                "blockierend", f"{wb['name']}: keine Felder ausgewählt",
                f"Für {ort} ist im Entwurf kein Feld ausgewählt.", kennung=f"felder-{wb['key']}",
                wb=wb["key"],
                vorschlaege=[{"label": f"Alle Felder in {ort} nutzen", "aktion": "ort_felder_alle",
                              "werte": {"ort": ort}}],
            ))
            continue
        eintraege.extend(_eintraege_fuer(wb, block, felder, wb["modus"] or "jeder_gegen_jeden", eintrag_ids))

    # Bestehende Veranstaltung bearbeiten: Blöcke mit begonnenen Spielen
    # bleiben, wie sie sind; entfernte Wettbewerbe werden gelöscht.
    bestand = _lade_bestand(conn, daten, wettbewerbe, eintraege, bloecke, hinweise)

    # Je Block takten; automatischer Modus, falls keiner gesetzt ist
    alle_slots = []
    for block in bloecke:
        block_eintraege = [e for e in eintraege if e["block"] is block]
        if bestand and block["id"] in bestand["gesperrte_bloecke"]:
            vorhandene = _bestehende_slots(conn, block_eintraege, bestand, block)
            block_ergebnisse[block["id"]] = {
                "slots": vorhandene, "schritte": [], "fehler": [],
                "ende_min": max((sl["ende_min"] for sl in vorhandene), default=None),
            }
            alle_slots.extend(vorhandene)
            continue
        if block["start_min"] is None or block["ende_min"] is None or block["ende_min"] <= block["start_min"]:
            block_ergebnisse[block["id"]] = {"slots": [], "schritte": [], "ende_min": None, "fehler": []}
            continue
        if not block_eintraege:
            block_ergebnisse[block["id"]] = {"slots": [], "schritte": [], "ende_min": None, "fehler": []}
            if not block["wettbewerbe"]:
                hinweise.append(_hinweis(
                    "info", f"Zeitblock {block['ort']} {block['start']}–{block['ende']} ist leer",
                    kennung=f"leer-{block['id']}", block=block["id"],
                ))
            continue
        auto = [e for e in block_eintraege if e["wettbewerb"]["modus"] is None]
        if auto:
            modi = {e["wettbewerb"]["key"]: "jeder_gegen_jeden" for e in auto}
            fits, _ = _passt(block, block_eintraege, blocker, modi=modi)
            reihenfolge = sorted({e["wettbewerb"]["key"] for e in auto},
                                 key=lambda k: -len(wb_by_key[k]["teams"]))
            for key in reihenfolge:
                if fits:
                    break
                for modus in AUTO_MODUS_REIHENFOLGE[1:]:
                    modi[key] = modus
                    fits, _ = _passt(block, block_eintraege, blocker, modi=modi)
                    if fits:
                        break
            for e in auto:
                e["modus"] = modi[e["wettbewerb"]["key"]]
                wb = e["wettbewerb"]
                wb["modus_vorschlag"] = e["modus"]
                wb["modus_begruendung"] = (
                    f"Vorschlag: {MODI[e['modus']]} – {_team_anzahl_text(wb)}, "
                    f"{len(e['felder'])} Felder, Zeitblock {block['start']}–{block['ende']}"
                    + ("." if fits else " (passt auch so nicht ganz, siehe Hinweise).")
                )
        ergebnis = plane_block(block, block_eintraege, blocker)
        block_ergebnisse[block["id"]] = ergebnis
        for text in ergebnis["fehler"]:
            hinweise.append(_hinweis(
                "blockierend", f"Zeitblock {block['ort']} {block['start']}–{block['ende']}: {text}",
                kennung=f"block-fehler-{block['id']}-{_kennung(text)}", block=block["id"],
                vorschlaege=[{"label": "Abwechselnd spielen lassen", "aktion": "block_ablauf",
                              "werte": {"block": block["id"], "ablauf": "abwechselnd"}}],
            ))
        for slot in ergebnis["slots"]:
            slot["block_id"] = block["id"]
            slot["spielzeit"] = block["spielzeit"]
        alle_slots.extend(ergebnis["slots"])
        if ergebnis["slots"] and ergebnis["ende_min"] > block["ende_min"]:
            hinweise.append(_ueberzug_hinweis(block, block_eintraege, blocker, ergebnis))
        _abwechslung_info(block, ergebnis, hinweise)

    # Showspiele (Jahrgangsfinale ohne Wertung)
    eintrag_by_id = {e["id"]: e for e in eintraege}
    showspiele = []
    for show in daten.get("showspiele") or []:
        if bestand and show.get("id") in bestand["gesperrte_showspiele"]:
            showspiele.append({
                "id": show.get("id"), "start": uhrzeit(show.get("start")), "quellen": [], "namen": [],
                "gueltig": False, "gesperrt": True, "court_id": None,
                "spielzeit": DEFAULT_GAME_DURATION_MINUTES, "start_min": minuten(show.get("start")),
            })
            continue
        quellen = []
        texte = []
        for side in ("wettbewerb_a", "wettbewerb_b"):
            key = show.get(side)
            eintrag = eintrag_by_id.get(f"{key}#")
            if eintrag is None:
                texte.append(f"Wettbewerb „{key or '–'}“ fehlt oder ist aufgeteilt.")
            else:
                quellen.append(eintrag)
        start = minuten(show.get("start"))
        info = {
            "id": show.get("id"), "start": uhrzeit(show.get("start")), "quellen": [q["id"] for q in quellen],
            "namen": [q["name"] for q in quellen], "gueltig": False, "court_id": None,
            "spielzeit": DEFAULT_GAME_DURATION_MINUTES, "start_min": start,
        }
        if start is None:
            texte.append("Bitte eine Uhrzeit angeben.")
        if len(quellen) == 2 and quellen[0]["id"] == quellen[1]["id"]:
            texte.append("Zwei verschiedene Wettbewerbe auswählen.")
        if not texte and len(quellen) == 2:
            enden = [
                max((s["ende_min"] for s in alle_slots if s["eintrag_id"] == q["id"]), default=None)
                for q in quellen
            ]
            erste_block = quellen[0]["block"]
            info["court_id"] = quellen[0]["felder"][0] if quellen[0]["felder"] else None
            info["spielzeit"] = erste_block["spielzeit"]
            if any(e is not None and start < e for e in enden):
                texte.append(
                    f"Beginnt um {info['start']}, die Wettbewerbe enden aber erst um "
                    f"{uhr(max(e for e in enden if e is not None))}."
                )
        if texte:
            hinweise.append(_hinweis(
                "blockierend", f"Showspiel {info['start'] or ''}: bitte prüfen", " ".join(texte),
                kennung=f"show-{show.get('id')}",
            ))
        else:
            info["gueltig"] = True
            alle_slots.append({
                "eintrag_id": f"show:{show.get('id')}", "showspiel": True,
                "team_a": None, "team_b": None, "phase": "Finale", "gruppe": "",
                "note": f"Showspiel ohne Wertung: Sieger {info['namen'][0]} gegen Sieger {info['namen'][1]}",
                "court_id": info["court_id"], "start_min": start, "ende_min": start + info["spielzeit"],
                "spielzeit": info["spielzeit"], "block_id": quellen[0]["block"]["id"],
            })
        showspiele.append(info)

    for slot in alle_slots:
        slot["startzeit"] = uhr(slot["start_min"])
        slot["endzeit"] = uhr(slot["ende_min"])
        slot["court_name"] = court_names.get(slot["court_id"], "")
        eintrag = eintrag_by_id.get(slot["eintrag_id"])
        if eintrag is not None:
            slot["team_a_id"] = eintrag["team_ids"].get(slot["team_a"]) if slot["team_a"] else None
            slot["team_b_id"] = eintrag["team_ids"].get(slot["team_b"]) if slot["team_b"] else None
        else:
            slot["team_a_id"] = slot["team_b_id"] = None

    _feld_konflikte(alle_slots, eintrag_by_id, bloecke, hinweise)
    _klassen_konflikte(alle_slots, eintrag_by_id, hinweise)
    if conn is not None and grunddaten.get("datum"):
        _fremde_belegung(conn, grunddaten["datum"], alle_slots, eintrag_by_id, hinweise,
                         ausnahmen=(bestand or {}).get("ersetzt", set()))

    akzeptiert = set(daten.get("akzeptierte_hinweise") or [])
    eindeutig = {}
    for hinweis in hinweise:
        eindeutig.setdefault(hinweis["id"], hinweis)
    hinweise = list(eindeutig.values())
    for hinweis in hinweise:
        hinweis["bestaetigt"] = hinweis["bestaetigbar"] and hinweis["id"] in akzeptiert
    hinweise.sort(key=lambda h: (STUFEN.index(h["stufe"]), h["bestaetigt"], h["titel"]))

    blockierend = [h for h in hinweise if h["stufe"] == "blockierend"]
    offen = [h for h in hinweise if h["stufe"] == "warnung" and not h["bestaetigt"]]

    # Sechskampf: Zeitfenster des Blocks, Disziplinen statt Spielplan
    for eintrag in eintraege:
        eintrag["disziplinen"] = _disziplinen(eintrag["wettbewerb"]["einstellungen"])
        eintrag["slots"] = sorted(
            (s for s in alle_slots if s["eintrag_id"] == eintrag["id"]),
            key=lambda s: (s["start_min"], s["court_name"]),
        )
        eintrag["raster"] = baue_spielraster(eintrag, blocker)
    timeline = baue_tagesplan(daten, eintraege, alle_slots, showspiele, blocker)
    return {
        "wettbewerbe": wettbewerbe,
        "eintraege": eintraege,
        "bloecke": bloecke,
        "block_ergebnisse": block_ergebnisse,
        "slots": sorted(alle_slots, key=lambda s: (s["start_min"], s["court_name"])),
        "showspiele": showspiele,
        "hinweise": hinweise,
        "anzahl": {stufe: sum(1 for h in hinweise if h["stufe"] == stufe) for stufe in STUFEN},
        "blockierend": blockierend,
        "offene_warnungen": offen,
        "uebernahme_moeglich": not blockierend and not offen and bool(eintraege),
        "timeline": timeline,
        "programmpunkte": blocker,
        "bestand": bestand,
    }


def _bestehende_slots(conn, eintraege, bestand, block):
    """Spiele eines gesperrten Blocks so, wie sie in der Datenbank stehen,
    damit Vorschau, Tagesplan und Konfliktprüfung sie sehen. Sie werden beim
    Übernehmen nicht angefasst (bestehend=True)."""
    result = []
    for eintrag in eintraege:
        competition_id = bestand["wettbewerbe"].get(eintrag["id"])
        if competition_id is None:
            continue
        for row in conn.execute(
            """
            SELECT s.*, ta.name AS team_a_name, tb.name AS team_b_name, c.game_duration_minutes
            FROM slots s
            JOIN competitions c ON c.id = s.competition_id
            LEFT JOIN teams ta ON ta.id = s.team_a_id
            LEFT JOIN teams tb ON tb.id = s.team_b_id
            WHERE s.competition_id = ? AND s.slot_typ = 'Spiel'
            ORDER BY s.startzeit, s.id
            """,
            (competition_id,),
        ).fetchall():
            start = minuten(row["startzeit"])
            if start is None:
                continue
            dauer = row["game_duration_minutes"] or block["spielzeit"]
            result.append({
                "eintrag_id": eintrag["id"], "bestehend": True,
                "team_a": row["team_a_name"], "team_b": row["team_b_name"],
                "phase": row["phase"] or "Gruppenphase", "gruppe": row["gruppe"] or "",
                "note": row["note"] or "", "court_id": row["court_id"],
                "start_min": start, "ende_min": start + dauer, "spielzeit": dauer,
                "block_id": block["id"],
            })
    return result


def _lade_bestand(conn, daten, wettbewerbe, eintraege, bloecke, hinweise):
    """Nur im Bearbeiten-Modus (daten["bestand"]): ordnet den Plan der
    bestehenden Veranstaltung zu. Ein Zeitblock ist gesperrt, sobald ein
    Wettbewerb darin begonnene oder beendete Spiele bzw. Sechskampf-Ergebnisse
    hat; seine Wettbewerbe und Spiele bleiben dann unverändert (wie bei
    „Ganzen Schulpokal planen“). Gibt None zurück, wenn nichts zu bearbeiten
    ist, sonst die Angaben, die uebernehmen() braucht."""
    roh = daten.get("bestand") or {}
    event_id = roh.get("event_id")
    if not event_id or conn is None:
        return None
    if conn.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is None:
        hinweise.append(_hinweis(
            "blockierend", "Die Veranstaltung gibt es nicht mehr",
            "Sie wurde inzwischen gelöscht. Bitte den Entwurf verwerfen.", kennung="bestand-weg",
        ))
        return None
    vorhanden = {
        row["id"]: row for row in conn.execute(
            "SELECT id, name, final_source_ids FROM competitions WHERE event_id = ?", (event_id,)
        ).fetchall()
    }
    zuordnung = {eid: cid for eid, cid in (roh.get("wettbewerbe") or {}).items() if cid in vorhanden}
    show_zuordnung = {sid: cid for sid, cid in (roh.get("showspiele") or {}).items() if cid in vorhanden}
    ids = list(vorhanden)
    gesperrt = set()
    if ids:
        platzhalter = ", ".join("?" for _ in ids)
        gesperrt = {
            row["competition_id"] for row in conn.execute(
                f"""
                SELECT DISTINCT competition_id FROM slots
                WHERE competition_id IN ({platzhalter}) AND slot_typ = 'Spiel'
                  AND (COALESCE(status, 'geplant') != 'geplant'
                       OR score_a IS NOT NULL OR score_b IS NOT NULL)
                UNION
                SELECT DISTINCT competition_id FROM sixkampf_team_results
                WHERE competition_id IN ({platzhalter})
                """,
                ids + ids,
            ).fetchall()
        }

    gesperrte_bloecke = set()
    for eintrag in eintraege:
        if zuordnung.get(eintrag["id"]) in gesperrt:
            gesperrte_bloecke.add(eintrag["block"]["id"])
    for block in bloecke:
        if block["id"] not in gesperrte_bloecke:
            continue
        namen = []
        for eintrag in eintraege:
            if eintrag["block"] is block:
                eintrag["gesperrt"] = True
                namen.append(eintrag["name"])
        hinweise.append(_hinweis(
            "info", f"Zeitblock {block['ort']} {block['start']}–{block['ende']} hat schon begonnen",
            f"Es gibt bereits Ergebnisse. {', '.join(namen)} und die Spiele darin bleiben unverändert; "
            "Änderungen an diesem Block werden nicht übernommen.",
            kennung=f"bestand-gesperrt-{block['id']}", block=block["id"],
        ))

    abgeleitet = {
        f'{wb["key"]}#{teil or ""}' for wb in wettbewerbe for teil in wb["teilmannschaften"]
    }
    entfernen_ids = []
    for eid, cid in zuordnung.items():
        if eid in abgeleitet:
            continue
        if cid in gesperrt:
            hinweise.append(_hinweis(
                "blockierend", f"{vorhanden[cid]['name']} hat schon Ergebnisse",
                "Ein Wettbewerb mit Ergebnissen kann hier nicht entfernt werden. Bitte wieder auswählen "
                "oder in der Wettbewerbsverwaltung archivieren.", kennung=f"bestand-entfernen-{cid}",
            ))
        else:
            entfernen_ids.append(cid)
            hinweise.append(_hinweis(
                "info", f"{vorhanden[cid]['name']} wird gelöscht",
                "Der Wettbewerb ist nicht mehr ausgewählt und hat noch keine Ergebnisse.",
                kennung=f"bestand-entfernen-{cid}",
            ))

    gesperrte_eintraege = {e["id"] for e in eintraege if e.get("gesperrt")}
    gesperrte_showspiele = set()
    finals_loeschen = []
    show_keys = {show.get("id"): show for show in daten.get("showspiele") or []}
    for cid, row in vorhanden.items():
        if not (row["final_source_ids"] or "").strip():
            continue
        sid = next((s for s, c in show_zuordnung.items() if c == cid), None)
        show = show_keys.get(sid)
        quellen_gesperrt = show is not None and any(
            f"{show.get(side)}#" in gesperrte_eintraege for side in ("wettbewerb_a", "wettbewerb_b")
        )
        if cid in gesperrt or quellen_gesperrt:
            if sid is not None:
                gesperrte_showspiele.add(sid)
            continue
        finals_loeschen.append(cid)

    # Diese Spiele stehen im Plan (neu oder, bei gesperrten Blöcken, als
    # bestehende Spiele) und zählen deshalb nicht als fremde Belegung.
    # Gesperrte Showspiele sind nicht im Plan und bleiben fremde Belegung.
    ersetzt = set(zuordnung.values()) | set(finals_loeschen)
    return {
        "event_id": event_id,
        "wettbewerbe": zuordnung,
        "showspiele": show_zuordnung,
        "gesperrte_bloecke": gesperrte_bloecke,
        "gesperrte_showspiele": gesperrte_showspiele,
        "entfernen_ids": entfernen_ids,
        "entfernen_namen": [vorhanden[cid]["name"] for cid in entfernen_ids],
        "finals_loeschen": finals_loeschen,
        "ersetzt": ersetzt,
        "neu": [e["name"] for e in eintraege if e["id"] not in zuordnung],
        "gesperrt_namen": [e["name"] for e in eintraege if e.get("gesperrt")],
    }


def baue_spielraster(eintrag, blocker):
    """Zeitraster fuer die Spielplan-Vorschau eines Wettbewerbs (wie im
    Spielplan-Editor): je Startzeit eine Zeile, je Feld eine Spalte, damit man
    auf einen Blick sieht, wer gleichzeitig spielt. Programmpunkte/Puffer im
    Zeitblock, die diesen Wettbewerb betreffen, erscheinen als eigene Zeile
    ueber alle Spalten. Jede Zelle ist eine Liste, damit eine (eigentlich
    blockierende) Doppelbelegung sichtbar bleibt statt verloren zu gehen."""
    slots = eintrag.get("slots") or []
    felder = {}
    for slot in slots:
        felder.setdefault(slot.get("court_id"), slot.get("court_name") or "ohne Feld")
    spalten = sorted(felder.items(), key=lambda item: (item[0] is None, natural_sort_key(item[1])))
    spalten_index = {court_id: i for i, (court_id, _) in enumerate(spalten)}

    zeilen_by_start = {}
    for slot in slots:
        zeile = zeilen_by_start.setdefault(slot["start_min"], {
            "start_min": slot["start_min"],
            "zeit": f'{slot["startzeit"]}–{slot["endzeit"]}',
            "zellen": [[] for _ in spalten],
        })
        zeile["zellen"][spalten_index[slot.get("court_id")]].append(slot)

    block = eintrag.get("block") or {}
    start, ende = block.get("start_min"), block.get("ende_min")
    jahrgaenge = set(eintrag.get("jahrgaenge") or [])
    punkte = []
    for punkt in blocker or []:
        if start is None or ende is None or not (start <= punkt["start"] < ende):
            continue
        if punkt["jahrgaenge"] and not (set(punkt["jahrgaenge"]) & jahrgaenge):
            continue
        punkte.append({
            "start_min": punkt["start"],
            "zeit": f'{uhr(punkt["start"])}–{uhr(punkt["ende"])}',
            "punkt": punkt,
        })

    zeilen = sorted(zeilen_by_start.values(), key=lambda z: z["start_min"])
    # Programmpunkte vor Spielen gleicher Startzeit (sort ist stabil)
    zeilen = sorted(punkte + zeilen, key=lambda z: z["start_min"])
    for nummer, zeile in enumerate((z for z in zeilen if "zellen" in z), start=1):
        zeile["runde"] = nummer
    return {"felder": [name for _, name in spalten], "zeilen": zeilen}


def _disziplinen(einstellungen):
    raw = str(einstellungen.get("disziplinen") or "")
    result = []
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        part = part.strip()
        if part and part not in result:
            result.append(part)
    return result


TAGESENDE_MIN = 24 * 60


def _ende_text(ende_min):
    if ende_min >= TAGESENDE_MIN:
        return "erst nach Mitternacht"
    return f"um {uhr(ende_min)}"


def _ueberzug_hinweis(block, eintraege, blocker, ergebnis):
    """Plan reicht ueber das Blockende: blockierend, mit Loesungsvorschlaegen.
    Nichts davon wird automatisch angewendet - auch nicht der Modus aus
    einer Vorlage."""
    vorschlaege = []
    for spielzeit in range(block["spielzeit"] - 1, MIN_SPIELZEIT - 1, -1):
        fits, _ = _passt(block, eintraege, blocker, spielzeit=spielzeit)
        if fits:
            vorschlaege.append({
                "label": f"Spielzeit auf {spielzeit} Min. kürzen", "aktion": "block_spielzeit",
                "werte": {"block": block["id"], "spielzeit": spielzeit},
            })
            break
    if block["wechselzeit"] > 1:
        fits, _ = _passt(block, eintraege, blocker, wechselzeit=1)
        if fits:
            vorschlaege.append({
                "label": "Wechselzeit auf 1 Min. kürzen", "aktion": "block_wechselzeit",
                "werte": {"block": block["id"], "wechselzeit": 1},
            })
    neues_ende = -(-ergebnis["ende_min"] // 5) * 5
    if neues_ende < TAGESENDE_MIN:
        # Ein Block über Mitternacht ist kein sinnvoller Vorschlag ("bis 29:00").
        vorschlaege.append({
            "label": f"Zeitblock bis {uhr(neues_ende)} verlängern", "aktion": "block_ende",
            "werte": {"block": block["id"], "ende": uhr(neues_ende)},
        })
    vorlage_modus = any(e["wettbewerb"].get("modus_quelle") == "vorlage" for e in eintraege)
    keys = sorted({e["wettbewerb"]["key"] for e in eintraege}, key=lambda k: -len(
        next(e for e in eintraege if e["wettbewerb"]["key"] == k)["team_names"]))
    for key in keys:
        aktuell = next(e for e in eintraege if e["wettbewerb"]["key"] == key)["modus"]
        for modus in MODI:
            if modus == aktuell:
                continue
            fits, _ = _passt(block, eintraege, blocker, modi={key: modus})
            if fits:
                name = next(e for e in eintraege if e["wettbewerb"]["key"] == key)["wettbewerb"]["name"]
                vorschlaege.append({
                    "label": f"{name}: Turniermodus „{MODI[modus]}“", "aktion": "wb_modus",
                    "werte": {"wb": key, "modus": modus},
                })
                break
    titel = (
        "Turniermodus aus der Vorlage passt nicht ins Zeitfenster"
        if vorlage_modus else "Spielplan passt nicht ins Zeitfenster"
    )
    return _hinweis(
        "blockierend",
        f"{titel} ({block['ort']} {block['start']}–{block['ende']})",
        f"Das letzte Spiel endet {_ende_text(ergebnis['ende_min'])}. Programmpunkte und Puffer "
        "werden nicht gekürzt; der Turniermodus bleibt, bis du einen Vorschlag wählst.",
        kennung=f"ueberzug-{block['id']}", block=block["id"], vorschlaege=vorschlaege,
    )


def _abwechslung_info(block, ergebnis, hinweise):
    if block["ablauf"] != "abwechselnd" or len(ergebnis.get("gruppen") or []) < 2:
        return
    schritte = ergebnis["schritte"]
    wiederholt = sum(1 for a, b in zip(schritte, schritte[1:]) if a[0] == b[0])
    if wiederholt:
        namen = [g[0]["wettbewerb"]["name"] for g in ergebnis["gruppen"]]
        hinweise.append(_hinweis(
            "info", f"Zeitblock {block['ort']} {block['start']}–{block['ende']}: nicht durchgehend abwechselnd",
            f"{wiederholt} Runden laufen ohne Wechsel, weil die Wettbewerbe ({', '.join(namen)}) "
            "unterschiedlich viele Runden haben. Das ist nur ein Hinweis.",
            kennung=f"abwechslung-{block['id']}", block=block["id"],
        ))


def _feld_konflikte(slots, eintrag_by_id, bloecke, hinweise):
    """Dasselbe physische Feld zweimal zur selben Zeit: immer blockierend."""
    by_court = {}
    for slot in slots:
        if slot["court_id"] is not None:
            by_court.setdefault(slot["court_id"], []).append(slot)
    gemeldet = set()
    for court_id, court_slots in by_court.items():
        court_slots.sort(key=lambda s: s["start_min"])
        for i, a in enumerate(court_slots):
            for b in court_slots[i + 1:]:
                if b["start_min"] >= a["ende_min"]:
                    break
                key = (court_id, a.get("block_id"), b.get("block_id"))
                if key in gemeldet:
                    continue
                gemeldet.add(key)
                vorschlaege = []
                block_a = next((bl for bl in bloecke if bl["id"] == a.get("block_id")), None)
                block_b = next((bl for bl in bloecke if bl["id"] == b.get("block_id")), None)
                if block_a and block_b and block_a is not block_b:
                    ende_a = max(s["ende_min"] for s in slots if s.get("block_id") == block_a["id"])
                    vorschlaege.append({
                        "label": f"Block {block_b['start']}–{block_b['ende']} erst um {uhr(ende_a)} beginnen",
                        "aktion": "block_start", "werte": {"block": block_b["id"], "start": uhr(ende_a)},
                    })
                hinweise.append(_hinweis(
                    "blockierend", f"{a['court_name'] or 'Feld'} um {a['startzeit']} doppelt belegt",
                    f"{_slot_label(a, eintrag_by_id)} und {_slot_label(b, eintrag_by_id)} "
                    "brauchen dasselbe Feld. Ein Feld kann nicht doppelt genutzt werden.",
                    kennung=f"feld-{court_id}-{a['start_min']}-{a.get('block_id')}-{b.get('block_id')}",
                    vorschlaege=vorschlaege,
                ))


def _slot_label(slot, eintrag_by_id):
    eintrag = eintrag_by_id.get(slot["eintrag_id"])
    if eintrag is not None:
        return eintrag["name"]
    return "Showspiel" if slot.get("showspiel") else "ein Spiel"


def _klassen_konflikte(slots, eintrag_by_id, hinweise):
    """Klasse zur selben Zeit in zwei verschiedenen Wettbewerben: Warnung,
    bewusst bestaetigbar. Teilmannschaften desselben Wettbewerbs (Jungen/
    Maedchen) sind kein Konflikt. Die Kennung enthaelt die betroffenen
    Zeiten, damit eine Aenderung des Plans neu bewertet werden muss."""
    by_team = {}
    for slot in slots:
        for team_id in (slot.get("team_a_id"), slot.get("team_b_id")):
            if team_id is not None:
                by_team.setdefault(team_id, []).append(slot)
    paare = {}
    team_names = {}
    for eintrag in eintrag_by_id.values():
        for name, team_id in eintrag["team_ids"].items():
            team_names[team_id] = name
    for team_id, team_slots in by_team.items():
        team_slots.sort(key=lambda s: s["start_min"])
        for i, a in enumerate(team_slots):
            for b in team_slots[i + 1:]:
                if b["start_min"] >= a["ende_min"]:
                    break
                ea, eb = eintrag_by_id[a["eintrag_id"]], eintrag_by_id[b["eintrag_id"]]
                if ea["wettbewerb"]["key"] == eb["wettbewerb"]["key"]:
                    continue
                pair = tuple(sorted((ea["wettbewerb"]["key"], eb["wettbewerb"]["key"])))
                paare.setdefault(pair, {"teams": set(), "zeiten": set()})
                paare[pair]["teams"].add(team_id)
                paare[pair]["zeiten"].add((team_id, a["startzeit"], b["startzeit"]))
    for (key_a, key_b), info in sorted(paare.items()):
        name_a = next(e for e in eintrag_by_id.values() if e["wettbewerb"]["key"] == key_a)["wettbewerb"]["name"]
        name_b = next(e for e in eintrag_by_id.values() if e["wettbewerb"]["key"] == key_b)["wettbewerb"]["name"]
        klassen = sorted(team_names.get(t, "?") for t in info["teams"])
        zeiten = sorted(info["zeiten"])
        hinweise.append(_hinweis(
            "warnung",
            f"Klassen gleichzeitig in {name_a} und {name_b}",
            f"Betroffen: {', '.join(klassen)} (z. B. {zeiten[0][1]} und {zeiten[0][2]}). "
            "Bitte prüfen, ob die Klassen genug Spieler für beide haben. Vorschläge: anderen "
            "Zeitblock wählen, Block verschieben oder bewusst zulassen.",
            kennung="klasse-" + _kennung(key_a, key_b, sorted(zeiten)),
            bestaetigbar=True, wb=key_a,
        ))


def _fremde_belegung(conn, datum, slots, eintrag_by_id, hinweise, ausnahmen=()):
    """Spiele anderer, nicht archivierter Veranstaltungen am selben Tag:
    gleiches Feld blockiert, gleiche Klasse ist eine Warnung (andere
    Sportart) bzw. blockierend (gleiche Sportart, wie im Generator).
    ausnahmen: competition-ids, deren Spiele beim Übernehmen ersetzt werden
    (Bearbeiten einer bestehenden Veranstaltung)."""
    rows = conn.execute(
        """
        SELECT s.startzeit, s.court_id, s.team_a_id, s.team_b_id, s.competition_id,
               c.name AS competition_name,
               c.sportart, c.game_duration_minutes, co.name AS court_name
        FROM slots s
        JOIN competitions c ON c.id = s.competition_id
        LEFT JOIN events e ON e.id = c.event_id
        LEFT JOIN courts co ON co.id = s.court_id
        WHERE s.slot_typ = 'Spiel'
          AND COALESCE(c.status, '') != 'archiviert'
          AND COALESCE(e.status, '') != 'archiviert'
          AND COALESCE(NULLIF(c.competition_date, ''), e.event_date) = ?
        """,
        (datum,),
    ).fetchall()
    rows = [row for row in rows if row["competition_id"] not in ausnahmen]
    if not rows:
        return
    for row in rows:
        start = minuten(row["startzeit"])
        if start is None:
            continue
        ende = start + (row["game_duration_minutes"] or DEFAULT_GAME_DURATION_MINUTES)
        for slot in slots:
            if not (slot["start_min"] < ende and start < slot["ende_min"]):
                continue
            if row["court_id"] is not None and row["court_id"] == slot["court_id"]:
                hinweise.append(_hinweis(
                    "blockierend", f"{row['court_name']} um {slot['startzeit']} schon belegt",
                    f"Am selben Tag spielt dort „{row['competition_name']}“ ({row['startzeit']}).",
                    kennung=f"fremd-feld-{row['court_id']}-{slot['start_min']}",
                ))
            teams = {slot.get("team_a_id"), slot.get("team_b_id")} - {None}
            for team_id in teams & {row["team_a_id"], row["team_b_id"]}:
                eintrag = eintrag_by_id.get(slot["eintrag_id"])
                gleiche_sportart = eintrag is not None and str(row["sportart"] or "").casefold() == \
                    eintrag["wettbewerb"]["sportart"].casefold()
                hinweise.append(_hinweis(
                    "blockierend" if gleiche_sportart else "warnung",
                    f"Klasse spielt um {slot['startzeit']} auch in „{row['competition_name']}“",
                    "Spiel einer anderen Veranstaltung am selben Tag.",
                    kennung=f"fremd-team-{team_id}-{slot['start_min']}-{row['startzeit']}",
                    bestaetigbar=not gleiche_sportart,
                ))


# ---------------------------------------------------------------------------
# Tagesplan (Kalenderansicht des Dashboards)
# ---------------------------------------------------------------------------

def _spalte(daten, wb):
    jahrgaenge = wb["jahrgaenge"]
    if len(jahrgaenge) > 1:
        if all(j.isdigit() and int(j) >= 10 for j in jahrgaenge) and not ist_schulpokal(daten):
            return "Oberstufe"
        return "Jahrgang " + "/".join(jahrgaenge)
    jahrgang = jahrgaenge[0]
    if ist_schulpokal(daten) or not jahrgang.isdigit() or int(jahrgang) < 10:
        return f"Jahrgang {jahrgang}"
    return "Oberstufe"


def _spalten_key(label):
    rest = label.removeprefix("Jahrgang ")
    first = rest.split("/")[0]
    if label == "Oberstufe":
        return (1, 0, label)
    return (0, int(first) if first.isdigit() else 999, label)


def baue_tagesplan(daten, eintraege, slots, showspiele, blocker):
    """Gleiche Darstellung wie der Tagesplan auf der Startseite
    (build_day_timeline), aber aus den Entwurfsdaten statt aus der
    Datenbank."""
    competitions = []
    ids = {}
    # Teilmannschaften (Tischtennis Jungen/Maedchen) eines Wettbewerbs
    # erscheinen als ein Block, sonst wird die Spalte zu schmal.
    by_wb = {}
    for eintrag in eintraege:
        key = eintrag["wettbewerb"]["key"]
        if key in by_wb:
            ids[eintrag["id"]] = by_wb[key]
            continue
        index = len(competitions) + 1
        by_wb[key] = index
        ids[eintrag["id"]] = index
        block = eintrag["block"]
        competitions.append({
            "id": index, "event_id": 0, "name": eintrag["wettbewerb"]["name"],
            "sportart": eintrag["wettbewerb"]["sportart"],
            "jahrgang": eintrag["jahrgaenge"][0],
            "year_column": _spalte(daten, eintrag["wettbewerb"]),
            "location": eintrag["wettbewerb"]["einstellungen"]["ort"],
            "start_time": block["start"], "end_time": block["ende"],
            "final_source_ids": "",
        })
    for show in showspiele:
        if not show["gueltig"]:
            continue
        show_id = len(competitions) + 1
        ids[f"show:{show['id']}"] = show_id
        competitions.append({
            "id": show_id, "event_id": 0, "name": "Showspiel", "sportart": "", "jahrgang": "mixed",
            "location": "", "start_time": show["start"], "end_time": None,
            "final_source_ids": ",".join(str(ids[q]) for q in show["quellen"]),
        })
    timeline_slots = [
        {
            "competition_id": ids.get(slot["eintrag_id"]), "slot_typ": "Spiel", "phase": slot["phase"],
            "startzeit": slot["startzeit"], "game_end_time": slot["endzeit"], "status": "geplant",
        }
        for slot in slots if slot["eintrag_id"] in ids
    ]
    programmpunkte = [
        {"titel": b["titel"], "start_time": uhr(b["start"]), "end_time": uhr(b["ende"]),
         "jahrgaenge_liste": b["jahrgaenge"]}
        for b in blocker
    ]
    columns = sorted({c["year_column"] for c in competitions if c.get("year_column")}, key=_spalten_key)
    timeline = build_day_timeline(columns, competitions, slots=timeline_slots, programmpunkte=programmpunkte)
    for e in eintraege:
        e["farbe"] = 0
    if timeline is None:
        return None
    # Gleiche Sportfarbe wie im Tagesplan auch fuer die Spielplan-Vorschau
    # des Wettbewerbs (Karte und Zeitraster), siehe assign_sport_colors.
    farbe_by_id = {
        item.get("competition_id"): item.get("farbe", 0)
        for column in timeline["columns"] for item in column["items"]
        if not item.get("is_programm")
    }
    for e in eintraege:
        e["farbe"] = farbe_by_id.get(ids[e["id"]], 0)
    index_by_id = {}
    for position, e in enumerate(eintraege):
        index_by_id.setdefault(ids[e["id"]], position)
    for column in timeline["columns"]:
        for item in column["items"]:
            item["wb_index"] = index_by_id.get(item.get("competition_id"))
    return timeline
