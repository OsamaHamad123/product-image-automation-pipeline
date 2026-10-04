"""catalog_match.explain: why a product has no pick, one reason and one Arabic sentence per product.

Outcomes here are routed by the real decide.route on candidates scored by the real score_candidate, so a reason is
read on what the engine actually decided. Each test failed before its change:

* the worker's trace held no reason at all (facade.outcome_to_legacy now stores trace['outcome']['explain'] for a
  product without a pick, and nothing for one with a pick);
* computing the reason never changes the decision, the pick, a status or a reason of any candidate;
* the sheet-side gaps that block a confident pick (no size, no barcode, a brand in no Brands Mapping entry and not
  discovered, a likely typo with its suggested spelling) are named, and replace a vaguer engine reason only where
  they explain it; a reason outside the sheet (search or label reader down, downloads, social posts) is never
  replaced;
* the typo finder suggests the right word for a swap, a missing letter and a glued word, and stays quiet on real
  words (derived forms, plurals, short words, brand words);
* scripts/smoke_live.py reads the same engine reasons from this module (its summary of the live runs is unchanged).
"""

import csv
import socket
from pathlib import Path

import pytest

from catalog_match import decide, explain, facade, settings
from catalog_match.identity import build_sku_spec
from catalog_match.models import (
    Candidate, FetchedImage, ProviderHealth, QualityReport, RankedCandidate, VerificationResult, VlmImageVerdict,
)
from catalog_match.score import rank_key, score_candidate

REPO = Path(__file__).resolve().parents[2]
RUN = REPO / "runs" / "2026-10-03"
MAPPINGS = {"mr john": {"brand": "Mr John", "synonyms": ["MR JOHN"], "excluded_competitors": []},
            "target": {"brand": "Target", "synonyms": ["TARGET"], "excluded_competitors": []},
            "almarai": {"brand": "Almarai", "synonyms": ["ALMARAI"], "excluded_competitors": []}}


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    # the sheet words come from the worker's products cache: none in these tests (lexicons only)
    monkeypatch.setattr(explain, "PRODUCTS_CACHE", tmp_path / "products_cache.json")
    explain._SHEET_CACHE.update(stamp=None, vocabulary=None)


def listing(title, n=1, domain="carrefouruae.com"):
    slug = title.lower().replace(" ", "-")
    return Candidate(image_url=f"https://cdn.{domain}/img/{n}.jpg", page_url=f"https://www.{domain}/mafuae/en/{slug}/p/{n}",
                     title=title, page_title=title, domain=domain, provider="serper", query_id="Q1", rank=n)


def reading(decision, **extra):
    return VlmImageVerdict(index=0, decision=decision, view="front_packshot", **extra)


def routed(spec, rows, verifier="ok"):
    """decide.route over real scores: rows are (candidate, verdict or None)."""
    ranked = []
    for i, (cand, verdict) in enumerate(rows):
        fetched = FetchedImage(candidate=cand, ok=True, content_sha256=f"{i:064d}", width=1200, height=1200,
                               path_or_bytes=b"x", phash=f"{i:016x}")
        ranked.append(RankedCandidate(candidate=cand, score=score_candidate(spec, cand), fetched=fetched,
                                      quality=QualityReport(hard_ok=True, quality_score=0.8), verdict=verdict))
    ranked.sort(key=lambda rc: rank_key(rc.candidate, rc.score, rc.quality.quality_score))
    verdicts = [rc.verdict for rc in ranked if rc.verdict is not None]
    result = VerificationResult(status=verifier, verdicts=verdicts, calls=1 if verdicts else 0)
    return decide.route(spec, ranked, result if verifier != "none" else None, [ProviderHealth("serper", "ok", 200)])


def snapshot(outcome):
    return (outcome.decision, outcome.failure_code, outcome.winner.candidate.image_url if outcome.winner else None,
            [(rc.candidate.image_url, rc.status, list(rc.reasons)) for rc in outcome.ranked],
            dict(outcome.reject_counts))


# ---------------------------------------------------------------------------
# Where the worker saves the result: trace['outcome']['explain']
# ---------------------------------------------------------------------------

