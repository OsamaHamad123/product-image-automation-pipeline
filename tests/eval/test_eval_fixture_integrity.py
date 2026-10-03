"""The golden fixtures are complete, well-formed and reproduce the cases the audit proved."""

import hashlib
import io
import re
from urllib.parse import urlsplit

import pytest
from PIL import Image

import imagegen
import runners

pytestmark = pytest.mark.eval

LABELS = {"correct_exact", "wrong_variant", "wrong_size", "wrong_pack", "wrong_brand", "wrong_product",
          "not_packshot", "unusable"}
PROVIDERS = {"serper", "bing_html", "off"}
DOWNLOADS = {"ok", "403", "html", "svg"}
EXPECTED = {"correct_pick", "no_pick", "not_found", "review_unselected"}
MATCH = {"yes", "no", "unsure"}
VIEWS = {"front_packshot", "other_side", "lifestyle", "multi_product", "banner", "not_product"}
SKU_KEYS = {"id", "stratum", "name_en", "name_ar", "brand", "brand_ar", "barcode", "category", "size",
            "candidates", "no_correct_candidate"}
CAND_KEYS = {"id", "image_url", "page_url", "title", "page_title", "domain", "width", "height", "provider", "rank",
             "gtin_on_page", "image_recipe", "download", "label"}
STRATA = {"dairy_variants", "juice_variants", "water", "abbreviation", "parent_brand", "multipack", "arabic_only",
          "barcode_edge", "private_label", "no_web_image", "image_quality", "pantry"}

# Named regression cases of the plan -> fixture SKU ids.
NAMED = {
    1: ["uae-001-almarai-full-fat-milk-1l"],
    2: ["uae-005-al-rawabi-laban-up-180ml"],
    3: ["uae-024-ag-mayonnaise-473ml"],
    4: ["uae-027-nido-fortified-2-25kg"],
    5: ["uae-020-masafi-water-1-5l"],
    6: ["uae-019-almarai-fresh-milk-full-fat-2-85l"],
    7: ["uae-032-pepsi-2-25l"],
    8: ["uae-060-tilda-pure-basmati-rice-5kg"],
    9: ["uae-010-meliha-long-life-organic-milk-180ml", "uae-011-meliha-chocolate-milk-180ml",
        "uae-021-mai-dubai-drinking-water-500ml", "uae-061-saba-sanabel-chakki-atta-5kg"],
    10: ["uae-037-arabic-only-almarai-milk-2l"],
    11: ["uae-048-carrefour-full-cream-milk-1l"],
    12: ["uae-043-invalid-barcode-lacnor-orange-180ml"],
    13: ["uae-051-no-correct-al-jadeed-arabic-bread"],
    14: ["uae-006-al-rawabi-fresh-milk-full-fat-2l"],
    15: ["uae-008-lacnor-full-cream-milk-1l"],
    16: ["uae-023-arwa-water-1-5l"],
    17: ["uae-013-baladna-fresh-milk-low-fat-1l"],
    18: ["uae-055-al-rawabi-fresh-milk-full-fat-1l"],
}


def _by_id(golden):
    return {s["id"]: s for s in golden["skus"]}


def _cands(sku, **where):
    return [c for c in sku["candidates"] if all(c.get(k) == v for k, v in where.items())]


