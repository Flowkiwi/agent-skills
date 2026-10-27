"""Selling prices: tax-excluded conversion and the prices concept (one sale offer per variant and country).

Used by `extract.py <dir> prices` with mappings/prices.json (see references/prices.md); writes concepts/prices.json
in the decision format read by plan_shared.py (taxes to create) and payload.py (sale offers), recorded with:
state.py decide <dir> --key prices --value-file concepts/prices.json
Every Decimal is built from str(value): a rate may be a JSON number in a hand-written mapping.
"""
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Dict, List, Optional, Tuple

import mapping
from mapping import MappingError, evaluate, is_spec, warn

CENT = Decimal("0.01")
DEFAULT_STATUS = "/api/statuses/ACTIVE"
KEYS = frozenset(("version", "concept", "countries", "channels", "status"))
COUNTRY_KEYS = frozenset(("country", "code", "currency", "tax", "rate", "tax_name", "tax_included", "price",
                          "compare_at_price"))
PRICE_FIELDS = ("price", "compare_at_price")
DECISION_KEYS = ("country", "code", "currency", "tax", "rate", "tax_name", "tax_included")
CODE = re.compile(r"^[A-Z]{2}$")
CURRENCY = re.compile(r"^[A-Z]{3}$")
MIN_TAX_NAME = 3
PREVIEW_EXAMPLES = 3


def decimal(value) -> Decimal:
    return Decimal(str(value).strip())


def to_tax_excluded(amount, rate, included: bool) -> str:
    """The tax-excluded amount with 2 decimals, half-up: amount / (1 + rate / 100) when it includes the tax."""
    value = decimal(amount)
    if included:
        value = value / (1 + decimal(rate) / 100)
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def offer_amounts(price, compare_at, rate, included: bool) -> Tuple[str, Optional[str]]:
    """(taxExcludedPrice, discountTaxExcludedPrice): a compare-at price above the price is the base price and the
    price its discount; otherwise the price alone (a compare-at price not above the price is ignored)."""
    converted = to_tax_excluded(price, rate, included)
    if compare_at in (None, ""):
        return converted, None
    base = to_tax_excluded(compare_at, rate, included)
    if Decimal(base) > Decimal(converted):
        return base, converted
    return converted, None


def plain_rate(rate) -> str:
    return mapping.plain_decimal(decimal(rate))


def api_rate(rate) -> str:
    """The rate as the API returns it ("20" -> "20.00")."""
    return str(decimal(rate).quantize(CENT, rounding=ROUND_HALF_UP))


def check_country(country, where: str) -> dict:
    if not isinstance(country, dict):
        raise MappingError("%s: an object with country, code, currency, tax, rate, tax_name, tax_included" % where)
    unknown = sorted(set(country) - COUNTRY_KEYS - mapping.DOC_KEYS)
    if unknown:
        raise MappingError("%s: unknown key(s) %s (valid: %s)" % (
            where, ", ".join(unknown), ", ".join(sorted(COUNTRY_KEYS | mapping.DOC_KEYS))))
    iri = country.get("country")
    if not isinstance(iri, str) or not iri.startswith("/api/countries/"):
        raise MappingError("%s.country: a country IRI from list_countries (/api/countries/<id>)" % where)
    if not isinstance(country.get("code"), str) or not CODE.match(country["code"]):
        raise MappingError("%s.code: the 2-letter uppercase country code (FR)" % where)
    if not isinstance(country.get("currency"), str) or not CURRENCY.match(country["currency"]):
        raise MappingError("%s.currency: the 3-letter uppercase currency code (EUR)" % where)
    tax = country.get("tax")
    if tax is not None and (not isinstance(tax, str) or not tax.startswith("/api/taxes/")):
        raise MappingError("%s.tax: a tax IRI from list_taxes, or null to create it" % where)
    rate = country.get("rate")
    try:
        if isinstance(rate, bool) or not isinstance(rate, (str, int, float)) or not decimal(rate).is_finite() \
                or decimal(rate) < 0:
            raise InvalidOperation()
    except InvalidOperation:
        raise MappingError("%s.rate: the tax rate in percent, a number >= 0 such as \"20\" or \"5.5\"" % where)
    name = country.get("tax_name")
    if name is not None and not isinstance(name, str):
        raise MappingError("%s.tax_name: a string" % where)
    if tax is None and (name is None or len(name.strip()) < MIN_TAX_NAME):
        raise MappingError("%s.tax_name: the name of the tax to create, 3+ characters (TVA 20 FR)" % where)
    if not isinstance(country.get("tax_included"), bool):
        raise MappingError("%s.tax_included: true when the prices include the tax, else false" % where)
    for field in PRICE_FIELDS:
        if field in country and not is_spec(country[field]):
            raise MappingError("%s.%s: a field spec ({\"from\": [...]})" % (where, field))
    if "compare_at_price" in country and "price" not in country:
        raise MappingError("%s.compare_at_price: needs a price spec in the same country" % where)
    return {k: v for k, v in country.items() if k in PRICE_FIELDS or k in mapping.DOC_KEYS}


