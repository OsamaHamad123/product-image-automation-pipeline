"""GTIN (barcode) normalisation and GS1 check-digit validation.

Generalises the old GTIN-13-only checker to GTIN-8/12/13/14 and handles the
spreadsheet artefacts seen in the owner's sheet ('6.29E+12', spaces, dashes, a
leading apostrophe). A valid barcode is returned as a zero-padded GTIN-14 so
every source compares equal regardless of the length it was written in.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

VALID_LENGTHS = (8, 12, 13, 14)

_SCIENTIFIC_RE = re.compile(r"\d\s*[eE]\s*[+-]?\s*\d")
_DECIMAL_RE = re.compile(r"\d[.,]\d")
_THOUSANDS_RE = re.compile(r"\d{1,3}(?:,\d{3}){2,}")
_STRIP_RE = re.compile(r"[\s\- ‐-―]+")
_EASTERN_DIGITS = str.maketrans({chr(0x0660 + i): str(i) for i in range(10)} | {chr(0x06F0 + i): str(i) for i in range(10)})


def check_digit(body: str) -> int:
    """GS1 mod-10 check digit for the digits that precede it (any GTIN length)."""
    total = 0
    # Weights alternate 3,1,3,... starting from the digit next to the check digit.
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10


def normalize_gtin(raw) -> Tuple[Optional[str], str]:
    """Return (gtin14 or None, status).

    status is one of: 'ok', 'missing', 'scientific_notation', 'not_numeric',
    'bad_length', 'all_zero', 'bad_check_digit'.
    """
    if raw is None:
        return None, "missing"
    if isinstance(raw, bool):
        return None, "not_numeric"
    if isinstance(raw, float):
        # A float has already lost digits or carries a decimal part; never guess.
        return None, "scientific_notation"
    s = str(raw).strip().translate(_EASTERN_DIGITS)
    if s.startswith("'"):
        s = s[1:].strip()
    if not s:
        return None, "missing"
    if _THOUSANDS_RE.fullmatch(s):  # '6,297,000,611,365' keeps every digit
        s = s.replace(",", "")
    if _SCIENTIFIC_RE.search(s) or _DECIMAL_RE.search(s):
        return None, "scientific_notation"
    digits = _STRIP_RE.sub("", s)
    if not digits:
        return None, "missing"
    if not digits.isdigit() or not digits.isascii():
        return None, "not_numeric"
    if len(digits) not in VALID_LENGTHS:
        return None, "bad_length"
    if set(digits) == {"0"}:
        return None, "all_zero"
    if check_digit(digits[:-1]) != int(digits[-1]):
        return None, "bad_check_digit"
    return digits.zfill(14), "ok"


def is_valid_gtin(raw) -> bool:
    return normalize_gtin(raw)[1] == "ok"


def gtin13(gtin14: Optional[str]) -> Optional[str]:
    """The 13-digit (EAN-13) form of a GTIN-14 when its indicator digit is 0, else the GTIN-14."""
    if not gtin14:
        return None
    return gtin14[1:] if len(gtin14) == 14 and gtin14.startswith("0") else gtin14


def same_gtin(a, b) -> Optional[bool]:
    """True/False when both values are valid GTINs, None when either is not."""
    ga, _ = normalize_gtin(a)
    gb, _ = normalize_gtin(b)
    if ga is None or gb is None:
        return None
    return ga == gb


def is_restricted(gtin14: Optional[str]) -> bool:
    """True for GS1 restricted-circulation numbers (in-store / company-internal codes).

    GTIN-13 prefixes 020-029, 040-049 and 200-299 (variable-measure and in-store codes,
    GTIN-12 '2...' and '4...' included) and RCN-8 codes starting 0 or 2 are only unique
    inside one company, so they identify nothing on the open web (Open Food Facts, Q4).
    """
    if not gtin14 or len(gtin14) != 14 or not gtin14.isdigit():
        return False
    if gtin14.startswith("000000"):                    # a GTIN-8 zero-padded to 14 digits
        return gtin14[6] in "02"
    if gtin14[0] != "0":
        return False                                   # GTIN-14 with a packaging indicator
    g13 = gtin14[1:]
    return g13[:2] in ("02", "04") or g13[0] == "2"


def is_global_gtin(raw) -> bool:
    """A valid GTIN that is also globally unique (not a restricted-circulation number)."""
    gtin14, status = normalize_gtin(raw)
    return status == "ok" and not is_restricted(gtin14)


def display_gtin(raw) -> Optional[str]:
    """A valid GTIN the way a sheet writes it: GTIN-8 as 8 digits, else EAN-13 (GTIN-14 when its indicator is set)."""
    gtin14, status = normalize_gtin(raw)
    if status != "ok" or not gtin14:
        return None
    return gtin14[6:] if gtin14.startswith("000000") else gtin13(gtin14)


def barcode_from_page(sheet_barcode, page_value) -> Optional[str]:
    """The barcode a store page stated, worth keeping with an approval: only when the sheet's barcode is missing or
    not a valid GTIN (the sheet's 'no_barcode' gap), and the page's value is a valid, globally unique GTIN
    (checksum-valid, not an in-store code). Display form (display_gtin); None otherwise. Never written to the sheet."""
    if is_valid_gtin(sheet_barcode) or not is_global_gtin(page_value):
        return None
    return display_gtin(page_value)
