"""Live dry run of the v2 image search on real sheet rows. Never writes to the sheet.

Usage (on the machine with the keys, from the repository root):

    python scripts/smoke_live.py --rows 2-31 --dry-run
    python scripts/smoke_live.py --rows 5 --rows 40-45 --dry-run --json smoke.json

For every row it prints the planned queries, the health of each provider call,
the top-5 candidates with their identity evidence, the VLM verdicts, the final
decision, the pick's review warnings, and the estimated cost of the run. Use it
on ~30 rows before switching the live sheet to SEARCH_ENGINE=v2 (evaluation layer 4).

This replaces scripts/verify_image_search.py, which counted "any image
returned" as success.
"""

import argparse
import json
import logging
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

log = logging.getLogger("smoke_live")

# Serper is about $1 per 1k queries (D7); a comparative Gemini call about $0.001 (D6: ~$0.002 per SKU).
DEFAULT_SERP_COST = 0.001
DEFAULT_VLM_COST = 0.001

WRITE_METHODS = ("update", "update_cell", "update_cells", "batch_update", "append_row", "append_rows", "insert_row",
                 "insert_rows", "delete_rows", "clear", "add_worksheet", "del_worksheet", "format", "update_acell")


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


def load_v2():
    """The catalog_match stages; a clear message when a stage is not merged yet."""
    try:
        from catalog_match import identity, pipeline, providers, settings, verify
    except ImportError as exc:
        raise SystemExit(f"catalog_match is not complete on this checkout ({exc}); merge WP-2..WP-4 first")
    return identity, pipeline, providers, settings, verify


class CountingProvider:
    """Delegates to a real provider and records every call and its health."""

    def __init__(self, inner, log_calls):
        self._inner, self._log = inner, log_calls
        self.name = inner.name
        self.sanctioned = inner.sanctioned

    def search(self, query, hl, spec):
        t0 = time.perf_counter()
        result = self._inner.search(query, hl, spec)
        self._log.append({"provider": self.name, "query": query, "hl": hl, "status": result.status,
                          "http_status": result.http_status, "count": len(result.candidates),
                          "ms": int(1000 * (time.perf_counter() - t0)), "error": result.error})
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
    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    def verify(self, spec, images):
        self.calls += 1
        return self._inner.verify(spec, images)


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


def run_row(row, mappings, identity, pipeline, providers_mod, verify_mod, serp_cost, vlm_cost):
    calls = []
    providers = [CountingProvider(p, calls) for p in providers_mod.default_providers()]
    verifier = CountingVerifier(verify_mod.GeminiVerifier())
    spec = identity.build_sku_spec({k: row.get(k, "") for k in ("name", "name_ar", "brand", "brand_ar", "barcode",
                                                                 "category", "size")}, mappings)
    t0 = time.perf_counter()
    outcome = pipeline.find_product_image(spec, providers=providers, verifier=verifier)
    seconds = time.perf_counter() - t0
    serp_calls = sum(1 for c in calls if c["provider"] != "off" and c["query"] != "lookup")
    cost = serp_calls * serp_cost + verifier.calls * vlm_cost
    top = [_describe(rc, i) for i, rc in enumerate(outcome.ranked[:5], 1)]
    # The pick is always reported, also when it ranks below the top 5 (live run: row 34's pick was invisible).
    winner = None
    if outcome.winner is not None:
        position = next(i for i, rc in enumerate(outcome.ranked, 1) if rc is outcome.winner)
        winner = _describe(outcome.winner, position)
    return {"row": row["row_number"], "name": row["name"], "brand": row["brand"], "sku_key": spec.sku_key,
            "brand_conf": spec.brand_conf, "gtin_status": spec.gtin_status, "variants": dict(spec.variants),
            "queries": list(outcome.queries), "provider_calls": calls, "decision": outcome.decision,
            "failure_code": outcome.failure_code,
            "winner": outcome.winner.candidate.image_url if outcome.winner else None, "winner_detail": winner,
            "warnings": winner["warnings"] if winner else [],
            "top": top, "reject_counts": dict(outcome.reject_counts), "vlm_calls": verifier.calls,
            "serp_calls": serp_calls, "cost_usd": round(cost, 4), "seconds": round(seconds, 1)}


def _describe(rc, position):
    from catalog_match.decide import warning_codes

    v = rc.verdict
    return {
        "rank": position, "status": rc.status, "reasons": list(rc.reasons), "warnings": warning_codes(rc.reasons),
        "provider": rc.candidate.provider, "domain": rc.candidate.domain,
        "title": rc.candidate.title or rc.candidate.page_title,
        "image_url": rc.candidate.image_url, "page_url": rc.candidate.page_url, "evidence": _evidence(rc),
        "vlm": None if v is None else {"decision": v.decision, "view": v.view, "brand": v.brand_text,
                                       "variant": v.variant_text, "size": v.size_text},
    }


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
    for q in r["queries"]:
        print(f"  query  {q}")
    for c in r["provider_calls"]:
        print(f"  health {c['provider']:10s} {c['status']:8s} http={c['http_status']} n={c['count']} "
              f"{c['ms']}ms {c['error'] or ''}  <- {c['query'][:70]}")
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
    print(f"  cost ~${r['cost_usd']:.4f} ({r['serp_calls']} SERP queries, {r['vlm_calls']} VLM calls), {r['seconds']}s")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", action="append", required=True, help="sheet rows, e.g. 2-31 (repeatable)")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="the only mode: nothing is written to the sheet, cache or queue")
    parser.add_argument("--json", help="also write the full results to this JSON file")
    parser.add_argument("--serp-cost", type=float, default=DEFAULT_SERP_COST, help="USD per SERP query")
    parser.add_argument("--vlm-cost", type=float, default=DEFAULT_VLM_COST, help="USD per VLM call")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    identity, pipeline, providers_mod, settings, verify_mod = load_v2()
    spreadsheet, worksheet = open_sheet_read_only()
    rows = read_sheet_rows(worksheet, parse_rows(args.rows))
    mappings = read_brand_mappings(spreadsheet)
    print(f"dry run on {len(rows)} rows | Serper key {'set' if settings.serper_api_key() else 'MISSING'} | "
          f"Gemini key {'set' if settings.gemini_api_key() else 'MISSING'} | model {settings.gemini_model()} | "
          f"auto-publish {'ON' if settings.auto_publish_enabled() else 'off'} (not applied in a dry run)")

    results, total = [], 0.0
    for row in rows:
        try:
            r = run_row(row, mappings, identity, pipeline, providers_mod, verify_mod, args.serp_cost, args.vlm_cost)
        except Exception as exc:
            log.exception("row %s failed", row["row_number"])
            r = {"row": row["row_number"], "name": row["name"], "error": f"{type(exc).__name__}: {exc}"}
            print(f"\n=== row {row['row_number']}: {row['name']} -> ERROR {r['error']}")
        else:
            print_row(r)
            total += r["cost_usd"]
        results.append(r)

    decisions = {}
    for r in results:
        decisions[r.get("decision", "ERROR")] = decisions.get(r.get("decision", "ERROR"), 0) + 1
    print(f"\n{len(results)} rows | decisions {decisions} | estimated cost ${total:.4f}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=1)
        print(f"results written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
