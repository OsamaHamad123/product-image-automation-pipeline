"""catalog_match.expand P0: the page's own main image next to a trusted listing's picture (normal flow, free).

Run exports 2026-10-04/05: in 35 % of the rows a tier-1/2 listing's picture showed another brand's pack (Yumway read
as 'Max Foods', Dr Bone as 'LOCK&LOCK', Fine tissue as Kleenex), 17 of the 48 rows with no pick among them. P0 reads
the pages of the best trusted listings while their pictures download and offers each page's own main image, so the
reader verifies it in the same batches. Providers, page reader, image fetcher and verifier are stage doubles;
everything between them is production code. Sockets are blocked for every test.
"""

import io
import logging
import socket
import threading
import time

import pytest
from PIL import Image, ImageDraw

from catalog_match import expand, pages, pipeline, settings
from catalog_match.fetch import phash_hex
from catalog_match.identity import build_sku_spec
from catalog_match.models import Candidate, FetchedImage, ProviderResult, VerificationResult
from catalog_match.verify import make_verdict

MAPPINGS = {"yumway": {"brand": "Yumway", "synonyms": ["Yumway"], "excluded_competitors": ["Max Foods"]}}
SPEC_ROW = {"name": "YUMWAY STRAIGHT CUT FRENCH FRIES 2.5 KG", "brand": "YUMWAY"}

CARREFOUR_PAGE = "https://www.carrefouruae.com/mafuae/en/frozen-fries/yumway-straight-cut-french-fries-2-5kg/p/412345"
LULU_PAGE = "https://gcc.luluhypermarket.com/en-ae/yumway-straight-cut-french-fries-2-5-kg/p/2088123"
BLOG_PAGE = "https://www.someblog.example.com/frozen/yumway-straight-cut-french-fries-2-5kg"
THUMB = "https://cdn.mafrservices.com/sys-master-root/h1/h2/max-foods-fries_480Wx480H.jpg"     # another brand's pack
PAGE_IMAGE = "https://cdn.mafrservices.com/sys-master-root/h3/h4/412345_main.jpg"             # the page's own image
LULU_IMAGE = "https://lulu.akinoncloudcdn.com/products/2025/10/10/2088123/yumway-fries_size1920x1920.jpg"
LULU_PAGE_IMAGE = "https://lulu.akinoncloudcdn.com/products/2025/10/10/2088123/yumway-fries-front_size1920x1920.jpg"
BLOG_IMAGE = "https://img.example-cdn.com/yumway-fries.jpg"

TITLE = "Yumway Straight Cut French Fries 2.5kg | Carrefour UAE"
READ_MATCH = {"brand_text": "Yumway", "variant_text": "Straight Cut French Fries", "size_text": "2.5 kg",
              "pack_count": 1, "view": "front_packshot", "brand_match": "yes", "variant_match": "yes",
              "size_match": "yes"}
