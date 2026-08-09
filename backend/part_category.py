"""Shared Part Type / Part Category labels for Product Hub + Analytics.

Canonical filter values:
  All | OE Parts | Accessories | Others

Existing stored labels (Genuine Parts, Non OEM parts, etc.) are normalized /
matched via aliases so historical uploads keep working.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

# Final Product Hub / Analytics Part Type options (excluding All).
PART_TYPE_OPTIONS = ["OE Parts", "Accessories", "Others"]

# Back-compat alias for older code that imported PART_CATEGORY_OPTIONS.
PART_CATEGORY_OPTIONS = list(PART_TYPE_OPTIONS)

# Map normalized lookup key -> canonical Part Type label.
_PART_TYPE_ALIASES: Dict[str, str] = {
    # OE Parts (includes legacy Genuine Parts)
    "oe parts": "OE Parts",
    "oe part": "OE Parts",
    "oe": "OE Parts",
    "oem": "OE Parts",
    "oem parts": "OE Parts",
    "oem part": "OE Parts",
    "genuine parts": "OE Parts",
    "genuine part": "OE Parts",
    "genuine": "OE Parts",
    # Accessories
    "accessories": "Accessories",
    "accessory": "Accessories",
    # Others (includes legacy Non OEM)
    "others": "Others",
    "other": "Others",
    "non oem parts": "Others",
    "non oem part": "Others",
    "non oem": "Others",
    "non-oem": "Others",
    "nonoem": "Others",
    "non oem parts": "Others",
}


def _alias_key(value: str) -> str:
    key = (value or "").strip().lower().replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", key).strip()


def normalize_part_category(value: str) -> str:
    """Normalize a raw upload / stored category to the canonical Part Type.

    Unknown non-empty values are kept as typed (upload never rejects a row).
    Empty stays empty.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    return _PART_TYPE_ALIASES.get(_alias_key(raw), raw)


# Keep old name used across server.py
_normalize_part_category = normalize_part_category


def is_all_part_type(value: Optional[str]) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    if not text:
        return True
    low = text.lower()
    return low in {"all", "all categories", "all part types", "n/a"} or text.startswith("All ")


def canonical_part_type(value: Optional[str]) -> Optional[str]:
    """Return a canonical Part Type, or None when selection is All / empty."""
    if is_all_part_type(value):
        return None
    canon = normalize_part_category(str(value))
    if canon in PART_TYPE_OPTIONS:
        return canon
    # Accept exact option match ignoring case
    for opt in PART_TYPE_OPTIONS:
        if opt.lower() == str(value).strip().lower():
            return opt
    return canon or None


def part_type_match_values(selected: Optional[str]) -> Optional[List[str]]:
    """All stored-label variants that should match the selected Part Type filter."""
    canon = canonical_part_type(selected)
    if not canon:
        return None
    values: Set[str] = {canon}
    for key, target in _PART_TYPE_ALIASES.items():
        if target == canon:
            # restore a readable variant from the alias key
            values.add(key)
            values.add(key.title())
            values.add(key.upper())
    # Explicit legacy labels
    if canon == "OE Parts":
        values.update({"OE Parts", "Genuine Parts", "Genuine", "OEM", "OEM Parts"})
    elif canon == "Accessories":
        values.update({"Accessories", "Accessory"})
    elif canon == "Others":
        values.update({"Others", "Other", "Non OEM parts", "Non OEM", "Non-OEM", "NonOEM"})
    # Deduplicate case-insensitively while keeping originals for $in
    return sorted(values)


def part_type_mongo_clause(selected: Optional[str], fields=("part_category", "category", "parts_type")) -> Optional[dict]:
    """Mongo match clause for Part Type across known product category fields."""
    values = part_type_match_values(selected)
    if not values:
        return None
    # Case-insensitive regex alternation anchored to full field value.
    escaped = [re.escape(v) for v in values]
    # Also include alias keys as loose patterns
    pattern = "^(?:" + "|".join(sorted(set(escaped), key=str.lower)) + ")$"
    ors = [{field: {"$regex": pattern, "$options": "i"}} for field in fields]
    return {"$or": ors}


def part_matches_type(row_or_value, selected: Optional[str]) -> bool:
    """True when a product/request row (or raw category string) matches selection."""
    canon_selected = canonical_part_type(selected)
    if not canon_selected:
        return True
    if isinstance(row_or_value, dict):
        raw = (
            row_or_value.get("part_category")
            or row_or_value.get("category")
            or row_or_value.get("parts_type")
            or ""
        )
    else:
        raw = row_or_value or ""
    return normalize_part_category(str(raw)) == canon_selected
