"""Live dry run of the v2 image search on real sheet rows. Never writes to the sheet.

Usage (on the machine with the keys, from the repository root):

    python scripts/smoke_live.py --probe                              # which paid services work with your keys
    python scripts/smoke_live.py --rows 2-61 --dry-run --json run3.json
    python scripts/smoke_live.py --rows 5 --rows 40-45 --dry-run --json smoke.json
    python scripts/compare_runs.py run2.json run3.json                # what changed between two dry runs

Without the Google Sheet (a machine with the keys but no credentials.json, e.g. a cloud session):

    python scripts/smoke_live.py --rows-file runs/2026-10-03/rows_2_61.csv \
        --brands-file runs/2026-10-03/brands_mapping_suggested.csv --dry-run --json runs/after.json

--rows-file reads the products from a CSV (a header row with any of row, name, name_ar, brand,
brand_ar, barcode, category, size; the sheet's header synonyms work too) or from an earlier
--json file (its rows' name and brand). --rows still picks rows from it. --brands-file reads a
Brands Mapping CSV in the tab's layout (Brand, Synonyms, Excluded Competitors, Sub-brands,
Official domains); without it a file run has no brand mappings (every brand is sheet_raw). Every
run prints how many Brands Mapping entries it uses and how many of its rows have a mapped brand, and
warns when none has.

--probe makes one cheap, read-only call per configured service (Serper images, web search,
shopping and lens; SerpApi; the primary and the strong label-reading model; Anthropic;
PhotoRoom; Cloudinary) and prints a table of what works and, in plain words, why not.
A service without a key is "not configured", one the settings switch off is "off"; neither
is an error. It never prints a key or any part of one, and writes nothing anywhere.

For every row the dry run prints the planned queries, the health of each provider call
(the expansion round's paid calls too), the top-5 candidates with their identity evidence,
the VLM verdicts, the final decision, the pick's review warnings, and the estimated cost.
It reads labels with the worker's own verifier (the models chosen in Settings) and runs the
expansion round when this version has one (--no-expansion measures without it).

The run ends with a summary, also written under "summary" in the --json file: rows, outage
rows excluded (no connection to the search or label-reading service), pre-selected count and
coverage %, why the other rows have no pick, which provider found each pick, the expansion
rounds, strong-model calls, the estimated cost in total and per measured product (outage rows
are in the total, not in the average), and the review warnings on the picks. scripts/compare_runs.py compares two such files (also the older
files that hold only the list of rows).

Use it on ~30 rows to check the live search end to end (evaluation layer 4).
This replaces scripts/verify_image_search.py, which counted "any image returned" as success.

Record once, replay for free (catalog_match/cassette.py):

    .venv\\Scripts\\python.exe scripts\\smoke_live.py --rows-file runs\\2026-10-03\\rows_2_61.csv ^
        --brands-file runs\\2026-10-03\\brands_mapping_suggested.csv --dry-run --json runs\\after.json ^
        --record runs\\cassette_2026-10 --record-shadow
    python scripts/replay_run.py runs/cassette_2026-10 --json runs/replayed.json      # offline, any code version
    python scripts/compare_runs.py runs/after.json runs/replayed.json

--record DIR stores every answer the run gets from outside (search responses, image downloads, product
pages, label-reader replies, the local-index rows and the wall-clock decisions) in the folder DIR, without
any key, header or token; the run itself is unchanged. The folder holds 100-250 MB for 60 rows (about
twice that with --record-shadow) and is ignored by version control (runs/ and cassette_*/): zip it to send it. --record-shadow also stores, after
each row's decision is made, answers a later code version may ask for (every pooled image up to 24, the
pages of the tier-1/2 listings, the retailer web search and the shopping search for a row without a pick,
one label reading of every downloaded image); it costs a little more, printed at the end. A replay that
missed answers writes a manifest; --record DIR --fill-misses MANIFEST then runs only those rows again,
answering from the cassette where it can and paying only for the missing answers, which it adds to DIR.
The keys, the CSE engine ids and the proxy password are never written to the folder, wherever an answer
echoes them. A write the folder refuses (a full disk, a file an antivirus holds) never changes the run: the
row prints a CASSETTE line saying what was not stored, and the --json results are written in any case.
"""

import argparse
import contextlib
import datetime as dt
import functools
import inspect
import json
import logging
import os
import re
import sys
import time
import traceback
from collections import Counter
from urllib.parse import quote, urlsplit

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

log = logging.getLogger("smoke_live")

# Serper is about $1 per 1k queries (D7); a comparative Gemini call about $0.001 (D6: ~$0.002 per SKU).
DEFAULT_SERP_COST = 0.001
DEFAULT_VLM_COST = 0.001
DEFAULT_SERPAPI_COST = 0.015     # one SerpApi Google Lens search (Developer plan); SERPAPI_LENS_PRICE_USD wins
DEFAULT_CSE_COST = 0.005         # a Google CSE query beyond the free 100 a day

FREE_PROVIDERS = frozenset({"off", "open_food_facts", "openfoodfacts", "bing_html", "local_index"})
SERPER_PROVIDERS = frozenset({"serper", "serper_web", "serper_shopping", "lens_serper"})
EXPANSION_PROVIDERS = frozenset({"serper_web", "serper_shopping", "lens_serper", "lens_serpapi"})
EXPANSION_SOURCES = EXPANSION_PROVIDERS | {"page"}      # 'page': an image read from a fetched product page
ANSWERED_STATUSES = ("ok", "empty")                     # calls the service answered (the billed ones)
PICK_DECISIONS = ("AUTO_PUBLISH", "REVIEW_PRESELECTED")
JSON_FORMAT = "smoke_live/2"
_EXPANSION_QUERY_RE = re.compile(r"^X(\d+|U)$")
# Transport failures (no HTTP answer): the machine had no connection, not a refusal by the service.
_CONNECTIVITY_RE = re.compile(
    r"timeout|timed out|connection|connect|name resolution|getaddrinfo|max retries|unreachable|"
    r"remote end closed|temporar(y|ily)|proxyerror|sslerror|ssl:", re.IGNORECASE)
# A lost connection to the label reader (its error codes: 'connection_error:...', 'error:SSLError', ...);
# a bare 'timeout' is the model being slow, not the connection.
_LINK_DOWN_RE = re.compile(r"connection|connect|name resolution|getaddrinfo|unreachable|refused|ssl|proxy",
                           re.IGNORECASE)

# Why a measured row has no pick (UNSELECTED_REASONS, with the words the summary prints) and how a row is read for it
# live in catalog_match.explain, shared with the worker (outcome.explain) and the review screen.
from catalog_match.explain import UNSELECTED_REASONS, brand_facts, unselected_reason  # noqa: E402,F401
from catalog_match.explain import host as _host  # noqa: E402

# (the CSE engine ids are no key, but they identify the account and are hidden with the keys)
SECRET_SETTINGS = ("SERPER_API_KEY", "GEMINI_API_KEY", "SERPAPI_API_KEY", "ANTHROPIC_API_KEY", "PHOTOROOM_API_KEY",
                   "CLOUDINARY_API_KEY", "CLOUDINARY_API_SECRET", "GOOGLE_SEARCH_API_KEYS", "GOOGLE_SEARCH_API_KEY",
                   "GOOGLE_SEARCH_CX_LIST", "GOOGLE_SEARCH_CX", "REMOVE_BG_API_KEY", "TELEGRAM_BOT_TOKEN", "PROXY_URL")
_QUERY_KEY_RE = re.compile(r"(?i)((?:(?:api_?)?key|(?<![a-z0-9])cx)(?:=|%3D))[^&\s'\"]+")

WRITE_METHODS = ("update", "update_cell", "update_cells", "batch_update", "append_row", "append_rows", "insert_row",
                 "insert_rows", "delete_rows", "clear", "add_worksheet", "del_worksheet", "format", "update_acell")


# ---------------------------------------------------------------------------
# Settings, looked up defensively (a package that adds a setting may not be merged yet)
# ---------------------------------------------------------------------------

def _empty(value):
    return value is None or value == "" or value == [] or value == ()


def setting(name, default=""):
    """A setting by its shared-contract name: catalog_match.settings, else config, else the environment."""
    value = None
    try:
        from catalog_match import settings as cm_settings
        value = cm_settings.get(name)
    except Exception:
        value = None
    if _empty(value):
        try:
            import config
            value = getattr(config, name, None)
        except Exception:
            value = None
    if _empty(value):
        value = os.getenv(name)
    return default if _empty(value) else value


def accessor(name, fallback=None):
    """catalog_match.settings.<name>() when this version has it, else fallback() (or None)."""
    try:
        from catalog_match import settings as cm_settings
        fn = getattr(cm_settings, name, None)
        if callable(fn):
            return fn()
    except Exception:
        pass
    return fallback() if callable(fallback) else None