def test_fixture_schema(golden, cassette):
    skus = golden["skus"]
    assert len(skus) >= 60
    ids = [s["id"] for s in skus]
    assert len(ids) == len(set(ids)), "duplicate SKU ids"
    assert STRATA <= {s["stratum"] for s in skus}, "a required stratum is missing"
    assert set(imagegen.recipes_in(skus)) == set(imagegen.RECIPES), "every image recipe is used by a candidate"
    assert sum(1 for s in skus if s["name_ar"]) / len(skus) >= 0.15

    for sku in skus:
        assert SKU_KEYS <= set(sku), f"{sku['id']}: missing {SKU_KEYS - set(sku)}"
        cids = [c["id"] for c in sku["candidates"]]
        assert len(cids) == len(set(cids)), f"{sku['id']}: duplicate candidate ids"
        urls = [runners.norm_url(c["image_url"]) for c in sku["candidates"]]
        assert len(urls) == len(set(urls)), f"{sku['id']}: two candidates share an image URL"
        assert sku["no_correct_candidate"] == (not any(c["label"] == "correct_exact" for c in sku["candidates"]))
        for c in sku["candidates"]:
            where = f"{sku['id']}/{c['id']}"
            assert CAND_KEYS <= set(c), f"{where}: missing {CAND_KEYS - set(c)}"
            assert c["label"] in LABELS, where
            assert c["provider"] in PROVIDERS, where
            assert c["download"] in DOWNLOADS, where
            assert set(c.get("surfaced_by", ["text"])) <= {"text", "gtin"}, where
            assert c["image_recipe"]["kind"] in imagegen.RECIPES, where
            assert c["image_url"].startswith("https://"), where
            if c["provider"] == "bing_html":
                m = c["bing_m"]      # the raw Bing m-JSON; the fixed parser reads t, purl, murl
                assert c["title"] == m.get("t", "") and c["page_url"] == m.get("purl", ""), where
                assert c["image_url"] == m["murl"] and c["width"] is None and c["height"] is None, where
            else:
                assert isinstance(c["width"], int) and isinstance(c["height"], int), where
            if c["provider"] == "off":
                assert c["gtin_on_page"] and sku["barcode"], where
                assert c["surfaced_by"] == ["gtin"], where
            if c["label"] == "correct_exact":
                assert c["download"] == "ok", f"{where}: a correct image must be downloadable"

        verdicts = cassette["verdicts"].get(sku["id"], {})
        for c in sku["candidates"]:
            if c["download"] != "ok":
                continue
            v = verdicts.get(c["id"])
            assert v is not None, f"{sku['id']}/{c['id']}: no cassette verdict"
            assert set(cassette["schema"]) <= set(v)
            assert {v["brand_match"], v["variant_match"], v["size_match"]} <= MATCH
            assert v["view"] in VIEWS
    assert "gemini_down" in cassette["scenarios"]

    named = golden["named_cases"]
    for case, sku_ids in NAMED.items():
        assert sorted(named[str(case)]) == sorted(sku_ids), f"named case {case}"
        for sid in sku_ids:
            sku = _by_id(golden)[sid]
            assert sku["named_case"] == case and sku["expected_v2"] in EXPECTED, sid


