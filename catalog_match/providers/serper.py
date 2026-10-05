"""Serper.dev Google Images: the primary, sanctioned image source (decision D7).

POST https://google.serper.dev/images with the X-API-KEY header and the JSON body
{q, gl: 'ae', hl, num: 20}. Google operators such as site: pass through in q.
Every image keeps the page evidence Google returned with it:
    imageUrl -> image_url, link -> page_url, title -> title and page_title,
    domain (or the link host) -> domain, imageWidth/imageHeight -> width/height,
    position -> rank.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
from typing import Any, Callable, List, Optional

import requests

from .. import cassette, settings
from ..models import Candidate, SkuSpec
from .base import BaseProvider, ProviderHTTPError, note_hedge, page_domain, response_text, to_int

logger = logging.getLogger(__name__)

SERPER_IMAGES_URL = "https://google.serper.dev/images"

# Free Serper accounts answer 400 "Query pattern not allowed for free accounts" to site:/OR queries.
_SITE_CLAUSE = re.compile(r"\(?\s*site:\S+(?:\s+OR\s+site:\S+)*\s*\)?", re.I)


def has_site_operators(query: str) -> bool:
    return bool(re.search(r"(?i)\bsite:", query or ""))


def without_site_operators(query: str) -> str:
    """The query with its site:/OR clause replaced by a plain 'UAE' hint (still aimed at UAE retailer pages)."""
    plain = re.sub(r"\s+", " ", _SITE_CLAUSE.sub(" ", query or "")).strip()
    return plain if re.search(r"(?i)\buae\b", plain) else f"{plain} UAE".strip()


def _pattern_not_allowed(status: int, body: str) -> bool:
    return status == 400 and "not allowed" in (body or "").lower()


# ---------------------------------------------------------------------------
# Hedged request (SERPER_HEDGE_AFTER_S): the slow tail of Serper's answers (p90 6 s, p95 7 s, 7 timeouts in 176
# calls on the owner's runs) is cut by asking a second time instead of waiting for the first to time out.
# ---------------------------------------------------------------------------

def _close(resp: Any) -> None:
    try:
        resp.close()
    except Exception:
        pass


def hedged_http(provider: Any, url: str, payload: Any, send: Callable[[], Any]) -> Any:
    """send() (one Serper POST) through the cassette, hedged when it is slow.

    When the first request has not answered after SERPER_HEDGE_AFTER_S seconds, ONE duplicate is sent and the first
    answer with HTTP 200 is used; the other request is left to finish on its own thread and its answer is closed
    and ignored. A request that failed first (an exception, a non-200 answer) waits for the other one; when both
    fail, an HTTP answer is returned in preference to an exception so the caller's own status handling runs.
    The duplicate takes a token from the provider's shared bucket without waiting (none left: no duplicate) and is
    counted with base.note_hedge, so the call's record says it cost one more credit.

    Off (a plain cassette.http call) when SERPER_HEDGE_AFTER_S is 0, for a provider with hedge = False (visual
    search, whose answers are slower by nature and dearer) and whenever a cassette is installed: a recorded
    or replayed run keys every answer by its request, row and attempt, and a second request would add an answer
    that the recording never saw (a replayed call must not be counted twice).
    """
    after = settings.serper_hedge_after_s() if getattr(provider, "hedge", True) else 0.0
    if after <= 0 or cassette.active() is not None:
        return cassette.http(provider.name, "POST", url, send, body=payload)
    answers: "queue.Queue" = queue.Queue()
    settled = threading.Event()

    def run(n: int) -> None:
        try:
            item = (n, send(), None)
        except BaseException as exc:         # delivered to the waiting caller, never lost on this thread
            item = (n, None, exc)
        if settled.is_set() and item[2] is None:
            _close(item[1])                  # the answer that came too late is not used
        answers.put(item)

    def start(n: int) -> None:
        threading.Thread(target=run, args=(n,), name=f"{provider.name}-request-{n}", daemon=True).start()

    start(0)
    try:
        item = answers.get(timeout=after)
    except queue.Empty:
        item = None
    if item is not None:                     # answered in time (well or badly): no second request
        settled.set()
        if item[2] is not None:
            raise item[2]
        return item[1]
    pending = 1
    try:
        if provider.bucket().try_acquire():
            start(1)
            note_hedge()
            pending = 2
            logger.info("%s: no answer after %.1fs; sent the request a second time", provider.name, after)
    except Exception:                        # a broken bucket never stops the request already sent
        logger.exception("%s: hedge skipped", provider.name)
    failures = []
    while pending:
        n, resp, exc = answers.get()
        pending -= 1
        if exc is None and getattr(resp, "status_code", None) == 200:
            settled.set()
            return resp
        failures.append((resp, exc))
    settled.set()
    for resp, exc in failures:
        if exc is None:
            return resp
    raise failures[0][1]


class SerperImagesProvider(BaseProvider):
    name = "serper"
    sanctioned = True
    rate_per_min = 120.0
    burst = 5
    timeout = 10.0
    # Learned once per process: this account refuses site: operators, so send the plain form directly.
    operators_blocked = False

    def __init__(self, api_key: Optional[str] = None, session: Any = None, bucket: Any = None,
                 timeout: Optional[float] = None, num: int = 20, gl: str = "ae") -> None:
        super().__init__(session=session, bucket=bucket, timeout=timeout)
        self._api_key = api_key
        self.num = int(num)
        self.gl = gl

    def api_key(self) -> str:
        return (self._api_key if self._api_key is not None else settings.serper_api_key()).strip()

    def _status_for_http(self, status: int, body: str) -> str:
        if status == 429:
            return "quota"
        # Serper answers 400 {"message": "Not enough credits"} when the balance is spent.
        if status in (400, 402) and ("credit" in body.lower() or "quota" in body.lower()):
            return "quota"
        return "error"

    def _post(self, key: str, query: str, hl: str):
        payload = {"q": query, "gl": self.gl, "hl": hl or "en", "num": self.num}
        http = self._session or requests
        return hedged_http(self, SERPER_IMAGES_URL, payload, lambda: http.post(
            SERPER_IMAGES_URL,
            headers={"X-API-KEY": key, "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        ))

    def _search(self, query: str, hl: str, spec: SkuSpec) -> List[Candidate]:
        key = self.api_key()
        if not key:
            raise RuntimeError("no SERPER_API_KEY configured")
        if SerperImagesProvider.operators_blocked and has_site_operators(query):
            query = without_site_operators(query)
        resp = self._post(key, query, hl)
        if resp.status_code != 200 and has_site_operators(query) \
                and _pattern_not_allowed(resp.status_code, response_text(resp)):
            SerperImagesProvider.operators_blocked = True
            logger.warning("serper: this account does not allow site: operators (free plan); "
                           "retailer-scoped queries are sent without them from now on")
            query = without_site_operators(query)
            resp = self._post(key, query, hl)
        if resp.status_code != 200:
            raise ProviderHTTPError(resp.status_code, response_text(resp))
        data = resp.json()
        return parse_images(data)


def parse_images(data: Any) -> List[Candidate]:
    """Candidates from a Serper /images response body (dict)."""
    if not isinstance(data, dict):
        raise ValueError("Serper response is not a JSON object")
    images = data.get("images") or []
    out: List[Candidate] = []
    for i, item in enumerate(images, start=1):
        if not isinstance(item, dict):
            continue
        image_url = str(item.get("imageUrl") or "").strip()
        if not image_url.lower().startswith(("http://", "https://")):
            continue
        page_url = str(item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        out.append(Candidate(
            image_url=image_url,
            page_url=page_url,
            page_title=title,
            title=title,
            domain=page_domain(page_url, str(item.get("domain") or ""), image_url),
            width=to_int(item.get("imageWidth")),
            height=to_int(item.get("imageHeight")),
            rank=to_int(item.get("position")) or i,
            sanctioned=True,
        ))
    return out
