"""/api/image-proxy?w=: the review list asks for 96 px thumbnails instead of the full store picture (ImageProxy::resize,
through the Laravel kernel with Http::fake and a fake DNS resolver; no real network):

* the picture is scaled down to w (aspect ratio kept) and served as WebP, with the same cache headers;
* a picture already that narrow is served as it is (never scaled up);
* w that is not a number is refused before anything is fetched; a number out of range is clamped to 32..400;
* the host and address checks apply exactly as without w.
"""

import io
from urllib.parse import quote

import pytest

from laqta_kernel import NEEDS_LARAVEL, stub_env
from test_dashboard_security import PNG, _png, candidate, dashboard  # noqa: F401 - candidate is a fixture

URL = "https://cdn.store.example/media/milk-1l.png"
WIDE = _png((800, 400))


def thumb(url, w):
    return ["/api/image-proxy?url=" + quote(url, safe="") + "&w=" + quote(str(w), safe=""), "GET"]


def size_of(body):
    from PIL import Image

    with Image.open(io.BytesIO(body)) as im:
        return im.format, im.size


@NEEDS_LARAVEL
def test_a_thumbnail_is_scaled_down_and_cached_like_the_picture(candidate, tmp_path):  # noqa: F811
    env, _calls = stub_env(tmp_path)
    web = {URL: {"body": WIDE, "headers": {"Content-Type": "image/png"}}}
    # one request per run: a faked answer's body is read once
    (res,), requested = dashboard(env, [thumb(URL, 96)], web=web)
    (full,), _requested = dashboard(env, [["/api/image-proxy?url=" + quote(URL, safe=""), "GET"]], web=web)
    assert (res["status"], res["type"]) == (200, "image/webp")
    assert size_of(res["body"]) == ("WEBP", (96, 48))
    assert res["cache"] == full["cache"] == "max-age=86400, private"
    assert len(res["body"]) < len(full["body"]) and full["body"] == WIDE
    assert requested == [URL]


@NEEDS_LARAVEL
def test_a_picture_already_that_narrow_is_served_as_it_is(candidate, tmp_path):  # noqa: F811
    env, _calls = stub_env(tmp_path)
    (res,), _requested = dashboard(env, [thumb(URL, 96)], web={URL: {"body": PNG}})
    assert (res["status"], res["type"], res["body"]) == (200, "image/png", PNG)          # 4 × 4: never scaled up


@NEEDS_LARAVEL
def test_a_bad_width_is_refused_and_an_out_of_range_one_is_clamped(candidate, tmp_path):  # noqa: F811
    env, _calls = stub_env(tmp_path)
    bad, requested = dashboard(env, [thumb(URL, w) for w in ("abc", "-5", "96px", "1e3", "999999")], web={URL: {"body": WIDE}})
    assert [r["status"] for r in bad] == [400] * 5 and requested == []
    assert all(r["body"] == b"Bad width" and r["cache"] == "no-store, private" for r in bad)
    big, small, empty = (dashboard(env, [thumb(URL, w)], web={URL: {"body": WIDE}})[0][0] for w in (5000, 1, ""))
    assert size_of(big["body"]) == ("WEBP", (400, 200))
    assert size_of(small["body"]) == ("WEBP", (32, 16))
    assert empty["body"] == WIDE                                                        # w= (empty): the picture


@NEEDS_LARAVEL
def test_the_host_and_address_checks_still_apply_with_a_width(candidate, tmp_path):  # noqa: F811
    env, _calls = stub_env(tmp_path)
    urls = ["https://elsewhere.example/a.png", "http://169.254.169.254/latest/meta-data/", "http://127.0.0.1:8000/api"]
    responses, requested = dashboard(env, [thumb(u, 96) for u in urls], web={u: {"body": WIDE} for u in urls})
    assert [r["status"] for r in responses] == [403] * 3 and requested == []
    (inside,), requested = dashboard(env, [thumb(URL, 96)], web={URL: {"body": WIDE}}, dns={"cdn.store.example": ["10.0.0.5"]})
    assert (inside["status"], inside["body"]) == (403, b"Image unavailable") and requested == []
    evil = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
    (hop,), requested = dashboard(env, [thumb(URL, 96)], web={URL: {"status": 302, "headers": {"Location": evil}},
                                                               evil: {"body": WIDE}})
    assert hop["status"] == 403 and requested == [URL]
    (page,), _requested = dashboard(env, [thumb(URL, 96)], web={URL: {"body": b"<html><body>hi</body></html>",
                                                                     "headers": {"Content-Type": "text/html"}}})
    assert page["status"] == 415                                                        # still raster images only


def test_the_resize_keeps_the_proxy_checks_in_front():
    from laqta_kernel import DASH

    body = (DASH / "app" / "Http" / "Controllers" / "ApiController.php").read_text(encoding="utf-8")
    body = body.split("public function imageProxy", 1)[1].split("\n    }\n", 1)[0]
    # the width is checked first, then the host, the fetch, the raster check, and only then the resize
    order = [body.index(s) for s in ("'Bad width'", "ImageProxy::allowedHost", "ImageProxy::fetch(",
                                     "'Upstream content is not a raster image'", "ImageProxy::resize(")]
    assert order == sorted(order)
    assert "withoutVerifying" not in body and "getMessage" not in body
