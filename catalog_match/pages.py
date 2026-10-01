"""Product-page extraction for the expansion round (sources package, P3).

PageFetcher().fetch_page(url, referer='') -> PageInfo
    One GET of a retailer product page with catalog_match.fetch's downloader conventions:
    curl_cffi with a Chrome TLS fingerprint when installed (else `requests`), the first
    attempt always direct, at most one more attempt through PROXY_URL (after a timeout,
    a 5xx or a connection error, or a 403/429 when a proxy is configured), 10 s per
    attempt and a 4 MB body limit. Each page host draws from its own token bucket
    (catalog_match.ratelimit, 20 pages a minute) and every URL is fetched at most once
    per run: results, failures included, stay in a short in-process cache. Redirects are
    followed; the PageInfo is filed under the page that was finally sent, and when that is
    another page (not just https / www / a trailing slash / a query), redirected_from keeps
    the URL that was asked for and page_candidates() drops the listing title and snippet.

extract(html, page_url) -> PageInfo
    Parses the HTML with the standard library only (html.parser and json); no script is
    ever executed. It reads, in this order of trust:
      * JSON-LD Product nodes (also inside @graph or a list): name, brand, image (a URL,
        a list, ImageObject {url|contentUrl, width, height}), gtin / gtin8 / gtin12 /
        gtin13 / gtin14 and description;
      * __NEXT_DATA__-style embedded page JSON (noon): the shallowest product-like
        object, i.e. one with a title and its own images (noon 'image_keys' become
        f.nooncdn.com URLs); recommendation carousels deeper in the tree are ignored;
      * og:image (with og:image:width / og:image:height), twitter:image, itemprop=image
        and <link rel="image_src">;
      * the page's main <img> when nothing above names an image (Amazon's landingImage:
        the largest data-a-dynamic-image size, data-old-hires);
      * og:title, twitter:title and <title> for the product name when there is no
        structured name.

page_candidates(info, title=..., snippet=..., query_id=..., rank=...) -> [Candidate]
    The product's main image (MAX_IMAGES_PER_PAGE = 1: never a back or side view; renditions of
    one image collapsed with providers.base.canonical_image_url, the largest known
    rendition kept), with
        provider 'page', sanctioned False (a scraped page never auto-publishes),
        page_url the page, domain its host (so source trust is the page's own trust),
        title the listing title that led to the page (search, shopping or lens result;
            the page's own name when the URL redirected to another page),
        page_title the product name the page states (the structured brand is written in
            front when the name leaves it out, so the brand and competitor rules see it),
        snippet the description (brand presence only, never size evidence),
        gtin_on_page only when the page states exactly ONE valid, globally unique GTIN.
    Thumbnails (Google's encrypted-tbn images, data: URIs, images known to be under
    MIN_IMAGE_SIDE px) are never returned.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import unquote, urljoin, urlsplit

from . import ratelimit, settings
from .fetch import HttpFetcher, _blocked, _retryable
from .gtin import is_global_gtin, normalize_gtin
from .models import Candidate
from .providers.base import canonical_image_url, to_int
from .retrieve import norm_image_url
from .text_norm import domain_matches, phrase_in, url_host

logger = logging.getLogger(__name__)

PAGE_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.5"
PAGE_TIMEOUT_S = 10.0
MAX_PAGE_BYTES = 4 * 1024 * 1024
MIN_PAGE_BYTES = 200
PAGES_PER_MIN = 20.0          # per page host
PAGE_BURST = 3
CACHE_TTL_S = 30 * 60.0
CACHE_FAIL_TTL_S = 5 * 60.0
CACHE_MAX = 512
MAX_IMAGES_PER_PAGE = 1           # the main product image only: a back-of-pack shot must never be pre-checked
MIN_IMAGE_SIDE = 300           # a page image known to be smaller is a thumbnail
MAX_JSON_NODES = 20000
MAX_JSON_DEPTH = 14
PROVIDER = "page"

NOON_IMAGE_ROOT = "https://f.nooncdn.com/p/"
_THUMB_HOSTS = ("gstatic.com",)
_THUMB_PATH_RE = re.compile(r"(?:^|[/_.-])(?:thumb|thumbs|thumbnail|thumbnails|icon|icons|sprite|placeholder|logo)"
                            r"(?:[/_.-]|$)", re.I)
_IMAGE_EXT_RE = re.compile(r"\.(?:jpe?g|png|webp|gif|avif)(?:$|[?#])", re.I)
_SIZE_HINT_RE = re.compile(r"(?:^|[?&])(?:w|width|h|height|sw|sh)=(\d{1,4})(?:&|$)", re.I)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

_NAME_KEYS = ("product_title", "productTitle", "productName", "product_name", "name", "title")
_BRAND_KEYS = ("brand", "brand_name", "brandName", "brand_en", "manufacturer")
_IMAGE_LIST_KEYS = ("image_keys", "imageKeys", "images", "image_urls", "imageUrls", "gallery", "media")
_IMAGE_ONE_KEYS = ("image", "image_url", "imageUrl", "main_image", "mainImage", "image_key", "imageKey")
_GTIN_KEYS = ("gtin", "gtin8", "gtin12", "gtin13", "gtin14", "ean", "ean13", "barcode", "upc")
_JSONLD_GTIN_KEYS = ("gtin", "gtin8", "gtin12", "gtin13", "gtin14")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class PageImage:
    url: str
    width: Optional[int] = None
    height: Optional[int] = None
    source: str = ""          # 'jsonld' | 'next_data' | 'og' | 'twitter' | 'link'


@dataclass
class PageInfo:
    url: str
    ok: bool = False
    error: Optional[str] = None
    name: str = ""            # the product name the page states (structured name, else og:title / <title>)
    brand: str = ""
    description: str = ""
    html_title: str = ""
    images: List[PageImage] = field(default_factory=list)
    gtins: List[str] = field(default_factory=list)   # valid GTIN-14s the page states for its product
    sources: List[str] = field(default_factory=list)  # which structures were found
    redirected_from: str = ""  # the URL that was asked for when the server answered with another page

    @property
    def domain(self) -> str:
        return url_host(self.url)


# ---------------------------------------------------------------------------
# HTML parsing (no script is ever run)
# ---------------------------------------------------------------------------

_MAIN_IMG_IDS = ("landingImage", "imgBlkFront", "main-image", "mainImage", "product-image", "productImage")


class _HeadParser(HTMLParser):
    """Collects meta tags, <title>, <link rel=image_src> and the JSON script bodies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: List[Tuple[str, str]] = []        # (property or name, content), in document order
        self.title_parts: List[str] = []
        self.link_images: List[str] = []
        self.main_imgs: List[Dict[str, str]] = []    # attributes of the page's main <img> (Amazon: landingImage)
        self.jsonld: List[str] = []
        self.next_data: List[str] = []
        self._in_title = False
        self._script: Optional[str] = None           # 'jsonld' | 'next' while inside such a script
        self._buf: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        a = {k.lower(): (v or "") for k, v in attrs if k}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").strip().lower()
            if key and "content" in a:
                self.meta.append((key, a["content"].strip()))
        elif tag == "title":
            self._in_title = True
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            if ("image_src" in rel or a.get("itemprop", "").strip().lower() == "image") and a.get("href"):
                self.link_images.append(a["href"].strip())
        elif tag == "img":
            if a.get("id", "").strip() in _MAIN_IMG_IDS or a.get("itemprop", "").strip().lower() == "image":
                self.main_imgs.append(a)
        elif tag == "script":
            stype = a.get("type", "").strip().lower()
            sid = a.get("id", "").strip()
            if stype == "application/ld+json":
                self._script, self._buf = "jsonld", []
            elif sid == "__NEXT_DATA__" or (stype == "application/json" and sid in ("__NUXT_DATA__", "__APP_DATA__")):
                self._script, self._buf = "next", []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._script is not None:
            text = "".join(self._buf)
            (self.jsonld if self._script == "jsonld" else self.next_data).append(text)
            self._script, self._buf = None, []

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._buf.append(data)
        elif self._in_title:
            self.title_parts.append(data)


