"""Poor descriptions and their validated generation (see references/products.md, "Descriptions").

A generated description is created content: it states only the intro placeholders ({title}, {vendor}) and
the feature values read from the source files. It replaces a poor description only after the explicit
approval recorded in state.json as the decision description_generation = {"approved": true}; without it the
poor description is kept, the generated one is stored in description_candidate and a poor_description
warning (not blocking) is raised.
"""
import html
import re
from typing import List, Optional, Tuple

import mapping
from mapping import MappingError, evaluate, is_spec, warn

DEFAULT_MIN_LENGTH = 80
PLACEHOLDERS = ("title", "vendor")
PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
GENERATE_KEYS = ("when", "min_length", "intro", "separator", "features", "origin", "reason")
FEATURE_KEYS = ("label", "suffix")
DEFAULT_SEPARATOR = ": "


def plain_text(html_text: Optional[str]) -> str:
    return " ".join(mapping.strip_html(html_text or "").split())


def is_poor(description_html: Optional[str], title: Optional[str], min_length: int = DEFAULT_MIN_LENGTH) -> bool:
    text = plain_text(description_html)
    if len(text) < min_length:
        return True
    return title is not None and text.casefold() == " ".join(title.split()).casefold()


def generate(intro: str, features: List[Tuple[str, Optional[str], str]], separator: str = DEFAULT_SEPARATOR):
    items = ["<li>%s%s%s%s</li>" % (html.escape(label, quote=False), separator, html.escape(value, quote=False),
                                    html.escape(suffix or "", quote=False))
             for label, value, suffix in features if value not in (None, "")]
    if not items:
        return None
    paragraph = "<p>%s</p>" % html.escape(intro, quote=False) if intro.strip() else ""
    return "%s<ul>%s</ul>" % (paragraph, "".join(items))


def check_generate(generate_spec, source_ids, where: str) -> None:
    if not isinstance(generate_spec, dict):
        raise MappingError("%s: generate must be an object" % where)
    for key in generate_spec:
        if key not in GENERATE_KEYS:
            raise MappingError("%s: unknown key '%s' (valid: %s)" % (where, key, ", ".join(GENERATE_KEYS)))
    if generate_spec.get("when", "poor") != "poor":
        raise MappingError("%s: when must be 'poor'" % where)
    min_length = generate_spec.get("min_length", DEFAULT_MIN_LENGTH)
    if isinstance(min_length, bool) or not isinstance(min_length, int) or min_length < 0:
        raise MappingError("%s: min_length must be an integer >= 0" % where)
    intro = generate_spec.get("intro", "")
    if not isinstance(intro, str):
        raise MappingError("%s: intro must be a string" % where)
    for name in PLACEHOLDER_RE.findall(intro):
        if name not in PLACEHOLDERS:
            raise MappingError("%s: intro placeholder {%s} is not allowed (only %s)" % (
                where, name, ", ".join("{%s}" % p for p in PLACEHOLDERS)))
    if not isinstance(generate_spec.get("separator", DEFAULT_SEPARATOR), str):
        raise MappingError("%s: separator must be a string" % where)
    features = generate_spec.get("features")
    if not isinstance(features, list) or not features:
        raise MappingError("%s: features must be a non-empty list" % where)
    for i, feature in enumerate(features):
        spot = "%s.features[%d]" % (where, i)
        if not isinstance(feature, dict) or not isinstance(feature.get("label"), str) or not feature["label"]:
            raise MappingError("%s: a feature is a field spec with a 'label'" % spot)
        if not is_spec(feature):
            raise MappingError("%s: a feature needs 'from' (or 'value')" % spot)
        if not isinstance(feature.get("suffix", ""), str):
            raise MappingError("%s: suffix must be a string" % spot)
        mapping.check_spec({k: v for k, v in feature.items() if k not in FEATURE_KEYS}, source_ids, spot)
    mapping.walk({k: generate_spec[k] for k in ("origin", "reason") if k in generate_spec}, source_ids, where)


def render_intro(intro: str, product: dict) -> Optional[str]:
    """The intro with its placeholders, None when one of them is empty (never `<p> de la marque .</p>`)."""
    values = {"title": (product.get("title") or "").strip(), "vendor": (product.get("vendor") or "").strip()}
    if any(not values[name] for name in PLACEHOLDER_RE.findall(intro)):
        return None
    return PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], intro)


def feature_text(value) -> Optional[str]:
    if isinstance(value, list):
        parts = [feature_text(item) for item in value]
        value = ", ".join(part for part in parts if part)
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text or None


def apply(product: dict, spec: Optional[dict], row, sources, warnings: List[dict], approved: bool) -> dict:
    """Generation step of one product (row: its first source row); returns the product, changed in place."""
    generate_spec = (spec or {}).get("generate")
    if not generate_spec or product.get("description_origin") == "fixed":
        return product
    if not is_poor(product["description_html"], product["title"],
                   generate_spec.get("min_length", DEFAULT_MIN_LENGTH)):
        return product
    features = []
    for feature in generate_spec["features"]:
        field_spec = {k: v for k, v in feature.items() if k not in FEATURE_KEYS}
        value = evaluate(field_spec, row, sources, [], "product.description_html.generate", product["handle"])
        features.append((feature["label"], feature_text(value), feature.get("suffix", "")))
    intro = render_intro(generate_spec.get("intro", ""), product)
    if intro is None:
        return product
    generated = generate(intro, features, generate_spec.get("separator", DEFAULT_SEPARATOR))
    if generated is None:
        return product
    if approved:
        product["description_source"] = product["description_html"]
        product["description_html"] = generated
        product["description_origin"] = "generated"
    else:
        product["description_candidate"] = generated
        warn(warnings, row.row, product["handle"], "poor_description", "product.description_html",
             "description %s: a generated one is available, approval needed (decision description_generation)" % (
                 "missing" if not plain_text(product["description_html"]) else "too short or equal to the title"))
    return product
