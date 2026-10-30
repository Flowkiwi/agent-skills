import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import demo_files  # noqa: E402
import sources  # noqa: E402

STRICT = "http://purl.oclc.org/ooxml/spreadsheetml/main"
STRICT_REL = "http://purl.oclc.org/ooxml/officeDocument/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def write_parts(path, parts):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in parts.items():
            zf.writestr(name, text)


class TmpDirTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.other = os.path.join(self.tmp, "other.xlsx")


class XlsxTest(TmpDirTest):
    def setUp(self):
        super().setUp()
        self.path = os.path.join(self.tmp, "demo-catalogue.xlsx")
        demo_files.write_demo_xlsx(self.path)
        self.tables = sources.read_xlsx(self.path)
        self.cat = self.tables[0]

    def test_sheets_and_merged(self):
        self.assertEqual([t.name for t in self.tables], ["Catalogue", "Notes"])
        self.assertEqual(self.tables[1].merged, ["A1:C1"])
        self.assertEqual(len(self.cat.rows), 9)
        self.assertTrue(all(len(r) == 18 for r in self.cat.rows))

    def test_cell_types(self):
        r3, r4, r8, r9 = self.cat.rows[2], self.cat.rows[3], self.cat.rows[7], self.cat.rows[8]
        self.assertEqual(r3[0], "1001")
        self.assertEqual(r3[2], "3601234560012")
        self.assertEqual(r4[7], "2.6")
        self.assertEqual(r4[8], "1964")
        self.assertEqual(r3[17], "2023-03-15")
        self.assertEqual(r4[17], "2024-03-15")
        self.assertEqual(r8[3], "Grille-pain Duo - Noir")
        self.assertEqual(r9[0], "1006")
        self.assertEqual(r9[12], "Rouge")          # row without r attributes keeps its columns
        self.assertIsNone(self.cat.rows[4][0])     # sparse row
        self.assertEqual(r4[3], "Grille-pain 4 fentes \u00ab\u00a0Stop Eject\u00a0\u00bb")  # raw, not cleaned

    def test_bool_error_and_1904(self):
        demo_files.write_xlsx(self.other, [{"name": "S", "rows": [[("b", "1"), ("e", "#N/A"), ("date", 0)]]}], date1904=True)
        self.assertEqual(sources.read_xlsx(self.other)[0].rows[0], ["TRUE", None, "1904-01-01"])

    def test_zero_padded_number_formats_keep_their_leading_zeros(self):
        demo_files.write_xlsx(self.other, [{"name": "S", "rows": [
            [("pad13", "12345678905"), ("pad5", "42"), ("pad13", "1.2345678905E10"), ("pad5", "123456"),
             ("pad5", "1.5"), ("n", "42")]]}])
        self.assertEqual(sources.read_xlsx(self.other)[0].rows[0],
                         ["0012345678905", "00042", "0012345678905", "123456", "1.5", "42"])

    def test_zero_pad_width(self):
        self.assertEqual(sources.zero_pad_width("0000000000000"), 13)
        self.assertEqual(sources.zero_pad_width("00000"), 5)
        for code in ("0", "0.00", "#,##0", '"EAN "0000', "General", ""):
            self.assertIsNone(sources.zero_pad_width(code), code)

    def test_helpers(self):
        self.assertEqual(sources.column_letter(26), "AA")
        self.assertEqual(sources.column_index("CH"), 85)
        self.assertEqual(sources.excel_number_text("1E-3"), "0.001")
        self.assertEqual(sources.excel_date(45000.5), "2023-03-15T12:00:00")

    def test_demo_rows_round_trip(self):
        self.assertEqual(self.cat.file, "demo-catalogue.xlsx")
        self.assertEqual(self.cat.rows[0], demo_files.DEMO_LABELS)
        self.assertEqual(self.cat.rows[1], demo_files.DEMO_CODES)
        self.assertEqual(self.cat.rows[4][3], "Grille-pain  vintage\u00a0crème ")
        self.assertEqual(self.cat.rows[5][3], "Bouilloire 1,7 L \u2014 sans fil")
        self.assertEqual(self.cat.rows[5][4], "<p>Bouilloire sans fil\u200b 1,7 L.</p>")
        self.assertEqual(self.cat.rows[2][9], "Le grille-pain 2 fentes, d\u2019une puissance de 1400 W\u2026")
        self.assertEqual(self.cat.rows[4][16:], [None, None])
        self.assertEqual(self.cat.rows[8][14:], [None, "49,90", None, None])
        self.assertEqual(self.tables[1].rows, [["Export du 12/03 - demo"]])
        self.assertFalse(self.tables[1].hidden)


