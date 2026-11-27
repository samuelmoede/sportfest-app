"""Regeln je Sportart als Hinweis bei der Ergebniseingabe.

Der Regeltext steht je Wettbewerb in competitions.regeln und wird im
Einrichtungsassistenten je Sportart gepflegt (Schritt Sportarten) oder auf
/wettbewerbe je Wettbewerb. Die Ergebniseingabe (Raster und Handy-
Feldansicht) zeigt ihn eingeklappt unter „Regeln“, damit Schiedsrichter bei
Bedarf nachlesen koennen."""

REGELN_MAX_LAENGE = 4000


def normalisiere_regeln(value):
    """Speicherwert fuer competitions.regeln: Zeilenenden vereinheitlicht,
    gekuerzt, leer -> None."""
    text = str(value or "").replace("\r\n", "\n").strip()
    return text[:REGELN_MAX_LAENGE] or None


def _wert(row, key):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def regeln_hinweise(competitions):
    """Liste {"titel", "text"} fuer die angezeigten Wettbewerbe - je Sportart
    einmal. Haben Wettbewerbe derselben Sportart verschiedene Texte, steht
    jeder Text mit dem Namen seines Wettbewerbs da. Ohne Text: kein Eintrag."""
    je_sportart = {}
    for competition in competitions or []:
        text = str(_wert(competition, "regeln") or "").strip()
        if not text:
            continue
        sportart = str(_wert(competition, "sportart") or "").strip() or "Wettbewerb"
        eintraege = je_sportart.setdefault(sportart.casefold(), {"sportart": sportart, "texte": []})
        if all(t["text"] != text for t in eintraege["texte"]):
            eintraege["texte"].append({"text": text, "name": _wert(competition, "name") or sportart})
    result = []
    for eintraege in sorted(je_sportart.values(), key=lambda e: e["sportart"].casefold()):
        if len(eintraege["texte"]) == 1:
            result.append({"titel": eintraege["sportart"], "text": eintraege["texte"][0]["text"]})
        else:
            for eintrag in eintraege["texte"]:
                result.append({"titel": eintrag["name"], "text": eintrag["text"]})
    return result