def _loads(text: str) -> Any:
    """Lenient JSON: HTML comment / CDATA wrappers and trailing commas are tolerated."""
    raw = (text or "").strip()
    if not raw:
        return None
    for wrapper in (("<!--", "-->"), ("/*<![CDATA[*/", "/*]]>*/"), ("<![CDATA[", "]]>")):
        if raw.startswith(wrapper[0]) and raw.endswith(wrapper[1]):
            raw = raw[len(wrapper[0]):-len(wrapper[1])].strip()
    for attempt in (raw, _TRAILING_COMMA_RE.sub(r"\1", raw)):
        try:
            return json.loads(attempt, strict=False)
        except ValueError:
            continue
    return None


def _clean(text: Any, limit: int = 300) -> str:
    if text is None or isinstance(text, (dict, list)):
        return ""
    value = html_mod.unescape(str(text))
    value = re.sub(r"<[^>]{0,200}>", " ", value)          # stray markup inside a description
    return " ".join(value.split())[:limit]


def _is_type(node: Dict[str, Any], name: str) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.split("/")[-1].lower() == name.lower() for x in types)


def _jsonld_nodes(data: Any) -> Iterable[Dict[str, Any]]:
    """Every dict of a JSON-LD document (lists, @graph and nested values walked, bounded)."""
    stack: List[Tuple[Any, int]] = [(data, 0)]
    seen = 0
    while stack and seen < MAX_JSON_NODES:
        node, depth = stack.pop(0)
        seen += 1
        if depth > MAX_JSON_DEPTH:
            continue
        if isinstance(node, list):
            stack.extend((x, depth + 1) for x in node)
        elif isinstance(node, dict):
            yield node
            for key, value in node.items():
                if isinstance(value, (dict, list)) and key not in ("isRelatedTo", "isSimilarTo", "itemListElement"):
                    stack.append((value, depth + 1))


