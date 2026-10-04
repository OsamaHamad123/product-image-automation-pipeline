"""Brand discovery: the stores' spelling of a sheet brand they write differently (identity, search accuracy).

discover(spec, candidates) -> Optional[Discovery]
find(spec, candidates)     -> Optional[Discovery]   (discover() + the per-process memory, see find())
hint(spec, candidates)     -> Optional[Discovery]   (a remembered spelling for one more query only)
planned_hint(spec)         -> Optional[Discovery]   (a remembered spelling the planned queries write from Q1)
apply(spec, discovery)     -> SkuSpec
as_hint(spec, discovery)   -> SkuSpec               (queries write the spelling; no brand evidence)
corrected_query(spec)      -> Optional[PlannedQuery]
sheet_query(spec)          -> Optional[PlannedQuery] (Q1 the sheet's way, after a remembered spelling found nothing)
forget_all()                                        (each run, and each Brands Mapping load, starts empty)

The owner's sheet sometimes misspells a brand ('RIO MARIE' for Rio Mare, 'INA PARAMANS' for Ina
Paarman's) or abbreviates it ('SUP/T', 'SUPER T/', 'SUPER/T' for Super Tasty). Without a Brands
Mapping synonym no listing then carries the brand: every candidate is tier 3, and the label reader
cannot confirm the brand either (live run 2026-10-03, rows 37, 45 and 49-52).

discover() runs after the first retrieval, only when the brand is not mapped and NO candidate states
the sheet's brand (title, page title or the page's own URL slug). The words that open each listing
title, page title and slug (after 'Buy' / 'Shop') are compared with the sheet brand:
  * spelling: the same number of words, exactly one word one edit away (a letter added, dropped or
    changed, or two neighbours swapped), never on its first or last letter, the word 4+ letters and
    the brand 7+ letters (the sheet's and the store's). 'rio marie' -> 'rio mare', 'ina paramans' -> 'ina paarmans'; 'american' is
    never 'americana' (the last letter differs: that is another brand, not a typo);
  * abbreviation: only for a sheet brand written with '/' or '.' or with a one-letter word, of 2+
    words; every sheet word starts its store word, at least one store word is longer, the first sheet
    word keeps 3+ letters and the store phrase 6+ letters. 'sup t' -> 'super tasty'.
The store phrase counts only where the same text also names one of the product-type words (sheet words
written together count: 'SOLIDTUNA' in 'Solid Tuna'), it must come from a UAE store (a known UAE
retailer outside its other-country sections, a .ae site, or a site's UAE section such as Tradeling's
'/ae-en/') or from two different sites, and it must not be a known other brand. Sites are the listing
pages' own hosts (a listing without a page counts for nothing: its image host may be the same store's
CDN), never a social network or a stock-photo site. When two different store phrases qualify ('American
Gold' and 'American Garden' for 'AMERICAN G/'), nothing is discovered: which brand the sheet means is
the sheet owner's call, never a guess.

apply() makes the spec accept the store spelling: it is added to match_brands (scoring, the label
reader's accepted names and its brand check) and to discovered_brands (queries write it, the prompt
names it). brand_conf does not change, so nothing auto-publishes on a discovered spelling, and a pick
whose brand evidence is only the store spelling carries the 'brand_spelling' review warning
(decide.review_warnings). corrected_query() is one more query written with the store spelling.

find() is what the pipeline calls: discover() plus a per-process memory of the spellings rows proved,
keyed by the sheet brand's letters and digits ('SUPER T/' and 'SUPER/T' share one key; '7UP' is not 'UP').
A spelling an earlier row proved for the same sheet brand, or for a sibling spelling of it by the same
word rules ('SUPER/T' after 'SUP/T'), is only a query hint (hint(), as_hint()): it becomes this row's
brand evidence (find(), apply()) only when this row's own listings name the product under it. 'AMERICAN
G/ MAYONNAISE' proving American Garden never lets a label reading 'American Garden' confirm 'AMERICAN G/
LIGHT MEAT TUNA', whose own listings show only Americana. Never when its own listings prove another
spelling, never for a mapped or learned brand, and always review-only with the warning. The worker, the
smoke run, the offline evaluation and the replay empty the memory when they start (forget_all), so a run
never inherits a spelling from an earlier run or an earlier Brands Mapping sheet.

The pipeline asks the memory before the first query (planned_hint): a remembered spelling writes the planned
queries from Q1 (as_hint: 'Super Tasty MEAT SOLID TUNA ...' for 'SUPER T/' after 'SUPER/T' proved it, never
'SUPER T MEAT ...' first). It still counts only as above, and when this row's listings do not name the product
under it, Q1 is sent once more the sheet's way (sheet_query), so the sheet's own spelling is never left
unsearched. A dashboard re-search runs in a process of its own (cli_bridge) and starts with an empty memory: a
spelling a later row of the run proved ('SUPER/T', row 52, after 'SUP/T', row 49) reaches it only once a
reviewer approves a pick that carries it (catalog_match.learning).

What a row used is kept: SearchOutcome.discovered_brands, and the stored trace's outcome.discovered_brands
(catalog_match.facade), which the run export and the stored no-pick reason read (catalog_match.explain).
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .models import Candidate, PlannedQuery, SkuSpec
from .text_norm import (
    any_brand_in, is_arabic, match_string, normalize, store_market, tokens, url_host, url_path_text,
)

logger = logging.getLogger(__name__)

QUERY_ID = "B1"
SHEET_QUERY_ID = "B2"            # the sheet's own spelling, after a remembered one found nothing (sheet_query)
LEAD_SKIP = frozenset({"buy", "shop", "order", "new"})
MIN_SPELLING_LETTERS = 7
MIN_WORD_LETTERS = 4
MIN_ABBREV_FIRST = 3
MIN_ABBREV_LETTERS = 6
MIN_JOINED_LETTERS = 6           # a sheet word found inside the run-together title ('solidtuna')
_ABBREV_MARK_RE = re.compile(r"[A-Za-z][/.]|[/.][A-Za-z]")


@dataclass(frozen=True)
class Discovery:
    phrase: str                  # the store spelling, normalised ('rio mare')
    display: str                 # as queries write it ('Rio Mare')
    kind: str                    # 'spelling' | 'abbreviation'
    sheet_phrase: str            # the sheet phrase it stands for ('rio marie')
    domains: Tuple[str, ...]     # the sites that write it


# ---------------------------------------------------------------------------
# Word comparison
# ---------------------------------------------------------------------------

def osa_distance(a: str, b: str) -> int:
    """Optimal string alignment distance: insertions, deletions, substitutions and adjacent swaps."""
    if a == b:
        return 0
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            row[j] = min(rows[i - 1][j] + 1, row[j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[i - 2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def _latin_word(w: str) -> bool:
    return w.isalpha() and not is_arabic(w)


def _one_inner_edit(sheet: str, store: str) -> bool:
    """One edit away, the first and last letters kept ('marie' / 'mare'; never 'american' / 'americana')."""
    if sheet == store or not (_latin_word(sheet) and _latin_word(store)):
        return False
    if min(len(sheet), len(store)) < MIN_WORD_LETTERS or abs(len(sheet) - len(store)) > 1:
        return False
    if sheet[0] != store[0] or sheet[-1] != store[-1]:
        return False
    return osa_distance(sheet, store) == 1


def spelling_of(sheet: Sequence[str], store: Sequence[str]) -> bool:
    """The store words are the sheet brand with one typo (see the module docstring)."""
    if len(sheet) != len(store) or min(sum(len(w) for w in sheet), sum(len(w) for w in store)) < MIN_SPELLING_LETTERS:
        return False
    diffs = [(s, w) for s, w in zip(sheet, store) if s != w]
    return len(diffs) == 1 and _one_inner_edit(*diffs[0])


def abbreviated(raw_brand: str, sheet: Sequence[str]) -> bool:
    """The sheet writes the brand short: 'SUP/T', 'SUPER T/', 'SUPER/T', 'A.B. FOODS'."""
    return bool(_ABBREV_MARK_RE.search(raw_brand or "")) or any(len(w) == 1 and w.isalpha() for w in sheet)


def abbreviation_of(sheet: Sequence[str], store: Sequence[str]) -> bool:
    """Every sheet word starts its store word and at least one is longer ('sup t' -> 'super tasty')."""
    if len(sheet) != len(store) or len(sheet) < 2 or len(sheet[0]) < MIN_ABBREV_FIRST:
        return False
    if not all(_latin_word(w) and w.startswith(s) for s, w in zip(sheet, store)):
        return False
    return any(s != w for s, w in zip(sheet, store)) and sum(len(w) for w in store) >= MIN_ABBREV_LETTERS


# ---------------------------------------------------------------------------
# Listings
# ---------------------------------------------------------------------------

def _texts(cand: Candidate) -> List[str]:
    from .score import strip_site_suffix
    return [strip_site_suffix(cand.title or ""), strip_site_suffix(cand.page_title or ""),
            url_path_text(cand.page_url, product_segment=True)]


def _leading(text: str) -> List[str]:
    words = tokens(text)
    while words and words[0] in LEAD_SKIP:
        words = words[1:]
    return words


def _stem(word: str) -> str:
    return word[:-1] if (not is_arabic(word) and len(word) > 3 and word.endswith("s")
                         and not word.endswith("ss")) else word


def names_the_product(spec: SkuSpec, text: str) -> bool:
    """The text names one of the SKU's product-type words (also a run-together sheet word)."""
    if not spec.class_tokens:
        return True
    words = tokens(text, strip_clitics=True)
    stems, joined = {_stem(w) for w in words}, "".join(words)
    for tok in spec.class_tokens:
        for w in tokens(tok, strip_clitics=True):
            if _stem(w) in stems or (len(w) >= MIN_JOINED_LETTERS and w in joined):
                return True
    return False


