"""Gesamtwertung einer Veranstaltung: Wertungsarten und Gleichstand.

Welche Wertungsart gilt, steht je Veranstaltung in events.wertung_modus
(Einrichtungsassistent, Schritt Abschluss). Leer = bisheriges Verhalten:
Schulpokal addiert Spielpunkte, alle anderen Platzpunkte je Wettbewerb.

„Platzpunkte je Sportart“ (Wunsch der Sportfachschaft, 2026-11-26) ist
mehrstufig:

1. Jeder Wettbewerb wird wie gewohnt als Tabelle gewertet (z. B. Tischtennis
   Jungen: Sieg 2, Unentschieden 1, Niederlage 0) und vergibt Platzpunkte
   (1. Platz = Anzahl Klassen, dann absteigend).
2. Wettbewerbe derselben Sportart und desselben Jahrgangs, die nur nach
   Teilmannschaft getrennt sind (Jungen/Mädchen), werden addiert. Aus der
   Summe entsteht der Platz in der Sportart, und dieser Platz bringt wieder
   Platzpunkte (1. = Anzahl Klassen). Ohne Teilmannschaften ändert dieser
   Schritt nichts.
3. Die Platzpunkte aller Sportarten ergeben die Jahrgangswertung.

Bei Gleichstand in der Gesamtwertung kann zuerst der direkte Vergleich in
einer Sportart entscheiden (events.wertung_entscheid_sportart, z. B.
Volleyball), danach die bisherigen Kriterien (Platzierungsbilanz,
Tor-/Punktedifferenz)."""

from app.services.ranking_points_service import assign_points_by_placement_groups
from app.utils.formatting import format_points_value

WERTUNG_SPIELPUNKTE = "spielpunkte"
WERTUNG_PLATZPUNKTE_WETTBEWERB = "platzpunkte_wettbewerb"
WERTUNG_PLATZPUNKTE_SPORTART = "platzpunkte_sportart"

WERTUNG_MODI = {
    WERTUNG_PLATZPUNKTE_SPORTART: "Platzpunkte je Sportart",
    WERTUNG_PLATZPUNKTE_WETTBEWERB: "Platzpunkte je Wettbewerb",
    WERTUNG_SPIELPUNKTE: "Spielpunkte addieren",
}

WERTUNG_BESCHREIBUNG = {
    WERTUNG_PLATZPUNKTE_SPORTART: (
        "Jede Sportart vergibt Platzpunkte (1. Platz = Anzahl Klassen, dann absteigend). "
        "Ist eine Sportart nach Teilmannschaften getrennt (z. B. Jungen/Mädchen), bekommt "
        "jede Teilmannschaft erst eigene Platzpunkte; deren Summe ergibt den Platz in der "
        "Sportart und daraus wieder die Platzpunkte."
    ),
    WERTUNG_PLATZPUNKTE_WETTBEWERB: (
        "Jeder Wettbewerb vergibt Platzpunkte (1. Platz = Anzahl Klassen, dann absteigend); "
        "Teilmannschaften zählen dabei jede für sich."
    ),
    WERTUNG_SPIELPUNKTE: (
        "Die Spielpunkte (Sieg, Unentschieden) aller Wettbewerbe eines Jahrgangs werden addiert."
    ),
}


def standard_modus(event_type):
    """Bisheriges Verhalten, wenn an der Veranstaltung nichts eingestellt ist."""
    return WERTUNG_SPIELPUNKTE if event_type == "Schulpokal" else WERTUNG_PLATZPUNKTE_WETTBEWERB


def normalisiere_modus(value):
    value = str(value or "").strip()
    return value if value in WERTUNG_MODI else None


def wirksamer_modus(event):
    """Modus einer Veranstaltung (sqlite3.Row oder dict)."""
    if event is None:
        return WERTUNG_PLATZPUNKTE_WETTBEWERB
    keys = event.keys()
    gespeichert = normalisiere_modus(event["wertung_modus"]) if "wertung_modus" in keys else None
    return gespeichert or standard_modus(event["event_type"] if "event_type" in keys else None)


def entscheid_sportart(event):
    if event is None or "wertung_entscheid_sportart" not in event.keys():
        return None
    return str(event["wertung_entscheid_sportart"] or "").strip() or None


# ---------------------------------------------------------------------------
# Platzpunkte je Sportart
# ---------------------------------------------------------------------------

def sportart_schluessel(competition):
    """Wettbewerbe mit gleichem Schluessel bilden eine Sportart-Wertung:
    gleiche Sportart, gleicher Jahrgang, nur die Teilmannschaft unterscheidet
    sich."""
    return (str(competition["sportart"] or "").strip().casefold(), str(competition["jahrgang"]))


