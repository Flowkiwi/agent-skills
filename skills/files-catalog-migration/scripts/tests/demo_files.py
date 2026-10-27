"""Invented demo source files (xlsx and csv) shared by the tests."""
import zipfile
from typing import List
from xml.sax.saxutils import escape, quoteattr

DEMO_LABELS = [
    "Sku Marchand", "Référence Commerciale", "EAN", "Descriptif court", "Descriptif Marketing", "Marque",
    "Poids net du produit", "[Dimensions] Poids Net (kg)", "[Dimensions] Poids Brut (kg)", "Notice (PDF)",
    "[Caractéristiques] Matière du corps", "[Fonctions] Décongélation", "Coloris",
    "Catégorie", "Visuel principal", "Prix TTC", "Visuel secondaire 9", "Date de sortie",
]
DEMO_CODES = [
    "sku_marchand", "ref_com", "ean", "accroche", "descriptif", "marque", "poids_net", "dim_poids_net",
    "dim_poids_brut", "notice", "matiere", "decongelation", "coloris", "categorie", "visuel_princ", "prix",
    "visuel_sec_9", "date_sortie",
]
DEMO_ROWS = [  # sheet rows 3 to 9, columns A to R
    [("n", "1001"), "2226", ("n", "3601234560012"), "Grille-pain 2 fentes Inox 1400W | TOASTINE",
     "<p>Grille-pain 2 fentes.<br/>Matière : Inox.</p>", "TOASTINE", "1,8", ("n", "1.8"), ("n", "2.1"),
     "Le grille-pain 2 fentes, d\u2019une puissance de 1400 W\u2026", "Inox", "OUI", "argenté",
     "Catalogue/PEM/Grille-pain", "https://example.com/img/1001.jpg", "39,90", None, ("date", 45000)],
    [("n", "1002"), "2227", ("n", "3601234560029"), "Grille-pain 4 fentes \u00ab\u00a0Stop Eject\u00a0\u00bb", None,
     "Toastine", "2,4", ("n", "2.6000000000000001"), ("n", "1964"), "Voir notice", "Acier inoxydable", "non",
     "Noir", "Catalogue/PEM/Grille-pain", None, "59.90", None, ("date", 45366)],
    [None, "2228", None, "Grille-pain  vintage\u00a0crème ", "Grille-pain vintage crème",
     "L\u2019Atelier Pain", "1,5", None, ("n", "2"), None, "inox ", "NON", "Crème", "Catalogue/PEM/Grille-pain",
     None, "45", None, None],
    [("n", "1004"), None, ("n", "3601234560043"), "Bouilloire 1,7 L \u2014 sans fil",
     "<p>Bouilloire sans fil\u200b 1,7 L.</p>", "Brioche & Co", "1,1", ("n", "1.1"), ("n", "1.4"),
     "https://example.com/notice/1004.pdf", "Plastique", None, "Blanc", "Catalogue/PEM/Bouilloire",
     "https://example.com/img/1004.jpg", "29,90", None, None],
    [None, None, None, "Toaster sans référence", None, "TOASTINE", None, None, None, None, "Inox", "OUI",
     "Noir", "Catalogue/PEM/Grille-pain", None, None, None, None],
    [("n", "1005"), "2230", ("n", "3601234560050"), ("rich", ["Grille-pain ", "Duo - Noir"]),
     "<p>Grille-pain Duo, deux coloris.</p>", "TOASTINE", "1,9", ("n", "1.9"), ("n", "2.2"), None, "Inox", "OUI",
     "Noir", "Catalogue/PEM/Grille-pain", "https://example.com/img/1005.jpg", "49,90", None, None],
    [("inline", "1006"), "2230", ("n", "3601234560067"), "Grille-pain Duo - Rouge",
     "<p>Grille-pain Duo, deux coloris.</p>", "TOASTINE", "1,9", ("n", "1.9"), ("n", "2.2"), None, "Inox", "OUI",
     "Rouge", "Catalogue/PEM/Grille-pain", None, "49,90", None, None],
]
DEMO_CSV = ("SKU;Prix public TTC;Prix barré\r\n1001;39,90 €;49,90 €\r\n1002;59,90 €;\r\n"
            "1004;27,90 €;\r\n9999;10,00 €;\r\n")

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
DATE_STYLE = 1
PADDED_STYLES = {"pad13": 2, "pad5": 3}  # number formats 0000000000000 and 00000


def column_letter(index):
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def text_element(text):
    space = ' xml:space="preserve"' if text != text.strip() else ""
    return "<t%s>%s</t>" % (space, escape(text))


class SharedStrings:
    def __init__(self):
        self.items = []  # type: List[str]

    def add(self, xml):
        self.items.append(xml)
        return len(self.items) - 1

    def xml(self):
        return '<sst xmlns="%s" count="%d" uniqueCount="%d">%s</sst>' % (
            MAIN_NS, len(self.items), len(self.items), "".join("<si>%s</si>" % item for item in self.items))


