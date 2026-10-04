"""Why a product has no pick, in the owner's words: one reason per product, and one plain Arabic sentence that
states the fact behind it and ends with what to do.

Read by the review screen (automation_queue.trace_json -> outcome.explain), the run export
(scripts/export_run.py) and scripts/smoke_live.py. Display only: routing never reads anything here, and nothing here
writes a candidate's reasons or status, so it can never change a search decision.

Engine side (moved unchanged from scripts/smoke_live.py, which imports it from here)
    unselected_reason(r)        one of UNSELECTED_REASONS' keys for a row record without a pick (a smoke_live row,
                                or outcome_record() / stored_record() below). It is read on the listings that name
                                the brand (tier 1 or 2): another brand's listing the label reader rejected is not why
                                the product has no pick.
    brand_facts(ranked, code)   verdicts / brand_found / only_social over those listings (smoke_live's row fields).

Sheet side (what the sheet row lacks for a confident pick)
    sheet_issues(row, spec)     no_size (no size, so no listing can confirm it), size_unit_typo (a food's only size is
                                'N MM' with N >= 50 and no weight or volume: '900 MM' fries, most likely '900 GM'; a
                                '9MM' cut never), no_barcode (missing or not a valid GTIN), brand_unknown (the brand is
                                in no Brands Mapping entry, learned or not, and no store spelling was discovered),
                                no_brand (the brand cell says the product has none, 'GENERIC / NO BRAND': never
                                brand_unknown), brand_has_product_word (the brand cell holds a word of the product
                                name: 'AMERICAN LIGHT' before 'MEAT TUNA', which the search already matches as
                                'AMERICAN' (known), or a last word that is a typo of a variant word, 'SQ SALITED'),
                                typo (a word one edit from a known word: 'CHICEKN' ->
                                'CHICKEN', or a sheet spelling the search already reads another way: 'LUNCHENMEAT').
    typo_suggestions(...)       the typo finder (Vocabulary: the lexicons, data/grocery_words.json and the words the
                                sheet itself repeats, read from the products cache the worker already writes).

One reason
    explain(record, issues)     {'key', 'label', 'engine', 'fact', 'action', 'text', 'sheet': [...]}. The engine
                                reason, unless a sheet gap explains it better: a likely typo (for a name nothing or
                                only weak listings matched), no brand (a product the sheet says has none: searched by
                                name only), an unknown brand (no listing names it; a word of the name in the brand
                                cell when that is why), no size (the label reader could not confirm a listing and no
                                listing could reach tier 1 without a size; a size written in MM when that is why),
                                no barcode (only weak listings, none confirming the sheet's size, and no barcode to
                                match instead). A reason outside the sheet (search or label reader down, downloads
                                failed, only social posts) is never replaced; the other gaps are listed under 'sheet'.
    explain_outcome(spec, out)  production: catalog_match.facade stores it as trace['outcome']['explain'] where the
                                worker saves the result; None for a product with a pick.
    explain_stored(row, cands)  a queue row saved before this module existed, from its stored trace and candidates
                                (cli_bridge 'explain_backfill' and the run export).
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

EXPLAIN_VERSION = 1
DATA = Path(__file__).resolve().parent / "data"
# the worker's own cache of the sheet rows (google_sheets.get_products writes it in the repository root)
PRODUCTS_CACHE = Path(__file__).resolve().parents[1] / "products_cache.json"

PICK_DECISIONS = ("AUTO_PUBLISH", "REVIEW_PRESELECTED")

# ---------------------------------------------------------------------------
# Engine side (scripts/smoke_live.py prints these English words in its run summary)
# ---------------------------------------------------------------------------

UNSELECTED_REASONS = (
    ("verifier_mismatch", "label reader saw another product"),
    ("unsure", "label reader was unsure"),
    ("download_failed", "every image download failed"),
    ("only_social", "only social-media images"),
    ("brand_not_found", "brand not found (no listing names it)"),
    ("not_found", "nothing matched the name"),
    ("provider_down", "search service refused or failed"),
    ("verifier_down", "label reader unavailable"),
    ("weak_only", "only weak candidates, none worth reading"),
)
_SOCIAL_LABELS = frozenset({
    "instagram", "cdninstagram", "facebook", "fbcdn", "fbsbx", "tiktok", "tiktokcdn", "pinterest", "pinimg",
    "twitter", "twimg", "snapchat", "reddit", "redditmedia", "youtube", "ytimg",
})
_SOCIAL_DOMAINS = ("x.com", "fb.com", "t.co", "redd.it", "threads.net")
_TIER_RE = re.compile(r"\btier=(\d|None)\b")
_UNKNOWN_TIER = "?"


def host(url):
    try:
        found = (urlsplit(str(url or "")).hostname or "").lower()
    except ValueError:
        return ""
    return found[4:] if found.startswith("www.") else found


def social_host(name):
    name = (name or "").lower()
    if not name:
        return False
    try:
        from . import decide
        check = getattr(decide, "_social_host", None)
        if callable(check):
            return bool(check(name))
    except Exception:
        pass
    if any(name == d or name.endswith("." + d) for d in _SOCIAL_DOMAINS):
        return True
    return not _SOCIAL_LABELS.isdisjoint(name.split(".")[:-1])


def is_social(image_url="", page_url="", domain=""):
    """True when the image, its page or its reported domain is a social network."""
    return any(social_host(h) for h in (host(image_url), host(page_url), (domain or "").lower()) if h)


def survives(status, reasons):
    return status != "excluded" and not any(str(x).startswith("hard:") for x in reasons or ())


def _tier(c):
    """A top-list entry's identity tier: its 'tier' field (or evidence.tier / identity_tier of a stored candidate),
    else the 'tier=N' of its evidence text (older smoke_live files); _UNKNOWN_TIER when it does not say."""
    if "tier" in c:
        return c["tier"]
    ev = c.get("evidence")
    if isinstance(ev, Mapping) and "tier" in ev:
        return _as_tier(ev.get("tier"))
    if "identity_tier" in c:
        return _as_tier(c.get("identity_tier"))
    m = _TIER_RE.search(str(ev or ""))
    if m is None:
        return _UNKNOWN_TIER
    return None if m.group(1) == "None" else int(m.group(1))


def _as_tier(value):
    if value is None or value == "":
        return None
    try:
        return int(str(value).strip().lstrip("Tt"))
    except ValueError:
        return None


def _survivors(r):
    return [c for c in r.get("top") or [] if survives(c.get("status"), c.get("reasons"))]


def _brand_survivors(r):
    """The top list's survivors that name the brand (tier 1 or 2); None when the record does not say the tiers."""
    found = _survivors(r)
    tiers = [_tier(c) for c in found]
    if any(t == _UNKNOWN_TIER for t in tiers):
        return None
    return [c for c, t in zip(found, tiers) if t in (1, 2)]


