"""Record / replay of a live dry run (P7): smoke_live.py --record, then scripts/replay_run.py, offline.

A fake web (requests-like doubles for Serper, Open Food Facts, Gemini, image downloads and product pages) and
a fake local-index store answer a real recording run of scripts/smoke_live.py: the real pipeline, providers,
fetcher, page reader, local index, expansion round and label readers (primary and strong) do the work. The
replay then runs the same rows with every socket and the database refused and must decide exactly as the
recording did, also from a zip of the cassette; with a changed input it must still run and report every
answer it could not find, and --fill-misses must top the cassette up so that the next replay is complete.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import gzip
import importlib.util
import io
import json
import os
import shutil
import threading
import time
import types
from pathlib import Path
from unittest import mock

import numpy as np
import pytest
import requests
from PIL import Image

from catalog_match import cassette, fetch as fetch_mod, local_index, retrieve, settings as cm_settings, verify
from catalog_match.verifiers import spend as spend_mod

REPO = Path(__file__).resolve().parents[1]
SERPER_KEY = "fake-serper-key-7f3a9c1d2e44"
GEMINI_KEY = "fake-gemini-key-b81e4c77aa90"
SLOW_PAGE_S = 1.5
RECORD_DEADLINE_S = 0.5


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The fake web
# ---------------------------------------------------------------------------

def packshot(color, box):
    """An 800 px packshot: one coloured product on white (the colour tells the fake label reader which it is)."""
    arr = np.full((800, 800, 3), 255, dtype=np.uint8)
    x0, y0, x1, y1 = box
    arr[y0:y1, x0:x1] = color
    arr[y0 + 40:y0 + 70, x0 + 20:x1 - 20] = (250, 250, 250)        # a label band
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def reading(brand, variant, size, view="front_packshot", brand_match="yes", variant_match="yes", size_match="yes"):
    return {"brand_text": brand, "variant_text": variant, "size_text": size, "pack_count": 1, "view": view,
            "brand_match": brand_match, "variant_match": variant_match, "size_match": size_match}


def product_page(name, brand, image_url):
    ld = json.dumps({"@context": "https://schema.org", "@type": "Product", "name": name, "brand": {"name": brand},
                     "image": image_url})
    return (f"<html><head><title>{name}</title><script type=\"application/ld+json\">{ld}</script></head>"
            f"<body><h1>{name}</h1><p>{'Fresh from the store. ' * 20}</p></body></html>")


class FakeResponse:
    def __init__(self, status=200, body=b"", ctype="application/json", url=""):
        self.status_code = status
        self.content = body
        self.headers = {"Content-Type": ctype, "Content-Length": str(len(body))}
        self.url = url

    @property
    def text(self):
        return self.content.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)

    def iter_content(self, chunk_size=65536):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i:i + chunk_size]

    def close(self):
        pass


class FakeWeb:
    """Serper (images, web, shopping), Open Food Facts, Gemini, image CDNs and store pages."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.images, self.pages, self.slow = {}, {}, {}
        self.serper, self.web = {}, {}
        self.readings = []          # (rgb, fields, strong fields or None)
        self.index_rows = {}        # brand word -> [CatalogRow]
        self.saved_pages = []

    # -- building the world -----------------------------------------------------
    def image(self, url, color, box, fields, strong=None):
        self.images[url] = packshot(color, box)
        self.readings.append((np.array(color, dtype=float), fields, strong))

    # -- requests-like entry points ---------------------------------------------
    def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        body = json or {}
        with self.lock:
            self.calls.append(("POST", url, body.get("q") or body.get("url") or ""))
        if url.startswith("https://google.serper.dev/"):
            assert (headers or {}).get("X-API-KEY") == SERPER_KEY
            q, endpoint = str(body.get("q") or "").casefold(), url.rsplit("/", 1)[-1]
            table = {"images": self.serper, "search": self.web}.get(endpoint, {})
            items = next((v for k, v in table.items() if k in q), [])
            key = {"images": "images", "search": "organic", "shopping": "shopping"}.get(endpoint, "organic")
            return FakeResponse(200, _dumps({key: items}))
        if url.startswith("https://generativelanguage.googleapis.com/"):
            assert (headers or {}).get("x-goog-api-key") == GEMINI_KEY
            return FakeResponse(200, _dumps(self.gemini(url, body)))
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url, params=None, headers=None, timeout=None, **kwargs):
        with self.lock:
            self.calls.append(("GET", url, ""))
        if "openfoodfacts.org" in url:
            return FakeResponse(404, b'{"status": 0, "status_verbose": "product not found"}')
        if url in self.slow:
            time.sleep(self.slow[url])
        if url in self.images:
            return FakeResponse(200, self.images[url], "image/jpeg", url)
        if url in self.pages:
            return FakeResponse(200, self.pages[url].encode("utf-8"), "text/html; charset=utf-8", url)
        return FakeResponse(404, b"<html>not found</html>", "text/html", url)

    def gemini(self, url, body):
        strong = "gemini-3.5-flash" in url
        parts = body["contents"][0]["parts"]
        out = []
        for label, part in enumerate([p for p in parts if "inlineData" in p], start=1):
            img = np.asarray(Image.open(io.BytesIO(base64.b64decode(part["inlineData"]["data"]))).convert("RGB"))
            pixels = img[img.min(axis=2) < 200].astype(float)
            mean = pixels.mean(axis=0) if len(pixels) else np.zeros(3)
            color, fields, strong_fields = min(self.readings, key=lambda r: float(np.abs(r[0] - mean).sum()))
            out.append(dict((strong_fields if strong and strong_fields else fields), image_index=label))
        text = _dumps({"images": out, "best_index": 1}).decode("utf-8")
        return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 900 + 300 * len(out), "candidatesTokenCount": 80 * len(out)}}

    # -- the local index store (DbCatalogStore stand-in) ----------------------------
    def find(self, required, extra, limit=200):
        return [r for word, rows in self.index_rows.items() if word in required for r in rows]


