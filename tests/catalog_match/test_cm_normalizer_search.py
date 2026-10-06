"""What the search does with the query normaliser's reading (catalog_match.normalizer, catalog_match.query_plan).

N1 in Q3's place, the query counts, the day's spend, and that neither the identity nor any decision moves because of
a reading. The model is a fake (test_cm_normalizer).
"""

import io

import requests
from PIL import Image, ImageDraw

from catalog_match import brand_discovery as bd
from catalog_match import normalizer as nz, pipeline, query_plan
from catalog_match.facade import outcome_summary
from catalog_match.fetch import phash_hex
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, FetchedImage, ProviderResult, QueryHint, VerificationResult
from catalog_match.verify import make_verdict
from test_cm_normalizer import (  # noqa: F401  (the offline fixture and the fakes)
    GOLD, MILK, PRAWNS, SUPT, FakeClient, _Session, _key, _offline, normaliser,
)


# ---------------------------------------------------------------------------
# The query plan: N1 in Q3's place, the rescue query
# ---------------------------------------------------------------------------

def _hint(spec):
    return nz.hint_of(normaliser().normalize(spec))


def test_n1_writes_the_abbreviations_out_in_q3s_place():
    plain = query_plan.build_queries(GOLD)
    hinted = query_plan.build_queries(query_plan.with_hint(GOLD, _hint(GOLD)))
    assert [q.query_id for q in plain] == ["Q1", "Q3"] and [q.query_id for q in hinted] == ["Q1", "N1"]
    assert hinted[0] == plain[0]                                   # Q1 stays the sheet's own words
    n1 = hinted[1]
    assert n1.text.startswith("AMERICAN GOLD Light Meat Tuna Flakes in Sunflower Oil 160g (site:")
    assert n1.text.endswith(plain[1].text[plain[1].text.index("(site:"):])
    assert n1.providers_hint == ("serper",)


def test_n1_never_writes_the_models_brand_guess():
    hinted = query_plan.build_queries(query_plan.with_hint(MILK, _hint(MILK)))   # the reading says 'Nestle'
    n1 = next(q for q in hinted if q.query_id == "N1")
    assert "nestle" not in n1.text.lower() and n1.text.startswith("Almarai Full Fat Milk 1L (site:")
    # the abbreviated brand of the sheet stays the prefix; the guess 'Super Tasty' is not written
    supt = query_plan.build_queries(query_plan.with_hint(SUPT, _hint(SUPT)))
    assert not any("tasty" in q.text.lower() for q in supt)


def test_a_reading_that_only_adds_words_keeps_q3():
    spec = build_sku_spec({"name": "COCA COLA CAN 330ML X 6", "brand": "COCA COLA"}, {})
    hint = QueryHint(expanded_name="Coca-Cola Original Taste 6 x 330ml", brand="Coca-Cola")
    assert [q.query_id for q in query_plan.build_queries(query_plan.with_hint(spec, hint))] == ["Q1", "Q3"]


def test_the_plan_keeps_its_length_with_every_reading():
    for spec in (SUPT, GOLD, PRAWNS, MILK):
        assert len(query_plan.build_queries(query_plan.with_hint(spec, _hint(spec)))) == \
            len(query_plan.build_queries(spec))
    assert query_plan.with_hint(GOLD, None) is GOLD


def test_the_rescue_query_writes_the_guess_and_never_the_brand_alone():
    nb = query_plan.rescue_query(SUPT, _hint(SUPT), ())
    assert (nb.query_id, nb.text) == ("NB", "Super Tasty White Meat Solid Tuna in Salt Water 185g")
    assert query_plan.rescue_query(SUPT, _hint(SUPT), [nb.text]) is None                # already sent
    assert query_plan.rescue_query(GOLD, _hint(GOLD), ()) is None                       # the guess is the sheet's
    assert query_plan.rescue_query(SUPT, QueryHint(expanded_name="Super Tasty", brand="Super Tasty"), ()) is None
    assert query_plan.rescue_query(SUPT, QueryHint(expanded_name="Tuna", brand=""), ()) is None


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def _png(seed=2):
    img = Image.new("RGB", (800, 800), (255, 255, 255))
    ImageDraw.Draw(img).rectangle([220, 120, 580, 680], fill=(30 + seed * 30, 80, 150))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class Search:
    """One provider: `answer(query)` gives the listings of a query (the same ones by default)."""

    kind, fallback, sanctioned = "search", False, True

    def __init__(self, answer):
        self.name, self.answer, self.queries, self.specs = "serper", answer, [], []

    def search(self, query, hl, spec_):
        self.queries.append(query)
        self.specs.append(spec_)
        return ProviderResult(provider="serper", status="ok", candidates=list(self.answer(query)))


class Images:
    def fetch(self, cands, spec_):
        out = []
        for c in cands:
            body = _png(len(c.image_url) % 5)
            with Image.open(io.BytesIO(body)) as im:
                out.append(FetchedImage(candidate=c, ok=True, content_sha256=f"{abs(hash(c.image_url)):064x}"[:64],
                                        width=im.width, height=im.height, path_or_bytes=body, phash=phash_hex(im)))
        return out