def test_named_cases_carry_the_audited_evidence(golden, cassette):
    s = _by_id(golden)

    # (1) Almarai Full Fat 1L: siblings, a 1500px white packshot on carrefouruae, a 403 GTIN banner from almarai.com
    sku = s[NAMED[1][0]]
    labels = {c["label"] for c in sku["candidates"]}
    assert {"wrong_variant", "wrong_size", "correct_exact"} <= labels
    titles = " | ".join(c["title"] for c in sku["candidates"])
    assert "Low Fat" in titles and "Full Fat 2L" in titles and "Strawberry" in titles
    correct = [c for c in _cands(sku, label="correct_exact") if c["domain"] == "carrefouruae.com"]
    assert correct and correct[0]["image_recipe"]["kind"] == "packshot_white"
    assert correct[0]["image_recipe"]["size"] == [1500, 1500]
    banner = [c for c in sku["candidates"]
              if urlsplit(c["image_url"]).hostname in ("almarai.com", "www.almarai.com") and c["download"] == "403"]
    assert banner and banner[0]["surfaced_by"] == ["gtin"] and banner[0]["label"] == "not_packshot"

    # (2) Laban Up 180ml: Arabic-titled correct listing, empty Bing desc, 1L sibling, wp-content banner
    sku = s[NAMED[2][0]]
    arabic = [c for c in _cands(sku, label="correct_exact") if "لبن أب الروابي 180 مل" in c["title"]]
    assert arabic and "al-rawabi-laban-up-180ml" in arabic[0]["page_url"]
    assert any(c.get("bing_m", {}).get("desc", None) == "" for c in sku["candidates"])
    assert any("1L" in c["title"] and c["label"] == "wrong_size" for c in sku["candidates"])
    assert any("wp-content/uploads" in c["image_url"] and c["label"] == "not_packshot" for c in sku["candidates"])

    # (3) A/G: correct American Garden, Heinz 'images/...package' URL and Hellmann's as distractors
    sku = s[NAMED[3][0]]
    assert sku["brand"] == "A/G"
    assert any("American Garden Mayonnaise 473ml" in c["title"] for c in _cands(sku, label="correct_exact"))
    heinz = [c for c in sku["candidates"] if c["image_url"] == "https://cdn.shop.com/images/heinz-mayonnaise-package.jpg"]
    assert heinz and heinz[0]["label"] == "wrong_brand"
    assert any("Hellmann" in c["title"] and c["label"] == "wrong_brand" for c in sku["candidates"])

    # (4) Nido 2.25kg under the parent brand Nestle
    sku = s[NAMED[4][0]]
    assert sku["brand"] == "Nestle" and "Nido" in sku["name_en"] and sku["size"] == "2.25kg"

    # (5) Masafi 1.5L: talabat title with '& Al Ain', '1-5l' slug, a 12 x 1.5L multipack
    sku = s[NAMED[5][0]]
    tal = [c for c in sku["candidates"] if c["page_title"] == "Masafi Water 1.5L - delivery in Dubai, Abu Dhabi & Al Ain"]
    assert tal and "masafi-water-1-5l" in tal[0]["page_url"] and tal[0]["label"] == "correct_exact"
    assert any("12 x 1.5L" in c["title"] and c["label"] == "wrong_pack" for c in sku["candidates"])

    # (6) the only correct Almarai listing has the slug almarai-full-fat-milk-canada
    sku = s[NAMED[6][0]]
    assert [c["id"] for c in _cands(sku, label="correct_exact")] == \
        [c["id"] for c in sku["candidates"] if "almarai-full-fat-milk-canada" in c["image_url"]]

    # (7) Pepsi 2.25L vs 'Pepsi 24x330ml'
    sku = s[NAMED[7][0]]
    assert sku["size"] == "2.25L"
    assert any("Pepsi 24x330ml" in c["title"] and c["label"] == "wrong_pack" for c in sku["candidates"])

    # (8) Tilda 5kg vs 'Tilda price offers' and a '5kg, 10kg, 20kg' listing
    sku = s[NAMED[8][0]]
    assert any(c["title"] == "Tilda price offers" and c["label"] == "not_packshot" for c in sku["candidates"])
    assert any("5kg, 10kg, 20kg" in c["title"] for c in sku["candidates"])

    # (9) the verify_image_search.py cases
    names = {(s[i]["brand"], s[i]["name_en"], s[i]["barcode"]) for i in NAMED[9]}
    assert ("Mai Dubai", "Drinking Water 500ml", "6297000611365") in names
    assert {b for b, _, _ in names} == {"Mai Dubai", "Meliha", "Saba Sanabel"}

    # (10) Arabic-only row
    sku = s[NAMED[10][0]]
    assert sku["name_en"] == "" and re.search(r"[؀-ۿ]", sku["name_ar"])
    # (11) private label
    assert s[NAMED[11][0]]["brand"] == "Carrefour"
    # (12) invalid barcode in spreadsheet scientific notation
    assert s[NAMED[12][0]]["barcode"] == "6.29E+12"
    # (13) candidates exist, none correct
    sku = s[NAMED[13][0]]
    assert sku["no_correct_candidate"] and sku["candidates"]

    # (14) every downloadable image of the SKU gets a 'no' from the model
    sku = s[NAMED[14][0]]
    for c in _cands(sku, download="ok"):
        v = cassette["verdicts"][sku["id"]][c["id"]]
        assert "no" in (v["brand_match"], v["variant_match"], v["size_match"]), c["id"]

    # (15) white carton on white, (16) 2000px minimalist bottle
    assert any(c["image_recipe"]["kind"] == "carton_white_on_white" for c in _cands(s[NAMED[15][0]], label="correct_exact"))
    big = [c for c in _cands(s[NAMED[16][0]], label="correct_exact") if c["image_recipe"].get("size") == [2000, 2000]]
    assert big and big[0]["image_recipe"]["shape"] == "bottle"

    # (17) no title, no page, a CDN URL without a slug
    sku = s[NAMED[17][0]]
    bare = [c for c in sku["candidates"] if c["title"] == "" and c["page_url"] == ""]
    assert bare and re.search(r"/IMG_\d+\.jpg$", bare[0]["image_url"])

    # (18) random noise and a shelf photo, both titled with the brand
    sku = s[NAMED[18][0]]
    kinds = {c["image_recipe"]["kind"]: c for c in sku["candidates"]}
    assert "Al Rawabi" in kinds["noise"]["title"] and "Al Rawabi" in kinds["lifestyle_clutter"]["title"]