def _brand_found(r):
    """True / False when the record or its top list says whether any listing named the brand, else None."""
    if "brand_found" in r:
        return bool(r["brand_found"])
    if not _survivors(r):
        return None
    brand = _brand_survivors(r)
    return None if brand is None else bool(brand)


def _only_social(r):
    """Every listing that names the brand is a social-network post (live run 2026-10-03, rows 29, 38, 41: the
    pipeline's failure code SOCIAL_ONLY when none of their pictures could be downloaded). Records without
    tiers: every survivor is a social post."""
    if r.get("failure_code") == "SOCIAL_ONLY":
        return True
    if "only_social" in r:
        return bool(r["only_social"])
    brand = _brand_survivors(r)
    pool = brand if brand is not None else _survivors(r)
    return bool(pool) and all(is_social(c.get("image_url") or c.get("url"), c.get("page_url"), c.get("domain"))
                              for c in pool)


def _vlm_decision(c):
    vlm = c.get("vlm")
    return vlm.get("decision") if isinstance(vlm, Mapping) else None


def _verdicts(r):
    """The label reader's readings of the listings that name the brand (all survivors without tiers)."""
    if isinstance(r.get("verdicts"), dict):
        return Counter(r["verdicts"])
    brand = _brand_survivors(r)
    pool = brand if brand is not None else _survivors(r)
    found = Counter(_vlm_decision(c) for c in pool if _vlm_decision(c))
    if brand is None:
        # no tiers in the record: the run's own count is the best it says (rows beyond the top list included)
        mismatches = int((r.get("reject_counts") or {}).get("vlm:MISMATCH") or 0)
        found["MISMATCH"] = max(found.get("MISMATCH", 0), mismatches)
    return found


def unselected_reason(r):
    """Why a row without a pick has none: one of UNSELECTED_REASONS' keys.

    Computed over the listings that name the brand (tier 1 or 2): in the live run of 2026-10-03 rows 38
    and 41 were 'label reader saw another product' because it rejected other brands' listings, while
    only social-network posts had shown the product; rows 3 and 26 had no listing of the brand at all.
    """
    decision, code = r.get("decision"), r.get("failure_code")
    if decision == "PROVIDER_DOWN":
        return "provider_down"
    if decision == "NOT_FOUND":
        return "not_found"
    if code == "SOCIAL_ONLY":
        return "only_social"
    # as decide.route: with no pick, a label reader that did not answer comes before 'only social posts'
    if decision == "VERIFIER_DOWN" or code == "VERIFIER_DOWN":
        return "verifier_down"
    if _only_social(r):
        return "only_social"
    if code == "DOWNLOAD_FAILED":
        return "download_failed"
    if _brand_found(r) is False:
        return "brand_not_found"
    verdicts = _verdicts(r)
    refuted = int((r.get("reject_counts") or {}).get("vlm:tier1_brand_refuted") or 0)
    if verdicts.get("UNSURE") and not refuted:
        return "unsure"
    if verdicts.get("MISMATCH") or refuted:
        return "verifier_mismatch"
    return "weak_only"


def brand_facts(ranked, failure_code=None):
    """smoke_live's row fields read on the listings that name the brand: verdicts, brand_found, only_social."""
    alive = [rc for rc in ranked or () if survives(rc.status, rc.reasons)]
    brand = [rc for rc in alive if rc.score is not None and rc.score.tier in (1, 2)]
    verdicts = Counter(rc.verdict.decision for rc in brand if rc.verdict is not None)
    return {
        "verdicts": dict(verdicts),
        "brand_found": bool(brand),
        "only_social": failure_code == "SOCIAL_ONLY" or (bool(brand) and all(
            is_social(rc.candidate.image_url, rc.candidate.page_url, rc.candidate.domain) for rc in brand)),
    }


# ---------------------------------------------------------------------------
# Records: what a reason is read on
# ---------------------------------------------------------------------------

def candidate_view(rc) -> Dict[str, Any]:
    """One ranked candidate as a record's top-list entry (the fields the reasons read)."""
    v = rc.verdict
    score = rc.score
    return {
        "status": rc.status, "reasons": [str(x) for x in rc.reasons],
        "tier": score.tier if score is not None else None,
        "size": score.size_status if score is not None else "unknown",
        "gtin": (score.matched or {}).get("gtin") if score is not None else None,
        "image_url": rc.candidate.image_url, "page_url": rc.candidate.page_url, "domain": rc.candidate.domain,
        "vlm": None if v is None else {"decision": v.decision, "view": v.view, "brand": v.brand_text,
                                       "variant": v.variant_text, "size": v.size_text},
    }


def outcome_record(outcome) -> Dict[str, Any]:
    """A SearchOutcome as the record unselected_reason() and explain() read."""
    record = {"decision": outcome.decision, "failure_code": outcome.failure_code,
              "reject_counts": dict(outcome.reject_counts or {}),
              "social_links": list(getattr(outcome, "social_links", None) or []),
              "top": [candidate_view(rc) for rc in outcome.ranked or ()]}
    record.update(brand_facts(outcome.ranked, outcome.failure_code))
    return record


def _loads(value, default):
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def stored_candidate_view(c: Mapping[str, Any]) -> Dict[str, Any]:
    """A stored candidate (a curation_candidates row, or a serialised candidate of a stored trace) as a top-list entry."""
    evidence = _loads(c.get("evidence", c.get("evidence_json")), {})
    evidence = evidence if isinstance(evidence, dict) else {}
    vlm = _loads(c.get("vlm", c.get("vlm_json")), None)
    vlm = vlm if isinstance(vlm, dict) else None
    reasons = _loads(c.get("reasons", c.get("reasons_json")), [])
    reasons = [str(x) for x in reasons] if isinstance(reasons, list) else []
    tier = evidence.get("tier") if "tier" in evidence else c.get("identity_tier", c.get("tier"))
    view = None
    if vlm is not None:
        view = {"decision": vlm.get("decision"), "view": vlm.get("view"),
                "brand": vlm.get("brand_text", vlm.get("brand")), "variant": vlm.get("variant_text", vlm.get("variant")),
                "size": vlm.get("size_text", vlm.get("size"))}
    return {
        "status": str(c.get("status") or "eligible"), "reasons": reasons, "tier": _as_tier(tier),
        "size": evidence.get("size") or "unknown", "gtin": evidence.get("gtin"),
        "image_url": c.get("url") or c.get("image_url") or "", "page_url": c.get("page_url") or "",
        "domain": c.get("domain") or c.get("source_domain") or evidence.get("page_domain") or "",
        "vlm": view,
    }