def cell_xml(value, ref, strings):
    attrs = ' r="%s"' % ref if ref else ""
    if value is None:
        return "<c%s/>" % attrs
    if isinstance(value, str):
        return '<c%s t="s"><v>%d</v></c>' % (attrs, strings.add(text_element(value)))
    kind, payload = value
    if kind == "inline":
        return '<c%s t="inlineStr"><is>%s</is></c>' % (attrs, text_element(payload))
    if kind == "rich":
        runs = "".join('<r><rPr><b val="%d"/></rPr>%s</r>' % (i % 2, text_element(run)) for i, run in enumerate(payload))
        return '<c%s t="s"><v>%d</v></c>' % (attrs, strings.add(runs))
    if kind == "n":
        return "<c%s><v>%s</v></c>" % (attrs, payload)
    if kind == "date":
        return '<c%s s="%d"><v>%s</v></c>' % (attrs, DATE_STYLE, payload)
    if kind in PADDED_STYLES:
        return '<c%s s="%d"><v>%s</v></c>' % (attrs, PADDED_STYLES[kind], payload)
    if kind in ("b", "e"):
        return '<c%s t="%s"><v>%s</v></c>' % (attrs, kind, escape(payload))
    raise ValueError("unknown demo cell kind: %r" % kind)


def sheet_xml(sheet, strings):
    no_refs = set(sheet.get("no_refs_rows", []))
    rows = []
    for number, row in enumerate(sheet["rows"], 1):
        if number in no_refs:
            rows.append("<row>%s</row>" % "".join(cell_xml(value, None, strings) for value in row))
        else:
            cells = "".join(cell_xml(value, "%s%d" % (column_letter(i), number), strings)
                            for i, value in enumerate(row) if value is not None)
            rows.append('<row r="%d">%s</row>' % (number, cells))
    merged = sheet.get("merged", [])
    merge_xml = ""
    if merged:
        merge_xml = '<mergeCells count="%d">%s</mergeCells>' % (
            len(merged), "".join('<mergeCell ref="%s"/>' % ref for ref in merged))
    return '<worksheet xmlns="%s" xmlns:r="%s"><sheetData>%s</sheetData>%s</worksheet>' % (
        MAIN_NS, REL_NS, "".join(rows), merge_xml)


STYLES = (
    '<styleSheet xmlns="%s">'
    '<numFmts count="2"><numFmt numFmtId="164" formatCode="0000000000000"/>'
    '<numFmt numFmtId="165" formatCode="00000"/></numFmts>'
    '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
    '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="165" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
    '</styleSheet>' % MAIN_NS
)


def write_xlsx(path, sheets, date1904=False):
    strings = SharedStrings()
    sheet_parts = [sheet_xml(sheet, strings) for sheet in sheets]
    count = len(sheets)
    overrides = "".join(
        '<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % n for n in range(1, count + 1))
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>%s</Types>' % overrides)
    root_rels = (
        '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>' % (PKG_REL_NS, REL_NS))
    sheet_entries = "".join(
        '<sheet name=%s sheetId="%d"%s r:id="rId%d"/>' % (
            quoteattr(sheet["name"]), n, ' state="hidden"' if sheet.get("hidden") else "", n)
        for n, sheet in enumerate(sheets, 1))
    workbook = (
        '<workbook xmlns="%s" xmlns:r="%s"><workbookPr%s/><sheets>%s</sheets></workbook>' % (
            MAIN_NS, REL_NS, ' date1904="1"' if date1904 else "", sheet_entries))
    rels = "".join(
        '<Relationship Id="rId%d" Type="%s/worksheet" Target="worksheets/sheet%d.xml"/>' % (n, REL_NS, n)
        for n in range(1, count + 1))
    rels += '<Relationship Id="rId%d" Type="%s/styles" Target="styles.xml"/>' % (count + 1, REL_NS)
    rels += '<Relationship Id="rId%d" Type="%s/sharedStrings" Target="sharedStrings.xml"/>' % (count + 2, REL_NS)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="%s">%s</Relationships>' % (PKG_REL_NS, rels))
        zf.writestr("xl/styles.xml", STYLES)
        zf.writestr("xl/sharedStrings.xml", strings.xml())
        for n, part in enumerate(sheet_parts, 1):
            zf.writestr("xl/worksheets/sheet%d.xml" % n, part)


def write_demo_xlsx(path):
    write_xlsx(path, [
        {"name": "Catalogue", "rows": [DEMO_LABELS, DEMO_CODES] + DEMO_ROWS, "merged": [], "hidden": False,
         "no_refs_rows": [9]},
        {"name": "Notes", "rows": [["Export du 12/03 - demo"]], "merged": ["A1:C1"], "hidden": False,
         "no_refs_rows": []},
    ])


def write_demo_csv(path):
    with open(path, "wb") as f:
        f.write(DEMO_CSV.encode("cp1252"))