def truthy(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def secret_values(lookup=None):
    """Every configured secret (and proxy credentials), longest first, for redaction."""
    lookup = lookup or setting
    values = set()
    for name in SECRET_SETTINGS:
        try:
            raw = lookup(name)
        except Exception:
            raw = ""
        items = raw if isinstance(raw, (list, tuple, set)) else str(raw or "").split(",")
        for item in items:
            item = str(item).strip()
            if name == "PROXY_URL" and item:
                parts = urlsplit(item)
                values.update(v for v in (parts.username, parts.password) if v and len(v) >= 4)
            if len(item) >= 6:
                values.update((item, quote(item, safe="")))      # as is, and as a URL query carries it
    return sorted(values, key=len, reverse=True)


def redact(text, secrets=None, query_keys=True):
    """text with every secret value replaced, and (query_keys) every 'key=...' / 'api_key=...' / 'cx=...' query
    value.

    Error texts get both; a whole --json document only the secret values, so that image URLs with a
    harmless 'key=' parameter stay comparable between runs.
    """
    text = "" if text is None else str(text)
    for secret in (secret_values() if secrets is None else secrets):
        text = text.replace(secret, "[hidden]")
    return _QUERY_KEY_RE.sub(r"\1[hidden]", text) if query_keys else text


def _utf8_stdout():
    """Arabic product names on a Windows console (cp1252 / cp1256) must not crash the report."""
    stream = sys.stdout
    encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
    if encoding != "utf8":
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Read-only sheet access
# ---------------------------------------------------------------------------

class ReadOnly:
    """Wraps a gspread object and refuses every write method."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        if name in WRITE_METHODS:
            raise PermissionError(f"read-only run: refused {name}() on the Google Sheet")
        value = getattr(self._inner, name)
        return ReadOnly(value) if name in ("spreadsheet", "worksheet") and not callable(value) else value

    def worksheet(self, title):
        return ReadOnly(self._inner.worksheet(title))


def parse_rows(spec):
    """'2-31' or '7' (repeatable) -> sorted sheet row numbers (row 1 is the header)."""
    rows = set()
    for part in spec or []:
        for chunk in str(part).split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                lo, hi = (int(x) for x in chunk.split("-", 1))
                rows.update(range(min(lo, hi), max(lo, hi) + 1))
            else:
                rows.add(int(chunk))
    return sorted(r for r in rows if r >= 2)


def open_sheet_read_only():
    """(spreadsheet, product worksheet) wrapped so that nothing can be written."""
    import config
    import google_sheets

    client = google_sheets.get_sheets_client()
    if not client:
        raise SystemExit("No Google Sheets client: check CREDENTIALS_FILE in .env")
    target = config.SPREADSHEET_NAME_OR_URL
    sh = client.open_by_url(target) if str(target).startswith("https://") else client.open(target)
    tab = getattr(config, "SPREADSHEET_TAB_NAME", "")
    ws = sh.worksheet(tab) if tab else sh.get_worksheet(0)
    return ReadOnly(sh), ReadOnly(ws)


def read_sheet_rows(worksheet, row_numbers):
    """Rows as dicts (row_number, name, name_ar, brand, brand_ar, barcode, category, size), read-only."""
    import google_sheets

    values = worksheet.get_all_values()
    if not values:
        return []
    headers = values[0]
    # Same header synonyms as the worker (google_sheets.get_products), so the dry run reads what production reads.
    cols = google_sheets.resolve_columns(headers)
    if cols["name"] < 0 and cols["name_ar"] < 0:
        raise SystemExit(f"No product name column in the sheet headers: {headers}")

    def cell(row, key):
        idx = cols[key]
        return row[idx].strip() if 0 <= idx < len(row) else ""

    out = []
    for number in row_numbers:
        if number - 1 >= len(values):
            break
        row = values[number - 1]
        record = {"row_number": number}
        record.update({key: cell(row, key) for key in ("name", "name_ar", "brand", "brand_ar", "barcode",
                                                       "category", "size")})
        if record["name"] or record["name_ar"]:
            record["name"] = record["name"] or record["name_ar"]
            out.append(record)
    return out


def _row_number(text):
    """A sheet row number written as '45' or as a spreadsheet export writes it ('45.0'); None otherwise."""
    try:
        value = float(str(text).strip())
    except ValueError:
        return None
    return int(value) if value.is_integer() and value >= 1 else None


def read_rows_file(path, row_numbers=None):
    """Rows (the read_sheet_rows shape) from a CSV with a header row, or from an earlier --json run."""
    import csv

    keys = ("name", "name_ar", "brand", "brand_ar", "barcode", "category", "size")
    if str(path).lower().endswith(".json"):
        doc = load_run(path)
        source = doc.get("rows", []) if isinstance(doc, dict) else doc
        rows = [{"row_number": int(r["row"]), **{k: str(r.get(k) or "").strip() for k in keys}}
                for r in source if isinstance(r, dict) and r.get("row") and (r.get("name") or r.get("name_ar"))]
    else:
        import google_sheets

        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            table = [row for row in csv.reader(fh)]
        if not table:
            return []
        cols = google_sheets.resolve_columns(table[0])
        heads = [google_sheets.normalize_header(h) for h in table[0]]
        for key in keys:            # the plain field names too ('name', 'brand', 'name_ar')
            plain = google_sheets.normalize_header(key)
            if cols.get(key, -1) < 0 and plain in heads:
                cols[key] = heads.index(plain)
        if cols.get("name", -1) < 0 and cols.get("name_ar", -1) < 0:
            raise SystemExit(f"No product name column in the file's headers: {table[0]}")
        # '#' normalises to nothing, so the raw header is checked too
        row_names = {google_sheets.normalize_header(h) for h in ("row", "row number", "row_number")}
        row_col = next((i for i, (raw, head) in enumerate(zip(table[0], heads))
                        if raw.strip() == "#" or (head and head in row_names)), -1)
        rows = []
        for i, line in enumerate(table[1:], start=2):
            def cell(key):
                idx = cols.get(key, -1)
                return line[idx].strip() if 0 <= idx < len(line) else ""
            number = _row_number(line[row_col]) if 0 <= row_col < len(line) else None
            number = number if number is not None else i
            record = {"row_number": number, **{k: cell(k) for k in keys}}
            if record["name"] or record["name_ar"]:
                record["name"] = record["name"] or record["name_ar"]
                rows.append(record)
    wanted = set(row_numbers or ())
    return [r for r in rows if not wanted or r["row_number"] in wanted]


def read_brands_file(path):
    """A Brands Mapping CSV (the tab's layout) in get_brand_mappings' shape."""
    import csv

    import google_sheets

    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        return google_sheets.parse_brand_mapping_rows([row for row in csv.reader(fh)])


def read_brand_mappings(spreadsheet):
    """The 'Brands Mapping' tab in get_brand_mappings' shape, read without creating the tab."""
    try:
        rows = spreadsheet.worksheet("Brands Mapping").get_all_values()
    except Exception as exc:
        log.warning("no 'Brands Mapping' tab readable (%s); brands resolve as sheet_raw", exc)
        return {}
    if not rows:
        return {}
    headers = [h.lower().strip() for h in rows[0]]

    def idx(*names):
        for n in names:
            if n in headers:
                return headers.index(n)
        return -1

    i_sub, i_dom = idx("sub-brands", "sub brands", "sub_brands"), idx("official domains", "official_domains")

    def split(row, i):
        return [s.strip() for s in row[i].split(",") if s.strip()] if 0 <= i < len(row) else []

    mappings = {}
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        brand = row[0].strip()
        syns = split(row, 1)
        if brand not in syns:
            syns.insert(0, brand)
        entry = {"brand": brand, "synonyms": syns, "excluded_competitors": split(row, 2)}
        if split(row, i_sub):
            entry["sub_brands"] = split(row, i_sub)
        if split(row, i_dom):
            entry["official_domains"] = split(row, i_dom)
        mappings[brand.lower()] = entry
    return mappings


def mapping_report(rows, mappings, identity):
    """Lines saying how many Brands Mapping entries the run uses and how many of its rows have a mapped brand,
    with a warning when none has: the live run of 2026-10-03 (smoke_6.json) ran without any mapping, so every
    brand was 'sheet_raw' (no auto-publish, misspellings unfixed, no excluded competitors) and nothing said so."""
    mappings = mappings or {}
    learned = sum(1 for v in mappings.values() if isinstance(v, dict) and (v.get("learned") or v.get("sources_only")))
    mapped = 0
    for row in rows or []:
        try:
            spec = identity.build_sku_spec({k: row.get(k, "") for k in ("name", "name_ar", "brand", "brand_ar",
                                                                        "barcode", "category", "size")}, mappings)
        except Exception:      # a row the identity cannot read is reported by the run itself
            continue
        if getattr(spec, "brand_conf", "") in ("mapped", "learned"):
            mapped += 1
    n = len(rows or [])
    entries = f"{len(mappings) - learned} entries" + (f" + {learned} learned from reviews" if learned else "")
    lines = [f"Brands Mapping: {entries} loaded | {mapped} of {n} rows have a mapped brand"]
    if n and not mapped:
        lines.append("WARNING: none of this run's brands is in the Brands Mapping: every brand is searched as the "
                     "sheet writes it (no auto-publish, sheet misspellings unfixed, no excluded competitors). Fill "
                     "the 'Brands Mapping' tab or pass --brands-file (e.g. runs/2026-10-03/brands_mapping_suggested.csv).")
    return lines


def load_v2():
    """The catalog_match stages; a clear message when a stage is not merged yet."""
    try:
        from catalog_match import identity, pipeline, providers, settings, verify
    except ImportError as exc:
        raise SystemExit(f"catalog_match is not complete on this checkout ({exc}); merge WP-2..WP-4 first")
    return identity, pipeline, providers, settings, verify


# ---------------------------------------------------------------------------
# Counting wrappers
# ---------------------------------------------------------------------------

class CountingProvider:
    """Delegates to a real provider and records every call and its health."""

    def __init__(self, inner, log_calls):
        self._inner, self._log = inner, log_calls
        self.name = inner.name
        self.sanctioned = inner.sanctioned

    def search(self, query, hl, spec):
        t0 = time.perf_counter()
        result = self._inner.search(query, hl, spec)
        call = {"provider": self.name, "query": query, "hl": hl, "status": result.status,
                "http_status": result.http_status, "count": len(result.candidates),
                "ms": int(1000 * (time.perf_counter() - t0)), "error": result.error}
        if getattr(result, "hedges", 0):
            call.update(hedged=True, hedges=int(result.hedges))
        self._log.append(call)
        return result

    def __getattr__(self, name):          # e.g. Open Food Facts lookup(spec)
        attr = getattr(self._inner, name)
        if name != "lookup":
            return attr

        def lookup(spec):
            result = attr(spec)
            self._log.append({"provider": self.name, "query": "lookup", "hl": "", "status": result.status,
                              "http_status": result.http_status, "count": len(result.candidates), "ms": None,
                              "error": result.error})
            return result
        return lookup


class CountingVerifier:
    """Delegates to the real verifier and records every call: status, error, billed calls, per-model usage."""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0          # verify() invocations
        self.log = []

    def verify(self, spec, images):
        self.calls += 1
        result = self._inner.verify(spec, images)
        usage = [dict(u) for u in (getattr(result, "usage", None) or []) if isinstance(u, dict)]
        self.log.append({"status": getattr(result, "status", "unknown"), "error": getattr(result, "error", None),
                         "calls": int(getattr(result, "calls", 0) or 0), "images": len(images or []),
                         "usage": usage})
        return result

    def __getattr__(self, name):
        if name.startswith("__") or name in ("_inner", "log", "calls"):
            raise AttributeError(name)
        return getattr(self._inner, name)


def production_verifier(pipeline, verify_mod):
    """The worker's own verifier (pipeline._default_verifier: the models chosen in Settings), else Gemini."""
    make = getattr(pipeline, "_default_verifier", None)
    if callable(make):
        return make()
    return verify_mod.GeminiVerifier()


def _accepts(fn, name):
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def provider_prices(serp_cost=DEFAULT_SERP_COST):
    """USD per answered call for each provider name; '_default' for a provider not listed."""
    try:
        serpapi = float(accessor("serpapi_lens_price_usd", lambda: setting("SERPAPI_LENS_PRICE_USD",
                                                                            DEFAULT_SERPAPI_COST)))
    except (TypeError, ValueError):
        serpapi = DEFAULT_SERPAPI_COST
    prices = {name: serp_cost for name in SERPER_PROVIDERS}
    prices.update({name: 0.0 for name in FREE_PROVIDERS})
    prices.update({"lens_serpapi": max(0.0, serpapi), "cse_legacy": DEFAULT_CSE_COST, "_default": serp_cost})
    return prices


def call_cost(call, prices):
    if call.get("query") == "lookup" or call.get("status") not in ANSWERED_STATUSES:
        return 0.0
    return credits(call) * float(prices.get(call.get("provider"), prices.get("_default", DEFAULT_SERP_COST)))


def credits(call):
    """What one recorded call spent: 1, plus one per hedged request (providers/serper.py sent it a second time)."""
    try:
        return 1 + max(0, int(call.get("hedges") or 0))
    except (TypeError, ValueError):
        return 1


def expansion_calls(outcome):
    """The expansion round's paid calls (provider_health entries of its sources or of queries X1..X5 / XU)."""
    out = []
    for h in getattr(outcome, "provider_health", None) or []:
        provider = getattr(h, "provider", "") or ""
        query_id = getattr(h, "query_id", "") or ""
        if provider in EXPANSION_PROVIDERS or _EXPANSION_QUERY_RE.match(query_id):
            call = {"provider": provider, "query": query_id or "expansion", "query_id": query_id, "hl": "",
                    "status": getattr(h, "status", ""), "http_status": getattr(h, "http_status", None),
                    "count": None, "ms": getattr(h, "latency_ms", None), "error": getattr(h, "error", None),
                    "round": "expansion"}
            if getattr(h, "hedges", 0):
                call.update(hedged=True, hedges=int(h.hedges))
            out.append(call)
    return out


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------

def _evidence(rc):
    s = rc.score
    bits = [f"tier={s.tier}", f"size={s.size_status}"]
    if s.hard_reject:
        bits.append("reject=" + ",".join(s.hard_reject))
    for key in ("brand", "variants", "coverage", "source_trust", "gtin"):
        if key in (s.matched or {}):
            bits.append(f"{key}={s.matched[key]}")
    if rc.quality is not None and not rc.quality.hard_ok:
        bits.append("quality=" + ",".join(rc.quality.hard_reasons))
    if rc.fetched is not None and not rc.fetched.ok:
        bits.append(f"fetch={rc.fetched.error}")
    return " ".join(bits)


def forget_brand_spellings():
    """A run starts with no store spelling an earlier run (or an earlier Brands Mapping) proved: brand_discovery."""
    from catalog_match import brand_discovery
    brand_discovery.forget_all()


def run_row(row, mappings, identity, pipeline, providers_mod, verify_mod, serp_cost, vlm_cost, expansion=None,
            prices=None, secrets=None, after=None):
    prices = prices or provider_prices(serp_cost)
    calls = []
    providers = [CountingProvider(p, calls) for p in providers_mod.default_providers()]
    verifier = CountingVerifier(production_verifier(pipeline, verify_mod))
    spec = identity.build_sku_spec({k: row.get(k, "") for k in ("name", "name_ar", "brand", "brand_ar", "barcode",
                                                                 "category", "size")}, mappings)
    kwargs = {}
    if expansion is not None and _accepts(pipeline.find_product_image, "expansion"):
        kwargs["expansion"] = expansion        # injected providers turn the round off unless asked for
    t0 = time.perf_counter()
    outcome = pipeline.find_product_image(spec, providers=providers, verifier=verifier, **kwargs)
    seconds = time.perf_counter() - t0

    all_calls = calls + expansion_calls(outcome)
    for c in all_calls:
        if c.get("error"):
            c["error"] = redact(c["error"], secrets)
    search_cost = sum(call_cost(c, prices) for c in all_calls)
    serp_calls = sum(credits(c) for c in all_calls if c["provider"] not in FREE_PROVIDERS and c["query"] != "lookup"
                     and c["status"] in ANSWERED_STATUSES)
    usage = [u for entry in verifier.log for u in entry["usage"]]
    vlm_calls = sum(entry["calls"] for entry in verifier.log) if verifier.log else int(outcome.vlm_calls or 0)
    verifier_cost = 0.0
    for entry in verifier.log:
        if entry["usage"]:
            verifier_cost += sum(float(u.get("usd") or 0.0) for u in entry["usage"])
        else:
            verifier_cost += entry["calls"] * vlm_cost
    exp = [c for c in all_calls if c.get("round") == "expansion"]

    top = [_describe(rc, i) for i, rc in enumerate(outcome.ranked[:5], 1)]
    # The pick is always reported, also when it ranks below the top 5 (live run: row 34's pick was invisible).
    winner = None
    if outcome.winner is not None:
        position = next(i for i, rc in enumerate(outcome.ranked, 1) if rc is outcome.winner)
        winner = _describe(outcome.winner, position)
    # the listings that name the brand: why a row has no pick is read on them, never on another brand's listing
    facts = brand_facts(outcome.ranked, outcome.failure_code)
    record = {
        "row": row["row_number"], "name": row["name"], "brand": row["brand"], "sku_key": spec.sku_key,
        "brand_conf": spec.brand_conf, "gtin_status": spec.gtin_status, "variants": dict(spec.variants),
        "discovered_brands": list(getattr(outcome, "discovered_brands", None) or []),
        "queries": list(outcome.queries), "provider_calls": all_calls, "decision": outcome.decision,
        "failure_code": outcome.failure_code,
        "winner": outcome.winner.candidate.image_url if outcome.winner else None, "winner_detail": winner,
        "winner_provider": winner["provider"] if winner else None,
        "winner_domain": winner["domain"] if winner else None,
        "warnings": winner["warnings"] if winner else [],
        "top": top, "reject_counts": dict(outcome.reject_counts), "vlm_calls": vlm_calls,
        "verifier_calls": [{k: (redact(v, secrets) if k == "error" and v else v) for k, v in entry.items()
                            if k != "usage"} for entry in verifier.log],
        "vlm_usage": usage, "strong_calls": sum(1 for u in usage if u.get("role") == "strong"),
        "verdicts": facts["verdicts"],
        "brand_found": facts["brand_found"],
        "only_social": facts["only_social"],
        "social_links": list(getattr(outcome, "social_links", None) or []),
        "expansion": {"ran": bool(exp), "kind": ("upgrade" if any(c.get("query_id") == "XU" for c in exp)
                                                 else "expand") if exp else "", "calls": len(exp)},
        "serp_calls": serp_calls,
        "cost": {"search": round(search_cost, 4), "verifier": round(verifier_cost, 4)},
        "cost_usd": round(search_cost + verifier_cost, 4), "seconds": round(seconds, 1),
    }
    record["outage"] = outage_reason(record)
    record["unselected_reason"] = (None if record["outage"] or record["decision"] in PICK_DECISIONS
                                   else unselected_reason(record))
    extra = after(spec, outcome) if after is not None else None      # --record-shadow, once the row is decided
    if extra:
        record["shadow"] = extra
    return record


def _describe(rc, position):
    from catalog_match.decide import warning_codes

    v = rc.verdict
    return {
        "rank": position, "status": rc.status, "reasons": list(rc.reasons), "warnings": warning_codes(rc.reasons),
        "tier": rc.score.tier if rc.score is not None else None,
        "provider": rc.candidate.provider, "domain": rc.candidate.domain, "query_id": rc.candidate.query_id,
        "sanctioned": rc.candidate.sanctioned, "title": rc.candidate.title or rc.candidate.page_title,
        "image_url": rc.candidate.image_url, "page_url": rc.candidate.page_url, "evidence": _evidence(rc),
        "vlm": None if v is None else {"decision": v.decision, "view": v.view, "brand": v.brand_text,
                                       "variant": v.variant_text, "size": v.size_text},
    }


# ---------------------------------------------------------------------------
# Row classification (works on the rows of this version and of older --json files)
# ---------------------------------------------------------------------------

def _connectivity_failure(call):
    return (call.get("status") not in ANSWERED_STATUSES and not call.get("http_status")
            and bool(_CONNECTIVITY_RE.search(str(call.get("error") or ""))))


def outage_reason(r):
    """Plain words when the row hit a connection outage (it is left out of the coverage), else None.

    A row with a pick is measured whatever failed on the way. A label reader that only timed out (after
    the search was answered) had a connection: the model was slow, and the row counts as "label reader
    unavailable"; only a lost connection to it (refused, DNS, SSL, proxy) makes the row an outage.
    """
    if "error" in r:
        return "connection lost" if _CONNECTIVITY_RE.search(str(r.get("error") or "")) else None
    if r.get("decision") in PICK_DECISIONS:
        return None
    if r.get("decision") == "PROVIDER_DOWN":
        failed = [c for c in r.get("provider_calls") or []
                  if c.get("query") != "lookup" and c.get("status") not in ANSWERED_STATUSES]
        if failed and all(_connectivity_failure(c) for c in failed):
            return "no connection to the search service"
    if r.get("decision") == "VERIFIER_DOWN" or r.get("failure_code") == "VERIFIER_DOWN":
        failed = [v for v in r.get("verifier_calls") or [] if v.get("status") != "ok"]
        cut = [v for v in failed if _LINK_DOWN_RE.search(str(v.get("error") or ""))]
        other = [v for v in failed if v not in cut and str(v.get("error") or "") != "circuit_open"]
        if cut and not other:
            return "no connection to the label reader"
    return None


def winner_info(r):
    """(provider, domain, query_id) of the row's pick, or (None, None, None)."""
    detail = r.get("winner_detail") or {}
    if not r.get("winner") and not detail:
        return None, None, None
    provider = r.get("winner_provider") or detail.get("provider")
    domain = r.get("winner_domain") or detail.get("domain") or _host(detail.get("page_url") or r.get("winner"))
    return provider or None, domain or None, detail.get("query_id") or None


def expansion_info(r):
    info = r.get("expansion")
    if isinstance(info, dict):
        return {"ran": bool(info.get("ran")), "kind": info.get("kind") or "", "calls": int(info.get("calls") or 0)}
    exp = [c for c in r.get("provider_calls") or [] if c.get("round") == "expansion"
           or c.get("provider") in EXPANSION_PROVIDERS]
    kind = ("upgrade" if any(c.get("query_id") == "XU" for c in exp) else "expand") if exp else ""
    return {"ran": bool(exp), "kind": kind, "calls": len(exp)}


def _from_expansion(r):
    provider, _domain, query_id = winner_info(r)
    return provider in EXPANSION_SOURCES or bool(query_id and _EXPANSION_QUERY_RE.match(query_id))


def summarize(rows):
    """The run summary: the same numbers for every dry run, so two runs compare."""
    rows = [r for r in rows or [] if isinstance(r, dict)]
    outage, errors, measured = [], [], []
    for r in rows:
        if outage_reason(r):
            outage.append(r)
        elif "error" in r:
            errors.append(r)
        else:
            measured.append(r)
    picks = [r for r in measured if r.get("decision") in PICK_DECISIONS]
    unselected = {key: [] for key, _label in UNSELECTED_REASONS}
    for r in measured:
        if r.get("decision") not in PICK_DECISIONS:
            unselected[unselected_reason(r)].append(r.get("row"))
    by_provider, by_domain = Counter(), Counter()
    for r in picks:
        provider, domain, _query = winner_info(r)
        by_provider[provider or "?"] += 1
        by_domain[domain or "?"] += 1
    ran = [r for r in rows if "error" not in r]
    exp = [(r, expansion_info(r)) for r in ran]
    models = {}
    for r in ran:
        for u in r.get("vlm_usage") or []:
            model = f"{u.get('provider') or '?'}:{u.get('model') or '?'}"
            entry = models.setdefault(model, {"calls": 0, "usd": 0.0, "strong": 0})
            entry["calls"] += 1
            entry["usd"] = round(entry["usd"] + float(u.get("usd") or 0.0), 6)
            entry["strong"] += 1 if u.get("role") == "strong" else 0
    total = sum(float(r.get("cost_usd") or 0.0) for r in ran)
    # Per product = what a product the run measured cost. Outage rows (few or no answered calls) are in the
    # total but not in the average: counting them as products made a product look cheaper than it is.
    measured_cost = sum(float(r.get("cost_usd") or 0.0) for r in measured)
    split = all(isinstance(r.get("cost"), dict) for r in ran) and bool(ran)
    warnings = Counter(w for r in picks for w in r.get("warnings") or [])
    decisions = Counter(r.get("decision", "ERROR") for r in rows)
    return {
        "rows": len(rows),
        "outage_rows": len(outage), "outage_row_numbers": [r.get("row") for r in outage],
        "errors": len(errors), "error_row_numbers": [r.get("row") for r in errors],
        "measured": len(measured),
        "preselected": len(picks),
        "auto_publish": sum(1 for r in picks if r.get("decision") == "AUTO_PUBLISH"),
        "coverage_pct": round(100.0 * len(picks) / len(measured), 1) if measured else None,
        "unselected": {key: len(v) for key, v in unselected.items()},
        "unselected_rows": unselected,
        "winner_providers": dict(by_provider.most_common()),
        "winner_domains": dict(by_domain.most_common()),
        "expansion": {"rows": sum(1 for _r, e in exp if e["ran"]),
                      "expand": sum(1 for _r, e in exp if e["kind"] == "expand"),
                      "upgrade": sum(1 for _r, e in exp if e["kind"] == "upgrade"),
                      "calls": sum(e["calls"] for _r, e in exp),
                      "winners": sum(1 for r in picks if _from_expansion(r))},
        "strong_calls": sum(int(r.get("strong_calls") or 0) for r in ran),
        "strong_rows": sum(1 for r in ran if int(r.get("strong_calls") or 0) > 0),
        "vlm_calls": sum(int(r.get("vlm_calls") or 0) for r in ran),
        "search_calls": sum(int(r.get("serp_calls") or 0) for r in ran),
        "models": models,
        "cost_usd": {"total": round(total, 4),
                     "per_product": round(measured_cost / len(measured), 4) if measured else None,
                     "search": round(sum(r["cost"].get("search", 0.0) for r in ran), 4) if split else None,
                     "verifier": round(sum(r["cost"].get("verifier", 0.0) for r in ran), 4) if split else None},
        "warnings": dict(sorted(warnings.items(), key=lambda kv: (-kv[1], kv[0]))),
        "picks_with_warnings": sum(1 for r in picks if r.get("warnings")),
        "decisions": dict(sorted(decisions.items())),
    }


def _rows_text(numbers, limit=12):
    numbers = [n for n in numbers if n is not None]
    if not numbers:
        return ""
    shown = ", ".join(str(n) for n in numbers[:limit])
    return f"rows {shown}{' ...' if len(numbers) > limit else ''}"


def _counts_text(counts):
    return ", ".join(f"{k} {v}" for k, v in counts.items()) or "-"


def format_summary(s):
    """The summary as printed at the end of a dry run (plain lines, UTF-8)."""
    lines = ["", "=== run summary ==="]
    outage = f"outage rows excluded {s['outage_rows']}"
    if s["outage_rows"]:
        outage += f" ({_rows_text(s['outage_row_numbers'])})"
    crashed = f"crashed rows excluded {s['errors']}"
    if s["errors"]:
        crashed += f" ({_rows_text(s['error_row_numbers'])})"
    lines.append(f"rows {s['rows']} | {outage} | {crashed} | measured {s['measured']}")
    coverage = "-" if s["coverage_pct"] is None else f"{s['coverage_pct']:.1f}%"
    lines.append(f"pre-selected {s['preselected']}/{s['measured']} = {coverage} (auto-publish {s['auto_publish']})")
    not_picked = s["measured"] - s["preselected"]
    lines.append(f"no pick {not_picked}:")
    for key, label in UNSELECTED_REASONS:
        count = s["unselected"].get(key, 0)
        rows = _rows_text(s["unselected_rows"].get(key) or [])
        lines.append(f"  {label:<42} {count:>4}  {rows}".rstrip())
    lines.append(f"picks by provider: {_counts_text(s['winner_providers'])}")
    lines.append(f"picks by site: {_counts_text(dict(list(s['winner_domains'].items())[:10]))}")
    e = s["expansion"]
    lines.append(f"expansion rounds: {e['rows']} rows (expand {e['expand']}, upgrade {e['upgrade']}), "
                 f"{e['calls']} paid calls, {e['winners']} picks came from it")
    lines.append(f"strong model: {s['strong_calls']} calls on {s['strong_rows']} rows | label-reader calls "
                 f"{s['vlm_calls']} | paid search calls {s['search_calls']}")
    for model, m in s["models"].items():
        lines.append(f"  {model}: {m['calls']} calls{' (' + str(m['strong']) + ' strong)' if m['strong'] else ''}, "
                     f"${m['usd']:.4f}")
    c = s["cost_usd"]
    per = "-" if c["per_product"] is None else f"${c['per_product']:.4f}"
    split = (f" (search ${c['search']:.4f}, label reading ${c['verifier']:.4f})"
             if c["search"] is not None and c["verifier"] is not None else "")
    lines.append(f"estimated cost ${c['total']:.4f} total, {per} per measured product{split}")
    lines.append(f"warnings on picks: {_counts_text(s['warnings'])} ({s['picks_with_warnings']} picks with a warning)")
    lines.append(f"decisions: {_counts_text(s['decisions'])}")
    return "\n".join(lines)


def load_run(path):
    """A --json file of any version as {"format", "meta", "summary", "rows"} (older files hold only the rows)."""
    with open(path, "r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return {"format": "smoke_live/1", "meta": {}, "summary": None, "rows": data}
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        return data
    raise ValueError(f"{path} is not a scripts/smoke_live.py --json file")


# ---------------------------------------------------------------------------
# Printing one row
# ---------------------------------------------------------------------------

def _print_candidate(c):
    print(f"  #{c['rank']} [{c['status']}] {c['provider']}/{c['domain']}: {c['title'][:80]}")
    print(f"       {c['evidence']}")
    if c["vlm"]:
        print(f"       VLM {c['vlm']['decision']} view={c['vlm']['view']} brand={c['vlm']['brand']!r} "
              f"variant={c['vlm']['variant']!r} size={c['vlm']['size']!r}")
    if c["reasons"]:
        print(f"       reasons {' '.join(c['reasons'])}")
    print(f"       {c['image_url']}")


def print_row(r):
    variants = " ".join(f"{k}={v}" for k, v in sorted(r.get("variants", {}).items())) or "-"
    print(f"\n=== row {r['row']}: {r['name']} | brand {r['brand']} ({r['brand_conf']}) | gtin {r['gtin_status']}"
          f" | variants {variants}")
    if r.get("discovered_brands"):
        print(f"  brand  the stores write it {', '.join(r['discovered_brands'])} (brand discovery; review only)")
    for q in r["queries"]:
        print(f"  query  {q}")
    for c in r["provider_calls"]:
        count = "-" if c.get("count") is None else c["count"]
        tag = " [expansion]" if c.get("round") == "expansion" else ""
        print(f"  health {c['provider']:10s} {c['status']:8s} http={c['http_status']} n={count} "
              f"{c['ms']}ms {c['error'] or ''}  <- {str(c['query'])[:70]}{tag}")
    for c in r["top"]:
        _print_candidate(c)
    detail = r.get("winner_detail")
    if detail and detail["rank"] > len(r["top"]):
        print("  pick (ranked below the top 5):")
        _print_candidate(detail)
    print(f"  DECISION {r['decision']} {r['failure_code'] or ''} -> {r['winner'] or '-'}")
    warnings = (detail or {}).get("warnings") or []
    if warnings:
        # decide.route's review warnings: what the reviewer must double-check before approving the pick
        print(f"  WARNINGS {' '.join(warnings)}")
    if r.get("outage"):
        print(f"  OUTAGE {r['outage']} (left out of the coverage)")
    elif r.get("unselected_reason"):
        print(f"  NO PICK {dict(UNSELECTED_REASONS).get(r['unselected_reason'], r['unselected_reason'])}")
    for link in r.get("social_links") or []:
        print(f"  SOCIAL POST {link}")
    usage = r.get("vlm_usage") or []
    if usage:
        models = Counter(f"{u.get('provider')}:{u.get('model')}{' (strong)' if u.get('role') == 'strong' else ''}"
                         for u in usage)
        print(f"  label reading {', '.join(f'{m} x{n}' for m, n in models.items())}")
    exp = r.get("expansion") or {}
    expansion = f", expansion {exp['kind']} {exp['calls']} calls" if exp.get("ran") else ""
    print(f"  cost ~${r['cost_usd']:.4f} ({r['serp_calls']} paid search calls, {r['vlm_calls']} VLM calls"
          f"{expansion}), {r['seconds']}s")


# ---------------------------------------------------------------------------
# --probe: which paid services work with the configured keys
# ---------------------------------------------------------------------------

PROBE_QUERY = "Almarai Fresh Milk Full Fat 1L"
PROBE_SITE_QUERY = "Almarai Fresh Milk site:luluhypermarket.com OR site:carrefouruae.com"
PROBE_IMAGE = "https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg"
PROBE_TIMEOUT_S = 20
SERPER_ROOT = "https://google.serper.dev"
GEMINI_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"
ANTHROPIC_ROOT = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
SERPAPI_ACCOUNT_URL = "https://serpapi.com/account.json"
PHOTOROOM_ACCOUNT_URL = "https://image-api.photoroom.com/v2/account"
CLOUDINARY_PING_URL = "https://api.cloudinary.com/v1_1/{cloud}/ping"
PROBE_TEXT = "Reply with the single word OK."

WORKS, FAILED, NOT_CONFIGURED, OFF = "works", "failed", "not configured", "off"
OFF_WORDS = ("off", "none", "false", "0", "disabled")
_PLAN_WORDS = ("not allowed", "not available", "not supported", "unsupported", "upgrade", "plan", "not enabled",
               "endpoint")


def parse_model(text):
    """'gemini:<model>' / 'claude:<model>' -> (provider, model); 'off' -> ('off', ''); anything else None."""
    value = str(text or "").strip()
    if value.lower() in OFF_WORDS:
        return ("off", "")
    if ":" not in value:
        return None
    provider, model = (part.strip() for part in value.split(":", 1))
    provider = provider.lower()
    if provider == "anthropic":
        provider = "claude"
    if provider == "gemini" and model.startswith("models/"):
        model = model[len("models/"):]
    if provider not in ("gemini", "claude") or not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,95}$", model):
        return None
    return (provider, model)


def model_text(value, bare_gemini=False):
    """A model setting as it may be printed: the id when it is one ('off' too), else a placeholder.

    The raw text is never shown: a key pasted into a model setting is not a configured secret, so
    redaction would not catch it. bare_gemini: the value is a bare Gemini model name (GEMINI_MODEL).
    """
    text = str(value or "").strip()
    if not text:
        return ""
    return text if parse_model(f"gemini:{text}" if bare_gemini else text) is not None else "(not a model id)"


def _body(resp):
    try:
        return str(resp.text or "")
    except Exception:
        return ""


def _json(resp):
    try:
        data = resp.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _transport_reason(exc):
    """Plain words for a request that got no HTTP answer. The exception text is never shown (it can hold URLs)."""
    name = type(exc).__name__.lower()
    if "timeout" in name:
        return "no answer in time (slow or no internet connection)"
    if "ssl" in name:
        return "the secure connection failed (a proxy or antivirus may be in the way)"
    if "proxy" in name:
        return "the proxy refused the connection"
    if "connection" in name or isinstance(exc, OSError):
        return "could not connect (no internet, DNS or firewall)"
    return f"unexpected error ({type(exc).__name__})"


class Probe:
    """One cheap read-only call per configured service. http: anything with get/post like `requests`."""

    def __init__(self, http=None, lookup=None, image_url=PROBE_IMAGE, timeout=PROBE_TIMEOUT_S,
                 serp_cost=DEFAULT_SERP_COST):
        if http is None:
            import requests
            http = requests
        self.http = http
        self.lookup = lookup or setting
        self.image_url = image_url or PROBE_IMAGE
        self.timeout = timeout
        self.serp_cost = serp_cost
        self.results = []

    # -- settings ---------------------------------------------------------------

    def _get(self, name, default=""):
        try:
            value = self.lookup(name)
        except Exception:
            value = None
        return default if _empty(value) else value

    def _key(self, name):
        return str(self._get(name) or "").strip()

    def _has_module(self, name):
        try:
            import importlib.util
            return importlib.util.find_spec(name) is not None
        except Exception:
            return False

    def expansion_enabled(self):
        try:
            max_calls = int(str(self._get("EXPANSION_MAX_CALLS", "4")).strip())
        except ValueError:
            max_calls = 4
        return truthy(self._get("EXPANSION_ENABLED", "true")) and max_calls > 0

    def visual_mode(self):
        mode = str(self._get("VISUAL_SEARCH", "auto")).strip().lower() or "auto"
        return mode if mode in ("auto", "off", "serper", "serpapi") else "off"

    def primary_model(self):
        value = str(self._get("VERIFIER_PRIMARY", "")).strip()
        if value:
            return value
        model = str(self._get("GEMINI_MODEL", "gemini-3.1-flash-lite")).strip() or "gemini-3.1-flash-lite"
        return f"gemini:{model}"

    def strong_model(self):
        return str(self._get("VERIFIER_STRONG", "")).strip()

    # -- one check ----------------------------------------------------------------

    def _add(self, service, label, status, reason, cost=0.0):
        self.results.append({"service": service, "label": label, "status": status, "reason": reason,
                             "cost_usd": round(float(cost), 4)})
        return self.results[-1]

    def _call(self, method, url, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return getattr(self.http, method)(url, **kwargs)

    # -- Serper -------------------------------------------------------------------

    def _serper_failure(self, status, body, endpoint_name):
        text = (body or "").lower()
        if status == 402 or "credit" in text:
            return "no Serper credit left: top up at serper.dev"
        if status in (401, 403) and not any(w in text for w in _PLAN_WORDS):
            return "the Serper key was rejected: check SERPER_API_KEY in Settings"
        if status == 429:
            return "too many requests or the credit ran out; try again later"
        if status >= 500:
            return f"Serper had an error (HTTP {status}); try again later"
        return f"Serper refused the {endpoint_name} request (HTTP {status})"

    def _serper(self, service, label, path, payload, items_keys, describe, unused_note=""):
        key = self._key("SERPER_API_KEY")
        if not key:
            return self._add(service, label, NOT_CONFIGURED, "no Serper key: add SERPER_API_KEY in Settings")
        try:
            resp = self._call("post", f"{SERPER_ROOT}/{path}", json=payload,
                              headers={"X-API-KEY": key, "Content-Type": "application/json"})
        except Exception as exc:
            return self._add(service, label, FAILED, _transport_reason(exc))
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 200:
            data = _json(resp)
            items = next((data[k] for k in items_keys if isinstance(data.get(k), list)), [])
            count = len(items)
            return self._add(service, label, WORKS, describe(count) + unused_note, self.serp_cost)
        body = _body(resp)
        text = body.lower()
        if path == "lens" and "credit" not in text and (status in (404, 405) or (
                status in (400, 402, 403) and any(w in text for w in _PLAN_WORDS))):
            return self._add(service, label, FAILED, "visual search (Lens) is not on your Serper plan")
        if path == "search" and status == 400 and "site" in text:
            return self._add(service, label, FAILED, "your Serper plan refuses site: searches")
        return self._add(service, label, FAILED, self._serper_failure(status, body, path))

    def check_serper(self):
        expansion_off = not self.expansion_enabled()
        unused = "" if self._has_module("catalog_match.expand") else " (this version does not use it yet)"
        self._serper("serper_images", "Serper images (Google Images)", "images",
                     {"q": PROBE_QUERY, "gl": "ae", "hl": "en", "num": 10}, ("images",),
                     lambda n: f"{n} images for a test search" if n else "answered, but no images for the test search")
        off_reason = "the expansion round is off (EXPANSION_ENABLED / EXPANSION_MAX_CALLS)"
        if expansion_off:
            self._add("serper_web", "Serper web search (store pages)", OFF, off_reason)
            self._add("serper_shopping", "Serper shopping", OFF, off_reason)
        else:
            self._serper("serper_web", "Serper web search (store pages)", "search",
                         {"q": PROBE_SITE_QUERY, "gl": "ae", "hl": "en", "num": 10}, ("organic",),
                         lambda n: f"site: search accepted, {n} store pages" if n
                         else "site: search accepted, no pages for the test search", unused)
            self._serper("serper_shopping", "Serper shopping", "shopping",
                         {"q": PROBE_QUERY, "gl": "ae", "hl": "en"}, ("shopping",),
                         lambda n: f"{n} listings for a test search" if n
                         else "answered, but no listings for the test search", unused)
        mode = self.visual_mode()
        if expansion_off:
            self._add("serper_lens", "Serper lens (visual search)", OFF, off_reason)
        elif mode not in ("auto", "serper"):
            self._add("serper_lens", "Serper lens (visual search)", OFF, f"VISUAL_SEARCH is '{mode}'")
        else:
            self._serper("serper_lens", "Serper lens (visual search)", "lens",
                         {"url": self.image_url, "gl": "ae", "hl": "en"},
                         ("visual_matches", "visualMatches", "organic", "matches", "images"),
                         lambda n: f"visual search answered ({n} matches for a test image)", unused)

    # -- SerpApi ------------------------------------------------------------------

    def check_serpapi(self):
        service, label = "serpapi_lens", "SerpApi Google Lens"
        mode = self.visual_mode()
        if not self.expansion_enabled():
            return self._add(service, label, OFF, "the expansion round is off (EXPANSION_ENABLED / "
                                                  "EXPANSION_MAX_CALLS)")
        if mode not in ("auto", "serpapi"):
            return self._add(service, label, OFF, f"VISUAL_SEARCH is '{mode}'")
        key = self._key("SERPAPI_API_KEY")
        if not key:
            return self._add(service, label, NOT_CONFIGURED,
                             "no SerpApi key (optional: visual search uses Serper lens without it)")
        try:
            # account.json is free (no search is used). SerpApi wants the key in the query string, so neither
            # the URL nor the exception text is ever shown.
            resp = self._call("get", SERPAPI_ACCOUNT_URL, params={"api_key": key})
        except Exception as exc:
            return self._add(service, label, FAILED, _transport_reason(exc))
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 200:
            data = _json(resp)
            left = data.get("total_searches_left", data.get("plan_searches_left"))
            plan = str(data.get("plan_name") or "").strip()
            plan_text = f"plan {plan[:40]}, " if plan and not any(s in plan for s in secret_values(self.lookup)) else ""
            if isinstance(left, (int, float)) and left <= 0:
                return self._add(service, label, FAILED, f"{plan_text}no searches left this month")
            left_text = f"{int(left)} searches left this month" if isinstance(left, (int, float)) else "key accepted"
            unused = "" if self._has_module("catalog_match.expand") else " (this version does not use it yet)"
            return self._add(service, label, WORKS, f"{plan_text}{left_text}{unused}")
        if status in (401, 403):
            return self._add(service, label, FAILED, "the SerpApi key was rejected: check SERPAPI_API_KEY in Settings")
        if status == 429:
            return self._add(service, label, FAILED, "too many requests or no searches left; try again later")
        return self._add(service, label, FAILED, f"SerpApi refused the request (HTTP {status})")

    # -- label-reading models -------------------------------------------------------

    def _gemini(self, model):
        key = self._key("GEMINI_API_KEY")
        if not key:
            return NOT_CONFIGURED, "no Gemini key: add GEMINI_API_KEY in Settings"
        body = {"contents": [{"role": "user", "parts": [{"text": PROBE_TEXT}]}],
                "generationConfig": {"maxOutputTokens": 16, "temperature": 0}}
        try:
            resp = self._call("post", f"{GEMINI_ROOT}/{model}:generateContent", json=body,
                              headers={"x-goog-api-key": key, "Content-Type": "application/json"})
        except Exception as exc:
            return FAILED, _transport_reason(exc)
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 200:
            return WORKS, f"{model} answered a one-line test"
        text = _body(resp).lower()
        if status == 400 and ("api_key_invalid" in text or "api key not valid" in text or "api key expired" in text):
            return FAILED, "the Gemini key was rejected: check GEMINI_API_KEY in Settings"
        if status == 400 and "location" in text:
            return FAILED, "Gemini is not available from this location"
        if status == 404:
            return FAILED, f"model {model} was not found (retired or misspelled): pick another model in Settings"
        if status in (401, 403):
            return FAILED, f"the Gemini key has no access to {model} (wrong key or the project is blocked)"
        if status == 429:
            return FAILED, (f"quota used up, or {model} is not available to a free-tier key: enable billing in "
                            "Google AI Studio or pick another model")
        if status >= 500:
            return FAILED, f"Gemini had an error (HTTP {status}); try again later"
        return FAILED, f"Gemini refused the test request (HTTP {status})"

    def _claude(self, model):
        key = self._key("ANTHROPIC_API_KEY")
        if not key:
            return NOT_CONFIGURED, "no Anthropic key: add ANTHROPIC_API_KEY in Settings to use Claude models"
        body = {"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": PROBE_TEXT}]}
        try:
            resp = self._call("post", f"{ANTHROPIC_ROOT}/messages", json=body,
                              headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION,
                                       "content-type": "application/json"})
        except Exception as exc:
            return FAILED, _transport_reason(exc)
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 200:
            return WORKS, f"{model} answered a one-line test"
        text = _body(resp).lower()
        if "credit balance" in text or status == 402:
            return FAILED, "no Anthropic credit left: add credit in the Anthropic console"
        if status == 401:
            return FAILED, "the Anthropic key was rejected: check ANTHROPIC_API_KEY in Settings"
        if status == 403:
            return FAILED, f"the Anthropic key may not use {model}"
        if status == 404:
            return FAILED, f"model {model} was not found: pick another model in Settings"
        if status == 429:
            return FAILED, "Anthropic rate limit reached; try again later"
        if status == 529 or status >= 500:
            return FAILED, f"Anthropic is busy or had an error (HTTP {status}); try again later"
        return FAILED, f"Anthropic refused the test request (HTTP {status})"

    def _model(self, service, role, model_id, source):
        parsed = parse_model(model_id)
        label = f"{role} label reader"
        if parsed is None:
            # The value itself is never shown: a key pasted into the wrong setting is not a configured
            # secret, so redaction would not catch it.
            return self._add(service, label, FAILED,
                             f"{source} is not a model id (use gemini:<model> or claude:<model>)")
        provider, model = parsed
        if provider == "off":
            return self._add(service, label, OFF, "switched off in Settings")
        label = f"{role} label reader ({model})"
        status, reason = self._gemini(model) if provider == "gemini" else self._claude(model)
        return self._add(service, label, status, reason)

    def check_models(self):
        primary = self.primary_model()
        source = "VERIFIER_PRIMARY" if str(self._get("VERIFIER_PRIMARY", "")).strip() else "GEMINI_MODEL"
        first = self._model("primary_model", "Primary", primary, source)
        strong = self.strong_model()
        if not strong:
            self._add("strong_model", "Strong label reader", NOT_CONFIGURED,
                      "no strong model set (this version reads every label with one model)")
        elif parse_model(strong) is not None and parse_model(strong) == parse_model(primary):
            self._add("strong_model", f"Strong label reader ({parse_model(strong)[1]})", first["status"],
                      "same model as the primary reader")
        else:
            row = self._model("strong_model", "Strong", strong, "VERIFIER_STRONG")
            if row["status"] == WORKS and not self._has_module("catalog_match.verifiers"):
                row["reason"] += " (this version does not use it yet)"
        self.check_anthropic()

    def check_anthropic(self):
        service, label = "anthropic", "Anthropic (Claude) key"
        key = self._key("ANTHROPIC_API_KEY")
        if not key:
            return self._add(service, label, NOT_CONFIGURED, "no Anthropic key (needed only for Claude models)")
        try:
            resp = self._call("get", f"{ANTHROPIC_ROOT}/models", params={"limit": 1},
                              headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION})
        except Exception as exc:
            return self._add(service, label, FAILED, _transport_reason(exc))
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 200:
            return self._add(service, label, WORKS, "the key is valid (model list read, nothing billed)")
        if status in (401, 403):
            return self._add(service, label, FAILED,
                             "the Anthropic key was rejected: check ANTHROPIC_API_KEY in Settings")
        return self._add(service, label, FAILED, f"Anthropic refused the request (HTTP {status})")

    # -- image processing ---------------------------------------------------------

    def check_photoroom(self):
        service, label = "photoroom", "PhotoRoom (background removal)"
        key = self._key("PHOTOROOM_API_KEY")
        if not key:
            return self._add(service, label, NOT_CONFIGURED,
                             "no PhotoRoom key (background removal uses another method)")
        try:
            resp = self._call("get", PHOTOROOM_ACCOUNT_URL, headers={"x-api-key": key})
        except Exception as exc:
            return self._add(service, label, FAILED, _transport_reason(exc))
        status = int(getattr(resp, "status_code", 0) or 0)
        sandbox = key.lower().startswith("sandbox_")
        if status == 200:
            images = _json(resp).get("images") or {}
            available = images.get("available") if isinstance(images, dict) else None
            if not sandbox and isinstance(available, (int, float)) and available <= 0:
                return self._add(service, label, FAILED, "the key works but no image credit is left")
            text = f"{int(available)} images left" if isinstance(available, (int, float)) else "key accepted"
            if sandbox:
                text += " (sandbox key: results carry a watermark)"
            return self._add(service, label, WORKS, text)
        if status in (401, 403):
            return self._add(service, label, FAILED, "the PhotoRoom key was rejected: check it in Settings")
        if status == 402:
            return self._add(service, label, FAILED, "the PhotoRoom subscription ended or no credit is left")
        return self._add(service, label, FAILED, f"PhotoRoom refused the request (HTTP {status})")

    def check_cloudinary(self):
        service, label = "cloudinary", "Cloudinary (image hosting)"
        cloud = self._key("CLOUDINARY_CLOUD_NAME")
        key, secret = self._key("CLOUDINARY_API_KEY"), self._key("CLOUDINARY_API_SECRET")
        if not (cloud and key and secret):
            return self._add(service, label, NOT_CONFIGURED,
                             "cloud name, API key or API secret missing: add them in Settings")
        if not re.match(r"^[A-Za-z0-9_-]{1,64}$", cloud):
            return self._add(service, label, FAILED, "the cloud name has characters a cloud name cannot have")
        try:
            resp = self._call("get", CLOUDINARY_PING_URL.format(cloud=cloud), auth=(key, secret))
        except Exception as exc:
            return self._add(service, label, FAILED, _transport_reason(exc))
        status = int(getattr(resp, "status_code", 0) or 0)
        if status == 200:
            return self._add(service, label, WORKS, "ping answered (read-only, nothing uploaded)")
        if status == 401:
            return self._add(service, label, FAILED, "the Cloudinary API key or secret was rejected")
        if status == 404:
            return self._add(service, label, FAILED, "the Cloudinary cloud name was not found")
        if status in (420, 429):
            return self._add(service, label, FAILED, "Cloudinary rate limit reached; try again later")
        return self._add(service, label, FAILED, f"Cloudinary refused the ping (HTTP {status})")

    # -- all ----------------------------------------------------------------------

    def run(self):
        for check in (self.check_serper, self.check_serpapi, self.check_models, self.check_photoroom,
                      self.check_cloudinary):
            try:
                check()
            except Exception as exc:     # one broken check never hides the others
                self._add(check.__name__.replace("check_", ""), check.__name__.replace("check_", "").title(),
                          FAILED, f"the check itself failed ({type(exc).__name__})")
        secrets = secret_values(self.lookup)
        for r in self.results:
            r["reason"] = redact(r["reason"], secrets)
            r["label"] = redact(r["label"], secrets)
        return self.results