def _domain(cand: Candidate) -> str:
    """The listing page's own host; '' without a page, or for a social network or a stock-photo site."""
    from .decide import _social_host
    from .score import trusted_domains
    from .text_norm import domain_matches
    host = url_host(cand.page_url) if cand.page_url else ""
    if not host or _social_host(host) or domain_matches(host, trusted_domains().get("stock_or_clipart", [])):
        return ""
    return host


def _uae_store(cand: Candidate) -> bool:
    """A known UAE retailer outside its other-country sections, a .ae site, or a site's UAE section ('/ae-en/')."""
    from .score import trusted_domains
    from .text_norm import domain_matches
    host = _domain(cand)
    market = store_market(cand.page_url)
    if not host or market == "foreign":
        return False
    return host.endswith(".ae") or market == "uae" or domain_matches(host, trusted_domains().get("uae_retailers", []))


def states_the_brand(spec: SkuSpec, cands: Iterable[Candidate]) -> bool:
    """True when any listing writes one of the spec's brand phrases (title, page title or slug)."""
    return any(any_brand_in(spec.match_brands, text) for c in cands for text in _texts(c) if text)


def _sheet_phrases(spec: SkuSpec) -> List[Tuple[str, List[str]]]:
    out = []
    for phrase in spec.match_brands:
        words = tokens(phrase)
        if words and all(_latin_word(w) for w in words) and (phrase, words) not in out:
            out.append((phrase, words))
    return out