def test_the_worker_trace_says_why_a_product_has_no_pick():
    spec = build_sku_spec({"name": "MR JOHN FRENCH FRIES", "brand": "MR JOHN"}, MAPPINGS)
    outcome = routed(spec, [(listing("Mr John French Fries 900g"), reading("UNSURE"))])
    assert outcome.decision == "REVIEW_UNSELECTED" and outcome.winner is None
    before = snapshot(outcome)
    trace = {}
    legacy = facade.outcome_to_legacy(outcome, trace, spec=spec)
    why = trace["outcome"]["explain"]
    assert why["key"] == "no_size" and why["engine"] == "unsure" and why["label"] == "حجم ناقص بالشيت"
    # the fact (no size and no barcode in the sheet, the label reader's doubt), then what to do
    assert why["text"].startswith("الشيت ما فيه حجم ولا باركود لهالمنتج")
    assert "قارئ الملصق ما تأكد من صورة وحدة" in why["text"]
    assert why["text"].endswith("أضف الحجم في الشيت ثم أعد البحث، أو اختر من الصور تحت.")
    assert {i["key"] for i in why["sheet"]} == {"no_size", "no_barcode"}
    # display only: the decision, the (absent) pick, every status and reason are exactly as routed
    assert snapshot(outcome) == before and legacy["decision"] == "REVIEW_UNSELECTED" and legacy["url"] is None


def test_a_product_with_a_pick_gets_no_reason():
    spec = build_sku_spec({"name": "MR JOHN FRENCH FRIES 900GM", "brand": "MR JOHN"}, MAPPINGS)
    outcome = routed(spec, [(listing("Mr John French Fries 900g"),
                             reading("MATCH", brand_match="yes", size_match="yes", variant_match="yes"))])
    assert outcome.decision == "REVIEW_PRESELECTED"
    trace = {}
    facade.outcome_to_legacy(outcome, trace, spec=spec)
    assert "explain" not in trace["outcome"]
    assert explain.explain_outcome(spec, outcome) is None


def test_a_failing_explanation_never_breaks_the_search_result(monkeypatch):
    spec = build_sku_spec({"name": "MR JOHN FRENCH FRIES", "brand": "MR JOHN"}, MAPPINGS)
    outcome = routed(spec, [(listing("Mr John French Fries 900g"), reading("UNSURE"))])

    def boom(*args, **kwargs):
        raise RuntimeError("lexicon unreadable")

    monkeypatch.setattr(explain, "explain_outcome", boom)
    trace = {}
    legacy = facade.outcome_to_legacy(outcome, trace, spec=spec)
    assert legacy["decision"] == "REVIEW_UNSELECTED" and "explain" not in trace["outcome"]


# ---------------------------------------------------------------------------
# One reason: the engine's, or the sheet gap that explains it
# ---------------------------------------------------------------------------

def test_an_unknown_brand_explains_listings_that_never_name_it():
    row = {"name": "BARTS TRADITON FRENCH FRIES 1KG", "brand": "BARTS TRADITON"}
    rows = [(listing("Emborg French Fries Straight Cut 1kg", 1), reading("UNSURE")),
            (listing("Simplot Golden Fries 1kg", 2), None)]
    unmapped = build_sku_spec(row, {})
    why = explain.explain_outcome(unmapped, routed(unmapped, rows))
    assert why["engine"] == "brand_not_found" and why["key"] == "brand_unknown"
    assert why["text"] == ("الماركة «BARTS TRADITON» مش موجودة في Brands Mapping، وما لقينا متجر بيكتبها. "
                           "أضف الماركة في Brands Mapping (مع طريقة كتابتها بالمتاجر) ثم أعد البحث.")
    mapped = build_sku_spec(row, {"bart's": {"brand": "Bart's", "synonyms": ["BARTS TRADITON"]}})
    why = explain.explain_outcome(mapped, routed(mapped, rows))
    assert why["key"] == "brand_not_found" and "brand_unknown" not in {i["key"] for i in why["sheet"]}
    assert why["text"].startswith("لقينا صورتين، بس ولا صفحة منها بتذكر الماركة «BARTS TRADITON»")


