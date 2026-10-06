"""Preview the reading of abbreviated sheet names (catalog_match.normalizer) on real rows. Read only; no search.

Usage (on the machine with the keys, from the repository root):

    python scripts/preview_normalizer.py --rows 20                   # the first 20 product rows of the sheet
    python scripts/preview_normalizer.py --rows 20 --start 50        # 20 rows from sheet row 50
    python scripts/preview_normalizer.py --rows 30 --rows-file runs/2026-10-03/rows_2_61.csv \
        --brands-file runs/2026-10-03/brands_mapping_suggested.csv  # without the Google Sheet
    python scripts/preview_normalizer.py --rows 20 --json preview.json

For every row it prints the sheet name and brand, what the model read (brand, product type, variant, size, pack, the
name written out, its confidence), today's queries and the queries with the reading:
    N1  takes the place of Q3 (the same UAE stores) when the reading writes out a word of the sheet name;
    NB  the model's brand guess, tried only when the search finds no listing that names the sheet brand (or the sheet
        has none), in the place of the last relaxation; a picture found that way always waits for review.
The run ends with the calls made, the readings that came from the cache (free), the tokens and the estimated cost.

It never writes to the Google Sheet and never searches (no Serper credit is spent). Each product read for the first
time costs one Gemini Flash-Lite call (about $0.0002) and is kept in the database cache, so the worker will not pay
for it again (--no-cache: neither read nor written). It runs whatever QUERY_NORMALIZER says, so the owner can look
before switching it on; it needs the Gemini key of the settings or .env, and stops at
QUERY_NORMALIZER_RUN_BUDGET_USD (or --max-usd).
"""

import argparse
import json
import logging
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
if SCRIPTS not in sys.path:
    # appended, never first: scripts/ has modules named like the repository's own (publish_check.py)
    sys.path.append(SCRIPTS)

log = logging.getLogger("preview_normalizer")

JSON_FORMAT = "preview_normalizer/1"


def _site_item(part):
    return len(part) > len("site:") and part.startswith("site:") and not any(c.isspace() or c in "()" for c in part)


def short(text):
    """A query as the owner reads it: the long site: clause at its end, '(site:a OR site:b ...)', becomes
    '(N stores)'. Parsed by hand in one pass (a regular expression with an optional ' OR ' between items
    backtracks exponentially on a long clause that does not match)."""
    start = (text or "").rfind("(site:")
    if start < 0 or not text.endswith(")"):
        return text
    parts = text[start + 1:-1].split(" OR ")
    if not all(_site_item(part) for part in parts):
        return text
    return f"{text[:start].rstrip()} ({len(parts)} stores)"


def read_rows(args, smoke):
    """(rows, brand mappings, source text): from --rows-file or the Google Sheet (read-only)."""
    wanted = list(range(args.start, args.start + args.rows))
    if args.rows_file:
        rows = smoke.read_rows_file(args.rows_file)
        rows = [r for r in rows if r["row_number"] >= args.start][:args.rows]
        mappings = smoke.read_brands_file(args.brands_file) if args.brands_file else {}
        return rows, mappings, f"rows from {args.rows_file}"
    spreadsheet, worksheet = smoke.open_sheet_read_only()
    rows = smoke.read_sheet_rows(worksheet, wanted)
    mappings = smoke.read_brands_file(args.brands_file) if args.brands_file else smoke.read_brand_mappings(spreadsheet)
    return rows, mappings, f"sheet rows {wanted[0]}-{wanted[-1]}"


def build_normalizer(args):
    from catalog_match import normalizer
    from catalog_match.verifiers.spend import MariaDbSpendStore

    cache = normalizer.MemoryNormalizerCache() if args.no_cache else normalizer.MariaDbNormalizerCache()
    return normalizer.QueryNormalizer(client=normalizer.GeminiNormalizerClient(), cache=cache,
                                      spend_store=MariaDbSpendStore(), budget=normalizer.RunBudget(),
                                      budget_usd=args.max_usd)


def preview_row(row, mappings, norm):
    """One row: the reading, today's queries and the queries with the reading."""
    from catalog_match import identity, normalizer, query_plan

    spec = identity.build_sku_spec({k: row.get(k, "") for k in ("name", "name_ar", "brand", "brand_ar", "barcode",
                                                                 "category", "size")}, mappings)
    reading = norm.normalize(spec)
    hint = normalizer.hint_of(reading)
    today = [(q.query_id, q.text) for q in query_plan.build_queries(spec)]
    relax = [(q.query_id, q.text) for q in query_plan.relaxations(spec)]
    planned = [(q.query_id, q.text) for q in query_plan.build_queries(query_plan.with_hint(spec, hint))]
    rescue = None
    if hint is not None and normalizer.needs_rescue(spec, hint, []):
        nb = query_plan.rescue_query(spec, hint, [t for _, t in planned])
        rescue = nb.text if nb is not None else None
    value = reading.value.as_dict() if reading.value is not None else None
    return {
        "row": row["row_number"], "name": spec.raw_name, "brand": spec.brand_raw or spec.brand_placeholder,
        "brand_conf": spec.brand_conf, "status": reading.status, "reading": value,
        "used": hint is not None, "today": today, "relaxations": relax, "with_reading": planned,
        "rescue": rescue, "rescue_replaces": relax[-1][0] if relax and rescue else None,
        "changed": [qid for qid, _ in planned if qid == query_plan.NORMALIZED_QUERY_ID],
        "usage": reading.usage, "ms": reading.ms,
    }