def check_mapping(m: dict) -> dict:
    """Checks the prices keys of the mapping and returns its field specs only, for mapping.validate_mapping."""
    if m.get("concept") != "prices":
        raise MappingError("concept must be 'prices', got %r" % (m.get("concept"),))
    unknown = sorted(set(m) - KEYS - mapping.DOC_KEYS)
    if unknown:
        raise MappingError("unknown key(s) %s (valid: %s)" % (
            ", ".join(unknown), ", ".join(sorted(KEYS - {"version", "concept"} | mapping.DOC_KEYS))))
    countries = m.get("countries")
    if not isinstance(countries, list) or not countries:
        raise MappingError("countries: a non-empty list of {\"country\", \"code\", \"currency\", \"tax\", \"rate\", "
                           "\"tax_name\", \"tax_included\"}")
    specs = [check_country(country, "countries[%d]" % i) for i, country in enumerate(countries)]
    found = [country["code"] for country in countries]
    duplicates = sorted({code for code in found if found.count(code) > 1})
    if duplicates:
        raise MappingError("countries: %s listed twice" % ", ".join(duplicates))
    channels = m.get("channels")
    if channels is not None and (not isinstance(channels, list) or not channels or not all(
            isinstance(c, str) and c.startswith("/api/channels/") for c in channels)):
        raise MappingError("channels: null (all channels) or a non-empty list of channel IRIs")
    status = m.get("status", DEFAULT_STATUS)
    if not isinstance(status, str) or not status.startswith("/api/statuses/"):
        raise MappingError("status: a status IRI such as %s" % DEFAULT_STATUS)
    doc = {k: v for k, v in m.items() if k in mapping.DOC_KEYS}
    return dict(doc, version=m.get("version"), concept="prices", countries=specs)


class PriceReader:
    """The price and compare-at price of each variant for a country, with sku and country on every warning."""

    def __init__(self, sources, warnings):
        self.sources = sources
        self.warnings = warnings

    def warn(self, row, handle, sku, code, field, message, country):
        warn(self.warnings, row, handle, code, field, message)
        self.warnings[-1].update(sku=sku, country=country)

    def raw(self, country, field, variant, row, handle, sku):
        """The value of the field: from the country spec when it has a price spec, else from products.json."""
        if "price" not in country:
            return variant.get(field)
        if field not in country or row is None:
            return None
        found = []  # type: List[dict]
        value = evaluate(country[field], row, self.sources, found, "prices.%s.%s" % (country["code"], field), handle)
        for warning in found:
            warning.update(sku=sku, country=country["code"])
        self.warnings.extend(found)
        if isinstance(value, list):
            if len(value) > 1:
                self.warn(row.row, handle, sku, "list_in_single_field", "prices.%s.%s" % (country["code"], field),
                          "%d values %r for a price (first one kept): give it a single value in the mapping" % (
                              len(value), value), country["code"])
            value = value[0] if value else None
        return None if value in (None, "") else value

    def amount(self, country, field, variant, row, handle, sku) -> Tuple[Optional[str], bool]:
        """(amount as a plain number string or None, True when a value was given but is not a usable amount)."""
        before = len(self.warnings)
        value = self.raw(country, field, variant, row, handle, sku)
        failed = any(w["code"] == "not_a_number" for w in self.warnings[before:])
        if value is None:
            return None, failed
        number = mapping.parse_number(str(value))
        if number is None or Decimal(number) < 0:
            self.warn(row.row if row is not None else None, handle, sku, "not_a_number",
                      "prices.%s.%s" % (country["code"], field), "SKU %s in %s: %r is not a positive amount, %s" % (
                          sku, country["code"], value,
                          "offer skipped" if field == "price" else "compare-at price ignored"),
                      country["code"])
            return None, True
        return number, False


