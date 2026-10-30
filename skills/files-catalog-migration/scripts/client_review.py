#!/usr/bin/env python3
"""client-review.md: what the client has to answer or supply, ready to send (see references/review.md).

Usage: client_review.py <dir> [--labels <file.json>]

Reads state.json, inventory.json, products.json, fixes.json, mappings/*.json and concepts/prices.json; writes
<dir>/client-review.md, grouped by concept: the open questions (a "question" whose origin is not client), the
choices made for the client (every "origin": "agent" with its reason) and the missing data. The labels follow
decisions.data_language (fr, en built in); for another language pass a JSON file with the keys of LABELS["fr"]
translated (the "_one" singular keys are optional). Prints the counts; exit 1 when a file is missing or no
labels exist for the data language.
"""
import argparse
import glob
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import clean
import dryrun
import mapping
from extract import slugify

ORDER = ("products", "taxonomy", "collections", "metafields", "metaobjects", "prices", "decisions")
OUTPUT = "client-review.md"

LABELS = {
    "fr": {
        "title": "Catalogue {client} : points à valider",
        "intro": "Ce document liste les questions restées ouvertes, les choix faits pour vous en attendant votre "
                 "réponse et les données manquantes. Répondez directement sous chaque point.",
        "questions": "Questions", "choices": "Choix faits pour vous", "missing": "Données manquantes",
        "column": "colonne",
        "concepts": {"products": "Produits et variantes", "taxonomy": "Catégories et caractéristiques",
                     "collections": "Collections", "metafields": "Champs complémentaires",
                     "metaobjects": "Fiches liées", "prices": "Prix de vente", "decisions": "Autres choix"},
        "generated_key": "{n} produits sans référence ont reçu une référence provisoire : {titles}",
        "generated_key_one": "{n} produit sans référence a reçu une référence provisoire : {titles}",
        "empty_columns": "Colonnes vides : {labels}",
        "generated_codes": "{barcodes} codes EAN et {skus} SKU provisoires ont été générés",
        "generated_codes_one": "{barcodes} code EAN et {skus} SKU provisoires ont été générés",
        "generated_descriptions": "{n} descriptions ont été rédigées à partir des caractéristiques du fichier",
        "generated_descriptions_one": "{n} description a été rédigée à partir des caractéristiques du fichier",
        "missing_price": "{n} variantes sans prix : {skus}",
        "missing_price_one": "{n} variante sans prix : {skus}",
    },
    "en": {
        "title": "{client} catalog: points to confirm",
        "intro": "This document lists the questions still open, the choices made for you while waiting for your "
                 "answer and the missing data. Answer directly below each point.",
        "questions": "Questions", "choices": "Choices made for you", "missing": "Missing data",
        "column": "column",
        "concepts": {"products": "Products and variants", "taxonomy": "Categories and characteristics",
                     "collections": "Collections", "metafields": "Additional fields",
                     "metaobjects": "Linked entries", "prices": "Selling prices", "decisions": "Other choices"},
        "generated_key": "{n} products without a reference were given a temporary reference: {titles}",
        "generated_key_one": "{n} product without a reference was given a temporary reference: {titles}",
        "empty_columns": "Empty columns: {labels}",
        "generated_codes": "Temporary codes were generated: {barcodes} EAN, {skus} SKU",
        "generated_codes_one": "Temporary codes were generated: {barcodes} EAN, {skus} SKU",
        "generated_descriptions": "{n} descriptions were written from the characteristics in the file",
        "generated_descriptions_one": "{n} description was written from the characteristics in the file",
        "missing_price": "{n} variants without a price: {skus}",
        "missing_price_one": "{n} variant without a price: {skus}",
    },
}


class ReviewError(Exception):
    pass


def read_json(path: str, required: Optional[str] = None):
    if not os.path.isfile(path):
        if required:
            raise ReviewError("%s not found: %s" % (path, required))
        return None
    with open(path, encoding="utf-8") as f:
        try:
            return json.load(f)
        except ValueError as error:
            raise ReviewError("%s is not valid JSON: %s" % (path, error))


def check_labels(labels) -> dict:
    reference = LABELS["fr"]
    if not isinstance(labels, dict):
        raise ReviewError("labels must be a JSON object with the keys of LABELS['fr']")
    missing = [k for k in reference if not k.endswith("_one") and not isinstance(labels.get(k), (str, dict))]
    concepts = labels.get("concepts") if isinstance(labels.get("concepts"), dict) else {}
    missing += ["concepts.%s" % k for k in reference["concepts"] if not isinstance(concepts.get(k), str)]
    if missing:
        raise ReviewError("labels file misses: %s" % ", ".join(missing))
    return labels