def print_row(r):
    print(f"\n=== row {r['row']}: {r['name']} | brand {r['brand'] or '-'} ({r['brand_conf']})")
    value = r["reading"]
    if value is None:
        print(f"  reading: none ({r['status']}): today's queries only")
    else:
        note = "" if r["used"] else "  -> confidence too low: not used"
        print(f"  reading ({'cache' if r['status'] == 'cache' else 'new'}, confidence {value['confidence']:.2f}): "
              f"brand {value['brand'] or '-'} | {value['expanded_name']}{note}")
        print(f"           type {value['product_type'] or '-'} | variant {value['variant'] or '-'} | "
              f"size {value['size'] or '-'} | pack {value['pack'] or '-'}")
    print("  today:")
    for qid, text in r["today"]:
        print(f"    {qid:3s} {short(text)}")
    for qid, text in r["relaxations"]:
        print(f"    {qid:3s} {short(text)}   (only when nothing is found)")
    print("  with the reading:" + ("" if r["changed"] or r["rescue"] else " the same"))
    if r["changed"] or r["rescue"]:
        for qid, text in r["with_reading"]:
            print(f"    {qid:3s} {short(text)}" + ("   <- new" if qid in r["changed"] else ""))
        if r["rescue"]:
            print(f"    NB  {r['rescue']}   <- only when no listing names the brand"
                  + (f", in {r['rescue_replaces']}'s place" if r["rescue_replaces"] else "")
                  + "; its picture always waits for review")


def summarize(results):
    calls = [r["usage"] for r in results if r.get("usage")]
    new = sum(1 for r in results if r.get("status") == "ok")
    usd = sum(float(u.get("usd") or 0.0) for u in calls)
    return {
        "rows": len(results), "new_readings": new,
        "cached": sum(1 for r in results if r.get("status") == "cache"),
        "not_read": {s: sum(1 for r in results if r.get("status") == s) for s in sorted(
            {r.get("status") for r in results if r.get("status") not in ("ok", "cache")})},
        "billed_calls": len(calls),
        "input_tokens": sum(int(u.get("input_tokens") or 0) for u in calls),
        "output_tokens": sum(int(u.get("output_tokens") or 0) for u in calls),
        "usd": round(usd, 6), "usd_per_new_reading": round(usd / new, 6) if new else None,
        "n1_rows": sum(1 for r in results if r.get("changed")),
        "rescue_rows": sum(1 for r in results if r.get("rescue")),
    }


def format_summary(s):
    lines = [f"\n{s['rows']} rows | new readings {s['new_readings']} | from the cache {s['cached']} (free)"
             + (f" | not read {s['not_read']}" if s["not_read"] else ""),
             f"N1 in Q3's place on {s['n1_rows']} rows | brand-guess query ready on {s['rescue_rows']} rows",
             f"billed calls {s['billed_calls']} | tokens in {s['input_tokens']}, out {s['output_tokens']} | "
             f"estimated cost ${s['usd']:.4f}"
             + (f" (${s['usd_per_new_reading']:.5f} per new reading)" if s["usd_per_new_reading"] else "")]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rows", type=int, default=20, help="how many product rows to preview (default 20)")
    parser.add_argument("--start", type=int, default=2, help="the first sheet row (row 1 is the header)")
    parser.add_argument("--rows-file", help="read the rows from a CSV or an earlier --json run instead of the sheet")
    parser.add_argument("--brands-file", help="a Brands Mapping CSV (with --rows-file; else the sheet's tab)")
    parser.add_argument("--no-cache", action="store_true", help="neither read nor write the database cache")
    parser.add_argument("--max-usd", type=float, default=None,
                        help="stop calling the model past this estimated cost (default QUERY_NORMALIZER_RUN_BUDGET_USD)")
    parser.add_argument("--json", help="also write the rows and the summary to this JSON file")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.rows < 1 or args.start < 2:
        parser.error("--rows must be 1 or more and --start 2 or more (row 1 is the header)")
    if args.json:
        target = os.path.abspath(args.json)
        if os.path.isdir(target) or not os.path.isdir(os.path.dirname(target)):
            parser.error(f"--json {args.json}: give a file name in a folder that exists")

    import smoke_live as smoke
    from catalog_match import normalizer, settings

    smoke._utf8_stdout()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    norm = build_normalizer(args)
    if not str(norm.client.api_key or "").strip():
        print("No Gemini key: add it in the dashboard (Settings, «المفاتيح») or GEMINI_API_KEY in .env, then run again.")
        return 2
    try:
        rows, mappings, source = read_rows(args, smoke)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(f"cannot read the rows / brands file: {exc}")
    from catalog_match import learning
    mappings = learning.load_and_apply(mappings)       # what the reviewers taught, as the worker sees it
    print(f"{source} | {len(rows)} products | brand mappings: {len(mappings)} | model {normalizer.MODEL} | "
          f"QUERY_NORMALIZER is {settings.query_normalizer()} | budget ${norm.budget_usd():.2f} | "
          f"about ${normalizer.estimate_usd():.5f} per new reading")
    secrets = smoke.secret_values()
    results = []
    for row in rows:
        try:
            r = preview_row(row, mappings, norm)
        except Exception as exc:          # one unreadable row never stops the preview
            r = {"row": row["row_number"], "name": row.get("name", ""), "status": "error",
                 "error": smoke.redact(f"{type(exc).__name__}: {exc}", secrets)}
            print(f"\n=== row {row['row_number']}: {row.get('name', '')} -> ERROR {r['error']}")
        else:
            print_row(r)
        results.append(r)
    summary = summarize(results)
    print(format_summary(summary))
    if args.json:
        doc = {"format": JSON_FORMAT, "summary": summary, "rows": results}
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(smoke.redact(json.dumps(doc, ensure_ascii=False, indent=1), secrets, query_keys=False))
        print(f"results written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
