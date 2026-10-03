"""catalog_match.pages: product-page extraction and the one-GET page fetcher (sources package).

The HTML fixtures (fixtures/pages/*.html) are hand-written in the shapes the retailers
use: og:image + JSON-LD Product (Lulu-like), JSON-LD @graph with ImageObject renditions
(Carrefour-like), __NEXT_DATA__ embedded page JSON (noon-like), a page with related
products in its JSON-LD (talabat-like) and a page with only meta tags. Sockets are blocked.
"""

import socket
from pathlib import Path

import pytest

from catalog_match import pages, ratelimit, settings
from catalog_match.gtin import check_digit
from catalog_match.identity import build_sku_spec
from catalog_match.score import score_candidate

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pages"
LULU_URL = "https://www.luluhypermarket.com/en-ae/barts-traditional-fries-1kg/p/88231"
CARREFOUR_URL = "https://www.carrefouruae.com/mafuae/en/frozen-bread/mehran-plain-paratha-400g/p/631098"
NOON_URL = "https://www.noon.com/uae-en/fancy-tuna-in-water-170g/N40123456A/p/"
TALABAT_URL = "https://www.talabat.com/uae/grocery/family-light-tuna-brine-185g"
KIBSONS_URL = "https://www.kibsons.com/en/ajmi-meat-masala-160g"


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.delenv("PROXY_URL", raising=False)
    pages.clear_cache()
    yield
    pages.clear_cache()