def _brand_name(value: Any) -> str:
    if isinstance(value, dict):
        return _clean(value.get("name") or value.get("brand_name") or value.get("title"), 80)
    if isinstance(value, list):
        for item in value:
            name = _brand_name(item)
            if name:
                return name
        return ""
    return _clean(value, 80)


def _image_entries(value: Any, page_url: str, source: str, noon: bool = False) -> List[PageImage]:
    out: List[PageImage] = []

    def one(item: Any) -> None:
        if isinstance(item, str):
            url = item.strip()
            is_link = url.lower().startswith(("http://", "https://", "//", "/"))
            if noon and url and not is_link:
                url = NOON_IMAGE_ROOT + url.lstrip("/") + ("" if _IMAGE_EXT_RE.search(url) else ".jpg")
            elif not is_link and not _IMAGE_EXT_RE.search(url):
                return                      # an image key or id this page's CDN scheme is unknown for
            url = urljoin(page_url, url) if url else ""
            if url.lower().startswith(("http://", "https://")):
                out.append(PageImage(url=url, source=source))
        elif isinstance(item, dict):
            url = item.get("url") or item.get("contentUrl") or item.get("src") or item.get("image") \
                or item.get("large") or item.get("original")
            if isinstance(url, str):
                before = len(out)
                one(url)
                if len(out) > before:
                    out[-1].width = to_int(item.get("width"))
                    out[-1].height = to_int(item.get("height"))

    if isinstance(value, list):
        for item in value[:20]:
            one(item)
    else:
        one(value)
    return out


def _gtins_of(node: Dict[str, Any], keys: Iterable[str]) -> List[str]:
    found: List[str] = []
    for key in keys:
        value = node.get(key)
        values = value if isinstance(value, list) else [value]
        for v in values:
            if isinstance(v, bool) or v is None or isinstance(v, (dict, list)):
                continue
            text = str(int(v)) if isinstance(v, int) else str(v).strip()
            gtin14, status = normalize_gtin(text)
            if status == "ok" and gtin14 and gtin14 not in found:
                found.append(gtin14)
    return found


def _main_product(products: List[Dict[str, Any]], hint: str) -> Optional[Dict[str, Any]]:
    """The page's own product: the one named like og:title / <title>, else the first one."""
    if not products:
        return None
    if hint and len(products) > 1:
        for node in products:
            name = _clean(node.get("name"), 200)
            if name and (phrase_in(name, hint) or phrase_in(hint, name)):
                return node
    return products[0]