def test_imagegen_deterministic():
    recipe = {"kind": "packshot_white", "size": [900, 900], "shape": "bottle", "label_text": "MASAFI|WATER|1.5L"}
    a, b = imagegen.generate(recipe, 7), imagegen.generate(dict(recipe), 7)
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()
    assert hashlib.sha256(imagegen.generate(recipe, 8)).hexdigest() != hashlib.sha256(a).hexdigest()
    # a white-background packshot really is mostly #FFFFFF, like the retailer packshots the audit measured
    assert imagegen.white_ratio(a) >= 0.40
    for kind in imagegen.RECIPES:
        size = imagegen.DEFAULT_SIZES[kind]
        with Image.open(io.BytesIO(imagegen.generate({"kind": kind, "label_text": "BRAND|ITEM|1L"}, 1))) as im:
            assert im.size == size, kind
    assert imagegen.white_ratio(imagegen.generate({"kind": "noise"}, 1)) < 0.01


def test_packshot_fixtures_reproduce_the_white_background_case(golden):
    """Every white-background packshot candidate has >= 40% pixels >= 254 (the legacy 'overexposed' trigger)."""
    for sku in golden["skus"]:
        for c in sku["candidates"]:
            recipe = c["image_recipe"]
            if recipe["kind"] in ("packshot_white", "tall_bottle_600x1200") and recipe.get("background") != "transparent" \
                    and c["download"] == "ok":
                assert imagegen.white_ratio(runners.candidate_image(sku, c)) >= 0.40, f"{sku['id']}/{c['id']}"


def test_correct_images_are_publishable_packshots(golden):
    """correct_exact images pass every hard gate D5 keeps: size, aspect ratio and a 3-98% foreground."""
    for sku in golden["skus"]:
        for c in sku["candidates"]:
            if c["label"] != "correct_exact":
                continue
            data = runners.candidate_image(sku, c)
            with Image.open(io.BytesIO(data)) as im:
                width, height = im.size
            where = f"{sku['id']}/{c['id']}"
            assert min(width, height) >= 250 and 0.25 <= width / height <= 4.0, where
            assert 0.03 <= imagegen.foreground_ratio(data) <= 0.98, where
            assert len(data) >= 10 * 1024, f"{where}: a real packshot of this size is never this small"


# ---------------------------------------------------------------------------
# The replay doubles behave like the real stages they stand in for
# ---------------------------------------------------------------------------

def test_query_kinds_and_site_filter(golden):
    sku = _by_id(golden)[NAMED[1][0]]
    gtin_hits = {c["id"] for c in runners.surfaced(sku, "serper", '"Almarai" 06281007000000')}
    assert gtin_hits == {c["id"] for c in sku["candidates"] if c["provider"] == "serper" and "gtin" in c["surfaced_by"]}
    assert runners.query_kind("6281007000000", sku) == "gtin"
    assert runners.query_kind("Almarai Full Fat Milk 1L", sku) == "text"
    site = runners.surfaced(sku, "serper", "Almarai Full Fat Milk 1L site:carrefouruae.com OR site:noon.com")
    assert site and {c["domain"] for c in site} <= {"carrefouruae.com", "noon.com"}
    ranks = [c["rank"] for c in runners.surfaced(sku, "serper", "Almarai Full Fat Milk 1L")]
    assert ranks == sorted(ranks)