def html(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def extract(name, url):
    return pages.extract(html(name), url)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def test_jsonld_product_with_og_image():
    info = extract("lulu_og_jsonld.html", LULU_URL)
    assert info.ok and "jsonld" in info.sources and "meta" in info.sources
    assert info.name == "Barts Traditional Fries 1kg"
    assert info.brand == "Barts"
    assert info.gtins == ["06291003000119"]
    # HTML entities and markup in the description are cleaned
    assert info.description == "Crispy & golden traditional cut fries. Keep frozen."
    # JSON-LD images first (the main one first), then og:image / twitter:image
    urls = [i.url for i in info.images]
    assert urls[0].endswith("barts-traditional-fries-1kg-1500x1500.jpg")
    assert any(u.endswith("-600x600.jpg") for u in urls)
    # the inline tracking script is never read as data
    assert not any("tracker.example" in u for u in urls)


def test_page_candidate_takes_the_main_image_only():
    info = extract("lulu_og_jsonld.html", LULU_URL)
    cands = pages.page_candidates(info, title="Barts Traditional Fries 1kg | Lulu UAE", snippet="",
                                  query_id="X1", rank=2)
    assert len(cands) == 1                        # never the back or side view
    c = cands[0]
    assert c.image_url.endswith("barts-traditional-fries-1kg-1500x1500.jpg")
    assert c.provider == "page" and c.sanctioned is False
    assert c.page_url == LULU_URL and c.domain == "luluhypermarket.com"
    assert c.title == "Barts Traditional Fries 1kg | Lulu UAE"
    assert c.page_title == "Barts Traditional Fries 1kg"
    assert c.query_id == "X1" and c.rank == 20
    assert c.gtin_on_page == "06291003000119"
    assert c.snippet.startswith("Crispy & golden")


def test_jsonld_graph_imageobjects_largest_rendition_and_gtin14():
    info = extract("carrefour_graph.html", CARREFOUR_URL)
    assert info.name == "Mehran Plain Paratha 400g" and info.brand == "Mehran"
    assert info.gtins == ["06291100007776"]
    [c] = pages.page_candidates(info)
    # the ?im=Resize=376 rendition and the full image are one image: the canonical URL, largest size
    assert c.image_url == "https://cdn.mafrservices.com/sys-master-root/h3a/h91/51234/631098_main.jpg"
    assert (c.width, c.height) == (1200, 1200)
    assert c.title == c.page_title == "Mehran Plain Paratha 400g"     # no listing title given


def test_next_data_product_not_recommendations():
    info = extract("noon_next_data.html", NOON_URL)
    assert "next_data" in info.sources
    assert info.name == "Fancy Tuna In Water 170g" and info.brand == "Al Alali"
    # the GTIN from the specification rows; the recommended product's GTIN is not this page's
    assert info.gtins == ["06295038123454"]
    urls = [i.url for i in info.images]
    assert urls[0] == "https://f.nooncdn.com/p/v1690000000/N40123456A_1.jpg"
    assert not any("N1_1" in u or "N2_1" in u or "logo" in u for u in urls)
    [c] = pages.page_candidates(info, title="Al Alali Fancy Tuna In Water 170g | noon")
    # the structured brand is written in front of a name that leaves it out
    assert c.page_title == "Al Alali Fancy Tuna In Water 170g"


def test_related_products_and_several_gtins():
    info = extract("related_products.html", TALABAT_URL)
    # the page's own product is the one named like og:title, not the first Product node
    assert info.name == "Family Light Meat Tuna in Brine 185g"
    assert info.images[0].url.endswith("family-light-tuna-brine-185g-large.jpg")
    assert not any("rio.jpg" in i.url or "delmonte" in i.url for i in info.images)
    [c] = pages.page_candidates(info)
    assert c.gtin_on_page is None                 # two different GTINs: none is claimed


def test_meta_only_page_and_broken_jsonld():
    info = extract("og_only.html", KIBSONS_URL)
    assert info.ok and info.sources == ["meta"]
    assert info.name == "Ajmi Meat Masala 160g - Kibsons"      # <title> when nothing else names it
    cands = pages.page_candidates(info)
    assert [c.image_url for c in cands] == [
        "https://www.kibsons.com/media/catalog/product/a/j/ajmi-meat-masala-160g.png"]


def test_amazon_main_image_without_structured_data():
    info = extract("amazon_like.html", "https://www.amazon.ae/Mehran-Paratha-400g/dp/B0C1234567")
    assert info.sources == ["img"] and info.name == "Mehran Paratha 400g : Amazon.ae: Grocery"
    [c] = pages.page_candidates(info, title="Mehran Paratha 400g : Amazon.ae: Grocery")
    # every rendition of the landing image is one image: the original file, with the largest known size
    assert c.image_url == "https://m.media-amazon.com/images/I/71abcDEF12L.jpg"
    assert (c.width, c.height) == (679, 679)
    assert all("sponsored" not in i.url for i in info.images)


def test_restricted_gtin_is_not_claimed():
    body = "29" + "1234567890"
    in_store = body + str(check_digit(body))
    doc = ('<html><head><script type="application/ld+json">{"@type": "Product", "name": "Lulu Bread 400g", '
           f'"gtin13": "{in_store}", "image": "https://lulu.example/bread.jpg"}}</script></head></html>')
    info = pages.extract(doc, "https://www.luluhypermarket.com/en-ae/bread/p/1")
    assert info.gtins                                   # it is a valid GTIN ...
    assert pages.page_candidates(info)[0].gtin_on_page is None   # ... but only unique inside one company


def test_extract_never_raises_on_garbage():
    for doc in ("", "<<<>>>", "<script type='application/ld+json'>[1, 2,", "\x00\xff" * 50,
                '<script id="__NEXT_DATA__" type="application/json">{"a": [</script>'):
        info = pages.extract(doc, "https://example.ae/p")
        assert info.ok and pages.page_candidates(info) == []


@pytest.mark.parametrize("url,w,h,thumb", [
    ("https://encrypted-tbn0.gstatic.com/images?q=tbn:abc", None, None, True),
    ("https://cdn.example.ae/p/fries.jpg", 120, 120, True),
    ("https://cdn.example.ae/p/fries.jpg", 800, 800, False),
    ("https://cdn.example.ae/p/fries_thumb.jpg", None, None, True),
    ("https://cdn.example.ae/media/thumbnail/fries.jpg", None, None, True),
    ("https://cdn.example.ae/p/fries.jpg?w=150", None, None, True),
    ("https://cdn.example.ae/p/fries.jpg?w=1200", None, None, False),
    ("data:image/png;base64,AAAA", None, None, True),
])
def test_is_thumbnail(url, w, h, thumb):
    assert pages.is_thumbnail(url, w, h) is thumb


def test_page_evidence_with_another_size_is_hard_rejected():
    spec = build_sku_spec({"name": "BARTS TRADITON FRIES 1KG", "brand": "BARTS TRADITON"}, {
        "barts": {"brand": "Barts", "synonyms": ["Barts", "BARTS TRADITON"], "excluded_competitors": ["Emborg"]}})
    doc = html("lulu_og_jsonld.html").replace("1kg", "2.5kg")
    [c] = pages.page_candidates(pages.extract(doc, LULU_URL))
    assert score_candidate(spec, c).hard_reject == ("size_conflict",)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status=200, body=b"", ctype="text/html; charset=utf-8"):
        self.status_code = status
        self._body = body
        self.headers = {"Content-Type": ctype}

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def close(self):
        pass