def _from_jsonld(info: PageInfo, scripts: List[str], page_url: str, hint: str) -> None:
    products: List[Dict[str, Any]] = []
    for text in scripts:
        data = _loads(text)
        if data is None:
            continue
        products.extend(n for n in _jsonld_nodes(data) if _is_type(n, "Product"))
    node = _main_product(products, hint)
    if node is None:
        return
    info.sources.append("jsonld")
    info.name = info.name or _clean(node.get("name"), 200)
    info.brand = info.brand or _brand_name(node.get("brand"))
    info.description = info.description or _clean(node.get("description"), 300)
    info.images.extend(_image_entries(node.get("image"), page_url, "jsonld"))
    for gtin in _gtins_of(node, _JSONLD_GTIN_KEYS):
        if gtin not in info.gtins:
            info.gtins.append(gtin)


_PRODUCT_SIGNAL_KEYS = _BRAND_KEYS + _GTIN_KEYS + (
    "sku", "price", "offers", "product_title", "productTitle", "productName", "product_name", "image_keys")


def _product_like(node: Dict[str, Any]) -> bool:
    """A product object: a name, its own images and a product signal (brand, price, sku, GTIN, ...).

    The signal keeps a page header or SEO block ({title, image: logo}) from passing as the product.
    """
    has_name = any(isinstance(node.get(k), str) and node.get(k).strip() for k in _NAME_KEYS)
    has_images = any(isinstance(node.get(k), list) and node.get(k) for k in _IMAGE_LIST_KEYS) or \
        any(isinstance(node.get(k), (str, dict)) and node.get(k) for k in _IMAGE_ONE_KEYS)
    has_signal = any(node.get(k) not in (None, "", [], {}) for k in _PRODUCT_SIGNAL_KEYS)
    return has_name and has_images and has_signal


def _gtins_within(node: Any) -> List[str]:
    """Valid GTINs anywhere inside one product object (its specifications, its offers)."""
    found: List[str] = []
    stack: List[Tuple[Any, int]] = [(node, 0)]
    seen = 0
    while stack and seen < 2000:
        cur, depth = stack.pop()
        seen += 1
        if depth > 6:
            continue
        if isinstance(cur, list):
            stack.extend((x, depth + 1) for x in cur)
        elif isinstance(cur, dict):
            for gtin in _gtins_of(cur, _GTIN_KEYS):
                if gtin not in found:
                    found.append(gtin)
            # specification rows: {"code": "gtin", "value": "6291..."}
            code = str(cur.get("code") or cur.get("key") or cur.get("name") or "").strip().lower()
            if code in _GTIN_KEYS:
                for gtin in _gtins_of({"v": cur.get("value")}, ("v",)):
                    if gtin not in found:
                        found.append(gtin)
            stack.extend((v, depth + 1) for k, v in cur.items() if isinstance(v, (dict, list))
                         and k not in ("recommendations", "related", "similar", "similarProducts", "relatedProducts"))
    return found


def _from_next_data(info: PageInfo, scripts: List[str], page_url: str) -> None:
    noon = domain_matches(url_host(page_url), ("noon.com",))
    for text in scripts:
        data = _loads(text)
        if data is None:
            continue
        queue = deque([(data, 0)])
        seen = 0
        node = None
        while queue and seen < MAX_JSON_NODES:
            cur, depth = queue.popleft()
            seen += 1
            if depth > MAX_JSON_DEPTH:
                continue
            if isinstance(cur, dict):
                if _product_like(cur):
                    node = cur
                    break
                queue.extend((v, depth + 1) for v in cur.values() if isinstance(v, (dict, list)))
            elif isinstance(cur, list):
                queue.extend((v, depth + 1) for v in cur[:200] if isinstance(v, (dict, list)))
        if node is None:
            continue
        info.sources.append("next_data")
        if not info.name:
            info.name = next((_clean(node.get(k), 200) for k in _NAME_KEYS if isinstance(node.get(k), str)
                              and node.get(k).strip()), "")
        if not info.brand:
            info.brand = next((_brand_name(node.get(k)) for k in _BRAND_KEYS if node.get(k)), "")
        for key in _IMAGE_LIST_KEYS + _IMAGE_ONE_KEYS:
            if node.get(key):
                info.images.extend(_image_entries(node.get(key), page_url, "next_data", noon=noon))
        for gtin in _gtins_within(node):
            if gtin not in info.gtins:
                info.gtins.append(gtin)
        return