def discover(spec: SkuSpec, cands: Iterable[Candidate]) -> Optional[Discovery]:
    """The store spelling of the sheet brand, or None (see the module docstring)."""
    cands = list(cands or ())
    if spec.brand_conf in ("mapped", "learned") or not spec.match_brands or not cands \
            or states_the_brand(spec, cands):
        return None
    phrases = _sheet_phrases(spec)
    if not phrases:
        return None
    found: Dict[str, Dict] = {}
    for cand in cands:
        if not _domain(cand):
            continue                       # no page of its own, or a social / stock site: not a store's word
        for text in _texts(cand):
            lead = _leading(text)
            if not lead or not names_the_product(spec, text):
                continue
            for phrase, sheet in phrases:
                store = lead[:len(sheet)]
                if spelling_of(sheet, store):
                    kind = "spelling"
                elif abbreviated(spec.brand_raw, sheet) and abbreviation_of(sheet, store):
                    kind = "abbreviation"
                else:
                    continue
                key = " ".join(store)
                entry = found.setdefault(key, {"kind": kind, "sheet": phrase, "domains": set(), "uae": False})
                entry["domains"].add(_domain(cand))
                entry["uae"] = entry["uae"] or _uae_store(cand)
    ranked = []
    for key, e in found.items():
        if not (e["uae"] or len(e["domains"]) >= 2):
            continue
        if spec.competitors and any_brand_in(spec.competitors, key):
            continue                         # a known other brand is never a spelling of this one
        ranked.append((not e["uae"], -len(e["domains"]), key, e))
    if not ranked:
        return None
    if len({key.replace(" ", "") for _, _, key, _ in ranked}) > 1:
        logger.info("brand discovery sku=%s: %r could be %s; not guessed", spec.sku_key, spec.brand_raw,
                    " or ".join(repr(k) for _, _, k, _ in sorted(ranked)))
        return None
    _, _, key, e = sorted(ranked)[0]
    found_one = Discovery(phrase=key, display=" ".join(w.capitalize() for w in key.split()), kind=e["kind"],
                          sheet_phrase=e["sheet"], domains=tuple(sorted(d for d in e["domains"] if d)))
    logger.info("brand discovery sku=%s: sheet %r is written %r by %s (%s)", spec.sku_key, e["sheet"],
                found_one.display, ", ".join(found_one.domains), found_one.kind)
    return found_one