def format_probe(results):
    width = max([len(r["label"]) for r in results] + [len("Service")]) + 2
    lines = [f"{'Service':<{width}}{'Status':<16}Details", f"{'-' * (width - 2):<{width}}{'-' * 14:<16}{'-' * 7}"]
    for r in results:
        lines.append(f"{r['label']:<{width}}{r['status']:<16}{r['reason']}")
    cost = sum(r["cost_usd"] for r in results)
    failed = sum(1 for r in results if r["status"] == FAILED)
    lines.append("")
    lines.append(f"{sum(1 for r in results if r['status'] == WORKS)} work, {failed} failed, "
                 f"{sum(1 for r in results if r['status'] == NOT_CONFIGURED)} not configured, "
                 f"{sum(1 for r in results if r['status'] == OFF)} off | this probe cost about ${cost:.4f}; "
                 "nothing was written anywhere")
    return "\n".join(lines)


def run_probe(args, http=None):
    # The HTTP client logs request URLs at DEBUG; SerpApi's has the key in it.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    probe = Probe(http=http, image_url=args.probe_image, serp_cost=args.serp_cost)
    results = probe.run()
    secrets = secret_values()
    print(redact(format_probe(results), secrets))
    if args.json:
        doc = {"format": "smoke_live_probe/1", "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
               "services": results}
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(redact(json.dumps(doc, ensure_ascii=False, indent=1), secrets, query_keys=False))
        print(f"probe results written to {args.json}")
    return 1 if any(r["status"] == FAILED for r in results) else 0