class Reads:
    """The label reader: reads every image as the given brand, a front pack shot, every flag 'yes'."""

    def __init__(self, brand_text, variant_text, size_text):
        self.reading = {"brand_text": brand_text, "variant_text": variant_text, "size_text": size_text,
                        "pack_count": None, "view": "front_packshot", "brand_match": "yes", "variant_match": "yes",
                        "size_match": "yes"}
        self.specs = []

    def verify(self, spec_, images):
        self.specs.append(spec_)
        return VerificationResult(status="ok", calls=1,
                                  verdicts=[make_verdict(spec_, i, self.reading) for i, _ in enumerate(images)])


def _search(spec, answer, verifier, norm, **kw):
    search = Search(answer)
    outcome = pipeline.find_product_image(spec, providers=[search], fetcher=Images(), verifier=verifier,
                                          expansion=False, pages=False, normalizer=norm, **kw)
    return outcome, search


MILK_LISTING = Candidate(image_url="https://img.example-cdn.com/almarai-ff-milk.jpg",
                         page_url="https://www.carrefouruae.com/mafuae/en/milk/almarai-full-fat-milk/p/7",
                         title="Almarai Full Fat Milk | Carrefour UAE", page_title="Almarai Full Fat Milk",
                         provider="serper", rank=1)


def _view(outcome):
    return (outcome.decision, outcome.failure_code, outcome.sku_key,
            outcome.winner.candidate.image_url if outcome.winner else None,
            sorted(outcome.winner.reasons) if outcome.winner else None,
            [(rc.candidate.image_url, rc.score.tier, rc.status, sorted(rc.reasons)) for rc in outcome.ranked],
            outcome.vlm_calls)


def test_no_identity_or_decision_moves_because_of_a_reading(monkeypatch):
    """The listings do not depend on the query here: with and without the normaliser (whose reading even guesses
    another brand), the spec the reader and the router see, the tiers, the pick, its reasons and the decision are
    the same; only Q3's words changed."""
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    runs = {}
    for label, norm in (("off", False), ("on", normaliser())):
        reader = Reads("Almarai", "Full Fat Milk", "1L")
        outcome, search = _search(MILK, lambda q: [MILK_LISTING], reader, norm)
        runs[label] = (outcome, search, reader)
        bd.forget_all()
    (off, s_off, _), (on, s_on, r_on) = runs["off"], runs["on"]
    assert _view(on) == _view(off)
    assert on.decision == "REVIEW_PRESELECTED" and on.sku_key == MILK.sku_key
    assert all(s == MILK for s in r_on.specs) and all(s.query_hint is None for s in r_on.specs)
    assert [q.split(" (site:")[0] for q in s_on.queries][:2] == ["Almarai FF MILK 1L", "Almarai Full Fat Milk 1L"]
    assert len(s_on.queries) == len(s_off.queries)
    assert on.query_normalizer["used"] == ["N1"] and on.query_normalizer["rescue"] is False
    assert outcome_summary(on)["query_normalizer"]["expanded_name"] == "Nestle Almarai Full Fat Milk 1L"
    assert off.query_normalizer == {} and outcome_summary(off)["query_normalizer"] == {}
    assert not any(r.endswith(nz.REASON) for r in on.winner.reasons)


def test_a_failing_model_leaves_the_search_exactly_as_without_it():
    """Gemini down (a timeout, a refused connection): the same queries, the same outcome, nothing used."""
    base, base_search = _search(GOLD, lambda q: [], Reads("American Gold", "", ""), False)
    for fail in (requests.Timeout("slow"), requests.ConnectionError("down")):
        client = nz.GeminiNormalizerClient(api_key=_key(), session=_Session(fail))
        outcome, search = _search(GOLD, lambda q: [], Reads("American Gold", "", ""), normaliser(client))
        assert search.queries == base_search.queries
        assert _view(outcome) == _view(base)
        assert outcome.query_normalizer["status"] in ("timeout", "connection_error")
        assert outcome.query_normalizer["used"] == [] and outcome.query_normalizer["rescue"] is False


def test_the_injected_pipeline_never_asks_the_model_unless_told(monkeypatch):
    monkeypatch.setenv("QUERY_NORMALIZER", "gemini")
    calls = []
    monkeypatch.setattr(nz, "default_normalizer", lambda: calls.append(1) or normaliser())
    _search(GOLD, lambda q: [], Reads("American Gold", "", ""), None)
    assert calls == []
    outcome, _ = _search(GOLD, lambda q: [], Reads("American Gold", "", ""), True)
    assert calls == [1] and outcome.query_normalizer["status"] == "ok"


def test_a_custom_query_makes_no_call():
    client = FakeClient()
    outcome, search = _search(GOLD, lambda q: [], Reads("American Gold", "", ""), normaliser(client),
                              custom_query="american gold tuna flakes")
    assert client.prompts == [] and search.queries == ["american gold tuna flakes"]
    assert outcome.query_normalizer == {}


def test_the_days_spend_counts_the_billed_call_once():
    import local_cache_db
    usage = {"role": "normalizer", "provider": "gemini", "model": nz.MODEL, "input_tokens": 300, "output_tokens": 60,
             "usd": 0.000165}
    items = local_cache_db.spend_from_outcome({"query_normalizer": {"status": "ok", "usage": usage}})
    assert items == [("gemini", 1, 0.000165)]
    assert local_cache_db.spend_from_outcome({"query_normalizer": {"status": "cache"}}) == []
    assert nz.usage_of({"query_normalizer": {"usage": usage}}) == usage and nz.usage_of({}) is None