def counted(labels: dict, key: str, n: int, **values) -> str:
    """The label of a count, its "_one" variant when n is 1 and the labels have one."""
    text = labels.get(key + "_one") if n == 1 else None
    return (text or labels[key]).format(n=n, **values)


def section_of(concept: str) -> str:
    return concept if concept in ORDER else "decisions"


def walk_questions(node, concept: str, path: List[str], found: List[dict]) -> None:
    if isinstance(node, dict):
        if path and node.get("question") and node.get("origin") != "client":
            found.append({"concept": concept, "question": str(node["question"])})
        for key, value in node.items():
            walk_questions(value, concept, path + [str(key)], found)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            walk_questions(value, concept, path + [str(index)], found)


def load_mappings(directory: str) -> List[Tuple[str, dict]]:
    found = []
    for path in sorted(glob.glob(os.path.join(directory, "mappings", "*.json"))):
        m = read_json(path)
        if isinstance(m, dict):
            found.append((m.get("concept") or os.path.splitext(os.path.basename(path))[0], m))
    return found


def sheet_of(inventory: dict, source: dict) -> Optional[dict]:
    for file in inventory.get("files") or []:
        if file.get("file") == source.get("file"):
            sheets = file.get("sheets") or []
            if source.get("sheet") is None:
                return sheets[0] if sheets else None
            return next((s for s in sheets if s.get("sheet") == source["sheet"]), None)
    return None


def column_label(column: dict) -> str:
    return column.get("label") or column.get("code") or column.get("letter") or "?"


def ref_label(ref: str, sources: dict, inventory: dict) -> str:
    """The client's column label of a '<source id>:<column>' reference (the column part when not found)."""
    source_id, _, name = str(ref).partition(":")
    sheet = sheet_of(inventory, sources.get(source_id) or {}) if name else None
    columns = (sheet or {}).get("columns") or []
    if name.startswith("@"):
        match = [c for c in columns if c.get("letter") == name[1:].upper()]
    else:
        wanted = clean.clean_text(name)
        match = [c for c in columns if c.get("code") == wanted]
        if len(match) != 1:
            match = [c for c in columns if c.get("label") == wanted]
    return column_label(match[0]) if len(match) == 1 else (name.lstrip("@") or str(ref))


def unique(values: List[str]) -> List[str]:
    seen = []  # type: List[str]
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def choice_text(choice: dict, sources: dict, inventory: dict, labels: dict) -> str:
    refs = mapping.mapping_refs(choice["spec"]) if choice["concept"] != "decisions" else []
    names = unique([ref_label(ref, sources, inventory) for ref in refs])
    if not names:
        return choice["reason"]
    return "%s (%s %s)" % (choice["reason"], labels["column"], ", ".join('"%s"' % name for name in names))


def empty_columns(mappings: List[Tuple[str, dict]], inventory: dict) -> List[str]:
    found = []  # type: List[str]
    for _, m in mappings:
        for source in (m.get("sources") or {}).values():
            sheet = sheet_of(inventory, source) if isinstance(source, dict) else None
            for column in (sheet or {}).get("columns") or []:
                if any(flag.get("flag") == "empty" for flag in column.get("flags") or []):
                    found.append(column_label(column))
    return unique(found)


def products_missing(directory: str, data: dict, mappings, inventory: dict, labels: dict) -> List[str]:
    products = data.get("products") or []
    titles = {p["handle"]: p.get("title") for p in products}
    fixes = read_json(os.path.join(directory, "fixes.json")) or {}
    keyless = unique([titles[slugify(str(e.get("key")))] or e.get("title") or e.get("key")
                      for e in fixes.get("generated_keys") or []
                      if isinstance(e, dict) and slugify(str(e.get("key"))) in titles])
    lines = []
    if keyless:
        lines.append(counted(labels, "generated_key", len(keyless), titles=", ".join(keyless)))
    empty = empty_columns(mappings, inventory)
    if empty:
        lines.append(labels["empty_columns"].format(labels=", ".join(empty)))
    variants = [v for p in products for v in p.get("variants") or []]
    barcodes = sum(1 for v in variants if v.get("barcode_generated"))
    skus = sum(1 for v in variants if v.get("sku_generated"))
    if barcodes or skus:
        lines.append(counted(labels, "generated_codes", barcodes, barcodes=barcodes, skus=skus))
    generated = sum(1 for p in products if p.get("description_origin") == "generated")
    if generated:
        lines.append(counted(labels, "generated_descriptions", generated))
    return lines