def _meta_images(parser: _HeadParser, page_url: str) -> List[PageImage]:
    out: List[PageImage] = []
    last_og: Optional[PageImage] = None
    for key, content in parser.meta:
        if key in ("og:image", "og:image:url", "og:image:secure_url"):
            url = urljoin(page_url, content)
            if url.lower().startswith(("http://", "https://")):
                if last_og is not None and norm_image_url(last_og.url) == norm_image_url(url):
                    continue          # og:image followed by its own secure_url
                last_og = PageImage(url=url, source="og")
                out.append(last_og)
        elif key == "og:image:width" and last_og is not None:
            last_og.width = to_int(content)
        elif key == "og:image:height" and last_og is not None:
            last_og.height = to_int(content)
        elif key in ("twitter:image", "twitter:image:src", "image"):
            url = urljoin(page_url, content)
            if url.lower().startswith(("http://", "https://")):
                out.append(PageImage(url=url, source="twitter"))
    for href in parser.link_images:
        url = urljoin(page_url, href)
        if url.lower().startswith(("http://", "https://")):
            out.append(PageImage(url=url, source="link"))
    return out


def _attr_images(parser: _HeadParser, page_url: str) -> List[PageImage]:
    """The main <img> of pages without structured data (Amazon's landingImage: the largest dynamic size first)."""
    out: List[PageImage] = []
    for attrs in parser.main_imgs:
        dynamic = _loads(attrs.get("data-a-dynamic-image", ""))
        if isinstance(dynamic, dict):
            sized = []
            for url, dims in dynamic.items():
                if isinstance(url, str) and isinstance(dims, list) and len(dims) == 2:
                    sized.append((to_int(dims[0]) or 0, to_int(dims[1]) or 0, url))
            for w, h, url in sorted(sized, key=lambda t: -(t[0] * t[1]))[:1]:
                out.extend(PageImage(url=i.url, width=w or None, height=h or None, source="img")
                           for i in _image_entries(url, page_url, "img"))
        for key in ("data-old-hires", "data-zoom-image", "data-large", "src", "data-src"):
            if attrs.get(key):
                out.extend(PageImage(url=i.url, source="img") for i in _image_entries(attrs[key], page_url, "img"))
    return out


def extract(html_text: str, page_url: str) -> PageInfo:
    """PageInfo of one product page (see the module docstring). Never raises."""
    info = PageInfo(url=page_url, ok=True)
    parser = _HeadParser()
    try:
        parser.feed(html_text or "")
        parser.close()
    except Exception as exc:  # html.parser is lenient; stay defensive anyway
        logger.debug("pages: HTML parse error on %s: %s", page_url, exc)
    meta = {}
    for key, content in parser.meta:
        meta.setdefault(key, content)
    info.html_title = _clean("".join(parser.title_parts), 200)
    hint = _clean(meta.get("og:title") or meta.get("twitter:title") or info.html_title, 200)
    try:
        _from_jsonld(info, parser.jsonld, page_url, hint)
    except Exception:
        logger.debug("pages: JSON-LD unreadable on %s", page_url, exc_info=True)
    try:
        _from_next_data(info, parser.next_data, page_url)
    except Exception:
        logger.debug("pages: embedded page JSON unreadable on %s", page_url, exc_info=True)
    meta_images = _meta_images(parser, page_url)
    if meta_images:
        info.sources.append("meta")
    info.images.extend(meta_images)
    attr_images = _attr_images(parser, page_url)
    if attr_images:
        info.sources.append("img")
    info.images.extend(attr_images)
    if not info.name:
        info.name = hint
    if not info.description:
        info.description = _clean(meta.get("og:description") or meta.get("description"), 300)
    return info


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------

def _known_small(width: Optional[int], height: Optional[int]) -> bool:
    sides = [s for s in (width, height) if s]
    return bool(sides) and min(sides) < MIN_IMAGE_SIDE