# ---------------------------------------------------------------------------
# One proven spelling per process, shared by the sheet's sibling spellings
# ---------------------------------------------------------------------------

MEMORY_MAX = 512
_memory: "OrderedDict[str, Discovery]" = OrderedDict()
_memory_lock = threading.Lock()


def memory_key(brand_raw: Optional[str]) -> str:
    """The sheet brand's letters and digits: 'SUPER T/' and 'SUPER/T' are 'supert', 'SUP/T' is 'supt', '7UP' '7up'."""
    return "".join(ch for ch in normalize(brand_raw) if ch.isalnum())


def remember(spec: SkuSpec, found: Discovery) -> None:
    """Keep a spelling this process proved (from the row's own listings) for the sheet brand that has it."""
    key = memory_key(spec.brand_raw)
    if not key:
        return
    with _memory_lock:
        _memory.pop(key, None)
        _memory[key] = found
        while len(_memory) > MEMORY_MAX:
            _memory.popitem(last=False)


def forget_all() -> None:
    """Empty the memory: at the start of a run (worker, smoke run, evaluation, replay) and in tests."""
    with _memory_lock:
        _memory.clear()


def _discovery_ok(spec: SkuSpec, cands: Sequence[Candidate]) -> bool:
    """discover()'s preconditions: an unmapped, unlearned brand that no listing writes the sheet's way."""
    return spec.brand_conf not in ("mapped", "learned") and bool(spec.match_brands) \
        and not states_the_brand(spec, cands)


def recall(spec: SkuSpec) -> Optional[Discovery]:
    """The store spelling a sibling row of this process proved for this sheet brand, or None.

    The same sheet brand (memory_key) first; otherwise a remembered store phrase this sheet brand is a
    spelling or an abbreviation of, by discover()'s own word rules ('SUPER/T' after 'SUP/T' proved Super
    Tasty). Two remembered phrases that both fit are never chosen between, and a known other brand never
    counts.
    """
    key = memory_key(spec.brand_raw)
    with _memory_lock:
        entries = list(_memory.items())
    exact = next((d for k, d in entries if k and k == key), None)
    if exact is not None:
        return exact
    related: Dict[str, Discovery] = {}
    for _key, d in entries:
        store = tokens(d.phrase)
        if spec.competitors and any_brand_in(spec.competitors, d.phrase):
            continue
        for _phrase, sheet in _sheet_phrases(spec):
            if spelling_of(sheet, store) or (abbreviated(spec.brand_raw, sheet) and abbreviation_of(sheet, store)):
                related[d.phrase] = d
    if len(related) == 1:
        return next(iter(related.values()))
    return None


def _named_under(spec: SkuSpec, found: Discovery, cands: Sequence[Candidate]) -> bool:
    """One of this row's own listings opens with the spelling and names the product ('Super Tasty L.Meat Tuna')."""
    store = tokens(found.phrase)
    return bool(store) and any(_domain(c) and _leading(text)[:len(store)] == store and names_the_product(spec, text)
                               for c in cands for text in _texts(c) if text)


def _own_or_remembered(spec: SkuSpec, cands: List[Candidate]) -> Tuple[Optional[Discovery], Optional[Discovery]]:
    """(this row's own discovery, the remembered one) once they agree; (None, None) when they differ."""
    remembered = recall(spec)
    own = discover(spec, cands)
    if own is not None and remembered is not None and remembered.phrase != own.phrase:
        logger.info("brand discovery sku=%s: %r is written %r here but %r in an earlier row; not guessed",
                    spec.sku_key, spec.brand_raw, own.display, remembered.display)
        return None, None
    return own, remembered