def stored_record(trace: Optional[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]] = (),
                  status: str = "", failure_code: Optional[str] = None) -> Dict[str, Any]:
    """The record of a queue row saved earlier: its stored trace (outcome, and the candidates of a failed row's full
    trace) or its stored review candidates."""
    trace = trace if isinstance(trace, Mapping) else {}
    outcome = trace.get("outcome") if isinstance(trace.get("outcome"), Mapping) else {}
    steps = [c for step in trace.get("steps") or [] if isinstance(step, Mapping)
             for c in step.get("candidates") or [] if isinstance(c, Mapping)]
    source = steps or [c for c in candidates or () if isinstance(c, Mapping)]
    code = outcome.get("failure_code") or failure_code
    decision = outcome.get("decision")
    if not decision:
        decision = ("NOT_FOUND" if code in ("NO_RESULTS", "ALL_CONFLICTED", "NO_MATCH") else
                    "PROVIDER_DOWN" if code == "PROVIDER_DOWN" else
                    "REVIEW_UNSELECTED" if status == "ready_for_review" else "")
    return {"decision": decision, "failure_code": code, "reject_counts": dict(outcome.get("reject_counts") or {}),
            "social_links": list(outcome.get("social_links") or []),
            "top": [stored_candidate_view(c) for c in source]}


# ---------------------------------------------------------------------------
# Typos: a word one edit from a known word
# ---------------------------------------------------------------------------

_ALPHA = "abcdefghijklmnopqrstuvwxyz"
_WORD_RE = re.compile(r"^[a-z]+$")
MIN_TYPO_LEN = 4          # shorter words are mostly shorthand ('LGT', 'WT', 'HUP')
MIN_SUBSTITUTION_LEN = 6  # one changed letter turns many short real words into each other ('pasta' / 'paste')
MAX_TYPOS = 3


def _walk_strings(value, skip=("why", "_doc", "doc", "note")):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            if str(k) in skip:
                continue
            yield from _walk_strings(v, skip)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _walk_strings(v, skip)