# ---------------------------------------------------------------------------
# --record / --record-shadow / --fill-misses (catalog_match.cassette)
# ---------------------------------------------------------------------------

def git_info(root=REPO_ROOT):
    """The checkout's commit and whether tracked files were changed ({'commit': '', ...} without git)."""
    import subprocess

    def run(*cmd):
        return subprocess.run(["git", "-C", root, *cmd], capture_output=True, text=True, timeout=15).stdout.strip()

    try:
        return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}
    except Exception:
        return {"commit": "", "dirty": None}


def strong_spend():
    """This month's strong-reader spend and budget as the cascade reads them (strong_usd None when unreadable)."""
    out = {"month": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m"), "strong_usd": None,
           "budget_usd": accessor("verifier_monthly_budget_usd")}
    try:
        from catalog_match.verifiers import spend
        out["strong_usd"] = float(spend.MariaDbSpendStore().role_spend("strong"))
    except Exception as exc:
        log.info("strong spend not readable (%s)", type(exc).__name__)
    return out


def recording_meta(args, rows, mappings, meta, expansion):
    """meta.json of a new cassette: what a replay needs to run the same products the same way. No secret."""
    from catalog_match import cassette
    return {
        "format": cassette.FORMAT, "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "today": dt.date.today().isoformat(), "git": git_info(), "settings": cassette.settings_snapshot(),
        "mappings": mappings, "rows": rows, "versions": cassette.library_versions(), "spend": strong_spend(),
        "run": {"expansion": expansion, "serp_cost": args.serp_cost, "vlm_cost": args.vlm_cost,
                "rows_spec": list(args.rows or []), "rows_file": os.path.basename(args.rows_file or ""),
                "brands_file": os.path.basename(args.brands_file or ""), "shadow": bool(args.record_shadow)},
        "run_meta": meta,
    }