def find(spec: SkuSpec, cands: Iterable[Candidate]) -> Optional[Discovery]:
    """The store spelling this row's own listings prove (discover()), or one an earlier row proved that this
    row's own listings name the product under; None otherwise (the remembered spelling is then only hint()).

    Live run 2026-10-03: 'SUP/T', 'SUPER T/' and 'SUPER/T' (rows 49-52) are one brand, Super Tasty; row 52's
    own results held no Super Tasty listing while row 49's did. Row 52 sends one query written 'Super Tasty'
    (hint) and takes the spelling once a listing it gets names its product under it: still review-only
    (brand_conf unchanged) and still with the brand_spelling warning. When the row's own listings prove a
    different spelling than the memory holds, neither is used: which brand the sheet means is the owner's call.
    """
    cands = list(cands or ())
    if not _discovery_ok(spec, cands):
        return None
    own, remembered = _own_or_remembered(spec, cands)
    if own is not None:
        remember(spec, own)
        return own
    if remembered is not None and _named_under(spec, remembered, cands):
        logger.info("brand discovery sku=%s: %r is written %r (proved by an earlier row on %s, named here)",
                    spec.sku_key, spec.brand_raw, remembered.display, ", ".join(remembered.domains) or "-")
        return remembered
    return None


def hint(spec: SkuSpec, cands: Iterable[Candidate]) -> Optional[Discovery]:
    """A spelling an earlier row proved for this sheet brand that this row's listings do not prove: worth one
    query written with it (as_hint), never brand evidence by itself."""
    cands = list(cands or ())
    if not _discovery_ok(spec, cands):
        return None
    own, remembered = _own_or_remembered(spec, cands)
    if own is not None or remembered is None or _named_under(spec, remembered, cands):
        return None
    return remembered


def planned_hint(spec: SkuSpec) -> Optional[Discovery]:
    """The spelling an earlier row of this process proved for this sheet brand (or a sibling spelling of it:
    recall()), to write this row's planned queries with from Q1 (as_hint), before anything is searched; None for
    a mapped, learned or already discovered brand, or without one.

    Before, a remembered spelling was only one more query after the plan: a 'SUPER T/' row searched after
    'SUPER/T' proved Super Tasty sent Q1 and Q3 as 'SUPER T MEAT SOLID TUNA ...' first (live run 2026-10-04,
    rows 49-52: each row's third query was the first one written 'Super Tasty').
    """
    if spec.brand_conf in ("mapped", "learned") or not spec.match_brands or spec.discovered_brands:
        return None
    return recall(spec)


def sheet_query(spec: SkuSpec, already: Sequence[str] = ()) -> Optional[PlannedQuery]:
    """Q1 written the sheet's way (SHEET_QUERY_ID), once: the planned queries used a remembered spelling
    (planned_hint) and this row's listings do not name the product under it ('AMERICAN G/' proved American
    Garden for a mayonnaise; the tuna row is searched as the sheet writes it too)."""
    from .query_plan import build_queries

    q1 = next((q for q in build_queries(spec) if q.query_id == "Q1"), None)
    if q1 is None or not q1.text or q1.text in set(already):
        return None
    return PlannedQuery(query_id=SHEET_QUERY_ID, text=q1.text, hl=q1.hl)


def apply(spec: SkuSpec, found: Discovery) -> SkuSpec:
    """The spec that also accepts the store spelling (brand_conf unchanged: never an auto-publish)."""
    return replace(spec, match_brands=tuple(dict.fromkeys(tuple(spec.match_brands) + (found.phrase,))),
                   discovered_brands=tuple(dict.fromkeys(tuple(spec.discovered_brands) + (found.display,))))


def as_hint(spec: SkuSpec, found: Discovery) -> SkuSpec:
    """The spec whose queries write the store spelling; its brand evidence (match_brands) is unchanged."""
    return replace(spec, discovered_brands=tuple(dict.fromkeys(tuple(spec.discovered_brands) + (found.display,))))


def discovered_phrases(spec: SkuSpec) -> Set[str]:
    return {match_string(d) for d in spec.discovered_brands if d}


def corrected_query(spec: SkuSpec, already: Sequence[str] = ()) -> Optional[PlannedQuery]:
    """Q1 written with the store spelling (query_plan prefers discovered_brands), unless it already ran."""
    from .query_plan import build_queries

    q1 = next((q for q in build_queries(spec) if q.query_id == "Q1"), None)
    if q1 is None or not q1.text or q1.text in set(already):
        return None
    return PlannedQuery(query_id=QUERY_ID, text=q1.text, hl=q1.hl)
