"""
Phase 2 text-signal extractors.

Pure functions that take a listing description (HTML/text) and return a
structured value or None. They never raise — bad input returns None.

Populated fields:
  * orientation           → comma-separated cardinal(s): "S", "N,E", "E,W"
  * light_hint            → extra signal of light ("bem exposto", "muita luz")
  * building_year         → 4-digit int
  * condominium_fee       → monthly €
  * energy_class          → normalized "A+", "A", "B-", …, "F"

None is returned if the pattern is absent. Callers treat None as unknown.
"""
from __future__ import annotations

import html
import re
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    # Strip HTML tags + decode entities + collapse whitespace.
    t = re.sub(r"<[^>]+>", " ", text)
    t = html.unescape(t)
    t = _WS_RE.sub(" ", t).strip()
    return t


# ---------------------------------------------------------------------------
# Orientation
# ---------------------------------------------------------------------------

# Map Portuguese keywords (and Spanish/English for resilience) to cardinals.
# Order matters — match longer phrases first to avoid partial matches.
_ORIENT_MAP = [
    # multi-direction phrasings
    (r"nascente[/\s]*sul|sul[/\s]*nascente",  "S,E"),
    (r"nascente[/\s]*norte|norte[/\s]*nascente", "N,E"),
    (r"poente[/\s]*sul|sul[/\s]*poente",       "S,W"),
    (r"poente[/\s]*norte|norte[/\s]*poente",   "N,W"),
    (r"nascente[/\s]*poente|poente[/\s]*nascente", "E,W"),
    (r"sul[/\s]*norte|norte[/\s]*sul",         "N,S"),
    # single cardinals (Portuguese)
    (r"\b(virad[oa]?\s+a\s+)?sul\b",     "S"),
    (r"\b(virad[oa]?\s+a\s+)?nascente\b","E"),
    (r"\b(virad[oa]?\s+a\s+)?poente\b",  "W"),
    (r"\b(virad[oa]?\s+a\s+)?norte\b",   "N"),
    # dupla exposição → mark as ambiguous dual
    (r"dupla\s+expos",                   "DUAL"),
]


def extract_orientation(text: Optional[str]) -> Optional[str]:
    """Return a comma-separated set of cardinals, or None if absent.

    Combines all matches: a listing saying "nascente" and "sul" returns "E,S".
    """
    t = _clean(text).lower()
    if not t:
        return None
    found: set = set()
    dual = False
    for pattern, value in _ORIENT_MAP:
        if re.search(pattern, t):
            if value == "DUAL":
                dual = True
                continue
            for v in value.split(","):
                found.add(v)
    if not found and not dual:
        return None
    if dual and not found:
        return "DUAL"
    # Sort with a preferred order (S first — best in northern hemisphere)
    order = {"S": 0, "E": 1, "W": 2, "N": 3}
    return ",".join(sorted(found, key=lambda c: order.get(c, 9)))


_LIGHT_HINT_PATTERNS = [
    r"muit[ao]\s+luz\s+natural",
    r"excelente\s+expos(i)?[çc][ãa]o\s+solar",
    r"excelente\s+luminosidade",
    r"luminosidade\s+natural\s+(extraordin[áa]ria|excelente|excepcional)",
    r"soalheir[oa]",
    r"bem\s+expost[oa]",
]


def has_light_hint(text: Optional[str]) -> bool:
    t = _clean(text).lower()
    if not t:
        return False
    return any(re.search(p, t) for p in _LIGHT_HINT_PATTERNS)


# ---------------------------------------------------------------------------
# Building year
# ---------------------------------------------------------------------------