def fill_inputs(args):
    """(rows, mappings, cassette meta) of a --fill-misses run: the manifest's rows as the cassette holds them."""
    with open(args.fill_misses, "r", encoding="utf-8-sig") as fh:
        manifest = json.load(fh)
    with open(os.path.join(args.record, "meta.json"), "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    wanted = {int(n) for n in manifest.get("rows") or []}
    rows = [r for r in meta.get("rows") or [] if int(r.get("row_number") or 0) in wanted]
    return rows, meta.get("mappings") or {}, meta


def settings_drift(recorded):
    """Settings that differ from the cassette's (a fill run must ask what the replay asks)."""
    from catalog_match import cassette
    now = cassette.settings_snapshot()
    names = sorted(set(now["values"]) | set((recorded or {}).get("values", {})))
    changed = [n for n in names if now["values"].get(n) != (recorded or {}).get("values", {}).get(n)]
    changed += [n for n, v in now["configured"].items() if v != (recorded or {}).get("configured", {}).get(n)]
    return changed


def _folder_mb(path):
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total / (1024 * 1024)


def recorded_decisions(results):
    """What each row decided live (a replay of the same code must decide the same): row -> decision and pick."""
    return {str(r.get("row")): {"decision": r.get("decision", "ERROR"), "failure_code": r.get("failure_code"),
                                "winner": r.get("winner"), "top": [c.get("image_url") for c in r.get("top") or []]}
            for r in results}


def start_cassette(args, rows, mappings, meta, expansion, secrets):
    """Install the --record cassette (mode 'fill' with --fill-misses); None without --record."""
    if not args.record:
        return None
    from catalog_match import cassette
    mode = "fill" if args.fill_misses else "record"
    cas = cassette.install(mode, args.record, redact=lambda text: redact(text, secrets, query_keys=False))
    if mode == "record":
        try:
            cas.write_meta(recording_meta(args, rows, mappings, meta, expansion))
        except Exception as exc:
            cassette.uninstall()
            raise SystemExit(f"--record {args.record}: the cassette cannot be written "
                             f"({redact(f'{type(exc).__name__}: {exc}', secrets)}); nothing was asked or paid yet")
        print(f"recording every external answer into {args.record}"
              f"{' (with shadow answers for later code versions)' if args.record_shadow else ''}")
    else:
        print(f"topping up the cassette {args.record}: recorded answers are reused, only missing ones are paid")
    return cas


def note_not_stored(record, report):
    """A row whose answers the cassette could not all store (a full disk, a file held by an antivirus): said
    under the row and kept in the --json row; the live result itself is unchanged."""
    lost = report.get("not_stored") or []
    if not lost:
        return
    record["cassette"] = {"complete": False, "not_stored": lost}
    print(f"  CASSETTE: {len(lost)} answer(s) of this row not stored as usual ({lost[0]['what']}: "
          f"{lost[0]['error'][:120]}); a replay reports what is missing, --fill-misses adds it")


def finish_cassette(cas, args, results):
    """Write the cassette's closing meta, uninstall it and print where it is and how to replay it."""
    from catalog_match import cassette
    try:
        meta = cas.read_meta()
        shadow = [r["shadow"] for r in results if isinstance(r.get("shadow"), dict)]
        totals = {k: sum(int(s.get(k) or 0) for s in shadow) for k in ("downloads", "pages", "search_calls",
                                                                        "verifier_calls")}
        totals["cost_usd"] = round(sum(float(s.get("cost_usd") or 0.0) for s in shadow), 4)
        totals["rows_stopped"] = [r.get("row") for r in results if (r.get("shadow") or {}).get("error")]
        not_stored = {str(n): len(rep["not_stored"]) for n, rep in sorted(cas.reports.items()) if rep["not_stored"]}
        if cas.mode == "record":
            meta.update(finished_at=dt.datetime.now().isoformat(timespec="seconds"),
                        rows_recorded=[r.get("row") for r in results], answers=cas.counts["recorded"],
                        shadow=totals if shadow else None, decisions=recorded_decisions(results),
                        not_stored=not_stored or None)
        else:
            meta.setdefault("fills", []).append({
                "at": dt.datetime.now().isoformat(timespec="seconds"), "git": git_info(),
                "manifest": os.path.basename(args.fill_misses or ""), "rows": [r.get("row") for r in results],
                "answers_added": cas.counts["recorded"], "answers_reused": cas.counts["served"],
                "not_stored": not_stored or None})
        cas.write_meta(meta)
    finally:
        cassette.uninstall()        # the breaker / spend / index-size hooks never outlive the run
    if not_stored:
        print(f"WARNING: {sum(not_stored.values())} answer(s) of rows {', '.join(not_stored)} were not stored as "
              f"usual (see the CASSETTE lines above); a replay lists what is missing")
    if shadow:
        print(f"shadow recording: {int(totals['downloads'])} extra downloads, {int(totals['pages'])} page reads, "
              f"{int(totals['search_calls'])} search calls, {int(totals['verifier_calls'])} label-reader calls, "
              f"about ${totals['cost_usd']:.4f} on top of the estimated cost above")
    verb = "recorded" if cas.mode == "record" else "added"
    print(f"cassette {args.record}: {cas.counts['recorded']} answers {verb}, {_folder_mb(args.record):.1f} MB "
          f"(keys and tokens are never stored). Zip the folder to send it; replay it offline with:\n"
          f"    python scripts/replay_run.py {args.record} --json runs/replayed.json")


# ---------------------------------------------------------------------------
# The dry run
# ---------------------------------------------------------------------------

def run_meta(args, settings, pipeline, expansion):
    """What this run measured with (no secret: keys only as set / missing)."""
    has_round = _accepts(pipeline.find_product_image, "expansion")
    return {
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "rows_spec": list(args.rows or []),
        "gemini_model": model_text(settings.gemini_model(), bare_gemini=True),
        "verifier_primary": model_text(accessor("verifier_primary", lambda: setting("VERIFIER_PRIMARY", ""))),
        "verifier_strong": model_text(accessor("verifier_strong", lambda: setting("VERIFIER_STRONG", ""))),
        "expansion": ("not in this version" if not has_round else "off (--no-expansion)" if expansion is False
                      else "on" if truthy(setting("EXPANSION_ENABLED", "true")) else "off in Settings"),
        "expansion_max_calls": str(setting("EXPANSION_MAX_CALLS", "")),
        "visual_search": str(setting("VISUAL_SEARCH", "")),
        "auto_publish": bool(settings.auto_publish_enabled()),
        "keys": {name: bool(str(setting(key, "") or "").strip()) for name, key in (
            ("serper", "SERPER_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("serpapi", "SERPAPI_API_KEY"),
            ("anthropic", "ANTHROPIC_API_KEY"))},
        "prices": {"serp_cost": args.serp_cost, "vlm_cost": args.vlm_cost},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", action="append", help="sheet rows, e.g. 2-31 (repeatable); required for a dry run "
                                                        "on the sheet, optional with --rows-file")
    parser.add_argument("--rows-file", help="read the products from this CSV or earlier --json run, not the sheet")
    parser.add_argument("--brands-file", help="a Brands Mapping CSV to use instead of the sheet's tab")
    parser.add_argument("--probe", action="store_true",
                        help="check each configured paid service with one cheap read-only call, then exit")
    parser.add_argument("--probe-image", default=PROBE_IMAGE, help="public image URL for the visual-search check")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="the only mode: nothing is written to the sheet, cache or queue")
    parser.add_argument("--no-expansion", action="store_true",
                        help="measure without the expansion round (to compare with a run that has it)")
    parser.add_argument("--json", help="also write the full results (or the probe table) to this JSON file")
    parser.add_argument("--serp-cost", type=float, default=DEFAULT_SERP_COST, help="USD per Serper query")
    parser.add_argument("--vlm-cost", type=float, default=DEFAULT_VLM_COST,
                        help="USD per VLM call when the verifier reports no per-model usage")
    parser.add_argument("--record", metavar="DIR",
                        help="record every external answer of this run into the folder DIR (a cassette: no key, "
                             "header or token is stored), to replay it offline with scripts/replay_run.py DIR")
    parser.add_argument("--record-shadow", action="store_true",
                        help="with --record: after each row is decided, also record answers later code may ask for "
                             "(more images, pages, X1/X2, label readings); costs a little more, printed at the end")
    parser.add_argument("--fill-misses", metavar="MANIFEST",
                        help="with --record DIR (an existing cassette): run only the rows of this misses manifest "
                             "(written by scripts/replay_run.py) and add the answers DIR does not hold yet")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _utf8_stdout()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.json:
        # checked before any paid call: the file is written only at the end of the run
        target = os.path.abspath(args.json)
        if os.path.isdir(target) or not os.path.isdir(os.path.dirname(target)):
            parser.error(f"--json {args.json}: give a file name in a folder that exists")
    if args.probe:
        return run_probe(args)
    if (args.record_shadow or args.fill_misses) and not args.record:
        parser.error("--record-shadow and --fill-misses need --record <cassette folder>")
    if args.record and not args.fill_misses and os.path.exists(os.path.join(args.record, "meta.json")):
        parser.error(f"--record {args.record}: this folder already holds a cassette; record into a new folder "
                     "(or top it up with --fill-misses <manifest>)")
    if args.fill_misses and not os.path.exists(os.path.join(args.record, "meta.json")):
        parser.error(f"--fill-misses: {args.record} holds no cassette (meta.json) to top up")
    if not args.rows and not args.rows_file and not args.fill_misses:
        parser.error("--rows (or --rows-file) is required for a dry run (or use --probe)")

    identity, pipeline, providers_mod, settings, verify_mod = load_v2()
    if args.fill_misses:
        try:
            rows, mappings, recorded = fill_inputs(args)
        except (OSError, ValueError) as exc:
            parser.error(f"--fill-misses: cannot read the manifest or the cassette: {exc}")
        drift = settings_drift(recorded.get("settings"))
        if drift:
            print(f"WARNING: these settings differ from the recording ({', '.join(drift)}): answers keyed on them "
                  "may not be the ones the replay asks for")
        print(f"rows {', '.join(str(r['row_number']) for r in rows) or '-'} from the misses manifest "
              f"{args.fill_misses}, with the cassette's own rows and brand mappings")
    elif args.rows_file:
        try:
            rows = read_rows_file(args.rows_file, parse_rows(args.rows) if args.rows else None)
            mappings = read_brands_file(args.brands_file) if args.brands_file else {}
        except (OSError, ValueError, KeyError) as exc:
            parser.error(f"cannot read the rows / brands file: {exc}")
        print(f"rows from {args.rows_file} | brand mappings: "
              f"{args.brands_file + ' (' + str(len(mappings)) + ' brands)' if args.brands_file else 'none'}")
    else:
        spreadsheet, worksheet = open_sheet_read_only()
        rows = read_sheet_rows(worksheet, parse_rows(args.rows))
        mappings = read_brands_file(args.brands_file) if args.brands_file else read_brand_mappings(spreadsheet)
    expansion = False if args.no_expansion else True
    if args.fill_misses:
        # the cassette's mappings already hold what the reviewers had taught at recording time
        expansion = bool(((recorded.get("run") or {}).get("expansion", expansion)))
    else:
        # what the reviewers taught, as the worker sees it (google_sheets.get_brand_mappings); none without a database
        from catalog_match import learning
        mappings = learning.load_and_apply(mappings)
    for line in mapping_report(rows, mappings, identity):
        print(line)
    meta = run_meta(args, settings, pipeline, expansion)
    print(f"dry run on {len(rows)} rows | Serper key {'set' if settings.serper_api_key() else 'MISSING'} | "
          f"Gemini key {'set' if settings.gemini_api_key() else 'MISSING'} | model {meta['gemini_model'] or '-'} | "
          f"auto-publish {'ON' if settings.auto_publish_enabled() else 'off'} (not applied in a dry run)")
    print(f"label readers: primary {meta['verifier_primary'] or 'gemini:' + meta['gemini_model']} | strong "
          f"{meta['verifier_strong'] or '-'} | expansion round {meta['expansion']}")

    prices = provider_prices(args.serp_cost)
    secrets = secret_values()
    results, total = [], 0.0
    forget_brand_spellings()
    cas = start_cassette(args, rows, mappings, meta, expansion, secrets)
    after = None
    if cas is not None and args.record_shadow:
        from catalog_match import cassette as cassette_mod
        after = functools.partial(cassette_mod.shadow_record, serp_cost=args.serp_cost)   # priced like the run
    cassette_failed = None
    try:
        for row in rows:
            with (cas.row_context(row["row_number"]) if cas is not None else contextlib.nullcontext()):
                try:
                    r = run_row(row, mappings, identity, pipeline, providers_mod, verify_mod, args.serp_cost,
                                args.vlm_cost, expansion=expansion, prices=prices, secrets=secrets, after=after)
                except Exception as exc:
                    r = {"row": row["row_number"], "name": row["name"], "brand": row.get("brand", ""),
                         "error": redact(f"{type(exc).__name__}: {exc}", secrets)}
                    # The exception text can hold a request URL with a key in it: only redacted text is logged
                    # (the traceback with -v, redacted as well), never log.exception's raw traceback.
                    log.error("row %s failed: %s", row["row_number"], r["error"])
                    log.debug("row %s traceback:\n%s", row["row_number"], redact(traceback.format_exc(), secrets))
                    print(f"\n=== row {row['row_number']}: {row['name']} -> ERROR {r['error']}")
                else:
                    print_row(r)
                    total += r["cost_usd"]
            if cas is not None:
                note_not_stored(r, cas.row_report(row["row_number"]))
            results.append(r)
    finally:
        if cas is not None:
            # the paid run's results come first: a cassette that cannot be finished is reported, never raised
            try:
                finish_cassette(cas, args, results)
            except Exception as exc:
                cassette_failed = redact(f"{type(exc).__name__}: {exc}", secrets)
                log.error("the cassette %s could not be finished: %s", args.record, cassette_failed)
            finally:
                from catalog_match import cassette as cassette_final
                cassette_final.uninstall()

    decisions = {}
    for r in results:
        decisions[r.get("decision", "ERROR")] = decisions.get(r.get("decision", "ERROR"), 0) + 1
    print(f"\n{len(results)} rows | decisions {decisions} | estimated cost ${total:.4f}")
    summary = summarize(results)
    print(format_summary(summary))
    if args.json:
        doc = {"format": JSON_FORMAT, "meta": meta, "summary": summary, "rows": results}
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(redact(json.dumps(doc, ensure_ascii=False, indent=1), secrets, query_keys=False))
        print(f"results written to {args.json}")
    if cassette_failed:
        print(f"WARNING: the cassette {args.record} could not be finished ({cassette_failed}). Its recorded answers "
              "are kept; if the message names a .tmp file, rename it to meta.json before zipping the folder.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