READ_MAX_FOODS = dict(READ_MATCH, brand_text="Max Foods", brand_match="no")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "")
    for name in ("PAGE_MAIN_IMAGES_MAX_PAGES", "PAGE_MAIN_IMAGES_WAIT_S", "SERPER_API_KEY", "EXPANSION_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    pages.clear_cache()


@pytest.fixture
def spec():
    return build_sku_spec(dict(SPEC_ROW), MAPPINGS)


# ---------------------------------------------------------------------------
# Stage doubles
# ---------------------------------------------------------------------------

def packshot_png(seed: int, size=(800, 800)) -> bytes:
    """A white-background product shot; the seed changes the drawing (and so the pHash)."""
    w, h = size
    img = Image.new("RGB", size, (255, 255, 255))
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = int(w * 0.28), int(h * 0.12), int(w * 0.72), int(h * 0.88)
    d.rectangle([x0, y0, x1, y1], fill=((seed * 67) % 200, (seed * 131) % 200, (seed * 29) % 200))
    for i in range(6):
        if (seed >> i) & 1:
            yy = y0 + int(30 + i * ((y1 - y0) - 60) / 6)
            d.rectangle([x0 + int(w * 0.025), yy, x1 - int(w * 0.025), yy + (y1 - y0) // 14], fill=(250, 250, 250))
    if seed % 3 == 0:
        d.ellipse([x0 - int(w * 0.075), y0 + int(h * 0.05), x0 + int(w * 0.075), y0 + int(h * 0.2)], fill=(20, 20, 160))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def listing(url, page_url, title=TITLE, provider="serper", n=1):
    return Candidate(image_url=url, page_url=page_url, page_title=title, title=title, provider=provider, rank=n)


def product_page(name, image, brand="Yumway"):
    return (f'<html><head><title>{name}</title><script type="application/ld+json">'
            f'{{"@type": "Product", "name": "{name}", "image": "{image}", '
            f'"brand": {{"@type": "Brand", "name": "{brand}"}}}}</script></head><body></body></html>')


class StubProvider:
    kind, sanctioned, fallback, name = "search", True, False, "serper"

    def __init__(self, cands):
        self.cands = list(cands)

    def search(self, query, hl, spec):
        return ProviderResult(provider="serper", status="ok" if self.cands else "empty", http_status=200,
                              candidates=list(self.cands))


class StubFetcher:
    def __init__(self, bodies, delay=0.0):
        self.bodies, self.delay = dict(bodies), delay
        self.fetched = []

    def fetch(self, cands, spec):
        if self.delay:
            time.sleep(self.delay)
        out = []
        for c in cands:
            self.fetched.append(c.image_url)
            body = self.bodies.get(c.image_url, "http_404")
            if isinstance(body, str):
                out.append(FetchedImage(candidate=c, ok=False, error=body))
                continue
            with Image.open(io.BytesIO(body)) as im:
                w, h = im.size
                ph = phash_hex(im)
            out.append(FetchedImage(candidate=c, ok=True, content_sha256=f"{abs(hash(body)):064x}"[:64],
                                    width=w, height=h, path_or_bytes=body, phash=ph))
        return out


class StubVerifier:
    def __init__(self, readings):
        self.readings = dict(readings)
        self.calls = []

    def verify(self, spec, images):
        self.calls.append([f.candidate.image_url for f in images])
        verdicts = [make_verdict(spec, i, self.readings.get(f.candidate.image_url, {})) for i, f in enumerate(images)]
        return VerificationResult(status="ok", verdicts=verdicts, calls=1)


class StubPages:
    def __init__(self, docs, delay=0.0):
        self.docs, self.delay = dict(docs), delay
        self.fetched = []
        self.lock = threading.Lock()

    def fetch_page(self, url, referer=""):
        with self.lock:
            self.fetched.append(url)
        if self.delay:
            time.sleep(self.delay)
        doc = self.docs.get(url)
        if doc is None:
            return pages.PageInfo(url=url, ok=False, error="http_404")
        return pages.extract(doc, url)


def run(spec, cands, *, docs=None, bodies=None, readings=None, page_delay=0.0, fetch_delay=0.0, use_pages=True,
        **kwargs):
    reader = StubPages(docs or {}, page_delay)
    fetcher = StubFetcher(bodies or {}, fetch_delay)
    verifier = StubVerifier(readings or {})
    outcome = pipeline.find_product_image(spec, providers=[StubProvider(cands)], fetcher=fetcher, verifier=verifier,
                                          expansion=False, pages=reader if use_pages else False, **kwargs)
    return outcome, {"pages": reader, "fetcher": fetcher, "verifier": verifier}


YUMWAY_DOCS = {CARREFOUR_PAGE: product_page("Yumway Straight Cut French Fries 2.5kg", PAGE_IMAGE)}
YUMWAY_BODIES = {THUMB: packshot_png(5, (480, 480)), PAGE_IMAGE: packshot_png(9, (1200, 1200))}
YUMWAY_READINGS = {THUMB: READ_MAX_FOODS, PAGE_IMAGE: READ_MATCH}


# ---------------------------------------------------------------------------
# The page's own main image is read next to the listing's picture
# ---------------------------------------------------------------------------

def test_wrong_listing_picture_the_pages_own_image_is_read_and_picked(spec):
    """Carrefour's Yumway listing is filed under a Max Foods pack: its page's own image is the pick."""
    outcome, d = run(spec, [listing(THUMB, CARREFOUR_PAGE)], docs=YUMWAY_DOCS, bodies=YUMWAY_BODIES,
                     readings=YUMWAY_READINGS)
    assert d["pages"].fetched == [CARREFOUR_PAGE]
    assert d["verifier"].calls == [[THUMB, PAGE_IMAGE]] or d["verifier"].calls == [[PAGE_IMAGE, THUMB]]
    assert outcome.decision == "REVIEW_PRESELECTED" and outcome.winner.candidate.image_url == PAGE_IMAGE
    page_rc = outcome.winner
    assert page_rc.candidate.provider == "page" and page_rc.candidate.sanctioned is False
    assert page_rc.score.tier in (1, 2) and page_rc.verdict.decision == "MATCH"
    assert "page_images" in outcome.timings
    # without P0 the row has no pick (the live run, before the expansion round)
    before, d = run(spec, [listing(THUMB, CARREFOUR_PAGE)], docs=YUMWAY_DOCS, bodies=YUMWAY_BODIES,
                    readings=YUMWAY_READINGS, use_pages=False)
    assert before.decision == "REVIEW_UNSELECTED" and d["pages"].fetched == []
    assert "page_images" not in before.timings


def test_a_page_image_never_auto_publishes(spec, monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.setenv("AUTO_PUBLISH_BRANDS", "*")
    mapped = build_sku_spec(dict(SPEC_ROW), MAPPINGS)
    outcome, _ = run(mapped, [listing(THUMB, CARREFOUR_PAGE)], docs=YUMWAY_DOCS, bodies=YUMWAY_BODIES,
                     readings=YUMWAY_READINGS)
    assert outcome.decision == "REVIEW_PRESELECTED" and outcome.winner.candidate.image_url == PAGE_IMAGE
    assert "auto_blocked:unsanctioned_source" in outcome.winner.reasons


def test_the_listings_own_picture_is_not_offered_again(spec):
    """Same URL (already in the pool, which stays sanctioned) or the same picture downloaded under another
    address: no second reading of it."""
    same_url = {CARREFOUR_PAGE: product_page("Yumway Straight Cut French Fries 2.5kg", PAGE_IMAGE)}
    outcome, d = run(spec, [listing(PAGE_IMAGE, CARREFOUR_PAGE)], docs=same_url,
                     bodies={PAGE_IMAGE: packshot_png(9, (1200, 1200))}, readings={PAGE_IMAGE: READ_MATCH})
    assert d["pages"].fetched == [CARREFOUR_PAGE] and d["verifier"].calls == [[PAGE_IMAGE]]
    assert outcome.winner.candidate.image_url == PAGE_IMAGE and outcome.winner.candidate.sanctioned is True
    copy = "https://cdn.mafrservices.com/sys-master-root/h5/h6/412345_copy.jpg"
    same_picture = {CARREFOUR_PAGE: product_page("Yumway Straight Cut French Fries 2.5kg", copy)}
    body = packshot_png(9, (1200, 1200))
    outcome, d = run(spec, [listing(PAGE_IMAGE, CARREFOUR_PAGE)], docs=same_picture,
                     bodies={PAGE_IMAGE: body, copy: body}, readings={PAGE_IMAGE: READ_MATCH, copy: READ_MATCH})
    assert copy in d["fetcher"].fetched                          # downloaded to compare, then left out
    assert d["verifier"].calls == [[PAGE_IMAGE]]
    assert all(rc.candidate.image_url != copy for rc in outcome.ranked)


def test_a_larger_copy_of_a_low_resolution_listing_picture_is_offered(spec):
    small, large = packshot_png(9, (420, 420)), packshot_png(9, (1500, 1500))
    copy = "https://cdn.mafrservices.com/sys-master-root/h5/h6/412345_1500.jpg"
    docs = {CARREFOUR_PAGE: product_page("Yumway Straight Cut French Fries 2.5kg", copy)}
    outcome, d = run(spec, [listing(PAGE_IMAGE, CARREFOUR_PAGE)], docs=docs, bodies={PAGE_IMAGE: small, copy: large},
                     readings={PAGE_IMAGE: READ_MATCH, copy: READ_MATCH})
    assert sorted(d["verifier"].calls[0]) == sorted([PAGE_IMAGE, copy])


# ---------------------------------------------------------------------------
# Which pages are read: trusted hosts, tier 1/2, at most PAGE_MAIN_IMAGES_MAX_PAGES distinct pages
# ---------------------------------------------------------------------------

def test_only_trusted_tier12_listing_pages_are_read(spec):
    blog = listing(BLOG_IMAGE, BLOG_PAGE, title="Yumway Straight Cut French Fries 2.5kg", n=1)
    other = listing("https://cdn.mafrservices.com/x/aida-fries.jpg",
                    "https://www.carrefouruae.com/mafuae/en/frozen-fries/aida-french-fries-1kg/p/99",
                    title="Aida French Fries 1kg | Carrefour UAE", n=2)
    indexed = listing(LULU_IMAGE, LULU_PAGE, title="Yumway Straight Cut French Fries 2.5 kg", provider="local_index",
                      n=3)
    outcome, d = run(spec, [blog, other, indexed], docs={},
                     bodies={BLOG_IMAGE: packshot_png(3), LULU_IMAGE: packshot_png(4)},
                     readings={BLOG_IMAGE: READ_MATCH, LULU_IMAGE: READ_MATCH})
    assert d["pages"].fetched == []                  # a blog, another product, a page the index already read
    assert "page_images" not in outcome.timings


def test_at_most_max_pages_distinct_pages(spec, monkeypatch):
    tracked = CARREFOUR_PAGE + "?srsltid=AfmBOoq123"
    third = "https://www.noon.com/uae-en/yumway-straight-cut-french-fries-2-5kg/N123/p/"
    cands = [listing(THUMB, CARREFOUR_PAGE, n=1),
             listing("https://cdn.mafrservices.com/x/yumway-2.jpg", tracked, n=2),
             listing(LULU_IMAGE, LULU_PAGE, title="Yumway Straight Cut French Fries 2.5 kg | Lulu", n=3),
             listing("https://f.nooncdn.com/p/yumway.jpg", third, title="Yumway Straight Cut French Fries 2.5kg | noon",
                     n=4)]
    _, d = run(spec, cands, docs=YUMWAY_DOCS, bodies=YUMWAY_BODIES, readings=YUMWAY_READINGS)
    assert len(d["pages"].fetched) == 2 and len(set(map(expand.page_key, d["pages"].fetched))) == 2
    monkeypatch.setenv("PAGE_MAIN_IMAGES_MAX_PAGES", "9")
    pages.clear_cache()
    _, d = run(spec, cands, docs=YUMWAY_DOCS, bodies=YUMWAY_BODIES, readings=YUMWAY_READINGS)
    assert len(d["pages"].fetched) == 3                                   # capped at 3
    monkeypatch.setenv("PAGE_MAIN_IMAGES_MAX_PAGES", "0")
    assert expand.resolve_pages(None, injected=False) is None


def test_a_page_that_names_another_product_or_a_reviewer_negative_is_not_offered(spec):
    other_size = {CARREFOUR_PAGE: product_page("Yumway Straight Cut French Fries 750g", PAGE_IMAGE)}
    outcome, d = run(spec, [listing(THUMB, CARREFOUR_PAGE)], docs=other_size, bodies=YUMWAY_BODIES,
                     readings=YUMWAY_READINGS)
    assert d["pages"].fetched == [CARREFOUR_PAGE] and PAGE_IMAGE not in d["fetcher"].fetched
    assert outcome.decision == "REVIEW_UNSELECTED"
    outcome, d = run(spec, [listing(THUMB, CARREFOUR_PAGE)], docs=YUMWAY_DOCS, bodies=YUMWAY_BODIES,
                     readings=YUMWAY_READINGS, exclude_urls=[PAGE_IMAGE])
    assert PAGE_IMAGE not in d["fetcher"].fetched and outcome.decision == "REVIEW_UNSELECTED"


def test_page_reader_resolution(monkeypatch):
    assert expand.resolve_pages(False, injected=False) is None
    assert expand.resolve_pages(None, injected=True) is None                 # tests and the offline eval
    assert isinstance(expand.resolve_pages(None, injected=False), pages.PageFetcher)
    double = StubPages({})
    assert expand.resolve_pages(double, injected=True) is double
    monkeypatch.setenv("PAGE_MAIN_IMAGES_WAIT_S", "60")
    assert settings.page_main_images_wait_s() == 10.0
    monkeypatch.setenv("PAGE_MAIN_IMAGES_WAIT_S", "soon")
    assert settings.page_main_images_wait_s() == 3.0
    assert settings.page_main_images_max_pages() == 2


# ---------------------------------------------------------------------------
# Time: the pages load while the pictures download; a slow page is left out
# ---------------------------------------------------------------------------

def test_a_slow_page_is_left_out_after_the_wait(spec, monkeypatch):
    monkeypatch.setenv("PAGE_MAIN_IMAGES_WAIT_S", "0.2")
    started = time.monotonic()
    outcome, d = run(spec, [listing(THUMB, CARREFOUR_PAGE)], docs=YUMWAY_DOCS, bodies=YUMWAY_BODIES,
                     readings=YUMWAY_READINGS, page_delay=1.5)
    elapsed = time.monotonic() - started
    assert d["pages"].fetched == [CARREFOUR_PAGE] and PAGE_IMAGE not in d["fetcher"].fetched
    assert outcome.decision == "REVIEW_UNSELECTED"
    assert elapsed < 1.2, elapsed                                       # never waited for the slow page


def test_pages_load_while_the_pictures_download(spec, monkeypatch):
    """The page wait overlaps the listings' download: a page as slow as the download adds (almost) nothing."""
    monkeypatch.setenv("PAGE_MAIN_IMAGES_WAIT_S", "3")
    started = time.monotonic()
    outcome, d = run(spec, [listing(THUMB, CARREFOUR_PAGE)], docs=YUMWAY_DOCS, bodies=YUMWAY_BODIES,
                     readings=YUMWAY_READINGS, page_delay=0.4, fetch_delay=0.4)
    elapsed = time.monotonic() - started
    assert outcome.winner is not None and outcome.winner.candidate.image_url == PAGE_IMAGE
    # two downloads of 0.4 s (the listing's, then the page image's) and the page read hidden behind the first
    assert elapsed < 1.6, elapsed


def test_p0_never_queues_behind_a_hosts_page_rate_limit():
    """fetch_page_now: a host with no page left right now is skipped (not cached, nothing downloaded, no token owed
    by the expansion round's later reads); with a page left it reads once and takes exactly one token."""
    class Bucket:
        def __init__(self, tokens):
            self.tokens, self.waited = tokens, 0

        def try_acquire(self):
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False

        def acquire(self):
            self.waited += 1
            return 0.0

    class Resp:
        status_code, headers = 200, {"Content-Type": "text/html; charset=utf-8"}

        def __init__(self, body):
            self.body = body

        def iter_content(self, chunk_size=65536):
            yield self.body

        def close(self):
            pass

    class Session:
        def __init__(self):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(url)
            return Resp(YUMWAY_DOCS[CARREFOUR_PAGE].encode("utf-8"))

    empty, full, session = Bucket(0), Bucket(1), Session()
    reader = pages.PageFetcher(session=session, bucket_factory=lambda host: empty)
    info = reader.fetch_page_now(CARREFOUR_PAGE)
    assert info.ok is False and info.error == "rate_limited" and session.calls == [] and empty.waited == 0
    reader = pages.PageFetcher(session=session, bucket_factory=lambda host: full)
    info = reader.fetch_page_now(CARREFOUR_PAGE)
    assert info.ok and session.calls == [CARREFOUR_PAGE] and full.tokens == 0 and full.waited == 0
    assert reader.fetch_page(CARREFOUR_PAGE) is info and session.calls == [CARREFOUR_PAGE]   # cached for X0
    assert pages.PageFetcher(session=session, bucket_factory=lambda host: empty).fetch_page_now(CARREFOUR_PAGE) is info


def test_the_page_cache_is_shared_with_the_expansion_round(spec, caplog):
    """A page P0 read is not fetched again by X0 (pages.PageFetcher's module cache), and X0 finds its main image
    already offered: no new candidate, no second reading."""
    calls = []

    class CountingFetcher(pages.PageFetcher):
        def _fetch_uncached(self, url, referer):
            calls.append(url)
            return pages.extract(YUMWAY_DOCS[url], url)

    class EmptyWeb:
        name = "serper_web"

        def search(self, query, hl, spec):
            return ProviderResult(provider="serper_web", status="empty", http_status=200)

    both_wrong = {THUMB: READ_MAX_FOODS, PAGE_IMAGE: READ_MAX_FOODS}
    fetcher, verifier = StubFetcher(YUMWAY_BODIES), StubVerifier(both_wrong)
    exp = expand.Expansion(web=EmptyWeb(), pages=CountingFetcher(), max_calls=1)
    caplog.set_level(logging.INFO, logger="catalog_match.expand")
    outcome = pipeline.find_product_image(spec, providers=[StubProvider([listing(THUMB, CARREFOUR_PAGE)])],
                                          fetcher=fetcher, verifier=verifier, expansion=exp,
                                          pages=CountingFetcher())
    assert "X0 read 1 pages" in caplog.text and "P0 read 1 trusted listing pages" in caplog.text
    assert calls == [CARREFOUR_PAGE]                          # read once, by P0; X0 used the cache
    assert outcome.decision == "REVIEW_UNSELECTED"
    assert sum(call.count(PAGE_IMAGE) for call in verifier.calls) == 1