def _dumps(data):
    return json.dumps(data).encode("utf-8")


def build_world():
    w = FakeWeb()
    # row 2: Almarai milk, a retailer packshot read as MATCH (and two that are not)
    w.image("https://cdn.carrefouruae.com/a1.jpg", (200, 30, 30), (250, 150, 550, 700),
            reading("ALMARAI", "Full Fat Fresh Milk", "1 L"))
    w.image("https://cdn.luluhypermarket.com/a2.jpg", (30, 160, 30), (200, 200, 600, 650),
            reading("ALMARAI", "", "", view="lifestyle", variant_match="unsure", size_match="unsure"))
    w.serper["almarai"] = [
        {"title": "Almarai Full Fat Fresh Milk 1L", "imageUrl": "https://cdn.carrefouruae.com/a1.jpg",
         "link": "https://www.carrefouruae.com/mafuae/en/milk/almarai-full-fat-fresh-milk-1l/p/111",
         "domain": "carrefouruae.com", "imageWidth": 800, "imageHeight": 800, "position": 1},
        {"title": "Almarai Full Fat Milk 1L", "imageUrl": "https://cdn.luluhypermarket.com/a2.jpg",
         "link": "https://www.luluhypermarket.com/en-ae/almarai-full-fat-milk-1l/p/112",
         "domain": "luluhypermarket.com", "imageWidth": 800, "imageHeight": 800, "position": 2},
    ]
    # its store page (never read live: the pick needs no page) shows a larger rendition: a shadow recording reads it
    w.pages["https://www.carrefouruae.com/mafuae/en/milk/almarai-full-fat-fresh-milk-1l/p/111"] = product_page(
        "Almarai Full Fat Fresh Milk 1L", "Almarai", "https://cdn.carrefouruae.com/a1-large.jpg")
    w.image("https://cdn.carrefouruae.com/a1-large.jpg", (90, 90, 210), (240, 140, 560, 710),
            reading("ALMARAI", "Full Fat Fresh Milk", "1 L"))
    # row 3: Nadec laban, the only image is another brand's pack; X1 finds the store page with the right one
    w.image("https://images.example-shop.ae/n1.jpg", (220, 200, 20), (250, 180, 560, 680),
            reading("AL RAWABI", "Laban", "2 L", brand_match="no"))
    w.serper["nadec"] = [
        {"title": "Nadec Laban Full Fat 2L", "imageUrl": "https://images.example-shop.ae/n1.jpg",
         "link": "https://www.example-shop.ae/nadec-laban-full-fat-2l", "domain": "example-shop.ae",
         "imageWidth": 800, "imageHeight": 800, "position": 1},
    ]
    page = "https://www.carrefouruae.com/mafuae/en/laban/nadec-laban-full-fat-2l/p/222"
    w.web["nadec"] = [{"title": "Nadec Laban Full Fat 2L | Carrefour UAE", "link": page,
                       "snippet": "Nadec Laban Full Fat 2 L", "position": 1}]
    w.pages[page] = product_page("Nadec Laban Full Fat 2L", "Nadec", "https://cdn.carrefouruae.com/n2.jpg")
    w.image("https://cdn.carrefouruae.com/n2.jpg", (130, 40, 160), (260, 120, 540, 720),
            reading("NADEC", "Laban Full Fat", "2 L"))
    # row 4: Puck cream cheese, a web image read UNSURE (the strong reader looks again), and two index pages:
    # one answers at once, the other only after the recording's read deadline
    w.image("https://f.nooncdn.com/p/p1.jpg", (30, 170, 170), (220, 220, 580, 620),
            reading("PUCK", "", "", view="other_side", variant_match="unsure", size_match="unsure"),
            strong=reading("PUCK", "", "", view="other_side", variant_match="unsure", size_match="unsure"))
    w.serper["puck"] = [
        {"title": "Puck Cream Cheese 500g", "imageUrl": "https://f.nooncdn.com/p/p1.jpg",
         "link": "https://www.noon.com/uae-en/puck-cream-cheese-500g/N111/p/", "domain": "noon.com",
         "imageWidth": 800, "imageHeight": 800, "position": 1},
    ]
    fast = "https://www.luluhypermarket.com/en-ae/puck-cream-cheese-500g/p/333"
    slow = "https://www.spinneys.com/en/puck-cream-cheese-500g-jar/p/444"
    w.pages[fast] = product_page("Puck Cream Cheese 500g", "Puck", "https://cdn.luluhypermarket.com/p2.jpg")
    w.pages[slow] = product_page("Puck Cream Cheese 500g Jar", "Puck", "https://cdn.spinneys.com/p3.jpg")
    w.slow[slow] = SLOW_PAGE_S
    w.image("https://cdn.luluhypermarket.com/p2.jpg", (240, 140, 20), (240, 200, 560, 640),
            reading("PUCK", "Cream Cheese", "500 g"))
    w.image("https://cdn.spinneys.com/p3.jpg", (230, 80, 160), (230, 160, 570, 690),
            reading("PUCK", "Cream Cheese", "500 g"))
    w.index_rows["puck"] = [
        local_index.CatalogRow(id=1, store="lulu", url=fast, slug_text="puck cream cheese 500g"),
        local_index.CatalogRow(id=2, store="spinneys", url=slow, slug_text="puck cream cheese 500g jar"),
    ]
    # row 5: KDD nectar, the primary reader cannot read the size; the strong reader's second look can
    w.image("https://cdn.carrefouruae.com/k1.jpg", (60, 60, 60), (300, 150, 500, 700),
            reading("KDD", "Mango Nectar", "", size_match="unsure"), strong=reading("KDD", "Mango Nectar", "250 ml"))
    w.serper["kdd"] = [
        {"title": "KDD Mango Nectar 250ml", "imageUrl": "https://cdn.carrefouruae.com/k1.jpg",
         "link": "https://www.carrefouruae.com/mafuae/en/juice/kdd-mango-nectar-250ml/p/555",
         "domain": "carrefouruae.com", "imageWidth": 800, "imageHeight": 800, "position": 1},
    ]
    return w


