"""PDF-Erzeugung fuer den Urkunden-Generator (A4 hochkant, eine Urkunde pro
Seite) mit fpdf2.

fpdf2 ist reines Python (einzige Abhaengigkeiten Pillow/fonttools mit
fertigen Wheels) und braucht im python:3.12-slim-Image keine
Systembibliotheken. Fuer Umlaute und Sonderzeichen (z.B. "Dvořák") wird die
mitgelieferte freie Schrift DejaVu Sans eingebettet (app/fonts/), statt der
PDF-Standardschriften, die nur Latin-1 koennen.
"""

from io import BytesIO
from pathlib import Path

from fpdf import FPDF
from PIL import Image

from app.services.urkunden_service import (
    URKUNDE_EHREN,
    URKUNDE_SIEGER,
    URKUNDE_TEILNEHMER,
)


FONT_DIR = Path(__file__).resolve().parents[1] / "fonts"
FONT = "DejaVu"

PAGE_WIDTH = 210
PAGE_HEIGHT = 297
CONTENT_MARGIN = 25
CONTENT_WIDTH = PAGE_WIDTH - 2 * CONTENT_MARGIN

FARBE_TEXT = (33, 37, 41)
FARBE_GRAU = (108, 117, 125)
FARBE_AKZENT = (31, 58, 95)
FARBEN_PLATZ = {
    1: (184, 134, 11),   # Gold
    2: (128, 128, 128),  # Silber
    3: (160, 82, 45),    # Bronze
}

TITEL = {
    URKUNDE_SIEGER: "SIEGERURKUNDE",
    URKUNDE_EHREN: "EHRENURKUNDE",
    URKUNDE_TEILNEHMER: "TEILNEHMERURKUNDE",
}


def passe_schriftgroesse(pdf, text: str, max_breite: float, start: float, minimum: float, style: str = "B"):
    """Groesste Schriftgroesse zwischen minimum und start, bei der text in
    eine Zeile der Breite max_breite passt. Passt es auch bei minimum nicht,
    wird minimum zurueckgegeben und der Text umbrochen (nie abgeschnitten)."""
    size = start
    while size > minimum:
        pdf.set_font(FONT, style, size)
        if pdf.get_string_width(text) <= max_breite:
            return size
        size -= 0.5
    pdf.set_font(FONT, style, minimum)
    return minimum


def _pt_to_mm(size: float) -> float:
    return size * 25.4 / 72


class UrkundenPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_auto_page_break(False)
        self.set_margins(CONTENT_MARGIN, 20, CONTENT_MARGIN)
        self.add_font(FONT, "", str(FONT_DIR / "DejaVuSans.ttf"))
        self.add_font(FONT, "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
        self.set_title("Urkunden")
        self.set_creator("Sportfest-App")


def _zentrierte_zeile(pdf, y: float, text: str, size: float, style: str = "", farbe=FARBE_TEXT):
    pdf.set_font(FONT, style, size)
    pdf.set_text_color(*farbe)
    pdf.set_xy(CONTENT_MARGIN, y)
    pdf.cell(CONTENT_WIDTH, _pt_to_mm(size) * 1.3, text, align="C")
    return y + _pt_to_mm(size) * 1.3


def _passende_zeile(pdf, y: float, text: str, start: float, minimum: float, style: str = "B", farbe=FARBE_TEXT):
    """Zentrierter Text, automatisch verkleinert; umbricht notfalls auf
    mehrere Zeilen, damit nichts abgeschnitten wird. Gibt das neue y zurueck."""
    size = passe_schriftgroesse(pdf, text, CONTENT_WIDTH, start, minimum, style)
    pdf.set_text_color(*farbe)
    pdf.set_xy(CONTENT_MARGIN, y)
    line_height = _pt_to_mm(size) * 1.3
    pdf.multi_cell(CONTENT_WIDTH, line_height, text, align="C")
    return pdf.get_y()


def _rahmen(pdf, farbe):
    pdf.set_draw_color(*farbe)
    pdf.set_line_width(1.2)
    pdf.rect(10, 10, PAGE_WIDTH - 20, PAGE_HEIGHT - 20)
    pdf.set_line_width(0.3)
    pdf.rect(13, 13, PAGE_WIDTH - 26, PAGE_HEIGHT - 26)


def _logo(pdf, logo_bytes, y: float, max_hoehe: float = 30, max_breite: float = 70):
    if not logo_bytes:
        return y
    try:
        with Image.open(BytesIO(logo_bytes)) as image:
            breite_px, hoehe_px = image.size
    except Exception:
        return y
    if not breite_px or not hoehe_px:
        return y
    faktor = min(max_breite / breite_px, max_hoehe / hoehe_px)
    breite, hoehe = breite_px * faktor, hoehe_px * faktor
    pdf.image(BytesIO(logo_bytes), x=(PAGE_WIDTH - breite) / 2, y=y, w=breite, h=hoehe)
    return y + hoehe + 4


def _zeichne_urkunde(pdf, eintrag, layout, logo_bytes):
    pdf.add_page()
    typ = eintrag.get("urkundentyp") or URKUNDE_TEILNEHMER
    platz = eintrag.get("platz")
    zeigt_platz = typ != URKUNDE_TEILNEHMER and platz is not None
    akzent = FARBEN_PLATZ.get(platz, FARBE_AKZENT) if typ == URKUNDE_SIEGER else FARBE_AKZENT

    _rahmen(pdf, akzent)

    y = _logo(pdf, logo_bytes, 22)
    if y == 22:
        y = 30
    if layout.get("schulname"):
        y = _passende_zeile(pdf, y, layout["schulname"], 13, 9, style="", farbe=FARBE_GRAU)
    veranstaltung = layout.get("veranstaltungsname") or eintrag.get("veranstaltung") or ""
    if veranstaltung:
        y = _passende_zeile(pdf, y + 1, veranstaltung, 17, 10, style="B", farbe=FARBE_TEXT)

    # Titel
    y = max(y + 10, 85)
    pdf.set_char_spacing(1.5)
    y = _passende_zeile(pdf, y, TITEL.get(typ, typ.upper()), 34, 20, style="B", farbe=akzent)
    pdf.set_char_spacing(0)

    pdf.set_draw_color(*akzent)
    pdf.set_line_width(0.6)
    pdf.line(PAGE_WIDTH / 2 - 25, y + 4, PAGE_WIDTH / 2 + 25, y + 4)

    y = _zentrierte_zeile(pdf, y + 14, "verliehen an", 13, farbe=FARBE_GRAU)
    y = _passende_zeile(pdf, y + 4, eintrag.get("recipient_name") or "", 34, 14, style="B")
    if eintrag.get("jahrgang_label") and eintrag.get("recipient_name") != eintrag.get("jahrgang_label"):
        y = _zentrierte_zeile(pdf, y + 1, eintrag["jahrgang_label"], 12, farbe=FARBE_GRAU)

    if zeigt_platz:
        y = _zentrierte_zeile(pdf, y + 12, "für den", 13, farbe=FARBE_GRAU)
        farbe_platz = FARBEN_PLATZ.get(platz, FARBE_AKZENT) if typ == URKUNDE_SIEGER else FARBE_AKZENT
        y = _zentrierte_zeile(pdf, y + 2, f"{platz}. Platz", 30, style="B", farbe=farbe_platz)
    else:
        y = _zentrierte_zeile(pdf, y + 12, "für die erfolgreiche Teilnahme", 15, farbe=FARBE_TEXT)

    quelle = f"{eintrag.get('quelle_praefix', '')} {eintrag.get('quelle_label', '')}".strip()
    y = _passende_zeile(pdf, y + 4, quelle, 16, 10, style="", farbe=FARBE_TEXT)

    if eintrag.get("leistung"):
        _passende_zeile(pdf, y + 6, f"Leistung: {eintrag['leistung']}", 13, 9, style="", farbe=FARBE_GRAU)

    # Ort/Datum und Unterschriften
    datum = layout.get("datum") or eintrag.get("datum") or ""
    ort = layout.get("ort") or ""
    ort_datum = ", den ".join(part for part in (ort, datum) if part) if ort else datum
    if ort_datum:
        _zentrierte_zeile(pdf, 228, ort_datum, 12, farbe=FARBE_TEXT)

    linie_y = 262
    breite = 65
    links_x = CONTENT_MARGIN
    rechts_x = PAGE_WIDTH - CONTENT_MARGIN - breite
    pdf.set_draw_color(*FARBE_GRAU)
    pdf.set_line_width(0.3)
    for x, label in (
        (links_x, layout.get("unterschrift_links", "")),
        (rechts_x, layout.get("unterschrift_rechts", "")),
    ):
        pdf.line(x, linie_y, x + breite, linie_y)
        size = passe_schriftgroesse(pdf, label or "", breite, 10, 7, style="")
        pdf.set_text_color(*FARBE_GRAU)
        pdf.set_xy(x, linie_y + 1.5)
        pdf.cell(breite, _pt_to_mm(size) * 1.3, label or "", align="C")


def pdf_seite_als_png(pdf_bytes: bytes, seite: int = 0, breite_px: int = 900) -> bytes:
    """Rendert eine PDF-Seite als PNG fuer die Vorschau im Browser. Handy-
    Browser zeigen eingebettete PDFs (iframe) nicht an, ein Bild schon. Das
    Bild entsteht aus genau dem PDF, das auch gedruckt wird (pypdfium2:
    fertige Wheels mit eingebautem PDFium, keine Systempakete noetig)."""
    import pypdfium2

    document = pypdfium2.PdfDocument(pdf_bytes)
    try:
        page = document[seite]
        bitmap = page.render(scale=breite_px / page.get_width())
        buffer = BytesIO()
        bitmap.to_pil().save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    finally:
        document.close()


def erzeuge_urkunden_pdf(eintraege, layout, logo_bytes=None) -> bytes:
    pdf = UrkundenPDF()
    for eintrag in eintraege:
        _zeichne_urkunde(pdf, eintrag, layout, logo_bytes)
    if not eintraege:
        pdf.add_page()
        _zentrierte_zeile(pdf, 130, "Keine Urkunden für die aktuelle Auswahl.", 14, farbe=FARBE_GRAU)
    return bytes(pdf.output())