def sportart_wertung(teile, team_ids):
    """Stufe 2 der Wertung „Platzpunkte je Sportart“.

    teile: Liste von dicts {"label", "punkte": {team_id: Platzpunkte},
    "plaetze": {team_id: Platz}} - je Teilmannschaft (bzw. ein Eintrag, wenn
    die Sportart nicht getrennt ist).
    team_ids: alle Klassen dieser Sportart (bestimmt die Anzahl N).

    Gibt {team_id: {"summe", "platz", "punkte", "teile": [...]}} zurueck.
    Klassen ohne Ergebnis in einer Teilmannschaft bekommen dort 0 Punkte.
    Gleiche Summe = gleicher Platz und gleiche Platzpunkte (wie bei den
    Tabellen der Wettbewerbe)."""
    team_ids = list(dict.fromkeys(team_ids))
    if not team_ids or not teile:
        return {}
    rows = []
    for team_id in team_ids:
        details = []
        summe = 0.0
        for teil in teile:
            punkte = teil["punkte"].get(team_id)
            summe += punkte or 0
            details.append({
                "label": teil.get("label") or "",
                "platz": teil.get("plaetze", {}).get(team_id),
                "punkte": punkte,
            })
        rows.append({"team_id": team_id, "summe": summe, "teile": details})
    rows.sort(key=lambda row: -row["summe"])

    place = 0
    previous = None
    for index, row in enumerate(rows, start=1):
        if previous is None or row["summe"] != previous:
            place = index
        row["platz"] = place
        previous = row["summe"]

    platzpunkte = [float(value) for value in range(len(team_ids), 0, -1)]
    assign_points_by_placement_groups(rows, platzpunkte, placement_key="platz", points_key="punkte")
    return {row["team_id"]: row for row in rows}


def sportart_detail_text(eintrag):
    """'Jungen 1. (7) + Mädchen 4. (4) = 11 → 1. Platz' für die Tabelle."""
    teile = eintrag.get("teile") or []
    if len(teile) < 2:
        return ""
    stuecke = []
    for teil in teile:
        if teil["punkte"] is None:
            stuecke.append(f"{teil['label']} –")
        else:
            stuecke.append(f"{teil['label']} {teil['platz']}. ({format_points_value(teil['punkte'])})")
    return f"{' + '.join(stuecke)} = {format_points_value(eintrag['summe'])} → {eintrag['platz']}. Platz"


# ---------------------------------------------------------------------------
# Gleichstand in der Gesamtwertung
# ---------------------------------------------------------------------------

def direkter_vergleich(team_ids, spiele):
    """Mini-Tabelle nur aus den Spielen der punktgleichen Klassen
    untereinander. spiele: Liste von dicts {team_a_id, team_b_id, score_a,
    score_b, points_win, points_draw, points_loss}. Gibt {team_id: (pkt,
    diff, plus)} zurueck."""
    team_set = set(team_ids)
    stats = {team_id: [0.0, 0, 0] for team_id in team_ids}
    for spiel in spiele:
        a, b = spiel["team_a_id"], spiel["team_b_id"]
        if a not in team_set or b not in team_set or a == b:
            continue
        score_a, score_b = spiel["score_a"], spiel["score_b"]
        if score_a is None or score_b is None:
            continue
        stats[a][1] += score_a - score_b
        stats[b][1] += score_b - score_a
        stats[a][2] += score_a
        stats[b][2] += score_b
        if score_a > score_b:
            stats[a][0] += spiel["points_win"]
            stats[b][0] += spiel["points_loss"]
        elif score_b > score_a:
            stats[b][0] += spiel["points_win"]
            stats[a][0] += spiel["points_loss"]
        else:
            stats[a][0] += spiel["points_draw"]
            stats[b][0] += spiel["points_draw"]
    return {team_id: tuple(values) for team_id, values in stats.items()}


def sortiere_gesamtwertung(rows, *, spiele_entscheid=None, entscheid_label=None):
    """Sortiert die Zeilen einer Jahrgangswertung und setzt row['place'].

    Reihenfolge: Gesamtpunkte, bei Gleichstand (optional) direkter Vergleich
    in der Entscheid-Sportart, dann Platzierungsbilanz (Anzahl 1. Plätze,
    2. Plätze ...), dann aufsummierte Tor-/Punktedifferenz. Wo der direkte
    Vergleich entschieden hat, steht ein Hinweis in row['tie_note']."""
    max_place = max((max(row["placements"], default=0) for row in rows), default=0)

    def bilanz(row):
        counts = row["placement_counts"]
        return tuple(-counts.get(place, 0) for place in range(1, max_place + 1))

    gruppen = {}
    for row in rows:
        gruppen.setdefault(row["total_points"], []).append(row)
    direkt = {}
    for gruppe in gruppen.values():
        if len(gruppe) < 2 or spiele_entscheid is None:
            continue
        stats = direkter_vergleich([row["team_id"] for row in gruppe], spiele_entscheid)
        for row in gruppe:
            pkt, diff, plus = stats[row["team_id"]]
            direkt[row["team_id"]] = (-pkt, -diff, -plus)

    def rang(row):
        return (
            -row["total_points"],
            direkt.get(row["team_id"], ()),
            bilanz(row),
            -row["tournament_score_diff"],
        )

    rows.sort(key=lambda row: (rang(row), row["team"].lower()))

    place = 0
    previous = None
    for index, row in enumerate(rows, start=1):
        key = rang(row)
        if previous is None or key != previous:
            place = index
        row["place"] = place
        row["tie_note"] = None
        previous = key

    # Hinweis nur, wo der direkte Vergleich zwei punktgleiche Klassen getrennt hat.
    if entscheid_label:
        for vorher, nachher in zip(rows, rows[1:]):
            if (
                vorher["total_points"] == nachher["total_points"]
                and direkt.get(vorher["team_id"]) != direkt.get(nachher["team_id"])
            ):
                hinweis = f"Gleichstand: direkter Vergleich {entscheid_label}"
                vorher["tie_note"] = vorher["tie_note"] or hinweis
                nachher["tie_note"] = hinweis
    return rows