ROWS_CSV = ("row,name,brand,barcode\n"
            "2,ALMARAI FULL FAT MILK 1L,ALMARAI,6281007012348\n"
            "3,NADEC LABAN FULL FAT 2L,NADEC,\n"
            "4,PUCK CREAM CHEESE 500G,PUCK,\n"
            "5,KDD MANGO NECTAR 250ML,KDD,\n")


def live_values(tmp_path):
    return {
        "SERPER_API_KEY": SERPER_KEY, "GEMINI_API_KEY": GEMINI_KEY, "SERPAPI_API_KEY": "", "ANTHROPIC_API_KEY": "",
        "GEMINI_MODEL": "gemini-3.1-flash-lite", "VERIFIER_PRIMARY": "gemini:gemini-3.1-flash-lite",
        "VERIFIER_STRONG": "gemini:gemini-3.5-flash", "VERIFIER_MONTHLY_BUDGET_USD": "5",
        "VERIFIER_STRONG_MAX_CALLS": "1", "VISUAL_SEARCH": "off", "EXPANSION_ENABLED": True, "EXPANSION_MAX_CALLS": 4,
        "ENABLE_BING_HTML_FALLBACK": False, "GOOGLE_SEARCH_API_KEYS": [], "GOOGLE_SEARCH_CX_LIST": [],
        "PROXY_URL": "", "AUTO_PUBLISH_ENABLED": False, "AUTO_PUBLISH_BRANDS": [], "LOCAL_INDEX_ENABLED": True,
        "LOCAL_INDEX_MAX_PAGES": 3, "LOCAL_INDEX_PAGE_TTL_DAYS": 30, "MODEL_PRICES": "", "GTIN_POLICY": "evidence",
        "CANDIDATE_STORE_DIR": str(tmp_path / "live_candidates"),
    }