def test_a_store_spelling_the_search_discovered_is_not_an_unknown_brand():
    spec = build_sku_spec({"name": "RIO MARIE LIGHT MEAT TUNA 3X70GM", "brand": "RIO MARIE"}, {})
    issues = explain.sheet_issues({"name": spec.raw_name, "brand": "RIO MARIE"}, spec=spec, discovered=["Rio Mare"])
    assert "brand_unknown" not in {i["key"] for i in issues}
    issues = explain.sheet_issues({"name": spec.raw_name, "brand": "RIO MARIE"}, spec=spec)
    assert [i["text"] for i in issues if i["key"] == "brand_unknown"] == ["الماركة «RIO MARIE» مش موجودة في Brands Mapping"]


def test_a_likely_typo_explains_a_name_nothing_matched():
    spec = build_sku_spec({"name": "TARGET CHICEKN LUNCHEON MEAT 340GM", "brand": "TARGET"}, MAPPINGS)
    outcome = routed(spec, [])
    assert (outcome.decision, outcome.failure_code) == ("NOT_FOUND", "NO_RESULTS")
    why = explain.explain_outcome(spec, outcome)
    assert why["engine"] == "not_found" and why["key"] == "typo"
    assert why["text"] == ("يمكن في غلطة إملائية بالاسم: «CHICEKN» قصدك «CHICKEN»؟ صحّح الاسم في الشيت ثم أعد البحث.")


def test_all_conflicted_names_what_the_listings_differ_in():
    spec = build_sku_spec({"name": "ALMARAI FRESH MILK 1L", "brand": "ALMARAI", "barcode": "6281007035309"}, MAPPINGS)
    outcome = routed(spec, [(listing("Almarai Fresh Milk 2L", 1), None), (listing("Almarai Fresh Milk 500ml", 2), None)])
    assert (outcome.decision, outcome.failure_code) == ("NOT_FOUND", "ALL_CONFLICTED")
    why = explain.explain_outcome(spec, outcome)
    assert why["key"] == "all_conflicted" and why["label"] == "كل الصور لمنتج ثاني"
    assert why["text"].startswith("لقينا صورتين بس كلها لمنتج ثاني (حجم مختلف)")


def test_a_reason_outside_the_sheet_is_never_replaced_by_a_sheet_gap():
    spec = build_sku_spec({"name": "MR JOHN FRENCH FRIES", "brand": "MR JOHN"}, MAPPINGS)
    outcome = routed(spec, [(listing("Mr John French Fries 900g"), None)], verifier="unknown")
    assert outcome.failure_code == "VERIFIER_DOWN" and outcome.winner is None
    why = explain.explain_outcome(spec, outcome)
    assert why["key"] == "verifier_down" and "no_size" in {i["key"] for i in why["sheet"]}
    assert why["text"] == "قارئ الملصق ما كان شغّال وقت البحث، فما انفحصت الصور. أعد البحث بعدين، أو اختر من الصور تحت."


def test_the_label_readers_doubt_stays_the_reason_when_the_sheet_has_a_size():
    spec = build_sku_spec({"name": "MR JOHN FRENCH FRIES 900GM", "brand": "MR JOHN"}, MAPPINGS)
    outcome = routed(spec, [(listing("Mr John Fries Crinkle Cut", 1), reading("UNSURE")),
                            (listing("Mr John Fries Value Pack", 2), reading("UNSURE"))])
    why = explain.explain_outcome(spec, outcome)
    assert why["key"] == "unsure" and why["label"] == "قارئ الملصق غير متأكد"
    # the concrete fact: how many of the brand's images, and that no page stated the sheet's size
    assert why["text"] == ("قارئ الملصق ما تأكد إنها نفس المنتج على صورتين للماركة، وولا صفحة كتبت الحجم «900g». "
                           "اختر من الصور تحت.")
    assert [i["key"] for i in why["sheet"]] == ["no_barcode"]


