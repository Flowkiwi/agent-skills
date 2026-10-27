import contextlib
import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import codes  # noqa: E402
import extract  # noqa: E402
import mapping  # noqa: E402
import payload  # noqa: E402
import plan_shared  # noqa: E402
import prices  # noqa: E402
import state  # noqa: E402
from concept_helpers import ConceptTestCase, read_json  # noqa: E402

FR = "/api/countries/0198a1b2-0000-7000-8000-00000000f001"
BE = "/api/countries/0198a1b2-0000-7000-8000-00000000b001"
BE_TAX = "/api/taxes/0198a1b2-0000-7000-8000-00000000b021"
FR_COUNTRY = {"country": FR, "code": "FR", "currency": "EUR", "tax": None, "rate": "20", "tax_name": "TVA 20 FR",
              "tax_included": True}
FR_MAPPING = {"version": 1, "concept": "prices", "countries": [FR_COUNTRY], "channels": None,
              "status": "/api/statuses/ACTIVE"}
BE_COUNTRY = {"country": BE, "code": "BE", "currency": "EUR", "tax": BE_TAX, "rate": 21, "tax_included": True,
              "price": {"from": ["main:prix"], "transform": ["decimal"]},
              "compare_at_price": {"from": ["main:matiere"]}, "origin": "agent", "reason": "Taux belge standard."}