_YEAR_PATTERNS = [
    # "construção em 2023", "construído em 2020", "edifício de 2015"
    r"constru(?:[çc][ãa]o|[ií]d[oa])\s+(?:em|do\s+ano|de)?\s*(\d{4})",
    r"edif[ií]cio\s+(?:de|do\s+ano|do)\s+(\d{4})",
    r"ano\s+de\s+constru[çc][ãa]o\s*[:\-]?\s*(\d{4})",
    r"ano\s+constru[çc][ãa]o\s*[:\-]?\s*(\d{4})",
    # "pr[eé]dio de 1950", "edifício dos anos 70"
    r"pr[eé]dio\s+(?:dos\s+anos|de)\s+(\d{4})",
]

_DECADE_PATTERN = re.compile(
    r"(?:pr[eé]dio|edif[ií]cio)\s+d[eo]s?\s+anos\s+(\d{2})\b",
    re.IGNORECASE,
)


def extract_building_year(text: Optional[str]) -> Optional[int]:
    t = _clean(text)
    if not t:
        return None
    for pat in _YEAR_PATTERNS:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            try:
                year = int(m.group(1))
                if 1800 <= year <= 2030:
                    return year
            except ValueError:
                continue
    # Decade form: "anos 70" → 1970
    m = _DECADE_PATTERN.search(t)
    if m:
        try:
            dec = int(m.group(1))
            if dec <= 30:
                return 2000 + dec
            return 1900 + dec
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# Condominium fee (monthly €)
# ---------------------------------------------------------------------------

# Capture a euro amount that follows a condomínio mention. Accept the currency
# sign before OR after the number, and optional words like "mensal", "mês",
# "por mês", "€/mês".
_CONDO_PATTERNS = [
    # "Condomínio: 45€/mês", "Valor condomínio 43€/mês", "condomínio 68€"
    r"condom[ií]nio[^.\n]{0,60}?(\d{1,4}(?:[.,]\d{1,2})?)\s*€",
    # "€ 45 mensal condomínio"
    r"€\s*(\d{1,4}(?:[.,]\d{1,2})?)[^.\n]{0,40}?condom[ií]nio",
    # "condomínio acessível (50€/mês)"
    r"condom[ií]nio[^.\n]{0,60}?\((\d{1,4})\s*€",
    # "Valor mensal condominio: 89"
    r"(?:valor\s+mensal|mensalidade)[^.\n]{0,40}?condom[ií]nio[^.\n]{0,10}?(\d{1,4}(?:[.,]\d{1,2})?)",
    # "Condomínio organizado (30 euros mensais)"
    r"condom[ií]nio[^.\n]{0,60}?(\d{1,4})\s+euros",
]


def extract_condominium_fee(text: Optional[str]) -> Optional[float]:
    t = _clean(text).lower()
    if not t:
        return None
    best: Optional[float] = None
    for pat in _CONDO_PATTERNS:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            try:
                raw = m.group(1).replace(".", "").replace(",", ".")
                v = float(raw)
                # Sanity: Lisbon condo fees are usually 20–500 €/mo.
                # Reject obvious misparses (years, sizes, prices).
                if 5.0 <= v <= 2500.0:
                    if best is None or v < best:
                        best = v
            except ValueError:
                continue
    return best


# ---------------------------------------------------------------------------
# Energy class
# ---------------------------------------------------------------------------