def decision_country(country: dict) -> dict:
    result = {key: country.get(key) for key in DECISION_KEYS}
    result["rate"] = plain_rate(country["rate"])
    return result


def build(products: List[dict], rows: Dict[str, list], m: dict, sources: dict, warnings: List[dict]) -> dict:
    """concepts/prices.json from the products of products.json, their rows and the prices mapping."""
    reader = PriceReader(sources, warnings)
    countries = m["countries"]
    offers = {}  # type: Dict[str, Dict[str, Dict[str, dict]]]
    for product in products:
        handle = product["handle"]
        product_rows = rows.get(handle) or []
        for index, variant in enumerate(product["variants"]):
            row = product_rows[index] if index < len(product_rows) else None
            row_number = row.row if row is not None else product.get("row")
            sku = variant.get("sku")
            for country in countries:
                code = country["code"]
                if not sku:
                    reader.warn(row_number, handle, None, "missing_sku", "prices.%s" % code,
                                "variant %d has no SKU, offer skipped: fill the codes (codes.py fill) and extract "
                                "the prices again" % (index + 1), code)
                    continue
                if code in offers.get(handle, {}).get(sku, {}):
                    reader.warn(row_number, handle, sku, "duplicate_sku", "prices.%s" % code,
                                "SKU %s is used by two variants of the product, offer skipped" % sku, code)
                    continue
                price, failed = reader.amount(country, "price", variant, row, handle, sku)
                if price is None:
                    if not failed:
                        reader.warn(row_number, handle, sku, "missing_price", "prices.%s.price" % code,
                                    "no price for SKU %s in %s, offer skipped" % (sku, code), code)
                    continue
                compare_at, _ = reader.amount(country, "compare_at_price", variant, row, handle, sku)
                base, discount = offer_amounts(price, compare_at, country["rate"], country["tax_included"])
                offers.setdefault(handle, {}).setdefault(sku, {})[code] = {
                    "taxExcludedPrice": base, "discountTaxExcludedPrice": discount}
    return {"countries": [decision_country(c) for c in countries], "channels": m.get("channels"),
            "status": m.get("status", DEFAULT_STATUS), "offers": offers, "warnings": warnings}


def plural(count: int, word: str, many: str) -> str:
    return "%d %s" % (count, word if count == 1 else many)


def format_preview(data: dict) -> str:
    lines = ["Sale offers per country (tax-excluded amounts sent to Flowkiwi):"]
    total = 0
    for country in data["countries"]:
        code = country["code"]
        found = [(handle, sku, by_country[code]) for handle, variants in data["offers"].items()
                 for sku, by_country in variants.items() if code in by_country]
        discounted = sum(1 for _, _, offer in found if offer["discountTaxExcludedPrice"] is not None)
        total += len(found)
        tax = "%s %s%%%s" % (country.get("tax_name") or country["tax"], country["rate"],
                              " to create" if country["tax"] is None else "")
        lines.append("%s (%s, tax %s, prices %s tax): %d offers, %d with a discount" % (
            code, country["currency"], tax, "include" if country["tax_included"] else "exclude", len(found),
            discounted))
        for handle, sku, offer in found[:PREVIEW_EXAMPLES]:
            discount = offer["discountTaxExcludedPrice"]
            lines.append("  %s %s: %s%s" % (handle, sku, offer["taxExcludedPrice"],
                                            ", discounted %s" % discount if discount is not None else ""))
    for warning in data.get("warnings", []):
        lines.append("%s: %s" % (warning["code"], warning["message"]))
    missing = sum(1 for w in data.get("warnings", []) if w["code"] == "missing_price")
    lines.append("%s for %s, %s missing" % (plural(total, "offer", "offers"),
                                            plural(len(data["countries"]), "country", "countries"),
                                            plural(missing, "variant price", "variant prices")))
    return "\n".join(lines)
