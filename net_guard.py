"""net_guard.py - the SSRF guard of every download of a URL that came from outside.

Search results, product pages, sitemaps and a reviewer's pick are untrusted: a URL among them can name this server
(127.0.0.1), the local network (10.x, 192.168.x), the cloud metadata service (169.254.169.254) or a public name
whose DNS answer is one of those. Nothing here may fetch such a URL.

assert_public_url(url) -> Target
    Raises BlockedURL (code 'blocked_url') when the URL may not be fetched:
      * a scheme other than http / https, no host, a control character anywhere, or an authority a second parser
        could read differently (user info, a backslash, a space);
      * a host whose DNS answer holds ANY address that is not public: loopback, RFC 1918, link-local
        (169.254.0.0/16, the metadata address included), CGNAT 100.64.0.0/10, multicast, unspecified, reserved and
        documentation ranges, IPv6 ULA fc00::/7 and link-local fe80::/10, and the IPv4-mapped, 6to4, Teredo and
        NAT64 forms of these.
    Raises HostLookupFailed (a ConnectionError, so callers keep calling it 'connection_error') when the name does
    not resolve. Answers are cached per host for DNS_TTL_S seconds: the parallel downloads of one row and the next
    rows' candidates on the same CDN cost one lookup.

follow(url, send, max_redirects) -> response
    send(hop_url, target) makes ONE request with redirects turned off. follow checks the URL before the first
    request and every Location before following it (at most max_redirects hops, then TooManyRedirects).

curl_resolve(target) -> curl_options for curl_cffi
    CURLOPT_RESOLVE pinned to the addresses that were checked, so curl does not look the name up again (a second
    answer, DNS rebinding, could be private). Empty for an IP literal or a non-ASCII name.

getaddrinfo(host) is the system resolver, looked up at call time (tests replace it; a replay's offline guard
patches socket.getaddrinfo and is honoured).
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple
from urllib.parse import urljoin, urlsplit

BLOCKED = "blocked_url"
MAX_REDIRECTS = 8
DNS_TTL_S = 60.0
DNS_CACHE_MAX = 4096
REDIRECT_STATUSES = (301, 302, 303, 307, 308)

# beyond what ipaddress calls private / reserved (CGNAT is neither; the rest is listed for older Pythons)
_BLOCKED_NETS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4",
    "::/128", "::1/128", "fc00::/7", "fe80::/10", "fec0::/10", "ff00::/8"))
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_BAD_AUTHORITY = re.compile(r"[\\@\s\x00-\x1f\x7f]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")        # urlsplit drops tabs and newlines silently; a client may not


class BlockedURL(ValueError):
    """The URL may not be fetched (not http(s), or its host is not on the public internet)."""

    code = BLOCKED


class HostLookupFailed(ConnectionError):
    """The host name does not resolve (the request could not have been made either)."""


class TooManyRedirects(ConnectionError):
    """More redirects than follow() allows."""


class Target(NamedTuple):
    url: str
    host: str
    port: int
    addresses: Tuple[str, ...]


def _short(url: Any) -> str:
    text = str(url or "")
    return text if len(text) <= 120 else text[:117] + "..."


# ---------------------------------------------------------------------------
# Addresses
# ---------------------------------------------------------------------------

def is_public_ip(value: Any) -> bool:
    """True only for an address on the public internet (see the module docstring for what is refused)."""
    try:
        ip = ipaddress.ip_address(str(value).split("%", 1)[0])      # an IPv6 zone id ('fe80::1%eth0') is dropped
    except ValueError:
        return False
    if ip.version == 6:
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip.teredo:
            embedded = ip.teredo[1]
        if embedded is None and any(ip in net for net in _NAT64):
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if embedded is not None:
            return is_public_ip(embedded)
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified
            or ip.is_reserved):
        return False
    if any(ip in net for net in _BLOCKED_NETS if net.version == ip.version):
        return False
    return bool(ip.is_global)


# ---------------------------------------------------------------------------
# DNS (cached)
# ---------------------------------------------------------------------------

_cache: Dict[str, Tuple[float, Tuple[str, ...]]] = {}
_cache_lock = threading.Lock()
_clock: Callable[[], float] = time.monotonic          # tests move it


def getaddrinfo(host: str) -> list:
    """The system resolver (looked up at call time: tests and the replay's offline guard replace it)."""
    return socket.getaddrinfo(host, None, 0, socket.SOCK_STREAM)


def reset_cache() -> None:
    with _cache_lock:
        _cache.clear()


def resolve(host: str) -> Tuple[str, ...]:
    """Every address the host resolves to (cached DNS_TTL_S seconds). Raises HostLookupFailed."""
    key = host.lower()
    now = _clock()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
    try:
        infos = getaddrinfo(host)
    except (OSError, UnicodeError, ValueError) as exc:
        raise HostLookupFailed(f"DNS lookup failed for {_short(host)} ({type(exc).__name__})") from None
    addresses = tuple(dict.fromkeys(str(info[4][0]) for info in infos or () if len(info) > 4 and info[4]))
    if not addresses:
        raise HostLookupFailed(f"DNS lookup gave no address for {_short(host)}")
    with _cache_lock:
        if len(_cache) >= DNS_CACHE_MAX:
            for stale in [k for k, (until, _a) in _cache.items() if until <= now] or list(_cache)[:DNS_CACHE_MAX // 4]:
                _cache.pop(stale, None)
        _cache[key] = (now + DNS_TTL_S, addresses)
    return addresses


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def assert_public_url(url: Any) -> Target:
    """The checked Target of an http(s) URL whose host resolves only to public addresses; else raises
    BlockedURL (or HostLookupFailed when the name does not resolve)."""
    text = str(url or "").strip()
    if _CONTROL.search(text):
        raise BlockedURL(f"control characters in the URL: {_short(text)!r}")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        raise BlockedURL(f"not a valid URL: {_short(text)}") from None
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise BlockedURL(f"only http and https can be fetched: {_short(text)}")
    if _BAD_AUTHORITY.search(parts.netloc or ""):
        raise BlockedURL(f"unusual characters in the host part: {_short(text)}")
    host = (parts.hostname or "").strip()
    if not host:
        raise BlockedURL(f"no host: {_short(text)}")
    addresses = resolve(host)
    bad = [a for a in addresses if not is_public_ip(a)]
    if bad:
        raise BlockedURL(f"{host} resolves to an address that is not public ({bad[0]}): {_short(text)}")
    return Target(text, host, port or (443 if scheme == "https" else 80), addresses)


def is_public_url(url: Any) -> bool:
    """assert_public_url as a yes / no (a name that does not resolve is a no)."""
    try:
        assert_public_url(url)
        return True
    except (BlockedURL, HostLookupFailed):
        return False


def _close(resp: Any) -> None:
    close = getattr(resp, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001 - best effort
            pass


def redirect_location(resp: Any) -> str:
    """The Location of a redirect answer, '' for any other answer."""
    try:
        status = int(getattr(resp, "status_code", 0) or 0)
    except (TypeError, ValueError):
        return ""
    if status not in REDIRECT_STATUSES:
        return ""
    headers = getattr(resp, "headers", None) or {}
    try:
        location = headers.get("Location") or headers.get("location") or ""
    except Exception:  # noqa: BLE001 - an odd headers object is no redirect
        return ""
    return str(location).strip()


def follow(url: str, send: Callable[[str, Target], Any], max_redirects: int = MAX_REDIRECTS) -> Any:
    """send(hop_url, target) for the URL and each redirect hop, every hop checked before it is requested.
    The answer of the last hop is returned (its .url is that hop's URL)."""
    current = str(url or "").strip()
    for _hop in range(max(0, int(max_redirects)) + 1):
        target = assert_public_url(current)
        resp = send(current, target)
        location = redirect_location(resp)
        if not location:
            return resp
        _close(resp)
        current = urljoin(current, location)
    raise TooManyRedirects(f"more than {max_redirects} redirects: {_short(url)}")


def curl_resolve(target: Optional[Target]) -> Dict[Any, Any]:
    """curl_options pinning curl to the checked addresses ({} when there is nothing to pin)."""
    if target is None or not target.addresses or not target.host.isascii():
        return {}
    try:
        ipaddress.ip_address(target.host)
        return {}                                           # an IP literal: curl does no lookup
    except ValueError:
        pass
    try:
        from curl_cffi import CurlOpt
    except Exception:  # pragma: no cover - curl_cffi missing: the caller uses requests, nothing to pin
        return {}
    addresses = ",".join(f"[{a}]" if ":" in a else a for a in target.addresses)
    return {CurlOpt.RESOLVE: [f"{target.host}:{target.port}:{addresses}"]}