# Canonical Portuguese labels. Accept both "Certificação Energética: A+"
# and "Categoria Energética B" and "classe energética C+".
_ENERGY_PATTERN = re.compile(
    r"(?:"
    r"certifica[cç][ãa]o\s+energ[ée]tica"
    r"|categoria\s+energ[ée]tica"
    r"|classe\s+energ[ée]tica"
    r"|certificad[oa]\s+energ[ée]tico"
    r"|efici[êe]ncia\s+energ[ée]tica"
    r")"
    r"\s*[:\-]?\s*"
    r"(A\+|[A-F][\+\-]?)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def extract_energy_class(text: Optional[str]) -> Optional[str]:
    t = _clean(text)
    if not t:
        return None
    m = _ENERGY_PATTERN.search(t)
    if not m:
        return None
    val = m.group(1).upper()
    # Normalize: A+ stays A+, A stays A, B- stays B- etc.
    # Reject single letters outside A–F.
    if not val:
        return None
    base = val[0]
    if base not in "ABCDEF":
        return None
    return val


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_all(text: Optional[str]) -> dict:
    """Run every extractor. Returns a dict with None for any missing fields."""
    return {
        "orientation":      extract_orientation(text),
        "light_hint":       has_light_hint(text),
        "building_year":    extract_building_year(text),
        "condominium_fee":  extract_condominium_fee(text),
        "energy_class":     extract_energy_class(text),
        "building_stage":   extract_building_stage(text),
    }


# ---------------------------------------------------------------------------
# Building stage — distinguishes live, renovation-ready, and approved-project listings.
# ---------------------------------------------------------------------------

# Ordered — first match wins. approved_project is strictly a superset of
# full_remodel (a project is already designed AND approved), so it comes first.
# These keywords are common in Portuguese real-estate listings.
_BUILDING_STAGE_PATTERNS = [
    # Approved/pending planning permission — unit is a shell or empty plot with design ready
    ("approved_project", [
        r"projec?to\s+aprovado",
        r"projec?to\s+de\s+arquitec?tura\s+aprovado",
        r"licenciamento\s+aprovado",
        r"com\s+projec?to\s+licenciado",
        r"pronto\s+a\s+construir",
        r"para\s+construir",
        r"com\s+pip\s+aprovado",
    ]),
    # Unit is inhabitable-but-dated → refurb (kitchen/bath replacements, paint, etc.)
    ("full_remodel", [
        r"para\s+remodela[çc][ãa]o\s+total",
        r"remodela[çc][ãa]o\s+total",
        r"total\s+remodela[çc][ãa]o",
        r"reconstru[çc][ãa]o\s+total",
    ]),
    # Heavier works — gutted, water damage, "para obras", etc.
    ("needs_reno", [
        r"para\s+recuperar",
        r"a\s+recuperar",
        r"para\s+recupera[çc][ãa]o",
        r"para\s+obras",
        r"para\s+restauro",
        r"requer\s+obras",
        r"necessita\s+de\s+obras",
        r"devoluto",
        r"pr[ée]dio\s+devoluto",
    ]),
    # Already renovated or move-in ready. Match masculine (-o) and feminine (-a)
    # endings since descriptions may refer to the unit, property, moradia, etc.
    ("turnkey", [
        r"pronto[oa]?\s+a\s+habitar",
        r"totalmente\s+remodelad[oa]",
        r"totalmente\s+renovad[oa]",
        r"totalmente\s+restaurad[oa]",
        # "renovado em 2023", "remodelada em 2022", "renovação em 2021"
        r"(?:renovad[oa]|remodelad[oa]|restaurad[oa])\s+em\s+20\d{2}",
        r"renova[çc][ãa]o\s+(?:total\s+)?em\s+20\d{2}",
        r"rec[eé]m\s+renovad[oa]",
        r"rec[eé]m\s+remodelad[oa]",
        r"chave\s+na\s+m[ãa]o",
        # Fresh new-build construction that IS already finished (not projeto)
        r"obra\s+nova\s+conclu[ií]da",
    ]),
]


def extract_building_stage(text: Optional[str]) -> Optional[str]:
    """Return one of approved_project / full_remodel / needs_reno / turnkey,
    or None if no pattern matches.
    """
    t = _clean(text).lower()
    if not t:
        return None
    for stage, patterns in _BUILDING_STAGE_PATTERNS:
        for pat in patterns:
            if re.search(pat, t):
                return stage
    return None


# Type annotation import (signals_text module did not previously import Optional for some callers)
from typing import Optional as _Optional  # noqa — exported via `Optional` already above


__all__ = [
    "extract_orientation",
    "has_light_hint",
    "extract_building_year",
    "extract_condominium_fee",
    "extract_energy_class",
    "extract_building_stage",
    "extract_all",
]