def test_weak_listings_without_a_size_or_a_barcode_ask_for_the_barcode():
    record = {"decision": "REVIEW_UNSELECTED", "failure_code": None, "reject_counts": {},
              "top": [{"status": "eligible", "reasons": [], "tier": 2, "size": "unknown", "vlm": None,
                       "image_url": "https://cdn.x.ae/1.jpg"}]}
    issues = [{"key": "no_barcode", "status": "missing", "text": "الباركود ناقص بالشيت"}]
    why = explain.explain(record, issues, brand="Mr John", size_text="900g")
    assert (why["engine"], why["key"]) == ("weak_only", "no_barcode")
    assert why["text"].startswith("ولا صفحة للماركة كتبت الحجم «900g»، والشيت ما فيه باركود يأكد المنتج")
    record["top"][0]["size"] = "match"          # a page confirmed the size: a barcode would add nothing
    assert explain.explain(record, issues)["key"] == "weak_only"


def test_every_sentence_is_arabic_with_no_code_and_ends_with_what_to_do():
    record = {"decision": "REVIEW_UNSELECTED", "failure_code": None, "reject_counts": {}, "top": []}
    for key in explain.REASON_KEYS:
        issues = {"typo": [{"key": "typo", "word": "CHICEKN", "suggest": "CHICKEN", "known": False}],
                  "brand_unknown": [{"key": "brand_unknown", "brand": "X", "empty": False}],
                  "no_size": [{"key": "no_size", "barcode": False}],
                  "no_barcode": [{"key": "no_barcode", "status": "missing"}]}.get(key, [])
        fact, action = explain._fact_and_action(key, record, {i["key"]: i for i in issues}, "X", "1L")
        assert fact and action.endswith(".") and not any("_" in part for part in (fact, action)), key
        assert key in explain.REASON_LABELS


# ---------------------------------------------------------------------------
# Typos
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, brand, expected", [
    ("TARGET CHICEKN LUNCHEON MEAT 340GM", "TARGET", [("CHICEKN", "CHICKEN", False)]),      # two letters swapped
    ("VIRGINIA WHTE MEAT TUNA IN S/F OIL 170GM", "VIRGINIA", [("WHTE", "WHITE", False)]),  # a missing letter (live row 60)
    ("CHEDDAR CHESE SLICES 200G", "KRAFT", [("CHESE", "CHEESE", False)]),
    ("TARGET CHICKN LUNCHENMEAT 340GM", "TARGET",                                         # live row 53: the search
     [("CHICKN", "CHICKEN", True), ("LUNCHENMEAT", "LUNCHEON MEAT", True)]),              # already reads them
    ("Almarai Lunchenmeet Beef", "Almarai", []),                                          # two edits: no guess
])
def test_typos_are_found_with_the_suggested_spelling(name, brand, expected):
    found = explain.typo_suggestions(name, explain.Vocabulary(), brand=brand)
    assert [(t["word"], t["suggest"], t["known"]) for t in found] == expected


@pytest.mark.parametrize("name", [
    "NESTLE MILKY BAR 25G", "BARILLA PASTA SAUCE 400G", "PUCK CREAMY CHEESE 240G", "KDD FLAVOURED MILK",
    "LAYS SALTED CHIPS 170G", "HUP HUP FRENCH FRIES REGULAR CUT 1 KG", "AL ALALI WHITE MEAT TUNA",
])
def test_real_words_are_not_typos(name):
    assert explain.typo_suggestions(name, explain.Vocabulary(), brand=name.split()[0]) == []


def test_the_brands_own_words_are_left_to_the_brands_mapping():
    assert explain.typo_suggestions("BARTS TRADITON FRENCH FRIES", explain.Vocabulary(), brand="BARTS TRADITON") == []
    found = explain.typo_suggestions("BARTS TRADITON FRENCH FRIES", explain.Vocabulary(), brand="BARTS")
    assert [(t["word"], t["suggest"]) for t in found] == [("TRADITON", "TRADITION")]


def test_a_typo_the_sheet_repeats_is_still_checked_against_the_lexicons():
    vocab = explain.Vocabulary.from_names(["TARGET CHICEKN LUNCHEON MEAT", "TARGET CHICEKN FRANKS", "ZWAN ZWANBURGER"])
    assert [t["suggest"] for t in explain.typo_suggestions("TARGET CHICEKN FRANKS", vocab, brand="TARGET")] == ["CHICKEN"]
    # a word only the sheet knows (used by two names) is a word, and a single-use neighbour of it is a typo
    vocab = explain.Vocabulary.from_names(["A MENUDO SAUCE", "B MENUDO STEW", "C MENDUO MIX"])
    assert [t["suggest"] for t in explain.typo_suggestions("C MENDUO MIX", vocab, brand="C")] == ["MENUDO"]