@contextlib.contextmanager
def live(world, tmp_path):
    """The owner's machine: keys, the fake web, a database-backed index and spend store (doubles)."""
    values = live_values(tmp_path)
    env = {k: (",".join(v) if isinstance(v, list) else str(v).lower() if isinstance(v, bool) else str(v))
           for k, v in values.items()}
    env.update(GOOGLE_SEARCH_API_KEY="", GOOGLE_SEARCH_CX="")
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, env))
        cfg = getattr(cm_settings, "_config", None)
        if cfg is not None:
            for k, v in dict(values, GOOGLE_SEARCH_API_KEY="", GOOGLE_SEARCH_CX="").items():
                stack.enter_context(mock.patch.object(cfg, k, v, create=True))
        stack.enter_context(mock.patch.object(requests, "post", world.post))
        stack.enter_context(mock.patch.object(requests, "get", world.get))
        stack.enter_context(mock.patch.object(fetch_mod, "_curl_requests", types.SimpleNamespace(get=world.get)))
        stack.enter_context(mock.patch.object(local_index.DbCatalogStore, "count", lambda self, fresh=False: 2))
        stack.enter_context(mock.patch.object(local_index.DbCatalogStore, "find", world.find))
        stack.enter_context(mock.patch.object(local_index.DbCatalogStore, "by_gtin", lambda self, gtin, limit=20: []))
        stack.enter_context(mock.patch.object(local_index.DbCatalogStore, "save_page",
                                              lambda self, row_id, rec: world.saved_pages.append((row_id, rec))))
        stack.enter_context(mock.patch.object(spend_mod.MariaDbSpendStore, "role_spend",
                                              lambda self, role="strong", month=None: 0.25))
        stack.enter_context(mock.patch.object(spend_mod.MariaDbSpendStore, "add", lambda self, entry, month=None: True))
        stack.enter_context(mock.patch.object(local_index, "READ_DEADLINE_S", RECORD_DEADLINE_S))
        _fresh_process()
        stack.callback(_fresh_process)
        stack.enter_context(cassette.offline())
        yield


def _fresh_process():
    from catalog_match import pages
    from catalog_match.providers.serper import SerperImagesProvider
    from catalog_match.verifiers import claude

    pages.clear_cache()
    local_index.reset_blocked_hosts()
    verify.BREAKER.reset()
    claude.CLAUDE_BREAKER.reset()
    SerperImagesProvider.operators_blocked = False


def _wait_background_reads():
    time.sleep(SLOW_PAGE_S + 0.3)        # the index read left running after the deadline finishes


def view(r):
    """What must not move between the recording and its replay."""
    return {
        "row": r["row"], "decision": r["decision"], "failure_code": r["failure_code"], "winner": r["winner"],
        "winner_provider": r["winner_provider"], "warnings": r["warnings"], "queries": r["queries"],
        "top": [(c["rank"], c["image_url"], c["status"], c["provider"], c["reasons"], c["vlm"]) for c in r["top"]],
        # sorted: the dry run logs parallel calls as they finish (wall clock); the pipeline merges them in order
        "provider_calls": sorted((c["provider"], c["query"], c["status"], c["count"] or 0, c.get("round") or "")
                                 for c in r["provider_calls"]),
        "vlm_calls": r["vlm_calls"], "strong_calls": r["strong_calls"], "verdicts": r["verdicts"],
        "reject_counts": r["reject_counts"], "cost_usd": r["cost_usd"],
    }


@pytest.fixture
def recorded(tmp_path, capsys):
    """One recording run of the three rows: (world, cassette folder, the run's --json document)."""
    world = build_world()
    rows = tmp_path / "rows.csv"
    rows.write_text(ROWS_CSV, encoding="utf-8")
    out, folder = tmp_path / "after.json", tmp_path / "cassette_test"
    smoke = _load("smoke_live_record_under_test", "smoke_live.py")
    with live(world, tmp_path):
        code = smoke.main(["--rows-file", str(rows), "--dry-run", "--json", str(out), "--record", str(folder)])
        _wait_background_reads()
    assert code == 0
    printed = capsys.readouterr().out
    assert f"cassette {folder}" in printed and "replay_run.py" in printed
    return {"world": world, "folder": folder, "doc": json.loads(out.read_text(encoding="utf-8")),
            "rows": rows, "smoke": smoke, "printed": printed}