def is_thumbnail(url: str, width: Optional[int] = None, height: Optional[int] = None) -> bool:
    """True for an image that is only a preview: Google's tbn images, data: URIs, tiny or icon-like files."""
    text = (url or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return True
    host = url_host(text)
    if domain_matches(host, _THUMB_HOSTS) or host.startswith("encrypted-tbn"):
        return True
    if _known_small(width, height):
        return True
    path = text.split("?", 1)[0]
    if _THUMB_PATH_RE.search(path.rsplit("/", 1)[-1]) or "/thumbnail" in path.lower() or "/thumbs/" in path.lower():
        return True
    hints = [int(m) for m in _SIZE_HINT_RE.findall(text.split("?", 1)[1] if "?" in text else "")]
    return bool(hints) and max(hints) < MIN_IMAGE_SIDE


def _ordered_images(info: PageInfo) -> List[PageImage]:
    """Distinct images in source order, each at its largest known rendition."""
    best: Dict[str, PageImage] = {}
    order: List[str] = []
    for img in info.images:
        url = canonical_image_url(img.url)
        key = norm_image_url(url)
        if not key:
            continue
        cand = PageImage(url=url, width=img.width, height=img.height, source=img.source)
        if key not in best:
            best[key] = cand
            order.append(key)
            continue
        cur = best[key]
        if (cand.width or 0) * (cand.height or 0) > (cur.width or 0) * (cur.height or 0):
            best[key] = PageImage(url=cur.url, width=cand.width, height=cand.height, source=cur.source)
    return [best[k] for k in order if not is_thumbnail(best[k].url, best[k].width, best[k].height)]


def page_gtin(info: PageInfo) -> Optional[str]:
    """The GTIN the page states for its product: exactly one valid, globally unique GTIN, else None."""
    usable = [g for g in info.gtins if is_global_gtin(g)]
    return usable[0] if len(set(usable)) == 1 else None


def page_title_of(info: PageInfo) -> str:
    name = info.name or info.html_title
    if info.brand and name and not phrase_in(info.brand, name):
        return f"{info.brand} {name}"
    return name


def page_candidates(info: Optional[PageInfo], *, title: str = "", snippet: str = "", query_id: str = "",
                    rank: int = 0, max_images: int = MAX_IMAGES_PER_PAGE) -> List[Candidate]:
    """Image candidates of one extracted page (see the module docstring)."""
    if info is None or not info.ok:
        return []
    images = _ordered_images(info)[:max(0, int(max_images))]
    page_title = page_title_of(info)
    gtin = page_gtin(info)
    if info.redirected_from:
        # The listing title and snippet described the URL that was asked for, not the page the
        # server sent instead (a sold-out product sent to its category, the home page or a
        # sibling size): only the page's own statements are evidence for its image.
        title, snippet = "", ""
    out = []
    for i, img in enumerate(images):
        out.append(Candidate(
            image_url=img.url,
            page_url=info.url,
            page_title=page_title,
            title=" ".join((title or "").split()) or page_title,
            snippet=" ".join((snippet or info.description or "").split())[:300],
            domain=info.domain,
            width=img.width,
            height=img.height,
            provider=PROVIDER,
            query_id=query_id,
            rank=max(0, int(rank or 0)) * 10 + i,
            gtin_on_page=gtin,
            sanctioned=False,
        ))
    return out


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

_cache: Dict[str, Tuple[float, PageInfo]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cache_get(url: str, now: float) -> Optional[PageInfo]:
    with _cache_lock:
        hit = _cache.get(url)
        if hit is None:
            return None
        stamp, info = hit
        ttl = CACHE_TTL_S if info.ok else CACHE_FAIL_TTL_S
        if now - stamp > ttl:
            _cache.pop(url, None)
            return None
        return info


def _cache_put(url: str, info: PageInfo, now: float) -> None:
    with _cache_lock:
        if len(_cache) >= CACHE_MAX:
            for key in sorted(_cache, key=lambda k: _cache[k][0])[:CACHE_MAX // 4]:
                _cache.pop(key, None)
        _cache[url] = (now, info)


def _cache_key(url: str) -> str:
    return url.split("#", 1)[0].strip()


def same_page(asked: str, final: str) -> bool:
    """True when a redirect kept the page: same host (no 'www.') and path; scheme, query and a
    trailing slash may change (http -> https, '?o=...' dropped)."""
    def key(url: str) -> Tuple[str, str]:
        try:
            parts = urlsplit(url)
        except ValueError:
            return url, ""
        return url_host(url), unquote(parts.path or "").rstrip("/").lower()

    return key(asked) == key(final)


class PageFetcher(HttpFetcher):
    """Fetches and extracts product pages; HttpFetcher's client, proxy fallback and streaming are reused."""

    def __init__(self, timeout: float = PAGE_TIMEOUT_S, max_bytes: int = MAX_PAGE_BYTES, session: Any = None,
                 bucket_factory=None, clock=time.monotonic) -> None:
        super().__init__(timeout=timeout, min_bytes=MIN_PAGE_BYTES, max_bytes=max_bytes, session=session)
        self._bucket_factory = bucket_factory or (lambda host: ratelimit.get_bucket(f"page:{host}", PAGES_PER_MIN,
                                                                                    PAGE_BURST))
        self._clock = clock
        self._local = threading.local()      # the final URL of this thread's last response (after redirects)

    def _get(self, url: str, headers: dict, proxy: Optional[str] = None):
        resp = super()._get(url, headers, proxy)
        final = getattr(resp, "url", None)
        self._local.final_url = final.strip() if isinstance(final, str) and final.strip() else url
        return resp

    def _page_headers(self, referer: str = "") -> dict:
        headers = {"Accept": PAGE_ACCEPT, "User-Agent": _user_agent(),
                   "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"}
        if referer:
            headers["Referer"] = referer
        return headers

    def fetch_page(self, url: str, referer: str = "") -> PageInfo:
        """PageInfo for one URL (cached per run; never raises)."""
        url = (url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return PageInfo(url=url, ok=False, error="bad_url")
        key = _cache_key(url)
        cached = _cache_get(key, self._clock())
        if cached is not None:
            return cached
        info = self._fetch_uncached(url, referer)
        _cache_put(key, info, self._clock())
        return info

    def _fetch_uncached(self, url: str, referer: str) -> PageInfo:
        host = url_host(url)
        try:
            self._bucket_factory(host).acquire()
        except Exception:  # pragma: no cover - a broken bucket must not stop the fetch
            logger.debug("pages: rate limiter failed for %s", host, exc_info=True)
        headers = self._page_headers(referer)
        proxy = settings.proxy_url() or None
        self._local.final_url = url
        body, error, ctype = self._download(url, headers)
        if body is None and (_retryable(error) or (proxy and _blocked(error))):
            body, error, ctype = self._download(url, headers, proxy)
        if body is None:
            logger.info("pages: %s not fetched (%s)", host, error)
            return PageInfo(url=url, ok=False, error=error or "error")
        if len(body) < self.min_bytes:
            return PageInfo(url=url, ok=False, error="too_small")
        if ctype and not any(t in ctype for t in ("html", "xml", "text/plain")):
            return PageInfo(url=url, ok=False, error="not_html")
        final = getattr(self._local, "final_url", "") or url
        if not final.lower().startswith(("http://", "https://")):
            final = url
        text = _decode(body, ctype)
        # The page the server actually sent is the evidence (its URL, its host's trust, its slug).
        info = extract(text, final)
        if not same_page(url, final):
            info.redirected_from = url
            logger.info("pages: %s redirected to %s", host, url_host(final))
        logger.info("pages: %s read (%s; %d images, gtin=%s)", host, ",".join(info.sources) or "no structure",
                    len(info.images), "yes" if info.gtins else "no")
        return info


def _user_agent() -> str:
    from .fetch import USER_AGENT
    return USER_AGENT


_CHARSET_RE = re.compile(r"charset=([\w.-]+)", re.I)
# <meta charset="windows-1256"> / <meta http-equiv="Content-Type" content="text/html; charset=...">
_META_CHARSET_RE = re.compile(rb"<meta[^>]{0,200}?charset\s*=\s*[\"']?\s*([\w.:-]+)", re.I)
_BOMS = ((b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be"))
_SNIFF_BYTES = 4096


def _decode(body: bytes, ctype: str) -> str:
    """The page text: a byte-order mark, else the Content-Type charset, else the page's own
    <meta charset> (older Arabic pages declare windows-1256 only there), else UTF-8."""
    for bom, enc in _BOMS:
        if body.startswith(bom):
            return body[len(bom):].decode(enc, errors="replace")
    candidates = []
    m = _CHARSET_RE.search(ctype or "")
    if m:
        candidates.append(m.group(1))
    meta = _META_CHARSET_RE.search(body[:_SNIFF_BYTES])
    if meta:
        declared = meta.group(1).decode("ascii", errors="ignore")
        # a readable <meta> means an ASCII-compatible encoding: a declared UTF-16 is UTF-8 (HTML spec)
        candidates.append("utf-8" if declared.lower().startswith("utf-16") else declared)
    for enc in candidates + ["utf-8"]:
        try:
            return body.decode(enc, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")