class PricesTest(ConceptTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        path = os.path.join(cls.dir, "products.json")
        data = read_json(path)
        codes.fill(data, "2", 1, "{handle}-{index}")
        codes.write_json(path, data)

    def test_conversion(self):
        self.assertEqual(prices.to_tax_excluded("19.90", "20", True), "16.58")
        self.assertEqual(prices.to_tax_excluded("5.00", "5.5", True), "4.74")
        self.assertEqual(prices.to_tax_excluded("19.9", "20", False), "19.90")
        self.assertEqual(prices.offer_amounts("39.90", "49.90", "20", True), ("41.58", "33.25"))
        self.assertEqual(prices.offer_amounts("39.90", "39.90", "20", True), ("33.25", None))

    def test_conversion_edges(self):
        self.assertEqual(prices.to_tax_excluded("5.00", 5.5, True), "4.74")
        self.assertEqual(prices.to_tax_excluded("0.125", "0", True), "0.13")
        self.assertEqual(prices.to_tax_excluded("10", 20, True), "8.33")
        self.assertEqual(prices.offer_amounts("39.90", None, "20", True), ("33.25", None))
        self.assertEqual(prices.offer_amounts("39.90", "29.90", "20", True), ("33.25", None))
        self.assertEqual(prices.offer_amounts("19.9", "24.9", "20", False), ("24.90", "19.90"))

    def test_extract_and_payload(self):
        p = self.extract_with(FR_MAPPING)
        self.assertEqual(p["offers"]["2226"]["1001"]["FR"], {"taxExcludedPrice": "41.58", "discountTaxExcludedPrice": "33.25"})
        self.assertEqual(p["offers"]["2228"]["2228-1"]["FR"]["taxExcludedPrice"], "37.50")
        self.assertEqual([w["code"] for w in p["warnings"]], ["missing_price"])
        st = self.state_with_decision("prices", p)
        self.assertEqual(plan_shared.plan(st)[-1]["key"], "tax:FR:20")
        offer = payload.build(self.products(), st, "2226", placeholders=True)["prices"][0]
        self.assertEqual(offer["body"]["taxExcludedPrice"], {"amount": "41.58", "currency": "EUR"})
        self.assertEqual(offer["body"]["tax"], "<tax:FR:20 IRI>")
        self.assertNotIn("channels", offer["body"])

    def test_decision_format(self):
        p = self.extract_with(FR_MAPPING)
        self.assertEqual(list(p), ["countries", "channels", "status", "offers", "warnings"])
        self.assertEqual(p["countries"], [FR_COUNTRY])
        self.assertEqual((p["channels"], p["status"]), (None, "/api/statuses/ACTIVE"))
        self.assertEqual(list(p["offers"]), ["2226", "2227", "2228", "1004", "2230"])
        self.assertEqual(p["offers"]["2230"], {"1005": {"FR": {"taxExcludedPrice": "41.58", "discountTaxExcludedPrice": None}},
                                               "1006": {"FR": {"taxExcludedPrice": "41.58", "discountTaxExcludedPrice": None}}})
        warning = p["warnings"][0]
        self.assertEqual((warning["handle"], warning["sku"], warning["country"], warning["row"], warning["field"]),
                         ("nokey-0001", "NOKEY-0001-1", "FR", 7, "prices.FR.price"))
        self.assertEqual(warning["stage"], "extract")

    def test_country_price_specs(self):
        p = self.extract_with(dict(FR_MAPPING, countries=[FR_COUNTRY, BE_COUNTRY], status="/api/statuses/DRAFT"))
        self.assertEqual(p["countries"][1], {"country": BE, "code": "BE", "currency": "EUR", "tax": BE_TAX,
                                             "rate": "21", "tax_name": None, "tax_included": True})
        self.assertEqual(p["offers"]["1004"]["1004"], {
            "FR": {"taxExcludedPrice": "23.25", "discountTaxExcludedPrice": None},
            "BE": {"taxExcludedPrice": "24.71", "discountTaxExcludedPrice": None}})
        self.assertEqual(p["offers"]["2226"]["1001"]["BE"], {"taxExcludedPrice": "32.98", "discountTaxExcludedPrice": None})
        found = [(w["code"], w["handle"], w["country"]) for w in p["warnings"]]
        self.assertEqual(found[:2], [("not_a_number", "2226", "BE"), ("not_a_number", "2227", "BE")])
        self.assertIn(("missing_price", "nokey-0001", "FR"), found)
        self.assertIn(("missing_price", "nokey-0001", "BE"), found)
        self.assertEqual(found.count(("not_a_number", "2230", "BE")), 2)
        self.assertTrue(all("sku" in w for w in p["warnings"]))

    def test_transform_warnings_carry_sku_and_country(self):
        be = dict(BE_COUNTRY, price={"from": ["main:coloris"], "transform": ["decimal"]})
        del be["compare_at_price"]
        p = self.extract_with(dict(FR_MAPPING, countries=[be]))
        self.assertEqual(p["offers"], {})
        first = p["warnings"][0]
        self.assertEqual((first["code"], first["sku"], first["country"]), ("not_a_number", "1001", "BE"))
        self.assertEqual([w["code"] for w in p["warnings"]], ["not_a_number"] * 7)

    def test_plan_taxes(self):
        p = self.extract_with(dict(FR_MAPPING, countries=[FR_COUNTRY, BE_COUNTRY, dict(
            FR_COUNTRY, country="/api/countries/de", code="DE", rate=7, tax_name="MwSt 7 DE", currency="EUR")]))
        st = self.state_with_decision("prices", p)
        entries = [e for e in plan_shared.plan(st) if e["module"] == "prices"]
        self.assertEqual([e["key"] for e in entries], ["tax:FR:20", "tax:DE:7"])
        self.assertEqual(entries[0], {
            "key": "tax:FR:20", "module": "prices", "operation": "create_tax",
            "lookup": 'list_taxes: country %s, rate 20 (the API returns "20.00")' % FR,
            "body": {"name": "TVA 20 FR", "rate": "20", "country": FR}, "spec": {"rate": "20", "country": FR},
            "depends_on": []})
        self.assertIn('(the API returns "7.00")', entries[1]["lookup"])
        state.record_shared(st, "tax:FR:20", "/api/taxes/fr20", "created", "prices", entries[0]["spec"])
        self.assertEqual([e["key"] for e in plan_shared.plan(st) if e["module"] == "prices"], ["tax:DE:7"])

    def test_payload_offers(self):
        p = self.extract_with(dict(FR_MAPPING, countries=[FR_COUNTRY, BE_COUNTRY], channels=["/api/channels/web"],
                                   status="/api/statuses/DRAFT"))
        st = self.state_with_decision("prices", p)
        state.record_shared(st, "tax:FR:20", "/api/taxes/fr20", "created", "prices", {"rate": "20", "country": FR})
        out = payload.build(self.products(), st, "2226", step="prices")
        self.assertEqual(out, {"handle": "2226", "prices": [
            {"sku": "1001", "country": "FR", "body": {
                "variant": "<variant IRI of 1001>", "country": FR, "tax": "/api/taxes/fr20",
                "status": "/api/statuses/DRAFT", "taxExcludedPrice": {"amount": "41.58", "currency": "EUR"},
                "discountTaxExcludedPrice": {"amount": "33.25", "currency": "EUR"}, "channels": ["/api/channels/web"]}},
            {"sku": "1001", "country": "BE", "body": {
                "variant": "<variant IRI of 1001>", "country": BE, "tax": BE_TAX, "status": "/api/statuses/DRAFT",
                "taxExcludedPrice": {"amount": "32.98", "currency": "EUR"}, "discountTaxExcludedPrice": None,
                "channels": ["/api/channels/web"]}}]})
        self.assertEqual([(o["sku"], o["country"]) for o in payload.build(self.products(), st, "2230", step="prices")["prices"]],
                         [("1005", "FR"), ("1005", "BE"), ("1006", "FR"), ("1006", "BE")])
        self.assertEqual(payload.build(self.products(), st, "nokey-0001", step="prices")["prices"], [])
        self.assertIn("prices", payload.STEPS)
        with self.assertRaisesRegex(payload.PayloadError, "tax:FR:20"):
            payload.build(self.products(), self.state_with_decision("prices", p), "2226", step="prices")

    def test_payload_refuses_unknown_skus(self):
        p = self.extract_with(FR_MAPPING)
        p["offers"]["2226"]["OLD-SKU"] = p["offers"]["2226"]["1001"]
        st = self.state_with_decision("prices", p)
        with self.assertRaisesRegex(payload.PayloadError, "OLD-SKU"):
            payload.build(self.products(), st, "2226", placeholders=True)

    def test_without_decision(self):
        st = self.state_with_decision("collections", {"status": "/api/statuses/ACTIVE", "collections": [],
                                                      "membership": {}, "warnings": []})
        self.assertEqual(payload.build(self.products(), st, "2226", placeholders=True)["prices"], [])
        self.assertEqual([e for e in plan_shared.plan(st) if e["module"] == "prices"], [])

    def test_variants_without_sku_are_skipped(self):
        data, rows, sources, _ = extract.product_rows(self.dir)
        product = next(p for p in data["products"] if p["handle"] == "2228")
        product["variants"][0]["sku"] = None
        result = prices.build([product], rows, FR_MAPPING, sources, [])
        self.assertEqual(result["offers"], {})
        self.assertEqual([(w["code"], w["sku"], w["country"]) for w in result["warnings"]], [("missing_sku", None, "FR")])

    def test_mapping_errors(self):
        bad_countries = [
            [], [dict(FR_COUNTRY, country="FR")], [dict(FR_COUNTRY, code="fr")], [dict(FR_COUNTRY, currency="euro")],
            [dict(FR_COUNTRY, tax="20")], [dict(FR_COUNTRY, rate="vingt")], [dict(FR_COUNTRY, rate="-1")],
            [dict(FR_COUNTRY, rate=True)], [dict(FR_COUNTRY, rate="Infinity")], [dict(FR_COUNTRY, tax_name="TV")], [dict(FR_COUNTRY, tax_name=None)],
            [dict(FR_COUNTRY, tax_included="yes")], [dict(FR_COUNTRY, prix={"from": ["main:prix"]})],
            [dict(FR_COUNTRY, price={"from": ["main:nope"]})], [dict(FR_COUNTRY, price="39.90")],
            [dict(FR_COUNTRY, compare_at_price={"from": ["main:prix"]})], [FR_COUNTRY, FR_COUNTRY],
            [dict(FR_COUNTRY, origin="robot")], ["FR"]]
        bad = [dict(FR_MAPPING, countries=c) for c in bad_countries] + [
            dict(FR_MAPPING, channels=[]), dict(FR_MAPPING, channels=["web"]), dict(FR_MAPPING, status="ACTIVE"),
            dict(FR_MAPPING, currency="EUR")]
        for m in bad:
            with self.subTest(m=m), self.assertRaises(mapping.MappingError):
                self.extract_with(m)
        with open(os.path.join(self.dir, "mappings", "prices.json"), "w", encoding="utf-8") as f:
            json.dump(dict(FR_MAPPING, concept="collections"), f)
        with self.assertRaisesRegex(mapping.MappingError, "concept must be 'prices'"):
            extract.main_concept(self.dir, "prices")

    def test_cli_prints_the_preview(self):
        self.write_mapping(FR_MAPPING)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = extract.main(["extract.py", self.dir, "prices"])
        self.assertEqual(code, 0)
        lines = out.getvalue().splitlines()
        self.assertIn("FR (EUR, tax TVA 20 FR 20% to create, prices include tax): 6 offers, 1 with a discount", lines)
        self.assertIn("  2226 1001: 41.58, discounted 33.25", lines)
        self.assertTrue(any(line.startswith("missing_price: ") for line in lines))
        self.assertEqual(lines[-1], "6 offers for 1 country, 1 variant price missing")


if __name__ == "__main__":
    unittest.main()