def test_replay_doubles_follow_the_stage_protocols(golden, cassette):
    from catalog_match import models

    sku = _by_id(golden)[NAMED[2][0]]
    provider = runners.FixtureProvider(models, sku, "serper", True)
    assert isinstance(provider, models.Provider)
    result = provider.search("Al Rawabi Laban Up 180ml", "en", None)
    assert result.status == "ok" and all(isinstance(c, models.Candidate) for c in result.candidates)
    assert provider.calls == [("Al Rawabi Laban Up 180ml", "en")]
    bing = runners.FixtureProvider(models, sku, "bing_html", False).search("Al Rawabi Laban Up 180ml", "en", None)
    assert bing.candidates and all(not c.sanctioned and c.width is None for c in bing.candidates)

    fetcher = runners.FixtureFetcher(models, sku)
    assert isinstance(fetcher, models.Fetcher)
    fetched = fetcher.fetch(result.candidates, None)
    assert all(f.ok and f.width and f.content_sha256 and len(f.phash) == 16 for f in fetched)

    verifier = runners.CassetteVerifier(models, sku, cassette)
    assert isinstance(verifier, models.Verifier)
    verdicts = verifier.verify(None, fetched)
    assert verdicts.status == "ok" and len(verdicts.verdicts) == len(fetched)
    index = runners.UrlIndex(sku)
    for f, v in zip(fetched, verdicts.verdicts):
        label = index.get(f.candidate.image_url)["label"]
        if label == "correct_exact":
            assert v.decision == "MATCH", f.candidate.image_url
        if label == "not_packshot":
            assert v.decision != "MATCH", f.candidate.image_url

    down = runners.CassetteVerifier(models, sku, cassette, "gemini_down").verify(None, fetched)
    assert down.status == "unknown" and {v.decision for v in down.verdicts} == {"UNKNOWN"}


def test_fetcher_mirrors_failed_downloads(golden):
    from catalog_match import models

    sku = _by_id(golden)["uae-054-no-usable-koita-organic-laban-1l"]
    cands = [runners._to_candidate(models, c) for c in sku["candidates"]]
    index = runners.UrlIndex(sku)
    result = {index.cid(f.candidate.image_url): (f.ok, f.error)
              for f in runners.FixtureFetcher(models, sku).fetch(cands, None)}
    assert result["c2"] == (False, "http_403")
    assert result["c3"] == (False, "not_image") and result["c6"] == (False, "not_image")
    assert result["c1"][0] is True


def test_verdict_decision_rules():
    base = {"brand_match": "yes", "variant_match": "yes", "size_match": "yes", "view": "front_packshot",
            "size_text": "", "pack_count": 1}
    assert runners.decide_verdict(base, None) == "MATCH"
    assert runners.decide_verdict(dict(base, size_match="unsure"), None) == "MATCH"
    assert runners.decide_verdict(dict(base, variant_match="no"), None) == "MISMATCH"
    assert runners.decide_verdict(dict(base, view="banner"), None) == "UNSURE"
    assert runners.decide_verdict(dict(base, brand_match="unsure"), None) == "UNSURE"


def test_legacy_reason_parsing():
    rules = runners.legacy_rules([
        "مستبعدة: فشل التحقق الهندسي لجودة الصورة (Image too blurry: Laplacian variance 11.00 (Threshold: 100.0), "
        "Image overexposed)",
        "تطابق مع منافس مستبعد: 'nada'",
        "مقبولة: كخيار بديل أخير من نتائج التصفية الأولية",
        "مستبعدة: فشل التحميل المتوازي أو التحقق من الحجم/النوع (REJECTED_STATUS_403)",
    ])
    assert rules == ["Image overexposed", "Image too blurry", "competitor_substring", "download_failed"]
