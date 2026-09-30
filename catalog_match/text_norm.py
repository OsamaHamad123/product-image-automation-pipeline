"""Bilingual (English/Arabic) text normalisation and token-boundary matching.

Every identity comparison in catalog_match goes through this module so that both
sides of a comparison are normalised the same way:

* normalize(text)      NFKC, casefold, Arabic letter folding, harakat/tatweel
                       removal, Eastern-Arabic/Persian digits to ASCII, collapsed
                       whitespace. Punctuation is kept.
* url_path_text(url)   the path of a URL as plain words: percent-decoded, host and
                       query dropped, separators turned into spaces, slug decimals
                       joined ('1-5l' -> '1.5 l').
* tokens(text)         word tokens (digits and letters split apart).
* phrase_in(p, text)   whole-token phrase match, never a raw substring
                       ('ag' does not match 'images', 'nada' does not match 'canada').

Matching strips common Arabic proclitics (ال / و / ب / ل and their combinations)
from Arabic tokens on BOTH sides, so 'المراعي' matches 'والمراعي'.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from functools import lru_cache
from typing import Iterable, List, Optional, Sequence
from urllib.parse import unquote, urlsplit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Character folding tables
# ---------------------------------------------------------------------------

_FOLD = {
    # alef forms -> bare alef
    "أ": "ا",  # أ
    "إ": "ا",  # إ
    "آ": "ا",  # آ
    "ٱ": "ا",  # ٱ
    "ى": "ي",  # ى -> ي
    "ة": "ه",  # ة -> ه
    # Persian letter shapes typed on Persian keyboards
    "ک": "ك",  # ک -> ك
    "ی": "ي",  # ی -> ي
    # Arabic decimal / thousands separators and comma
    "٫": ".",       # ٫
    "٬": ",",       # ٬
    "،": ",",       # ،
}
# Eastern-Arabic (U+0660..0669) and Persian (U+06F0..06F9) digits -> ASCII
for _i in range(10):
    _FOLD[chr(0x0660 + _i)] = str(_i)
    _FOLD[chr(0x06F0 + _i)] = str(_i)
_FOLD_TABLE = str.maketrans(_FOLD)

# tatweel, harakat (U+064B..U+0652) and the other Arabic combining marks
_STRIP_RE = re.compile("[ـؐ-ًؚ-ٰٟۖ-ۭ]")
_WS_RE = re.compile(r"\s+")

# A token is a run of digits (with an optional decimal part) or a run of letters.
_TOKEN_RE = re.compile(r"\d+(?:\.\d+)?|[^\W\d_]+")

_ARABIC_RE = re.compile("[؀-ۿ]")

# Proclitics, longest first. A prefix is stripped only when >= 3 chars remain.
_CLITICS = ("وال", "بال", "كال", "فال", "لل", "ال", "و", "ب", "ل")

# Units that may follow a hyphenated slug decimal such as '1-5l' or '2-25-kg'.
_SLUG_UNIT = r"(?:ml|cl|l|lt|ltr|ltrs|litre|litres|liter|liters|kg|kgs|g|gm|gms|gr|oz|lb|lbs)"
_SLUG_DECIMAL_RE = re.compile(r"(?<!\d)(\d{1,2})-(\d{1,2})(?=[-_ ]?" + _SLUG_UNIT + r"(?![^\W\d_]))")
_URL_SEP_RE = re.compile(r"[-_+/%]")
_DOT_NOT_DECIMAL_RE = re.compile(r"(?<!\d)\.|\.(?!\d)")
_PERCENT_ESC_RE = re.compile(r"%[0-9a-fA-F]{2}")
_URL_EXTENSIONS = {
    "jpg", "jpeg", "png", "webp", "gif", "avif", "bmp", "svg", "tif", "tiff", "heic",
    "html", "htm", "php", "aspx", "asp", "jsp",
}


def normalize(text: Optional[str]) -> str:
    """NFKC + casefold + Arabic folding + digit folding + collapsed whitespace."""
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = s.casefold()
    s = s.translate(_FOLD_TABLE)
    s = _STRIP_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


def is_arabic(token: str) -> bool:
    return bool(token) and bool(_ARABIC_RE.match(token))


def strip_arabic_clitics(token: str) -> str:
    """Strip leading ال / و / ب / ل (and combinations) from an Arabic token.

    Applied repeatedly until stable so the result is idempotent; a prefix is only
    removed when at least 3 characters remain. Latin tokens are returned as-is.
    """
    if not is_arabic(token):
        return token
    changed = True
    while changed:
        changed = False
        for prefix in _CLITICS:
            if token.startswith(prefix) and len(token) - len(prefix) >= 3:
                token = token[len(prefix):]
                changed = True
                break
    return token


def tokens(text: Optional[str], strip_clitics: bool = False) -> List[str]:
    """Split normalised text into word tokens; digits and letters are split apart."""
    toks = _TOKEN_RE.findall(normalize(text))
    if strip_clitics:
        toks = [strip_arabic_clitics(t) for t in toks]
    return toks


def norm_phrase(text: Optional[str]) -> str:
    """Readable normalised phrase: tokens joined by single spaces ('Al-Marai' -> 'al marai')."""
    return " ".join(tokens(text))


@lru_cache(maxsize=8192)
def match_string(text: Optional[str]) -> str:
    """The form both sides of phrase_in are reduced to (tokens, clitics stripped)."""
    return " ".join(tokens(text, strip_clitics=True))


def match_key(text: Optional[str]) -> str:
    """Alias of match_string, used as a dictionary key for lookups."""
    return match_string(text)


@lru_cache(maxsize=4096)
def _phrase_regex(match_phrase: str) -> "re.Pattern[str]":
    return re.compile(r"(?<![\w])" + re.escape(match_phrase) + r"(?![\w])")


def phrase_in(phrase: Optional[str], text: Optional[str]) -> bool:
    """True when every token of `phrase` appears contiguously, on token boundaries, in `text`."""
    p = match_string(phrase)
    if not p:
        return False
    t = match_string(text)
    if not t:
        return False
    return _phrase_regex(p).search(t) is not None


def any_phrase_in(phrases: Iterable[str], text: Optional[str]) -> Optional[str]:
    """Return the first phrase of `phrases` found in `text`, else None."""
    t = match_string(text)
    if not t:
        return None
    for phrase in phrases:
        p = match_string(phrase)
        if p and _phrase_regex(p).search(t):
            return phrase
    return None


def alnum_len(text: Optional[str]) -> int:
    """Number of letters/digits in the text (Arabic letters count)."""
    return sum(1 for ch in normalize(text) if ch.isalnum())


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def _split_url(url: str):
    parts = urlsplit(url)
    if not parts.scheme and not parts.netloc and not url.startswith("/"):
        first = url.split("/", 1)[0]
        if "." in first and " " not in first:
            parts = urlsplit("//" + url)
    return parts


def url_host(url: Optional[str]) -> str:
    """Lower-cased host without 'www.' and port; '' when there is none."""
    if not url:
        return ""
    try:
        host = _split_url(str(url).strip()).hostname or ""
    except ValueError:
        return ""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _decode(path: str) -> str:
    for _ in range(2):  # also undo one level of double encoding ('%2520')
        if not _PERCENT_ESC_RE.search(path):
            break
        path = unquote(path)
    return path


def url_path_text(url: Optional[str], filename_only: bool = False) -> str:
    """Turn a URL path into plain words for evidence matching.

    Scheme, host, query string and fragment are dropped; the path is
    percent-decoded; a known file extension is removed; hyphenated slug decimals
    before a unit are joined ('1-5l' -> '1.5 l'); '-', '_', '+', '/', '%' and
    non-decimal '.' become spaces. With filename_only=True only the last path
    segment is used (image CDN directories such as 'images/' or 'wp-content/uploads'
    carry no product evidence).
    """
    if not url:
        return ""
    try:
        path = _split_url(str(url).strip()).path
    except ValueError:
        return ""
    path = normalize(_decode(path))
    segments = [seg for seg in path.split("/") if seg]
    if not segments:
        return ""
    if filename_only:
        segments = segments[-1:]
    last = segments[-1]
    if "." in last:
        stem, ext = last.rsplit(".", 1)
        if ext in _URL_EXTENSIONS:
            segments[-1] = stem
    text = "/".join(segments)
    text = _SLUG_DECIMAL_RE.sub(r"\1.\2 ", text)
    text = _URL_SEP_RE.sub(" ", text)
    text = _DOT_NOT_DECIMAL_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def url_key(url: Optional[str]) -> str:
    """Comparable form of an image URL: host (no www) + path, lower-cased, no query/fragment."""
    if not url:
        return ""
    try:
        parts = _split_url(str(url).strip())
    except ValueError:
        return str(url).strip().lower()
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = _decode(parts.path or "").rstrip("/")
    return (host + path).lower()


def domain_matches(host: str, domains: Sequence[str]) -> bool:
    """True when host equals one of the domains or is a sub-domain of one."""
    host = (host or "").lower()
    if not host:
        return False
    for d in domains:
        d = (d or "").lower().strip().lstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return True
    return False