def test_the_products_cache_gives_the_sheet_words(tmp_path):
    import json

    path = tmp_path / "products_cache.json"
    path.write_text(json.dumps({"products": [{"product_name": "A MENUDO SAUCE"}, {"product_name": "B MENUDO STEW"}]}),
                    encoding="utf-8")
    vocab = explain.default_vocabulary(path)
    assert vocab.count("menudo") == 2 and vocab.count("sauce") == 1


# ---------------------------------------------------------------------------
# Sheet gaps and duplicate barcodes
# ---------------------------------------------------------------------------

def test_sheet_gaps_with_their_short_texts():
    row = {"name": "KDD MILK", "brand": "KDD", "barcode": "6.29E+12"}
    issues = {i["key"]: i for i in explain.sheet_issues(row, mappings={})}
    assert issues["no_size"]["text"] == "الحجم ناقص بالشيت"
    assert issues["no_barcode"]["status"] == "scientific_notation"
    assert issues["no_barcode"]["text"].startswith("الباركود بالشيت مش صالح: مكتوب بصيغة علمية")
    assert issues["brand_unknown"]["text"] == "الماركة «KDD» مش موجودة في Brands Mapping"
    ok = explain.sheet_issues({"name": "ALMARAI MILK 1L", "brand": "ALMARAI", "barcode": "6281007035309"},
                              mappings=MAPPINGS)
    assert ok == []
    empty = explain.sheet_issues({"name": "SOME MILK 1L", "brand": "", "barcode": "6281007035309"}, mappings={})
    assert [(i["key"], i["text"]) for i in empty] == [("brand_unknown", "خانة الماركة فاضية بالشيت")]


def test_duplicate_barcodes_are_only_those_of_different_products():
    rows = [{"row_number": 2, "product_name": "Almarai Milk 1L", "barcode": "6281007035309"},
            {"row_number": 3, "product_name": "Almarai Laban 1L", "barcode": "6281007035309"},
            {"row_number": 4, "product_name": "almarai  milk 1l", "barcode": "6281007035309"},
            {"row_number": 5, "product_name": "KDD Milk", "barcode": "6281007035316"},
            {"row_number": 6, "product_name": "KDD Juice", "barcode": "N/A"},
            {"row_number": 7, "product_name": "KDD Water", "barcode": "N/A"}]
    assert explain.duplicate_barcodes(rows) == {2: [3, 4], 3: [2, 4], 4: [2, 3]}


# ---------------------------------------------------------------------------
# The live run of 2026-10-03, and scripts/smoke_live.py
# ---------------------------------------------------------------------------

def test_the_live_runs_unselected_rows_get_a_concrete_sentence():
    import importlib.util

    spec = importlib.util.spec_from_file_location("smoke_live_explain", REPO / "scripts" / "smoke_live.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script.unselected_reason is explain.unselected_reason
    assert script.UNSELECTED_REASONS is explain.UNSELECTED_REASONS
    live = {r["row"]: r for r in script.load_run(RUN / "smoke_6.json")["rows"]}
    with open(RUN / "rows_2_61.csv", encoding="utf-8-sig", newline="") as fh:
        sheet = {int(r["row"]): r for r in csv.DictReader(fh)}
    texts = {}
    for number in (12, 29, 37):
        row = {"name": sheet[number]["name"], "brand": sheet[number]["brand"]}
        issues = explain.sheet_issues(row, mappings={})
        texts[number] = explain.explain(live[number], issues, brand=row["brand"])
    assert texts[12]["key"] == "verifier_mismatch" and "«TOMEX COATED CRUNCHY FRIES 2.5KG»" in texts[12]["text"]
    assert texts[29]["key"] == "only_social" and texts[29]["text"].startswith("المنتج ظاهر بس بمنشورات تواصل اجتماعي (5)")
    assert texts[37]["key"] == "brand_unknown" and "«INA PARAMANS»" in texts[37]["text"]
    assert any(i["key"] == "typo" and i["word"] == "SEASOING" and i["known"] for i in texts[37]["sheet"])