def prices_missing(directory: str, labels: dict) -> List[str]:
    """Read from the concept file: a recorded decision keeps the warnings of its extraction."""
    prices = read_json(os.path.join(directory, "concepts", "prices.json")) or {}
    several = len(prices.get("countries") or []) > 1
    by_sku = {}  # type: Dict[str, List[str]]
    for warning in prices.get("warnings") or []:
        if warning.get("code") == "missing_price":
            sku = warning.get("sku") or warning.get("handle") or "?"
            by_sku.setdefault(sku, [])
            if warning.get("country") and warning["country"] not in by_sku[sku]:
                by_sku[sku].append(warning["country"])
    if not by_sku:
        return []
    skus = ["%s (%s)" % (sku, ", ".join(c)) if several and c else sku for sku, c in by_sku.items()]
    return [counted(labels, "missing_price", len(skus), skus=", ".join(skus))]


def review(directory: str, labels: Optional[dict] = None) -> Tuple[str, Dict[str, int], List[dict]]:
    """(cleaned text, counts, agent choices left out for lack of a reason)."""
    state = read_json(os.path.join(directory, "state.json"), "run state.py init")
    decisions = state.get("decisions") or {}
    if labels is None:
        language = decisions.get("data_language")
        if not language:
            raise ReviewError("data_language is not decided: run state.py decide")
        if language not in LABELS:
            raise ReviewError("no labels for '%s': pass --labels" % language)
        labels = LABELS[language]
    else:
        labels = check_labels(labels)
    inventory = read_json(os.path.join(directory, "inventory.json"), "run inventory.py")
    data = read_json(os.path.join(directory, "products.json"), "run extract.py")
    mappings = load_mappings(directory)
    products_sources = next((m.get("sources") for c, m in mappings if c == "products"), None) or {}

    sections = {concept: ([], [], []) for concept in ORDER}  # type: Dict[str, Tuple[list, list, list]]
    for concept, m in mappings:
        found = []  # type: List[dict]
        walk_questions(m, concept, [], found)
        sections[section_of(concept)][0].extend(q["question"] for q in found)
    omitted = []
    for choice in dryrun.agent_choices(directory, state):
        concept = choice["concept"]
        if concept == "decisions" and choice["path"] in ORDER:
            concept = choice["path"]
        if not choice.get("reason"):
            omitted.append(choice)
            continue
        m = next((m for c, m in mappings if c == choice["concept"]), {})
        sources = dict(products_sources, **(m.get("sources") or {}))
        sections[section_of(concept)][1].append(choice_text(choice, sources, inventory, labels))
    sections["products"][2].extend(products_missing(directory, data, mappings, inventory, labels))
    sections["prices"][2].extend(prices_missing(directory, labels))

    lines = ["# " + labels["title"].format(client=state.get("client") or ""), "", labels["intro"]]
    for concept in ORDER:
        parts = sections[concept]
        if not any(parts):
            continue
        lines += ["", "## " + labels["concepts"][concept]]
        for heading, items in zip(("questions", "choices", "missing"), parts):
            if items:
                lines += ["", "### " + labels[heading], ""] + ["- " + item for item in items]
    counts = {name: sum(len(sections[c][i]) for c in ORDER)
              for i, name in enumerate(("questions", "choices", "missing"))}
    return clean.clean_text("\n".join(lines)), counts, omitted


def render(directory: str, labels: Optional[dict] = None) -> str:
    return review(directory, labels)[0]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("dir")
    parser.add_argument("--labels", help="JSON file with the keys of LABELS['fr'] translated")
    args = parser.parse_args(argv)
    try:
        labels = read_json(args.labels, "give an existing labels file") if args.labels else None
        text, counts, omitted = review(args.dir, labels)
    except ReviewError as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    with open(os.path.join(args.dir, OUTPUT), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    for choice in omitted:
        print("warning: agent choice without reason, not listed: [%s] %s" % (choice["concept"], choice["path"]),
              file=sys.stderr)
    print("%d questions, %d choices, %d missing data items" % (counts["questions"], counts["choices"],
                                                               counts["missing"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