class XlsxEdgeTest(TmpDirTest):
    def test_hidden_sheet_and_empty_rows_kept(self):
        demo_files.write_xlsx(self.other, [
            {"name": "Visible", "rows": [["a"]]},
            {"name": "Caché", "rows": [["x", None, "z"], [], [None, ("n", "0.1")]], "hidden": True},
        ])
        visible, hidden = sources.read_xlsx(self.other)
        self.assertFalse(visible.hidden)
        self.assertTrue(hidden.hidden)
        self.assertEqual(hidden.name, "Caché")
        self.assertEqual(hidden.rows, [["x", None, "z"], [None, None, None], [None, "0.1", None]])

    def test_empty_row_before_row_without_r(self):
        demo_files.write_xlsx(self.other, [{"name": "S", "rows": [["a"], [], ["b"]], "no_refs_rows": [3]},
                                           {"name": "T", "rows": [["a"], [None], [], ["b"]], "no_refs_rows": [2, 4]}])
        first, second = sources.read_xlsx(self.other)
        self.assertEqual(first.rows, [["a"], [None], ["b"]])
        self.assertEqual(second.rows, [["a"], [None], [None], ["b"]])

    def test_strict_namespace_absolute_targets_and_rels_order(self):
        workbook = (
            '<workbook xmlns="%s" xmlns:r="%s"><workbookPr date1904="true"/><sheets>'
            '<sheet name="First" sheetId="2" r:id="rId9"/>'
            '<sheet name="Second" sheetId="1" r:id="rId3" state="veryHidden"/>'
            '<sheet name="Chart" sheetId="3" r:id="rId4"/>'
            '</sheets></workbook>' % (STRICT, STRICT_REL))
        rels = (
            '<Relationships xmlns="%s">'
            '<Relationship Id="rId3" Type="%s/worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId9" Type="%s/worksheet" Target="/xl/worksheets/sheet2.xml"/>'
            '<Relationship Id="rId4" Type="%s/chartsheet" Target="chartsheets/sheet1.xml"/>'
            '<Relationship Id="rId5" Type="%s/sharedStrings" Target="/xl/strings.xml"/>'
            '<Relationship Id="rId6" Type="%s/styles" Target="theStyles.xml"/>'
            '</Relationships>' % ((PKG_REL,) + (STRICT_REL,) * 5))
        strings = (
            '<sst xmlns="%s">'
            '<si><t>plain</t></si>'
            '<si><r><rPr><b/></rPr><t xml:space="preserve">Rich </t></r><r><t>text</t></r>'
            '<rPh sb="0" eb="4"><t>PHONETIC</t></rPh></si>'
            '<si><t>line_x000D_\nbreak _x005F_x0041_</t></si>'
            '<si><t/></si>'
            '</sst>' % STRICT)
        styles = (
            '<styleSheet xmlns="%s"><numFmts>'
            '<numFmt numFmtId="164" formatCode="dd/mm/yyyy"/>'
            '<numFmt numFmtId="165" formatCode="#,##0.00\\ &quot;kg&quot;"/>'
            '<numFmt numFmtId="166" formatCode="[Red]0.00;[Blue]-0.00"/>'
            '</numFmts>'
            '<cellStyleXfs><xf numFmtId="14"/></cellStyleXfs>'
            '<cellXfs><xf numFmtId="0"/><xf numFmtId="164"/><xf numFmtId="165"/><xf numFmtId="166"/>'
            '<xf numFmtId="47"/></cellXfs></styleSheet>' % STRICT)
        first = (
            '<worksheet xmlns="%s"><sheetData>'
            '<row r="1"><c r="B1" t="s"><v>1</v></c><c t="s"><v>0</v></c><c t="s"><v>3</v></c><c t="s"><v>2</v></c></row>'
            '<row r="3"><c r="A3" s="1"><v>1</v></c><c s="2"><v>1.5</v></c><c s="3"><v>2</v></c>'
            '<c s="4"><v>0.25</v></c><c t="str"><f>A1</f><v>calc</v></c><c t="d"><v>2024-01-02</v></c></row>'
            '<row><c t="inlineStr"><is><r><t>in</t></r><r><t>line</t></r></is></c><c s="1"/><c t="str"><v>  </v></c></row>'
            '</sheetData><mergeCells count="1"><mergeCell ref="A3:B3"/></mergeCells></worksheet>' % STRICT)
        second = '<worksheet xmlns="%s"><sheetData><row><c><v>7</v></c></row></sheetData></worksheet>' % STRICT
        write_parts(self.other, {
            "[Content_Types].xml": "<Types/>",
            "_rels/.rels": '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/officeDocument" '
                           'Target="xl/workbook.xml"/></Relationships>' % (PKG_REL, STRICT_REL),
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": rels,
            "xl/strings.xml": strings,
            "xl/theStyles.xml": styles,
            "xl/worksheets/sheet1.xml": second,
            "xl/worksheets/sheet2.xml": first,
        })
        tables = sources.read_xlsx(self.other)
        self.assertEqual([(t.name, t.hidden) for t in tables], [("First", False), ("Second", True)])
        first_rows = tables[0].rows
        self.assertEqual(first_rows[0], [None, "Rich text", "plain", None, "line\r\nbreak _x0041_", None])
        self.assertEqual(first_rows[1], [None] * 6)
        self.assertEqual(first_rows[2], ["1904-01-02", "1.5", "2", "1904-01-01T06:00:00", "calc", "2024-01-02"])
        self.assertEqual(first_rows[3], ["inline", None, "  ", None, None, None])
        self.assertEqual(tables[0].merged, ["A3:B3"])
        self.assertEqual(tables[1].rows, [["7"]])

    def test_row_without_r_follows_previous_row(self):
        sheet = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                 '<row r="2"><c r="C2"><v>1</v></c></row><row><c><v>2</v></c></row>'
                 '<row><c r="B7"><v>3</v></c></row></sheetData></worksheet>')
        demo_files.write_xlsx(self.other, [{"name": "S", "rows": [["placeholder"]]}])
        with zipfile.ZipFile(self.other) as zf:
            parts = {name: zf.read(name) for name in zf.namelist()}
        parts["xl/worksheets/sheet1.xml"] = sheet
        write_parts(self.other, parts)
        rows = sources.read_xlsx(self.other)[0].rows
        self.assertEqual(rows, [[None] * 3, [None, None, "1"], ["2", None, None],
                                [None] * 3, [None] * 3, [None] * 3, [None, "3", None]])

    def test_number_text(self):
        cases = {"3601234560012": "3601234560012", "2.6000000000000001": "2.6", "1964": "1964",
                 "1.0E+3": "1000", "1.23E+12": "1230000000000", "-0.5": "-0.5", "0.1": "0.1",
                 "39.900000000000006": "39.900000000000006", "1E+20": "100000000000000000000"}
        for raw, expected in cases.items():
            self.assertEqual(sources.excel_number_text(raw), expected, raw)

    def test_dates_and_formats(self):
        self.assertEqual(sources.excel_date(45000), "2023-03-15")
        self.assertEqual(sources.excel_date(45000.25), "2023-03-15T06:00:00")
        self.assertEqual(sources.excel_date(1, date1904=True), "1904-01-02")
        for code in ["dd/mm/yyyy", "d-mmm-yy", "[$-40C]d mmmm yyyy", "yyyy-mm-dd h:mm", "[h]:mm:ss"]:
            self.assertTrue(sources.is_date_format(code), code)
        for code in ["General", "0.00", '#,##0 "kg"', "[Red]0.00", '0.0" mm"', "0\\ \\m", "@"]:
            self.assertFalse(sources.is_date_format(code), code)

    def test_columns_round_trip(self):
        for index, letter in [(0, "A"), (25, "Z"), (26, "AA"), (701, "ZZ"), (702, "AAA"), (16383, "XFD")]:
            self.assertEqual(sources.column_letter(index), letter)
            self.assertEqual(sources.column_index(letter), index)