class FakeSession:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class CountingBucket:
    def __init__(self):
        self.taken = 0

    def acquire(self):
        self.taken += 1
        return 0.0


def fetcher(session, buckets=None):
    buckets = {} if buckets is None else buckets

    def factory(host):
        return buckets.setdefault(host, CountingBucket())

    return pages.PageFetcher(session=session, bucket_factory=factory)


def test_fetch_page_reads_and_caches_one_get_per_url():
    body = html("lulu_og_jsonld.html").encode("utf-8")
    session = FakeSession([FakeResponse(200, body)])
    buckets = {}
    f = fetcher(session, buckets)
    first = f.fetch_page(LULU_URL)
    again = f.fetch_page(LULU_URL + "#reviews")
    assert first.ok and first.name == "Barts Traditional Fries 1kg"
    assert again is first and len(session.calls) == 1            # one GET per page
    url, kwargs = session.calls[0]
    assert url == LULU_URL and kwargs["timeout"] == pages.PAGE_TIMEOUT_S
    assert kwargs["headers"]["Accept"].startswith("text/html")
    assert "proxies" not in kwargs                                 # direct first
    assert buckets["luluhypermarket.com"].taken == 1               # per-host rate limit


def test_fetch_failure_is_cached_and_proxy_is_only_a_fallback(monkeypatch):
    monkeypatch.setenv("PROXY_URL", "http://proxy.local:8080")
    timeout = type("ReadTimeout", (Exception,), {})("read timed out")
    session = FakeSession([timeout, FakeResponse(503, b"")])
    f = fetcher(session)
    info = f.fetch_page(CARREFOUR_URL)
    assert not info.ok and info.error == "http_503"
    assert "proxies" not in session.calls[0][1]
    assert session.calls[1][1]["proxies"] == {"http": "http://proxy.local:8080", "https": "http://proxy.local:8080"}
    assert f.fetch_page(CARREFOUR_URL) is info and len(session.calls) == 2   # the failure is not re-fetched


def test_fetch_rejects_non_html_and_oversized_pages():
    session = FakeSession([FakeResponse(200, b"\x89PNG" + b"0" * 4000, ctype="image/png"),
                           FakeResponse(200, b"<html>" + b"x" * (pages.MAX_PAGE_BYTES + 10))])
    f = fetcher(session)
    assert f.fetch_page("https://www.noon.com/a").error == "not_html"
    assert f.fetch_page("https://www.noon.com/b").error == "too_large"
    assert f.fetch_page("ftp://x").error == "bad_url" and len(session.calls) == 2


def test_default_bucket_is_shared_per_host():
    ratelimit.reset_registry()
    try:
        f = pages.PageFetcher(session=FakeSession([]))
        assert f._bucket_factory("noon.com") is ratelimit.get_bucket("page:noon.com", pages.PAGES_PER_MIN)
    finally:
        ratelimit.reset_registry()


# ---------------------------------------------------------------------------
# Review fixes: redirects and page encodings
# ---------------------------------------------------------------------------

class RedirectedResponse(FakeResponse):
    """A response that arrived after redirects: .url is the page the server finally sent."""

    def __init__(self, url, status=200, body=b"", ctype="text/html; charset=utf-8"):
        super().__init__(status, body, ctype)
        self.url = url