def replay(folder, out, *extra):
    replay_mod = _load("replay_run_under_test", "replay_run.py")
    code = replay_mod.main([str(folder), "--json", str(out), *extra])
    return code, json.loads(Path(out).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_the_recording_run_decides_as_the_fake_web_intends(recorded):
    rows = {r["row"]: r for r in recorded["doc"]["rows"]}
    assert not [r for r in rows.values() if "error" in r], rows
    assert rows[2]["decision"] == "REVIEW_PRESELECTED" and rows[2]["winner"] == "https://cdn.carrefouruae.com/a1.jpg"
    # the expansion round's web search found the store page of the right Nadec pack
    assert rows[3]["winner"] == "https://cdn.carrefouruae.com/n2.jpg" and rows[3]["expansion"]["ran"]
    # the index page that answered in time gives the pick; the late one gives nothing (as live)
    assert rows[4]["winner"] == "https://cdn.luluhypermarket.com/p2.jpg"
    assert all("cdn.spinneys.com" not in c["image_url"] for c in rows[4]["top"])
    # the strong reader's second look read the size the primary reader could not
    assert rows[5]["strong_calls"] == 1 and rows[5]["winner"] == "https://cdn.carrefouruae.com/k1.jpg"
    meta = json.loads((recorded["folder"] / "meta.json").read_text(encoding="utf-8"))
    assert meta["format"] == cassette.FORMAT and [r["row_number"] for r in meta["rows"]] == [2, 3, 4, 5]
    assert meta["settings"]["secrets"]["SERPER_API_KEY"] is True
    assert "SERPER_API_KEY" not in meta["settings"]["values"]
    assert meta["versions"]["pillow"] and meta["run"]["expansion"] is True
    assert {f.name for f in recorded["folder"].iterdir()} >= {"meta.json", "http.jsonl", "verifier.jsonl",
                                                               "local_index.jsonl", "blobs"}


def test_the_replay_decides_exactly_as_the_recording_offline(recorded, tmp_path):
    calls_before = len(recorded["world"].calls)
    code, doc = replay(recorded["folder"], tmp_path / "replayed.json")
    assert code == 0
    assert len(recorded["world"].calls) == calls_before, "the replay reached the fake web"
    assert [view(r) for r in doc["rows"]] == [view(r) for r in recorded["doc"]["rows"]]
    for r in doc["rows"]:
        assert r["replay"]["complete"] and not r["replay"]["approximate"], r["replay"]
        assert r["replay"]["misses"] == [] and r["replay"]["answers"] > 0
    assert doc["replay"]["outbound_attempts"] == 0 and doc["replay"]["complete"] == 4
    assert doc["format"] == "smoke_live/2" and doc["summary"]["rows"] == 4
    assert not (tmp_path / "replayed.misses.json").exists()


def test_the_replay_works_from_a_zip_of_the_cassette(recorded, tmp_path):
    archive = shutil.make_archive(str(tmp_path / "cassette_zipped"), "zip", root_dir=recorded["folder"].parent,
                                  base_dir=recorded["folder"].name)
    code, doc = replay(archive, tmp_path / "from_zip.json")
    assert code == 0
    assert [view(r) for r in doc["rows"]] == [view(r) for r in recorded["doc"]["rows"]]
    assert all(r["replay"]["complete"] for r in doc["rows"])


def test_no_key_header_or_token_is_stored(recorded):
    found = []
    for path in recorded["folder"].rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        if path.suffix == ".gz":
            data = gzip.decompress(data)
        for secret in (SERPER_KEY, GEMINI_KEY, "X-API-KEY", "x-goog-api-key"):
            if secret.encode("utf-8") in data:
                found.append((path.name, secret))
    assert found == []


def test_a_changed_query_is_reported_as_a_miss_and_fill_misses_tops_the_cassette_up(recorded, tmp_path, capsys):
    real = retrieve.build_queries

    def reworded(spec, custom=None):
        return [dataclasses.replace(q, text=q.text + " original") if q.query_id == "Q1" else q
                for q in real(spec, custom)]

    with mock.patch.object(retrieve, "build_queries", reworded):
        code, doc = replay(recorded["folder"], tmp_path / "changed.json")
        strict, _doc = replay(recorded["folder"], tmp_path / "changed_strict.json", "--strict")
    assert code == 0 and strict == 1
    for r in doc["rows"]:
        assert not r["replay"]["complete"]
        missed = [m for m in r["replay"]["misses"] if m["kind"] == "serper"]
        assert missed and missed[0]["request"]["body"]["q"].endswith(" original"), r["replay"]
        q1 = next(c for c in r["provider_calls"] if c["provider"] == "serper")
        assert q1["status"] == "error" and "not_recorded" in q1["error"]
    manifest_path = tmp_path / "changed.misses.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["format"] == cassette.MISSES_FORMAT and manifest["rows"] == [2, 3, 4, 5]
    printed = capsys.readouterr().out
    assert "INCOMPLETE" in printed and "answers not in the cassette" in printed

    # the owner's machine, same code: only the missing answers are asked for, and added to the cassette
    world = recorded["world"]
    before = len(world.calls)
    with live(world, tmp_path), mock.patch.object(retrieve, "build_queries", reworded):
        fill = recorded["smoke"].main(["--dry-run", "--record", str(recorded["folder"]),
                                       "--fill-misses", str(manifest_path)])
        _wait_background_reads()
    assert fill == 0
    asked = world.calls[before:]
    assert asked and all(c[1].startswith("https://google.serper.dev/") for c in asked), asked
    assert all(c[2].endswith(" original") for c in asked if c[1].endswith("/images")), asked
    meta = json.loads((recorded["folder"] / "meta.json").read_text(encoding="utf-8"))
    assert meta["fills"] and meta["fills"][-1]["answers_added"] == len(asked)

    with mock.patch.object(retrieve, "build_queries", reworded):
        code, again = replay(recorded["folder"], tmp_path / "filled.json")
    assert code == 0 and all(r["replay"]["complete"] for r in again["rows"]), [r["replay"] for r in again["rows"]]


def test_a_changed_prompt_reuses_the_recorded_readings_and_says_so(recorded, tmp_path):
    real = verify.build_prompt

    def prompt(spec, n_images, focus=False):
        return real(spec, n_images, focus) + "\nRead slowly."

    with mock.patch.object(verify, "build_prompt", prompt):
        code, doc = replay(recorded["folder"], tmp_path / "prompt.json")
    assert code == 0
    assert [view(r)["decision"] for r in doc["rows"]] == [view(r)["decision"] for r in recorded["doc"]["rows"]]
    assert [r["winner"] for r in doc["rows"]] == [r["winner"] for r in recorded["doc"]["rows"]]
    for r in doc["rows"]:
        assert r["replay"]["complete"] and r["replay"]["approximate"], r["replay"]
        assert any("another recorded call" in a for a in r["replay"]["approximations"])


def test_shadow_recording_leaves_the_live_decisions_alone_and_stores_more(recorded, tmp_path, capsys):
    folder = tmp_path / "cassette_shadow"
    out = tmp_path / "after_shadow.json"
    world = build_world()
    with live(world, tmp_path):
        code = recorded["smoke"].main(["--rows-file", str(recorded["rows"]), "--dry-run", "--json", str(out),
                                       "--record", str(folder), "--record-shadow"])
        _wait_background_reads()
    assert code == 0
    printed = capsys.readouterr().out
    assert "shadow recording:" in printed
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert [view(r) for r in doc["rows"]] == [view(r) for r in recorded["doc"]["rows"]]
    lines = [json.loads(x) for x in (folder / "http.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(x.get("shadow") for x in lines)
    shadow_reads = [json.loads(x) for x in (folder / "verifier.jsonl").read_text(encoding="utf-8").splitlines()
                    if json.loads(x).get("shadow")]
    assert shadow_reads, "the unread images got a reading"
    assert all(isinstance(r.get("shadow"), dict) for r in doc["rows"])
    code, replayed = replay(folder, tmp_path / "shadow_replayed.json")
    assert [view(r) for r in replayed["rows"]] == [view(r) for r in doc["rows"]]
    assert all(r["replay"]["complete"] for r in replayed["rows"])


def test_record_refuses_a_folder_that_already_holds_a_cassette(recorded):
    with pytest.raises(SystemExit):
        recorded["smoke"].main(["--rows-file", str(recorded["rows"]), "--dry-run", "--record",
                                str(recorded["folder"])])
    with pytest.raises(SystemExit):
        recorded["smoke"].main(["--rows-file", str(recorded["rows"]), "--dry-run", "--record-shadow"])