class CsvTest(TmpDirTest):
    def setUp(self):
        super().setUp()
        self.demo_csv = os.path.join(self.tmp, "demo-tarifs.csv")
        demo_files.write_demo_csv(self.demo_csv)

    def test_cp1252_semicolon(self):
        table = sources.read_csv(self.demo_csv)   # demo_files.write_demo_csv
        self.assertEqual(table.rows[0], ["SKU", "Prix public TTC", "Prix barré"])
        self.assertEqual(table.rows[1], ["1001", "39,90 €", "49,90 €"])
        self.assertEqual(table.rows[2][2], None)

    def test_detection(self):
        self.assertEqual(sources.detect_encoding("\ufeffa;b".encode("utf-8")), "utf-8-sig")
        self.assertEqual(sources.detect_encoding("€".encode("cp1252")), "cp1252")
        self.assertEqual(sources.detect_encoding("é".encode("utf-8")), "utf-8")
        self.assertEqual(sources.detect_delimiter("SKU;Prix\n1001;39,90\n1002;5,00"), ";")
        self.assertEqual(sources.detect_delimiter('a,b\n"x;y",2'), ",")
        self.assertEqual(sources.detect_delimiter("a\tb\n1\t2"), "\t")

    def test_unknown_extension(self):
        with self.assertRaises(ValueError):
            sources.read_source("catalogue.pdf")

    def test_demo_table(self):
        table = sources.read_source(self.demo_csv)[0]
        self.assertEqual((table.file, table.name, table.merged, table.hidden), ("demo-tarifs.csv", "csv", [], False))
        self.assertEqual(len(table.rows), 5)
        self.assertEqual(table.rows[4], ["9999", "10,00 €", None])

    def test_more_encodings_and_delimiters(self):
        self.assertEqual(sources.detect_encoding("a".encode("utf-16")), "utf-16")
        self.assertEqual(sources.detect_encoding(b"\x81\xe9"), "latin-1")
        self.assertEqual(sources.detect_delimiter("a|b|c\n1|2|3\n4|5,5|6"), "|")
        self.assertEqual(sources.detect_delimiter("only one column\nvalue"), ";")

    def test_utf16_tsv_with_quoted_newline_and_empty_line(self):
        path = os.path.join(self.tmp, "export.tsv")
        with open(path, "wb") as f:
            f.write('SKU\tNom\r\n1\t"deux\r\nlignes"\r\n\r\n3\t\r\n'.encode("utf-16"))
        table = sources.read_source(path)[0]
        self.assertEqual(table.rows, [["SKU", "Nom"], ["1", "deux\r\nlignes"], [None, None], ["3", None]])


if __name__ == "__main__":
    unittest.main()