LULU_HOME = "https://www.luluhypermarket.com/en-ae/"
HOME_DOC = ('<html><head><title>Lulu Hypermarket UAE | Online Shopping</title>'
            '<meta property="og:title" content="Lulu Hypermarket UAE | Online Shopping">'
            '<meta property="og:image" content="https://www.luluhypermarket.com/medias/summer-deals-1200x630.jpg">'
            '</head><body>' + "x" * 300 + '</body></html>')
BARTS = build_sku_spec({"name": "BARTS TRADITIONAL FRIES 1KG", "brand": "BARTS"},
                       {"barts": {"brand": "Barts", "synonyms": ["Barts"], "excluded_competitors": ["Emborg"]}})


def test_a_redirected_page_is_evidence_for_itself_only():
    # a sold-out product page that sends the browser to the store's home page
    session = FakeSession([RedirectedResponse(LULU_HOME, body=HOME_DOC.encode("utf-8"))])
    info = fetcher(session).fetch_page(LULU_URL)
    assert info.ok and info.url == LULU_HOME and info.redirected_from == LULU_URL
    [c] = pages.page_candidates(info, title="Barts Traditional Fries 1kg | Lulu UAE",
                                snippet="Barts traditional fries 1kg, frozen")
    # the listing title described the product URL, not the home page's banner: it is not evidence here
    assert c.page_url == LULU_HOME and c.title == c.page_title == "Lulu Hypermarket UAE | Online Shopping"
    assert "Barts" not in c.snippet
    assert score_candidate(BARTS, c).tier == 3        # no longer a tier-1 'Barts 1kg on Lulu' candidate


@pytest.mark.parametrize("final", [
    LULU_URL.replace("https://", "http://"),           # scheme
    LULU_URL + "/",                                    # trailing slash
    LULU_URL + "?o=12&utm_source=google",              # query string
    LULU_URL.replace("www.", ""),                      # www.
])
def test_a_redirect_that_keeps_the_page_keeps_the_listing_title(final):
    session = FakeSession([RedirectedResponse(final, body=html("lulu_og_jsonld.html").encode("utf-8"))])
    info = fetcher(session).fetch_page(LULU_URL)
    assert info.redirected_from == ""
    [c] = pages.page_candidates(info, title="Barts Traditional Fries 1kg | Lulu UAE")
    assert c.title == "Barts Traditional Fries 1kg | Lulu UAE"


ARABIC_NAME = "بهارات اللحم عجمي 160 غرام"


@pytest.mark.parametrize("encoding,meta", [
    ("windows-1256", '<meta charset="windows-1256">'),
    ("windows-1256", '<meta http-equiv="Content-Type" content="text/html; charset=windows-1256">'),
    ("utf-8", ""),
])
def test_page_encoding_declared_in_the_page_is_honoured(encoding, meta):
    doc = (f'<html><head>{meta}<title>{ARABIC_NAME}</title>'
           '<meta property="og:image" content="https://www.example.ae/media/ajmi-160g.jpg"></head>'
           '<body>' + "x" * 300 + '</body></html>')
    session = FakeSession([FakeResponse(200, doc.encode(encoding), ctype="text/html")])
    info = fetcher(session).fetch_page("https://www.example.ae/ajmi-meat-masala")
    assert info.name == ARABIC_NAME


def test_byte_order_mark_and_http_charset_win():
    doc = f'<html><head><meta charset="windows-1256"><title>{ARABIC_NAME}</title></head></html>' + "x" * 300
    assert pages._decode(b"\xef\xbb\xbf" + doc.encode("utf-8"), "text/html").startswith("<html>")
    assert ARABIC_NAME in pages._decode(b"\xef\xbb\xbf" + doc.encode("utf-8"), "text/html")
    assert ARABIC_NAME in pages._decode(doc.encode("windows-1256"), "text/html; charset=windows-1256")
    # a <meta> that claims UTF-16 on a readable page is UTF-8 (HTML spec), not garbled UTF-16
    utf16_claim = doc.replace("windows-1256", "utf-16")
    assert ARABIC_NAME in pages._decode(utf16_claim.encode("utf-8"), "text/html")