def _json(name):
    with open(DATA / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def lexicon() -> Tuple[frozenset, frozenset]:
    """(words a typo may be corrected to, shorthand that is no typo): the variants lexicon, the common brand
    words, the abbreviations' expansions, the sheet spellings' readings and data/grocery_words.json; the
    abbreviation keys, the identity stop and unit words are known shorthand."""
    from .text_norm import tokens

    words, known = set(), set()

    def add(texts, target):
        for text in texts:
            target.update(t for t in tokens(text) if _WORD_RE.match(t))

    try:
        lex = _json("variants_lexicon.json")
        add(_walk_strings(lex.get("contexts")), words)
        add(_walk_strings(lex.get("axes")), words)
    except Exception:  # pragma: no cover - a broken data file never breaks a search
        logger.debug("explain: variants lexicon unreadable", exc_info=True)
    for name, key in (("common_words.json", "words"), ("grocery_words.json", "words")):
        try:
            add(_json(name).get(key) or [], words)
        except Exception:  # pragma: no cover
            logger.debug("explain: %s unreadable", name, exc_info=True)
    try:
        for group in (_json("abbreviations.json").get("groups") or {}).values():
            expand = group.get("expand") or {}
            add(expand.values(), words)
            add(expand.keys(), known)
    except Exception:  # pragma: no cover
        logger.debug("explain: abbreviations unreadable", exc_info=True)
    try:
        for key, entry in (_json("sheet_spellings.json").get("entries") or {}).items():
            add([entry.get("reads") or ""], words)
            add([key], known)
    except Exception:  # pragma: no cover
        logger.debug("explain: sheet spellings unreadable", exc_info=True)
    try:
        from . import identity
        known.update(t for t in identity._STOPWORDS | identity._UNIT_WORDS if _WORD_RE.match(t))
    except Exception:  # pragma: no cover
        pass
    words = {w for w in words if len(w) >= 3}
    return frozenset(words), frozenset(known | words)


class Vocabulary:
    """The words a sheet name is checked against: the lexicons, and how many sheet rows use each word."""

    def __init__(self, sheet_counts: Optional[Mapping[str, int]] = None):
        self.words, self.known = lexicon()
        self.sheet = dict(sheet_counts or {})

    @classmethod
    def from_names(cls, names: Iterable[str]) -> "Vocabulary":
        return cls(sheet_word_counts(names))

    def count(self, word: str) -> int:
        return int(self.sheet.get(word, 0))

    def is_known(self, word: str) -> bool:
        return word in self.known or self.count(word) >= 2

    def suggestable(self, word: str) -> bool:
        return len(word) >= MIN_TYPO_LEN and (word in self.words or self.count(word) >= 2)


def sheet_word_counts(names: Iterable[str]) -> Dict[str, int]:
    """How many names use each plain word (once per name)."""
    from .text_norm import tokens

    counts: Counter = Counter()
    for name in names or ():
        counts.update({t for t in tokens(name) if _WORD_RE.match(t)})
    return dict(counts)


_SHEET_CACHE: Dict[str, Any] = {"stamp": None, "vocabulary": None}


def default_vocabulary(path: Optional[Path] = None) -> Vocabulary:
    """The lexicons and the words of the sheet's product names as the worker last read them (products_cache.json,
    google_sheets.get_products); the lexicons alone without that file. Never reads the Google Sheet."""
    path = Path(path or PRODUCTS_CACHE)
    try:
        st = os.stat(path)
        stamp = (str(path), st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    if _SHEET_CACHE["vocabulary"] is not None and _SHEET_CACHE["stamp"] == stamp:
        return _SHEET_CACHE["vocabulary"]
    names: List[str] = []
    if stamp is not None:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            names = [str(p.get("product_name") or "") for p in (data.get("products") or []) if isinstance(p, Mapping)]
        except Exception:
            logger.debug("explain: products cache unreadable", exc_info=True)
    vocab = Vocabulary.from_names(names)
    _SHEET_CACHE.update(stamp=stamp, vocabulary=vocab)
    return vocab


def _edit_kind(token: str, word: str) -> Optional[str]:
    """How `token` (the sheet's spelling) is one edit from `word`: 'swap' (two neighbours swapped), 'missing'
    (the sheet lacks one letter), 'extra' (the sheet has one more), 'changed' (one letter differs); else None."""
    lt, lw = len(token), len(word)
    if lt == lw:
        diff = [i for i in range(lt) if token[i] != word[i]]
        if len(diff) == 1:
            return "changed"
        if len(diff) == 2 and diff[1] == diff[0] + 1 and token[diff[0]] == word[diff[1]] \
                and token[diff[1]] == word[diff[0]]:
            return "swap"
        return None
    if abs(lt - lw) != 1:
        return None
    short, long_ = (token, word) if lt < lw else (word, token)
    i = 0
    while i < len(short) and short[i] == long_[i]:
        i += 1
    if short[i:] != long_[i + 1:]:
        return None
    if lt < lw:
        return "missing"
    # one more letter at the very end is a derived form ('milky', 'creamy'), not a typo
    return None if i == len(short) else "extra"


_KIND_ORDER = {"swap": 0, "missing": 1, "extra": 2, "changed": 3}


def _plural_pair(a: str, b: str) -> bool:
    return a in (b + "s", b + "es") or b in (a + "s", a + "es")


def _neighbours(token: str) -> Iterable[str]:
    for i in range(len(token) + 1):
        for c in _ALPHA:
            yield token[:i] + c + token[i:]                      # the sheet lacks a letter
        if i < len(token):
            yield token[:i] + token[i + 1:]                      # the sheet has one more
            for c in _ALPHA:
                if c != token[i]:
                    yield token[:i] + c + token[i + 1:]          # one letter changed
        if i < len(token) - 1:
            yield token[:i] + token[i + 1] + token[i] + token[i + 2:]   # two letters swapped


def _correction(token: str, vocab: Vocabulary, lexicon_only: bool = False) -> Optional[str]:
    best = None
    for word in set(_neighbours(token)):
        if word == token or _plural_pair(token, word):
            continue
        in_lexicon = word in vocab.words and len(word) >= MIN_TYPO_LEN
        if not (in_lexicon or (not lexicon_only and vocab.suggestable(word))):
            continue
        kind = _edit_kind(token, word)
        if kind is None or (kind == "changed" and len(token) < MIN_SUBSTITUTION_LEN):
            continue
        rank = (_KIND_ORDER[kind], 0 if in_lexicon else 1, -vocab.count(word), word)
        if best is None or rank < best[0]:
            best = (rank, word)
    return best[1] if best else None


def _glued_correction(token: str, vocab: Vocabulary) -> Optional[str]:
    """A word glued to the next one with a typo in one of them: 'LUNCHENMEAT' -> 'LUNCHEON MEAT'."""
    if len(token) < 8:
        return None
    for i in range(3, len(token) - 2):
        a, b = token[:i], token[i:]
        for left, right in ((a, b), (b, a)):
            if right in vocab.words and len(right) >= 3 and len(left) >= MIN_TYPO_LEN and left not in vocab.words:
                fixed = _correction(left, vocab, lexicon_only=True)
                if fixed and _edit_kind(left, fixed) in ("swap", "missing", "extra"):
                    return f"{fixed} {right}" if left == a else f"{right} {fixed}"
    return None


def _styled(text: str, like: str) -> str:
    if like.isupper():
        return text.upper()
    if like[:1].isupper():
        return text.title()
    return text


def _as_written(name: str, token: str) -> str:
    """The token as the sheet writes it (its letters, in the sheet's case)."""
    m = re.search(r"(?<![A-Za-z])" + re.escape(token) + r"(?![A-Za-z])", name or "", re.IGNORECASE)
    return m.group(0) if m else token.upper()


def known_spellings(name: str, name_ar: str = "", category: str = "") -> List[Dict[str, Any]]:
    """The sheet spellings (data/sheet_spellings.json) in a name: the search already reads them another way."""
    from . import sheet_names
    from .text_norm import tokens

    toks = tokens(name)
    context = sheet_names.raw_context(name, name_ar, category)
    ctx = set(tokens(context, strip_clitics=True)) | set(tokens(sheet_names.readable(name, context), strip_clitics=True))
    out, used = [], set()
    for rule in sheet_names.rules():
        n = len(rule.toks)
        for i in range(len(toks) - n + 1):
            if tuple(toks[i:i + n]) != rule.toks or used & set(range(i, i + n)):
                continue
            if rule.context is not None and not (rule.context & ctx):
                continue
            used |= set(range(i, i + n))
            written = " ".join(_as_written(name, t) for t in rule.toks)
            out.append({"word": written, "suggest": _styled(rule.words, written), "known": True, "tokens": rule.toks})
    return out


def typo_suggestions(name: str, vocab: Optional[Vocabulary] = None, brand: str = "", name_ar: str = "",
                     category: str = "") -> List[Dict[str, Any]]:
    """Likely typos in a sheet product name: [{'word', 'suggest', 'known'}], at most MAX_TYPOS.

    known=True: a sheet spelling the search already reads the stores' way (data/sheet_spellings.json): worth fixing in
    the sheet, but not why a product has no pick. Otherwise a plain word of 4+ letters that no lexicon has and fewer
    than two sheet names use, one edit from a known word (a swap, a missing or an extra letter; a changed letter only
    in words of 6+ letters); a word glued to the next with a typo in one of them. The brand's own words are left to
    the Brands Mapping, and a word the sheet repeats is still checked against the lexicons ('CHICKN' in many rows).
    """
    from .text_norm import tokens

    vocab = vocab or default_vocabulary()
    found = known_spellings(name, name_ar, category)
    covered = {t for f in found for t in f.pop("tokens")}
    brand_words = set(tokens(brand))
    seen = set()
    for token in tokens(name):
        if (token in covered or token in brand_words or token in seen or not _WORD_RE.match(token)
                or len(token) < MIN_TYPO_LEN or token in vocab.known):
            continue
        seen.add(token)
        repeated = vocab.count(token) >= 2
        fixed = _correction(token, vocab, lexicon_only=repeated) or (None if repeated else _glued_correction(token, vocab))
        if fixed:
            written = _as_written(name, token)
            found.append({"word": written, "suggest": _styled(fixed, written), "known": False})
    return found[:MAX_TYPOS]


# ---------------------------------------------------------------------------
# Sheet side
# ---------------------------------------------------------------------------

BARCODE_STATUS_TEXT = {
    "scientific_notation": "مكتوب بصيغة علمية (مثل 6.29E+12) فضاعت أرقامه",
    "bad_check_digit": "رقم التحقق فيه غلط",
    "bad_length": "عدد أرقامه مش صحيح",
    "not_numeric": "فيه حروف",
    "all_zero": "كله أصفار",
}


def _quote(text) -> str:
    return f"«{' '.join(str(text or '').split())}»"


MIN_MM_TYPO = 50          # '900 MM' fries are 900 g; a '9MM' or '12 MM' cut is a cut
_MM_RE = re.compile(r"(?<![\w.,])(\d+(?:[.,]\d+)?)\s*(mm)(?![^\W\d_])", re.IGNORECASE)


@lru_cache(maxsize=1)
def food_words() -> frozenset:
    """Words that name a food: data/grocery_words.json without its 'non_food' list, and the variants lexicon's
    product contexts ('fries', 'tuna', 'masala')."""
    from .text_norm import tokens

    out = set()
    try:
        data = _json("grocery_words.json")
        out.update(w for w in data.get("words") or [] if _WORD_RE.match(str(w)))
        out.difference_update(data.get("non_food") or [])
    except Exception:  # pragma: no cover
        logger.debug("explain: grocery words unreadable", exc_info=True)
    try:
        for words in (_json("variants_lexicon.json").get("contexts") or {}).values():
            out.update(t for w in words for t in tokens(w) if _WORD_RE.match(t))
    except Exception:  # pragma: no cover
        logger.debug("explain: variants lexicon unreadable", exc_info=True)
    return frozenset(out)


def _food(spec) -> bool:
    """The product type is a food: its last product-type word names one ('FRENCH FRIES'; never 'ICE CREAM SCOOP'
    or 'KITCHEN TISSUE'), or, with no such word, the name states a variant (a flavour, a fat level)."""
    from .text_norm import tokens

    words = [t for tok in getattr(spec, "class_tokens", ()) or () for t in tokens(tok)]
    if not words:
        return bool(getattr(spec, "variants", None))
    head, food = words[-1], food_words()
    return head in food or (len(head) > 3 and head.endswith("s") and head[:-1] in food)


def size_unit_typo(row: Mapping[str, Any], spec) -> Optional[Dict[str, Any]]:
    """{'key': 'size_unit_typo', 'word': '900 MM', 'suggest': '900 GM'} when a food's only size is written in
    millimetres from MIN_MM_TYPO up (live run 2026-10-04, row 4: 'BATO FRENCH FRIES 900 MM' had no size, so a
    2.5 KG bag was pre-selected); None otherwise. Display only: the search never reads '900 MM' as a weight."""
    if getattr(spec, "size", None) is not None:
        return None
    found = [m for text in (str(row.get("size") or ""), str(getattr(spec, "raw_name", "") or row.get("name") or ""))
             for m in _MM_RE.finditer(text)]
    if len(found) != 1:
        return None
    number, unit = found[0].group(1), found[0].group(2)
    try:
        value = float(number.replace(",", "."))
    except ValueError:
        return None
    if value < MIN_MM_TYPO or not _food(spec):
        return None
    return {"key": "size_unit_typo", "word": f"{number} {unit}", "suggest": f"{number} {_styled('gm', unit)}"}


def brand_product_word(spec, vocab: Optional["Vocabulary"] = None) -> Optional[Dict[str, Any]]:
    """{'key': 'brand_has_product_word', 'brand', 'suggest', 'word', 'fix'?, 'known'} for an unmapped sheet brand
    that holds a word of the product name; None otherwise.

    known=True: identity already matches the brand without the words that begin a variant phrase of the name
    ('AMERICAN LIGHT' + 'MEAT TUNA' is matched as 'AMERICAN', live run 2026-10-04 rows 27-28): worth fixing in the
    sheet, not why a product has no pick. known=False: the brand's last word is not a word but a typo of a variant
    word of the name ('SQ SALITED DRY PRAWNS': 'SALITED' is 'SALTED', row 79); the brand is left whole (what remains,
    'SQ', is too short to match), and that is why no listing names it.
    """
    from . import variants as variants_mod
    from .sheet_names import spec_name
    from .text_norm import is_arabic, tokens

    brand = " ".join(str(getattr(spec, "brand_raw", "") or "").split())
    if getattr(spec, "brand_conf", "") != "sheet_raw" or not brand:
        return None
    btoks = tokens(brand)
    canonical = " ".join(str(getattr(spec, "brand_canonical", "") or "").split())
    ctoks = tokens(canonical)
    if ctoks and len(ctoks) < len(btoks) and btoks[:len(ctoks)] == ctoks:
        return {"key": "brand_has_product_word", "brand": brand, "suggest": canonical,
                "word": brand[len(canonical):].strip(" /\\.,;:-_|&+"), "known": True}
    if len(btoks) < 2 or any(is_arabic(t) for t in btoks) or not _WORD_RE.match(btoks[-1]):
        return None
    vocab = vocab or default_vocabulary()
    last = btoks[-1]
    if last in vocab.known or len(last) < MIN_TYPO_LEN:
        return None
    fixed = _correction(last, vocab, lexicon_only=True)
    if not fixed:
        return None
    name = spec_name(spec)
    ntoks = tokens(name, strip_clitics=True)
    n = len(btoks)
    start = next((i for i in range(len(ntoks) - n + 1) if ntoks[i:i + n] == btoks), None)
    if start is None:
        return None
    at = start + n - 1
    text = " ".join(ntoks[:at] + [fixed] + ntoks[at + 1:])
    if not any(a == at for _axis, _value, (a, _b) in variants_mod.phrase_spans(text, variants_mod.spec_context(spec))):
        return None
    written = _as_written(brand, last)
    kept = brand[:brand.upper().rfind(written.upper())].strip(" /\\.,;:-_|&+")
    if not kept:
        return None
    return {"key": "brand_has_product_word", "brand": brand, "suggest": kept, "word": written,
            "fix": _styled(fixed, written), "known": False}


def sheet_issue_text(issue: Mapping[str, Any]) -> str:
    key = issue.get("key")
    if key == "no_size":
        return "الحجم ناقص بالشيت"
    if key == "size_unit_typo":
        return f"الحجم مكتوب {_quote(issue.get('word'))} — غالبًا قصدك {_quote(issue.get('suggest'))}"
    if key == "no_brand":
        return "المنتج بلا ماركة بالشيت"
    if key == "brand_has_product_word":
        text = (f"عمود الماركة فيه كلمة من اسم المنتج: {_quote(issue.get('brand'))} — الماركة غالبًا "
                f"{_quote(issue.get('suggest'))}")
        if issue.get("fix"):
            text += f"، و{_quote(issue.get('word'))} قصدك {_quote(issue.get('fix'))}"
        return text
    if key == "no_barcode":
        status = str(issue.get("status") or "missing")
        return "الباركود ناقص بالشيت" if status == "missing" else \
            "الباركود بالشيت مش صالح: " + BARCODE_STATUS_TEXT.get(status, "مش رقم GTIN صحيح")
    if key == "brand_unknown":
        if issue.get("empty"):
            return "خانة الماركة فاضية بالشيت"
        return f"الماركة {_quote(issue.get('brand'))} مش موجودة في Brands Mapping"
    if key == "typo":
        if issue.get("known"):
            return f"{_quote(issue.get('word'))} بالشيت، والبحث بيقراها {_quote(issue.get('suggest'))}"
        return f"يمكن {_quote(issue.get('word'))} قصدك {_quote(issue.get('suggest'))}"
    if key == "duplicate_barcode":
        rows = "، ".join(str(r) for r in issue.get("rows") or [])
        return f"نفس الباركود مكتوب لمنتج ثاني (صف {rows})"
    return ""


def sheet_issues(row: Mapping[str, Any], spec=None, mappings=None, vocab: Optional[Vocabulary] = None,
                 discovered: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """What the sheet row lacks for a confident pick, each with its short Arabic text.

    row: the sheet row as identity.build_sku_spec reads it (name, name_ar, brand, brand_ar, barcode, category, size).
    spec: its SkuSpec when the caller has it (else built with mappings). discovered: store spellings of the brand
    the search found (brand_discovery): such a brand is not 'unknown'. A brand cell that says the product has none
    is 'no_brand', never 'brand_unknown' (the Run page's sheet card does not list it: the owner wrote it so).
    """
    from .identity import build_sku_spec

    if spec is None:
        spec = build_sku_spec(row, mappings)
    out: List[Dict[str, Any]] = []
    gtin_ok = getattr(spec, "gtin_status", "") == "ok"
    if getattr(spec, "size", None) is None:
        out.append({"key": "no_size", "barcode": gtin_ok})
        unit = size_unit_typo(row, spec)
        if unit is not None:
            out.append(unit)
    if not gtin_ok:
        out.append({"key": "no_barcode", "status": getattr(spec, "gtin_status", "") or "missing"})
    brand_raw = str(row.get("brand") or row.get("brand_ar") or "").strip()
    placeholder = getattr(spec, "brand_placeholder", "") if getattr(spec, "brand_conf", "") == "none" else ""
    if placeholder:
        out.append({"key": "no_brand", "brand": placeholder})
    elif getattr(spec, "brand_conf", "") not in ("mapped", "learned") and not (
            tuple(getattr(spec, "discovered_brands", ()) or ()) or tuple(discovered or ())):
        out.append({"key": "brand_unknown", "brand": brand_raw, "empty": not brand_raw})
    word = brand_product_word(spec, vocab)
    if word is not None:
        out.append(word)
    name = str(row.get("name") or row.get("product_name") or "")
    for typo in typo_suggestions(name, vocab, brand=brand_raw, name_ar=str(row.get("name_ar") or ""),
                                 category=str(row.get("category") or "")):
        out.append(dict({"key": "typo"}, **typo))
    for issue in out:
        issue["text"] = sheet_issue_text(issue)
    return out


def duplicate_barcodes(products: Sequence[Mapping[str, Any]]) -> Dict[int, List[int]]:
    """{row: [other rows]} for the rows whose valid barcode another, differently named product also carries."""
    from .gtin import normalize_gtin

    groups: Dict[str, List[Tuple[int, str]]] = {}
    for p in products or ():
        try:
            gtin, status = normalize_gtin(p.get("barcode"))
        except Exception:
            continue
        if status != "ok" or not gtin:
            continue
        name = " ".join(str(p.get("product_name") or p.get("name") or "").casefold().split())
        groups.setdefault(gtin, []).append((int(p.get("row_number") or 0), name))
    out: Dict[int, List[int]] = {}
    for rows in groups.values():
        if len({name for _r, name in rows}) < 2:
            continue
        for row, _name in rows:
            out[row] = sorted(r for r, _n in rows if r != row)
    return out


# ---------------------------------------------------------------------------
# One reason, one sentence
# ---------------------------------------------------------------------------

REASON_LABELS = {
    "typo": "غلطة إملائية بالاسم",
    "brand_unknown": "ماركة غير معروفة",
    "brand_has_product_word": "كلمة من الاسم بعمود الماركة",
    "no_brand": "منتج بلا ماركة",
    "no_size": "حجم ناقص بالشيت",
    "size_unit_typo": "وحدة الحجم غلط",
    "no_barcode": "باركود ناقص بالشيت",
    "unsure": "قارئ الملصق غير متأكد",
    "verifier_mismatch": "قارئ الملصق شاف منتج ثاني",
    "brand_not_found": "ولا صفحة بتذكر الماركة",
    "weak_only": "صور ضعيفة بس",
    "all_conflicted": "كل الصور لمنتج ثاني",
    "not_found": "ما انلقت ولا صورة",
    "only_social": "صور تواصل اجتماعي بس",
    "download_failed": "الصور ما تحمّلت",
    "verifier_down": "قارئ الملصق ما اشتغل",
    "provider_down": "البحث ما اشتغل",
}
REASON_KEYS = tuple(REASON_LABELS)
# a likely typo explains a name nothing (or only weak or other products' listings) matched; never a label reader's doubt
_NAME_REASONS = frozenset({"not_found", "all_conflicted", "brand_not_found", "weak_only"})
# reasons no sheet gap can explain: the search or the label reader was down, nothing downloaded, only social posts
_OUTSIDE_SHEET = frozenset({"provider_down", "verifier_down", "download_failed", "only_social"})
_CONFLICT_TEXT = (("size_conflict", "حجم مختلف"), ("pack_conflict", "عدد عبوات مختلف"),
                  ("competitor_brand", "ماركة ثانية"), ("variant_conflict", "نوع مختلف"),
                  ("gtin_mismatch", "باركود مختلف"), ("stock_or_clipart", "صور مخزون"),
                  ("reviewer_negative", "صور رفضتها قبل"))


def _images(n: int) -> str:
    if n == 1:
        return "صورة وحدة"
    if n == 2:
        return "صورتين"
    if 3 <= n <= 10:
        return f"{n} صور"
    return f"{n} صورة"


def engine_reason(record: Mapping[str, Any]) -> str:
    """unselected_reason(), with 'all_conflicted' told apart from an empty search (NOT_FOUND / ALL_CONFLICTED)."""
    key = unselected_reason(record)
    if key == "not_found" and record.get("failure_code") == "ALL_CONFLICTED":
        return "all_conflicted"
    return key


def _brand_listings(record) -> List[Mapping[str, Any]]:
    brand = _brand_survivors(record)
    return brand if brand is not None else _survivors(record)


_SIZE_RE = re.compile(r"\bsize=(\w+)")


def _size_status(c) -> str:
    """A listing's size evidence: its 'size' field, else its evidence (a stored dict, or smoke_live's 'size=...')."""
    if c.get("size"):
        return str(c["size"])
    ev = c.get("evidence")
    if isinstance(ev, Mapping):
        return str(ev.get("size") or "")
    m = _SIZE_RE.search(str(ev or ""))
    return m.group(1) if m else ""


def _size_confirmed(record) -> bool:
    return any(_size_status(c) == "match" or c.get("gtin") == "match" for c in _brand_listings(record))


def _read_text(record) -> str:
    """What the label reader read on the first brand listing it rejected ('Americana', '400 g')."""
    for c in _brand_listings(record):
        vlm = c.get("vlm") if isinstance(c.get("vlm"), Mapping) else None
        if vlm and vlm.get("decision") == "MISMATCH":
            parts = [str(vlm.get(k) or "").strip() for k in ("brand", "variant", "size")]
            text = " ".join(p for p in parts if p)
            if text:
                return text[:80]
    return ""


def _conflicts(record) -> str:
    counts = record.get("reject_counts") or {}
    found = []
    for code, text in _CONFLICT_TEXT:
        n = sum(int(v or 0) for k, v in counts.items() if str(k).startswith(code))
        if n:
            found.append(text)
    return "، ".join(found)


def _fact_and_action(key: str, record, issues: Mapping[str, Mapping[str, Any]], brand: str, size_text: str
                     ) -> Tuple[str, str]:
    top = record.get("top") or []
    alive = _survivors(record)
    n_alive = len(alive) or len(top)
    verdicts = _verdicts(record)
    if key == "typo":
        typo = issues["typo"]
        return (f"يمكن في غلطة إملائية بالاسم: {_quote(typo.get('word'))} قصدك {_quote(typo.get('suggest'))}؟",
                "صحّح الاسم في الشيت ثم أعد البحث.")
    if key == "brand_unknown":
        if issues["brand_unknown"].get("empty"):
            return "خانة الماركة فاضية بالشيت، فما في ماركة نتأكد منها على الصور", "أضف الماركة في الشيت ثم أعد البحث."
        return (f"الماركة {_quote(brand)} مش موجودة في Brands Mapping، وما لقينا متجر بيكتبها",
                "أضف الماركة في Brands Mapping (مع طريقة كتابتها بالمتاجر) ثم أعد البحث.")
    if key == "brand_has_product_word":
        word = issues.get("brand_has_product_word") or {}
        fix = (f"، و{_quote(word.get('word'))} قصدك {_quote(word.get('fix'))}" if word.get("fix") else "")
        return (f"عمود الماركة فيه كلمة من اسم المنتج: {_quote(word.get('brand') or brand)}، والماركة غالبًا "
                f"{_quote(word.get('suggest'))}{fix}، فولا صفحة بتذكر الماركة متل ما هي مكتوبة",
                "صحّح عمود الماركة بالشيت ثم أعد البحث" + ("، أو اختر من الصور تحت." if n_alive else "."))
    if key == "no_brand":
        return ("المنتج بلا ماركة بالشيت، فالبحث بالاسم بس وصعب نلاقي صورته الصحيحة",
                "إذا إله ماركة اكتبها بعمود الماركة، " + ("أو اختر من الصور تحت، " if n_alive else "")
                + "أو صوّره وارفع الصورة.")
    if key == "size_unit_typo":
        unit = issues.get("size_unit_typo") or {}
        return (f"الحجم بالشيت مكتوب {_quote(unit.get('word'))}، وهاد طول مش وزن، فما في صورة قدرنا نتأكد إنها نفس "
                "العبوة", f"صحّح الحجم في الشيت (غالبًا {_quote(unit.get('suggest'))}) ثم أعد البحث، أو اختر من الصور تحت.")
    if key == "no_size":
        what = "حجم" if issues["no_size"].get("barcode") else "حجم ولا باركود"
        unsure = int(verdicts.get("UNSURE") or 0)
        tail = f"، وقارئ الملصق ما تأكد من {_images(unsure)}" if unsure else ""
        return (f"الشيت ما فيه {what} لهالمنتج، فما في صورة قدرنا نتأكد إنها نفس العبوة{tail}",
                "أضف الحجم في الشيت ثم أعد البحث، أو اختر من الصور تحت.")
    if key == "no_barcode":
        size = _quote(size_text) if size_text else "اللي بالشيت"
        return (f"ولا صفحة للماركة كتبت الحجم {size}، والشيت ما فيه باركود يأكد المنتج",
                "أضف الباركود في الشيت ثم أعد البحث، أو اختر من الصور تحت.")
    if key == "unsure":
        n = int(verdicts.get("UNSURE") or 0) or n_alive
        size = "" if "no_size" in issues or _size_confirmed(record) else (
            f"، وولا صفحة كتبت الحجم {_quote(size_text)}" if size_text else "، وولا صفحة كتبت الحجم")
        return (f"قارئ الملصق ما تأكد إنها نفس المنتج على {_images(n)} للماركة{size}", "اختر من الصور تحت.")
    if key == "verifier_mismatch":
        read = _read_text(record)
        return (("قارئ الملصق شاف منتج ثاني على صور الماركة" + (f" (قرأ {_quote(read)})" if read else "")),
                "اختر من الصور تحت إذا وحدة منها صحيحة، أو تأكد من الاسم بالشيت.")
    if key == "brand_not_found":
        what = f"الماركة {_quote(brand)}" if brand else "الماركة"
        return (f"لقينا {_images(n_alive)}، بس ولا صفحة منها بتذكر {what}",
                "اختر من الصور تحت إذا وحدة منها صحيحة، أو صحّح اسم الماركة في الشيت أو Brands Mapping ثم أعد البحث.")
    if key == "weak_only":
        return (f"لقينا {_images(n_alive)} للماركة، بس ولا وحدة وصلت للثقة اللي بتخلينا نختارها لحالنا",
                "اختر من الصور تحت.")
    if key == "all_conflicted":
        why = _conflicts(record)
        return ((f"لقينا {_images(len(top))} بس كلها لمنتج ثاني" + (f" ({why})" if why else "")) if top
                else "كل الصور اللي لقيناها لمنتج ثاني" + (f" ({why})" if why else ""),
                "تأكد من الحجم والنوع بالشيت ثم أعد البحث، أو ارفع صورة من جهازك.")
    if key == "not_found":
        return "البحث ما رجّع ولا صورة لهالمنتج", "تأكد من الاسم والماركة في الشيت ثم أعد البحث، أو ارفع صورة من جهازك."
    if key == "only_social":
        n = len(record.get("social_links") or []) or len([c for c in alive if is_social(
            c.get("image_url"), c.get("page_url"), c.get("domain"))])
        return ((f"المنتج ظاهر بس بمنشورات تواصل اجتماعي ({n})" if n else "المنتج ظاهر بس بمنشورات تواصل اجتماعي")
                + "، وما في متجر عرض صورته", "افتح المنشور واحفظ الصورة ثم ارفعها، أو اختر من الصور تحت.")
    if key == "download_failed":
        return f"لقينا {_images(n_alive)} بس ما قدرنا نحمّل ولا وحدة من مواقعها", "أعد البحث بعدين، أو ارفع صورة من جهازك."
    if key == "verifier_down":
        return "قارئ الملصق ما كان شغّال وقت البحث، فما انفحصت الصور", "أعد البحث بعدين، أو اختر من الصور تحت."
    if key == "provider_down":
        return "خدمة البحث ما ردّت وقت البحث (رصيد أو انقطاع)", "أعد البحث بعدين."
    return "النظام ما اختار صورة لهالمنتج", "اختر من الصور تحت."


def choose_reason(engine: str, issues: Mapping[str, Mapping[str, Any]], record: Mapping[str, Any]) -> str:
    """The engine reason, or the sheet gap that explains it (see the module docstring). Each gap replaces only the
    engine reasons it can explain, so a reason outside the sheet (provider_down, verifier_down, download_failed,
    only_social) always stays."""
    typo = issues.get("typo")
    if typo is not None and not typo.get("known") and engine in _NAME_REASONS:
        return "typo"
    # no brand to search for or to confirm: whatever the search found, by name only, is unconfirmed
    if "no_brand" in issues and engine not in _OUTSIDE_SHEET:
        return "no_brand"
    if "brand_unknown" in issues and (engine in ("not_found", "brand_not_found") or (
            engine == "all_conflicted" and any(str(k).startswith("competitor_brand")
                                               for k in (record.get("reject_counts") or {})))):
        word = issues.get("brand_has_product_word")
        return "brand_has_product_word" if word is not None and not word.get("known") else "brand_unknown"
    if engine in ("unsure", "weak_only") and "no_size" in issues:
        return "size_unit_typo" if "size_unit_typo" in issues else "no_size"
    # weak listings and nothing to tell them apart: neither a page stating the size nor a barcode to match
    if engine == "weak_only" and "no_barcode" in issues and not _size_confirmed(record):
        return "no_barcode"
    return engine


def explain(record: Mapping[str, Any], issues: Sequence[Mapping[str, Any]] = (), brand: str = "",
            size_text: str = "", source: str = "search") -> Dict[str, Any]:
    """One reason for a record without a pick: {'v', 'key', 'label', 'engine', 'fact', 'action', 'text', 'sheet',
    'source'}. 'sheet' holds every sheet gap of the row (with its short text), the reason's own included."""
    engine = engine_reason(record)
    by_key: Dict[str, Mapping[str, Any]] = {}
    for issue in issues or ():
        name = str(issue.get("key"))
        current = by_key.get(name)
        # the sentence names the first typo the search does not already read the stores' way
        if current is None or (name == "typo" and current.get("known") and not issue.get("known")):
            by_key[name] = issue
    key = choose_reason(engine, by_key, record)
    fact, action = _fact_and_action(key, record, by_key, brand, size_text)
    sheet = [{k: v for k, v in dict(i).items() if k in ("key", "text", "word", "suggest", "known", "status", "brand")}
             for i in issues or ()]
    text = f"{fact} {action}" if fact.endswith("؟") else f"{fact}. {action}"
    return {"v": EXPLAIN_VERSION, "key": key, "label": REASON_LABELS.get(key, key), "engine": engine,
            "fact": fact, "action": action, "text": text, "sheet": sheet, "source": source}


def _row_size_text(row: Mapping[str, Any], spec) -> str:
    """The size as the sheet states it: the size cell, else the size read from the name ('900g', '3x185g')."""
    size = " ".join(str(row.get("size") or "").split())
    if size:
        return size
    s = getattr(spec, "size", None)
    try:
        return s.canonical() if s is not None else ""
    except Exception:
        return ""


def explain_outcome(spec, outcome, vocab: Optional[Vocabulary] = None) -> Optional[Dict[str, Any]]:
    """The reason a searched product has no pick (None when it has one). The sheet gaps are read on the spec the
    search ran with; the typo check reads the lexicons and the products cache (never the Google Sheet)."""
    if outcome.decision in PICK_DECISIONS and outcome.winner is not None:
        return None
    record = outcome_record(outcome)
    row = {"name": spec.raw_name, "name_ar": spec.name_ar, "brand": spec.brand_raw or spec.brand_ar,
           "category": spec.category, "size": ""}
    issues = sheet_issues(row, spec=spec, vocab=vocab,
                          discovered=tuple(getattr(outcome, "discovered_brands", None) or ()))
    return explain(record, issues, brand=spec.brand_raw or spec.brand_ar, size_text=_row_size_text(row, spec),
                   source="search")


def queue_sheet_row(queue_row: Mapping[str, Any]) -> Dict[str, str]:
    """The sheet row a queue row was searched for (its columns and payload_json), as build_sku_spec reads it."""
    payload = _loads(queue_row.get("payload_json"), {})
    payload = payload if isinstance(payload, dict) else {}
    return {"name": str(queue_row.get("product_name") or ""), "brand": str(queue_row.get("brand") or ""),
            "barcode": str(queue_row.get("barcode") or ""), "name_ar": str(payload.get("name_ar") or ""),
            "brand_ar": str(payload.get("brand_ar") or ""), "category": str(payload.get("category") or ""),
            "size": str(payload.get("size") or "")}


def stored_discovered(trace: Optional[Mapping[str, Any]], candidates=()) -> List[str]:
    """The stores' spellings of the brand a stored search used: its trace's outcome.discovered_brands, and a
    candidate's 'warn:brand_spelling:' reason (a pick saved before the outcome kept them)."""
    outcome = trace.get("outcome") if isinstance(trace, Mapping) else None
    found = [str(d) for d in ((outcome or {}).get("discovered_brands") or []) if str(d or "").strip()] \
        if isinstance(outcome, Mapping) else []
    for c in candidates or ():
        reasons = _loads(c.get("reasons", c.get("reasons_json")), [])
        for r in reasons if isinstance(reasons, list) else []:
            if str(r).startswith("warn:brand_spelling:"):
                found.append(str(r).split(":", 2)[2])
    return list(dict.fromkeys(found))


def explain_stored(queue_row: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]] = (), mappings=None,
                   vocab: Optional[Vocabulary] = None) -> Optional[Dict[str, Any]]:
    """The reason for a queue row saved earlier, from what is stored: its trace (outcome, and a failed row's
    candidates) and its review candidates. None when the stored result has a pick. A store spelling of the brand
    is in the outcome's discovered_brands, or on a candidate's 'warn:brand_spelling:' reason (stored_discovered)."""
    from .identity import build_sku_spec

    trace = _loads(queue_row.get("trace_json"), {})
    trace = trace if isinstance(trace, dict) else {}
    outcome = trace.get("outcome") if isinstance(trace.get("outcome"), dict) else {}
    if outcome.get("decision") in PICK_DECISIONS and (
            outcome.get("winner_url") or any(str(c.get("status")) == "preselected" for c in candidates or ())):
        return None
    record = stored_record(trace, candidates, str(queue_row.get("status") or ""), queue_row.get("failure_code"))
    if not record.get("decision"):
        return None
    row = queue_sheet_row(queue_row)
    spec = build_sku_spec(row, mappings)
    issues = sheet_issues(row, spec=spec, vocab=vocab, discovered=stored_discovered(trace, candidates))
    return explain(record, issues, brand=row["brand"] or row["brand_ar"], size_text=_row_size_text(row, spec),
                   source="stored")
