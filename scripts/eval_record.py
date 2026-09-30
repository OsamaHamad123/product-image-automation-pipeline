"""Record a real golden set from the live sheet, for offline replay (evaluation layer 3).

Run on the owner's machine, which has the Serper and Gemini keys. It is never run in CI.

    python scripts/eval_record.py --rows 2-201                 # record 200 sheet rows
    python scripts/eval_record.py --import-labels tests/eval/fixtures/recorded/2026-10-02/labels.csv

What it does per sheet row (the sheet is only read, never written):
  1. builds the SKU spec and the deterministic query plan (catalog_match.identity / query_plan);
  2. runs every configured provider for every planned query and saves what they returned;
  3. downloads the candidates (catalog_match.fetch) and stores each image once, by sha256, in blobs/;
  4. asks the real verifier (catalog_match.verify) about the downloaded images, 4 per call, and
     stores its readings in the cassette schema.

Output folder tests/eval/fixtures/recorded/<date>/:
  golden_skus.json   same schema as the committed fixture; image_file instead of image_recipe; label empty
  vlm_cassette.json  same schema as the committed cassette
  provider_log.json  every provider call with its health (status, HTTP code, latency, error)
  blobs/<sha256>.<ext>
  labels.csv         one row per candidate for staff to fill in the label column (UTF-8, opens in Excel)

Staff labels each candidate with one of: correct_exact, wrong_variant, wrong_size, wrong_pack, wrong_brand,
wrong_product, not_packshot, unusable (curation is_selected is never used as a label). --import-labels then
writes the labels into golden_skus.json, and the set replays offline with:

    python scripts/eval_report.py --engine v2 --golden <dir>/golden_skus.json --cassette <dir>/vlm_cassette.json
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

log = logging.getLogger("eval_record")

LABELS = ("correct_exact", "wrong_variant", "wrong_size", "wrong_pack", "wrong_brand", "wrong_product",
          "not_packshot", "unusable")
CSV_FIELDS = ("sku_id", "row_number", "name", "name_ar", "brand", "size", "barcode", "candidate_id", "provider",
              "query_kind", "rank", "title", "page_title", "page_url", "image_url", "image_file", "width", "height",
              "download", "vlm_decision", "label", "notes")
EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "BMP": "bmp", "TIFF": "tif"}
FETCH_TO_DOWNLOAD = {"http_403": "403", "not_image": "html"}
VERDICT_FIELDS = ("brand_text", "variant_text", "size_text", "pack_count", "view", "brand_match", "variant_match",
                  "size_match")


def _sku_id(row, spec):
    return f"rec-{row['row_number']:05d}-{(spec.sku_key or 'nokey')[:16]}"


def _query_kind(planned):
    qid = getattr(planned, "query_id", "")
    return "gtin" if qid == "Q4" else "text"


def _blob(data, out_dir):
    from PIL import Image
    import io

    sha = hashlib.sha256(data).hexdigest()
    with Image.open(io.BytesIO(data)) as im:
        fmt = (im.format or "JPEG").upper()
    rel = Path("blobs") / f"{sha}.{EXT.get(fmt, 'img')}"
    target = out_dir / rel
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return rel.as_posix(), sha, "image/" + ("jpeg" if fmt == "JPEG" else fmt.lower())


def _bytes(fetched):
    data = fetched.path_or_bytes
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    return Path(str(data)).read_bytes()


def record_row(row, mappings, stages, out_dir, provider_log):
    identity, query_plan, providers_mod, fetch_mod, verify_mod = stages
    spec = identity.build_sku_spec({k: row.get(k, "") for k in ("name", "name_ar", "brand", "brand_ar", "barcode",
                                                                 "category", "size")}, mappings)
    sku_id = _sku_id(row, spec)
    providers = providers_mod.default_providers()
    planned = query_plan.build_queries(spec)
    found = {}                                   # image_url -> candidate dict
    order = []

    def add(cand, kind):
        key = cand.image_url
        if key not in found:
            found[key] = {"cand": cand, "kinds": {kind}}
            order.append(key)
        else:
            found[key]["kinds"].add(kind)

    for provider in providers:
        if hasattr(provider, "lookup"):
            result = provider.lookup(spec)
            provider_log.append({"sku_id": sku_id, "provider": provider.name, "query": "lookup",
                                 **{k: v for k, v in vars(result.health()).items() if k != "provider"}})
            for cand in result.candidates:
                add(cand, "gtin")
            continue
        for q in planned:
            hint = tuple(getattr(q, "providers_hint", ()) or ())
            if hint and provider.name not in hint:
                continue
            result = provider.search(q.text, q.hl, spec)
            provider_log.append({"sku_id": sku_id, "provider": provider.name, "query": q.text, "query_id": q.query_id,
                                 "status": result.status, "http_status": result.http_status,
                                 "latency_ms": result.latency_ms, "error": result.error, "count": len(result.candidates)})
            for cand in result.candidates:
                add(cand, _query_kind(q))

    cands = [found[k]["cand"] for k in order]
    fetcher = fetch_mod.HttpFetcher()
    fetched = []
    for start in range(0, len(cands), 8):     # the fetcher takes the top 8 of what it is given; record them all
        fetched.extend(fetcher.fetch(cands[start:start + 8], spec))
    fetched_by_url = {f.candidate.image_url: f for f in fetched}
    verdicts = {}
    ok_images = [f for f in fetched if f.ok]
    verifier = verify_mod.GeminiVerifier()
    for start in range(0, len(ok_images), 4):
        batch = ok_images[start:start + 4]
        result = verifier.verify(spec, batch)
        for v in result.verdicts:
            if 0 <= v.index < len(batch):
                verdicts[batch[v.index].candidate.image_url] = v

    candidates, cassette = [], {}
    for i, url in enumerate(order, 1):
        cand, kinds = found[url]["cand"], found[url]["kinds"]
        cid = f"c{i}"
        f = fetched_by_url.get(url)
        entry = {
            "id": cid, "provider": cand.provider, "rank": cand.rank, "image_url": cand.image_url,
            "page_url": cand.page_url, "title": cand.title, "page_title": cand.page_title, "snippet": cand.snippet,
            "domain": cand.domain, "width": cand.width, "height": cand.height, "gtin_on_page": cand.gtin_on_page,
            "surfaced_by": sorted(kinds), "image_recipe": None, "label": "",
            "download": "not_fetched" if f is None else ("ok" if f.ok else FETCH_TO_DOWNLOAD.get(f.error, f.error or "error")),
        }
        if f is not None and f.ok:
            rel, sha, mime = _blob(_bytes(f), out_dir)
            entry.update(image_file=rel, image_sha256=sha, mime=mime)
            v = verdicts.get(url)
            if v is not None:
                cassette[cid] = {k: getattr(v, k) for k in VERDICT_FIELDS}
                cassette[cid]["recorded_decision"] = v.decision
        candidates.append(entry)
    sku = {
        "id": sku_id, "stratum": "recorded", "named_case": None, "named_case_title": None,
        "name_en": row.get("name", ""), "name_ar": row.get("name_ar", ""), "brand": row.get("brand", ""),
        "brand_ar": row.get("brand_ar", ""), "barcode": row.get("barcode", ""), "category": row.get("category", ""),
        "size": row.get("size", ""), "no_correct_candidate": None, "expected_v2": None,
        "row_number": row["row_number"], "sku_key": spec.sku_key, "candidates": candidates,
    }
    return sku, cassette


def write_labelling_csv(golden, cassette, path):
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for sku in golden["skus"]:
            for c in sku["candidates"]:
                v = cassette["verdicts"].get(sku["id"], {}).get(c["id"], {})
                writer.writerow({
                    "sku_id": sku["id"], "row_number": sku.get("row_number"), "name": sku["name_en"],
                    "name_ar": sku["name_ar"], "brand": sku["brand"], "size": sku["size"], "barcode": sku["barcode"],
                    "candidate_id": c["id"], "provider": c["provider"], "query_kind": ",".join(c["surfaced_by"]),
                    "rank": c["rank"], "title": c["title"], "page_title": c["page_title"], "page_url": c["page_url"],
                    "image_url": c["image_url"], "image_file": c.get("image_file", ""), "width": c["width"],
                    "height": c["height"], "download": c["download"], "vlm_decision": v.get("recorded_decision", ""),
                    "label": c.get("label", ""), "notes": "",
                })


def import_labels(csv_path):
    """Merge a filled-in labels.csv back into the golden_skus.json next to it."""
    csv_path = Path(csv_path)
    golden_path = csv_path.parent / "golden_skus.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    by_sku = {s["id"]: s for s in golden["skus"]}
    problems, applied = [], 0
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        for line in csv.DictReader(fh):
            label = (line.get("label") or "").strip()
            if not label:
                continue
            if label not in LABELS:
                problems.append(f"{line['sku_id']}/{line['candidate_id']}: unknown label {label!r}")
                continue
            sku = by_sku.get(line["sku_id"])
            cand = next((c for c in (sku or {}).get("candidates", []) if c["id"] == line["candidate_id"]), None)
            if cand is None:
                problems.append(f"{line['sku_id']}/{line['candidate_id']}: not in golden_skus.json")
                continue
            cand["label"] = label
            if line.get("notes"):
                cand["note"] = line["notes"]
            applied += 1
    for sku in golden["skus"]:
        labels = [c["label"] for c in sku["candidates"]]
        if labels and all(labels):
            sku["no_correct_candidate"] = "correct_exact" not in labels
        elif not labels:
            sku["no_correct_candidate"] = True
    unlabelled = sum(1 for s in golden["skus"] for c in s["candidates"] if not c["label"])
    golden_path.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{applied} labels applied, {unlabelled} candidates still unlabelled, {len(problems)} problems")
    for p in problems:
        print("  " + p)
    if unlabelled:
        print("replay only after every candidate is labelled; unlabelled SKUs would count as wrong")
    return 1 if problems else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", action="append", help="sheet rows to record, e.g. 2-201 (repeatable)")
    parser.add_argument("--out", help="output folder (default tests/eval/fixtures/recorded/<today>)")
    parser.add_argument("--import-labels", metavar="CSV", help="merge a filled-in labels.csv and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if args.import_labels:
        return import_labels(args.import_labels)
    if not args.rows:
        parser.error("--rows is required when recording")

    import smoke_live   # read-only sheet access shared with the live smoke test
    try:
        from catalog_match import fetch, identity, providers, query_plan, settings, verify
    except ImportError as exc:
        raise SystemExit(f"catalog_match is not complete on this checkout ({exc}); merge WP-2..WP-4 first")
    if not settings.serper_api_key() or not settings.gemini_api_key():
        print("warning: SERPER_API_KEY or GEMINI_API_KEY is missing; the recording will not be representative")

    out_dir = Path(args.out or Path(REPO_ROOT) / "tests" / "eval" / "fixtures" / "recorded" / dt.date.today().isoformat())
    out_dir.mkdir(parents=True, exist_ok=True)
    spreadsheet, worksheet = smoke_live.open_sheet_read_only()
    rows = smoke_live.read_sheet_rows(worksheet, smoke_live.parse_rows(args.rows))
    mappings = smoke_live.read_brand_mappings(spreadsheet)
    (out_dir / "brand_mappings.json").write_text(json.dumps(
        {"version": 1, "description": "Brands Mapping tab at recording time", "mappings": mappings},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    stages = (identity, query_plan, providers, fetch, verify)
    golden = {"version": 1, "description": f"Recorded from sheet rows {args.rows} on {dt.date.today().isoformat()}",
              "labels": list(LABELS), "named_cases": {}, "skus": []}
    cassette = {"version": 1, "description": "Verifier readings recorded with the live model",
                "schema": list(VERDICT_FIELDS), "model": settings.gemini_model(),
                "scenarios": {"gemini_down": {"decision": "UNKNOWN"}}, "verdicts": {}}
    provider_log = []
    for i, row in enumerate(rows, 1):
        try:
            sku, verdicts = record_row(row, mappings, stages, out_dir, provider_log)
        except Exception:
            log.exception("row %s could not be recorded", row["row_number"])
            continue
        golden["skus"].append(sku)
        cassette["verdicts"][sku["id"]] = verdicts
        print(f"{i}/{len(rows)} row {row['row_number']}: {len(sku['candidates'])} candidates, "
              f"{len(verdicts)} verdicts")

    for name, doc in (("golden_skus.json", golden), ("vlm_cassette.json", cassette),
                      ("provider_log.json", provider_log)):
        (out_dir / name).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    write_labelling_csv(golden, cassette, out_dir / "labels.csv")
    print(f"recorded {len(golden['skus'])} SKUs into {out_dir}; hand labels.csv to staff, then run "
          f"--import-labels {out_dir / 'labels.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
