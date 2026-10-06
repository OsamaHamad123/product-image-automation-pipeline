"""Record a real golden set from the live sheet, for offline replay (evaluation layer 3).

Run on the owner's machine, which has the Serper and Gemini keys. It is never run in CI.

    python scripts/eval_record.py --rows 2-201                 # record 200 sheet rows
    python scripts/eval_record.py --import-labels tests/eval/fixtures/recorded/2026-10-02/labels.csv

What it does per sheet row (the sheet is only read, never written):
  1. builds the SKU spec and the deterministic query plan (catalog_match.identity / query_plan);
  2. runs every configured provider for every planned query AND the relaxations R1/R2 (no early stop,
     so the replay can decide itself when to stop) and saves what they returned. Each candidate keeps
     the ids of the queries that returned it (surfaced_by: "Q1".."Q4", "R1", "R2"; "gtin" for the
     Open Food Facts lookup), and the replay serves a candidate only to those queries: a recorded set
     measures the query plan, the early stop and the relaxations, not a merged pool;
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
writes the labels into golden_skus.json, and the set replays offline (with the vlm_cassette.json and the
brand_mappings.json recorded next to it) with:

    python scripts/eval_report.py --engine v2 --golden <dir>/golden_skus.json

The reviews already made in the dashboard can fill part of labels.csv first (read-only on the database):

    python scripts/eval_record.py --prefill-labels-from-db tests/eval/fixtures/recorded/2026-10-02/labels.csv

Each review decision (review_decisions) is matched to the candidates of the same product (the same
sku_key; the same sheet row when either side has no sku_key) and the same image URL (exact, else the
same host and path when that is unambiguous); candidates of that product whose downloaded file is the
same image (the same sha256 in image_file) get the same label. An approval labels the image
correct_exact; a rejection is labelled by its reason code (REVIEW_LABELS below; LOW_QUALITY -> unusable).
Cosmetic rejections (halo, background bleed, cropped margin) say nothing about the product and stay empty.
Labels already in the file are never overwritten; the rest stays empty for the owner to fill in.

One step, from what the database already holds (no search, no paid call, no sheet access; the dashboard's
«صدّر مجموعة اختبار» on the Health page runs the same through cli_bridge.py 'eval_export'):

    python scripts/eval_record.py --from-db [--zip laqta_eval_set.zip] [--max-side 384]

Every product with a review decision (review_decisions: approvals and rejections, a rejection taken back left out)
becomes one SKU: the sheet row as it was queued (automation_queue and its payload), the candidates still stored for
review (curation_candidates: the engine's pick and the alternatives, with titles, page links and domains, the
label reader's reading), plus each reviewed image and the engine's recorded pick when they are no longer stored
(an approval removes the product's candidates). Labels come from the reviews exactly as --prefill-labels-from-db
gives them (the same file gets the same label); everything else stays unlabelled for labels.csv. Each candidate's
image is the candidate store's file (temp/candidates/<sha256>.<ext>) downscaled to --max-side px into blobs/; the
replay serves it back at its recorded size. The folder is tests/eval/fixtures/recorded/<date>/ (<date>-2 ... when
that exists) with golden_skus.json, vlm_cassette.json, brand_mappings.json (the dashboard's Brands Mapping cache),
labels.csv and manifest.json (counts, what was stripped). Every configured secret value is replaced by [hidden] and
link query parameters that can carry a credential or a signed session are dropped; the database keeps no reviewer
identity. Replay it with the recorded readings (no model is called):

    python scripts/eval_report.py --engine v2 --golden tests/eval/fixtures/recorded/<date>/golden_skus.json
"""

import argparse
import csv
import datetime as dt
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

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
# Dashboard rejection reason (review_decisions.reason_code) -> label. Approvals are correct_exact.
REVIEW_LABELS = {
    "WRONG_PRODUCT": "wrong_product", "WRONG_BRAND": "wrong_brand", "WRONG_VARIANT": "wrong_variant",
    "WRONG_SIZE": "wrong_size", "WRONG_PACK": "wrong_pack", "NOT_PACKSHOT": "not_packshot",
    "LOW_QUALITY": "unusable",
    "BRAND_STYLE_MISMATCH": "wrong_brand",      # the catalog page's old name of WRONG_BRAND (cli_bridge alias)
}
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
VERDICT_FIELDS = ("brand_text", "variant_text", "size_text", "pack_count", "view", "brand_match", "variant_match",
                  "size_match")


def _sku_id(row, spec):
    return f"rec-{row['row_number']:05d}-{(spec.sku_key or 'nokey')[:16]}"


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
    # the whole plan and both relaxations, so the replay can apply its own early stop and relax rules
    planned = list(query_plan.build_queries(spec)) + list(query_plan.relaxations(spec))
    found = {}                                   # image_url -> candidate dict (first provider that returned it)
    order = []

    def add(cand, tag):
        key = cand.image_url
        if key not in found:
            found[key] = {"cand": cand, "kinds": {tag}}
            order.append(key)
        else:
            found[key]["kinds"].add(tag)

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
                add(cand, q.query_id)

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


# ---------------------------------------------------------------------------
# --prefill-labels-from-db: the dashboard's review decisions as labels
# ---------------------------------------------------------------------------

def review_label(decision):
    """The label a review decision gives its image, or None (manual upload, cosmetic or missing reason)."""
    action = str(decision.get("action") or "").strip().lower()
    if action == "approved":
        return "correct_exact"
    if action == "rejected":
        return REVIEW_LABELS.get(str(decision.get("reason_code") or "").strip().upper())
    return None


def _url_key(url):
    try:
        from catalog_match.text_norm import url_key
        return url_key(url)
    except Exception:
        return str(url or "").strip().lower()


_IMAGE_FILE_RE = re.compile(r"\.(?:jpe?g|jfif|png|webp|gif|avif|bmp|tiff?)$", re.IGNORECASE)


def _near_key(url):
    """host + path of an image URL whose path names the image file, else '' (then only the exact URL matches).

    Only then is the query string a size or cache parameter; behind an image proxy ('/_next/image?url=...',
    'img.php?id=...') the query names the image, and host + path would match another image.
    """
    url = str(url or "").strip()
    try:
        path = urlsplit(url).path
    except ValueError:
        return ""
    return _url_key(url) if _IMAGE_FILE_RE.search(path.rstrip("/")) else ""


def _file_sha(line):
    stem = os.path.basename(str(line.get("image_file") or "")).split(".", 1)[0].lower()
    return stem if _SHA_RE.match(stem) else ""


def _row_text(value):
    try:
        return str(int(str(value).strip()))
    except (TypeError, ValueError):
        return ""


def load_review_decisions():
    """Every review decision of the dashboard's database, oldest first (read-only)."""
    try:
        import config  # noqa: F401  (.env: the database the dashboard uses)
    except Exception:
        pass
    import local_cache_db

    return local_cache_db.get_review_decisions()


def _same_product(decision, sku_key, row_number):
    key = str(decision.get("sku_key") or "").strip()
    if key and sku_key:
        return key == sku_key
    return bool(row_number) and _row_text(decision.get("row_number")) == row_number


def prefill_labels_from_db(csv_path, decisions=None):
    """Fill the empty labels of labels.csv from the dashboard's review decisions; returns the counts."""
    csv_path = Path(csv_path)
    if decisions is None:
        decisions = load_review_decisions()
    golden_path = csv_path.parent / "golden_skus.json"
    sku_keys = {}
    if golden_path.exists():
        golden = json.loads(golden_path.read_text(encoding="utf-8"))
        sku_keys = {s.get("id"): str(s.get("sku_key") or "").strip() for s in golden.get("skus", [])}
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or CSV_FIELDS)
        lines = list(reader)

    products = {}                                   # sku_id -> {"sku_key", "row", "lines"}
    for line in lines:
        entry = products.setdefault(line.get("sku_id"), {"sku_key": sku_keys.get(line.get("sku_id"), ""),
                                                         "row": _row_text(line.get("row_number")), "lines": []})
        entry["lines"].append(line)

    counts = {"decisions": len(decisions or []), "usable": 0, "matched": 0, "unmatched": 0, "ambiguous": 0,
              "not_mapped": 0, "conflicts": 0}
    suggested = {}                                  # id(line) -> (label, why)
    ordered = sorted(enumerate(decisions or []),
                     key=lambda p: (str(p[1].get("created_at") or ""), int(_row_text(p[1].get("id")) or 0), p[0]))
    for _i, decision in ordered:
        if not decision.get("image_url"):
            continue
        label = review_label(decision)
        if label is None:
            counts["not_mapped"] += 1
            continue
        counts["usable"] += 1
        url = str(decision["image_url"]).strip()
        hits, ambiguous = [], False
        for product in products.values():
            if not _same_product(decision, product["sku_key"], product["row"]):
                continue
            exact = [ln for ln in product["lines"] if str(ln.get("image_url") or "").strip() == url]
            if not exact:
                key = _near_key(url)
                near = [ln for ln in product["lines"] if key and _near_key(ln.get("image_url")) == key]
                if len({str(ln.get("image_url") or "").strip() for ln in near}) > 1:
                    ambiguous = True        # host + path match several different images: never guess
                    continue
                exact = near
            hits.extend(exact)
        if not hits:
            counts["ambiguous" if ambiguous else "unmatched"] += 1
            continue
        counts["matched"] += 1
        action = str(decision.get("action")).strip().lower()
        why = "approved" if action == "approved" else f"rejected {str(decision.get('reason_code')).strip().upper()}"
        for line in hits:
            previous = suggested.get(id(line))
            if previous is not None and previous[0] != label:
                counts["conflicts"] += 1        # reviewed twice with different outcomes: the later one counts
            suggested[id(line)] = (label, why)

    # The same downloaded file (sha256) of the same product is the same image: it gets the same label,
    # when one of its copies was reviewed and every label known for that file agrees (the dashboard's
    # and the owner's own labels in the file: a label the owner set on one copy is never contradicted).
    same_file = {}
    for product in products.values():
        by_sha = {}                                 # sha -> {"reviewed": bool, "labels": set}
        for line in product["lines"]:
            sha = _file_sha(line)
            if not sha:
                continue
            group = by_sha.setdefault(sha, {"reviewed": False, "labels": set()})
            current = (line.get("label") or "").strip()
            if current:
                group["labels"].add(current)
            if id(line) in suggested:
                group["reviewed"] = True
                group["labels"].add(suggested[id(line)][0])
        for line in product["lines"]:
            group = by_sha.get(_file_sha(line))
            if id(line) not in suggested and group and group["reviewed"] and len(group["labels"]) == 1:
                same_file[id(line)] = (next(iter(group["labels"])), "same image file as a reviewed candidate")
    suggested.update(same_file)

    result = {"prefilled": 0, "approved": 0, "rejected": 0, "same_file": 0, "kept": 0, "disagree": [],
              "empty": 0}
    for line in lines:
        hit = suggested.get(id(line))
        current = (line.get("label") or "").strip()
        if hit is None:
            result["empty"] += 0 if current else 1
            continue
        label, why = hit
        if current:
            result["kept"] += 1
            if current != label:
                result["disagree"].append(f"{line.get('sku_id')}/{line.get('candidate_id')}: file says {current}, "
                                          f"dashboard says {label} ({why})")
            continue
        line["label"] = label
        if not (line.get("notes") or "").strip():
            line["notes"] = f"prefilled from dashboard review: {why}"
        result["prefilled"] += 1
        bucket = ("same_file" if id(line) in same_file else "approved" if label == "correct_exact" else "rejected")
        result[bucket] += 1

    fd, tmp = tempfile.mkstemp(prefix=".labels-", suffix=".csv", dir=str(csv_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(lines)
        os.replace(tmp, csv_path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    result.update(counts)
    return result


def print_prefill(result, csv_path):
    print(f"{result['prefilled']} labels prefilled from dashboard reviews ({result['approved']} approved -> "
          f"correct_exact, {result['rejected']} from rejections, {result['same_file']} same image file) in {csv_path}")
    print(f"{result['empty']} candidates are still empty for you to label; {result['kept']} labels already in the "
          f"file were kept")
    print(f"review decisions read {result['decisions']}: {result['matched']} matched this recording, "
          f"{result['unmatched']} are for other products or images, {result['ambiguous']} matched several images "
          f"(left empty), {result['not_mapped']} carry no product label (manual uploads, cosmetic rejections)")
    if result["conflicts"]:
        print(f"{result['conflicts']} images were reviewed twice with different outcomes: the later review was used")
    if result["disagree"]:
        print(f"{len(result['disagree'])} labels in the file differ from the dashboard review (kept, please check):")
        for text in result["disagree"][:20]:
            print("  " + text)


def run_prefill(csv_path):
    """--prefill-labels-from-db: plain sentences (exit 1) for a database that is down or a locked file."""
    if not os.path.isfile(csv_path):
        print(f"{csv_path} was not found: give the labels.csv of a recording folder", file=sys.stderr)
        return 1
    try:
        decisions = load_review_decisions()
    except Exception as exc:
        print(f"could not read the dashboard's review decisions ({type(exc).__name__}): is MariaDB running, and "
              "is DB_DATABASE in .env the dashboard's database? labels.csv was not changed", file=sys.stderr)
        return 1
    try:
        result = prefill_labels_from_db(csv_path, decisions=decisions)
    except PermissionError:
        print(f"could not write {csv_path}: close it in Excel (or any program that has it open) and run again; "
              "it was not changed", file=sys.stderr)
        return 1
    print_prefill(result, csv_path)
    return 0


# ---------------------------------------------------------------------------
# --from-db: the reviewed products of the dashboard's database as a labelled set, in one step
# ---------------------------------------------------------------------------

DB_SET_MAX_SIDE = 384               # blobs are downscaled to this long side: a set of a few hundred products stays small
DB_SET_JPEG_QUALITY = 82
DB_SET_PREFIX = "db"
RECORDED_DIR = Path(REPO_ROOT) / "tests" / "eval" / "fixtures" / "recorded"
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".tif", ".tiff", "")
# query parameters that can carry a credential or a signed session in an image or page link
_SECRET_QUERY_KEYS = frozenset({
    "key", "api_key", "apikey", "token", "access_token", "auth", "authorization", "sig", "signature", "secret",
    "client_secret", "cx", "session", "sessionid", "x-amz-signature", "x-amz-credential", "x-amz-security-token",
    "x-goog-signature", "x-goog-credential", "expires", "x-amz-expires", "policy", "key-pair-id",
})


class DbReader:
    """Read-only access to the dashboard's database for --from-db (tests pass their own object with these methods)."""

    def review_decisions(self):
        return load_review_decisions()

    def _select(self, sql):
        import local_cache_db

        conn = local_cache_db.get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(sql)
            return [dict(r) for r in cursor.fetchall()]
        finally:
            local_cache_db._close(conn)

    def queue_rows(self):
        return self._select(
            "SELECT id, `row_number`, barcode, product_name, brand, payload_json, sku_key, alt_sku_key, status, "
            "failure_code, trace_json, searched_at FROM automation_queue ORDER BY id")

    def curation_rows(self):
        return self._select(
            "SELECT id, `row_number`, product_name, brand, image_url, title, width, height, source_domain, "
            "is_selected, status, sku_key, reasons_json, evidence_json, vlm_json, content_sha256, identity_tier, "
            "page_url FROM curation_candidates ORDER BY is_selected DESC, id")


def _json_field(value, default):
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def clean_url(url):
    """The link without query parameters that could carry a credential or a signed session."""
    url = str(url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    from urllib.parse import parse_qsl, urlencode, urlunsplit

    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _SECRET_QUERY_KEYS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def _secret_values():
    try:
        import smoke_live
        return [v for v in smoke_live.secret_values() if v]
    except Exception:
        return []


def _redact(doc, secrets):
    """Every configured secret value replaced by [hidden], wherever a string carries it."""
    if not secrets:
        return doc
    if isinstance(doc, dict):
        return {k: _redact(v, secrets) for k, v in doc.items()}
    if isinstance(doc, list):
        return [_redact(v, secrets) for v in doc]
    if isinstance(doc, str):
        for value in secrets:
            if value in doc:
                doc = doc.replace(value, "[hidden]")
    return doc


def _host(url):
    try:
        return (urlsplit(str(url or "")).netloc or "").lower().replace("www.", "")
    except ValueError:
        return ""


def stored_image(store_dir, sha):
    """The candidate store's file of this sha256 (temp/candidates/<sha>.<ext>), or None."""
    sha = str(sha or "").strip().lower()
    if not _SHA_RE.match(sha) or not store_dir:
        return None
    for ext in _IMAGE_EXTS:
        path = Path(store_dir) / f"{sha}{ext}"
        if path.is_file():
            return path
    return None


def small_blob(path, out_dir, max_side=DB_SET_MAX_SIDE):
    """A copy of the image at most max_side px on its long side, in blobs/ (JPEG, PNG when it has transparency).
    Returns (relative path, mime, (width, height) of the original), or None for a file that does not decode."""
    from PIL import Image, ImageOps
    import io

    try:
        with Image.open(path) as im:
            im.load()
            im = ImageOps.exif_transpose(im)
            size = im.size
            alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            im = im.convert("RGBA" if alpha else "RGB")
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            if alpha:
                im.save(buf, format="PNG", optimize=True)
            else:
                im.save(buf, format="JPEG", quality=DB_SET_JPEG_QUALITY, optimize=True)
    except Exception:
        return None
    data = buf.getvalue()
    ext, mime = ("png", "image/png") if alpha else ("jpg", "image/jpeg")
    rel = Path("blobs") / f"{hashlib.sha256(data).hexdigest()}.{ext}"
    target = Path(out_dir) / rel
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return rel.as_posix(), mime, size


def _product_key(row):
    key = str(row.get("sku_key") or "").strip()
    return key if key else f"row:{_row_text(row.get('row_number'))}"


def _decision_label(decision):
    label = review_label(decision)
    if label is None:
        return None
    action = str(decision.get("action") or "").strip().lower()
    why = "approved" if action == "approved" else f"rejected {str(decision.get('reason_code') or '').strip().upper()}"
    return label, why


def _same_image(a, b):
    a, b = str(a or "").strip(), str(b or "").strip()
    if not a or not b:
        return False
    return a == b or (bool(_near_key(a)) and _near_key(a) == _near_key(b))


def _pick_lane(reasons):
    try:
        from catalog_match.decide import lane_of
        return lane_of(reasons or [])
    except Exception:
        return None


def build_db_product(key, decisions, queue_row, curation, store_dir, out_dir, max_side, counts):
    """One reviewed product as (golden SKU, its cassette readings)."""
    from catalog_match import explain

    trace = _json_field((queue_row or {}).get("trace_json"), {})
    outcome = trace.get("outcome") if isinstance(trace, dict) and isinstance(trace.get("outcome"), dict) else {}
    first = decisions[-1] if decisions else {}
    if queue_row:
        sheet = explain.queue_sheet_row(queue_row)
        row_number = queue_row.get("row_number")
    else:
        sheet = {"name": str(first.get("product_name") or ""), "brand": str(first.get("brand") or "")}
        row_number = first.get("row_number")
    row_text = _row_text(row_number) or "0"

    entries = []                           # (candidate dict, reading or None)
    for c in curation:
        evidence = _json_field(c.get("evidence_json"), {})
        reasons = _json_field(c.get("reasons_json"), [])
        vlm = _json_field(c.get("vlm_json"), None)
        sanctioned = bool(evidence.get("sanctioned")) if isinstance(evidence, dict) else False
        entries.append(({
            "provider": "serper" if sanctioned else "page", "image_url": clean_url(c.get("image_url")),
            "page_url": clean_url(c.get("page_url")), "title": str(c.get("title") or ""),
            "page_title": str(c.get("title") or ""),
            "domain": str(c.get("source_domain") or (evidence or {}).get("page_domain") or _host(c.get("page_url"))),
            "width": c.get("width"), "height": c.get("height"),
            "gtin_on_page": (evidence or {}).get("page_gtin") if isinstance(evidence, dict) else None,
            "recorded_status": str(c.get("status") or ""), "recorded_reasons": [str(r) for r in reasons or []],
            "identity_tier": c.get("identity_tier"), "_sha": c.get("content_sha256"),
        }, vlm if isinstance(vlm, dict) else None))
    for d in decisions:
        url = clean_url(d.get("image_url"))
        if not url or any(_same_image(e["image_url"], url) for e, _ in entries):
            continue
        entries.append(({"provider": "page", "image_url": url, "page_url": "", "title": "", "page_title": "",
                         "domain": str(d.get("page_domain") or ""), "width": None, "height": None,
                         "gtin_on_page": None, "recorded_status": "reviewed", "recorded_reasons": [],
                         "identity_tier": d.get("identity_tier"), "_sha": None},
                        {"decision": d.get("vlm_decision")} if d.get("vlm_decision") else None))
    winner = clean_url(outcome.get("winner_url"))
    if winner and not any(_same_image(e["image_url"], winner) for e, _ in entries):
        entries.append(({"provider": "page", "image_url": winner, "page_url": "", "title": "", "page_title": "",
                         "domain": _host(winner), "width": None, "height": None, "gtin_on_page": None,
                         "recorded_status": "preselected", "recorded_reasons": [], "identity_tier": None,
                         "_sha": None}, None))

    # labels: every usable review of the product, the later one wins; the same downloaded file gets the same label
    labels = {}
    for d in decisions:
        hit = _decision_label(d)
        if hit is None:
            counts["decisions_without_label"] += 1
            continue
        matched = [i for i, (e, _) in enumerate(entries) if _same_image(e["image_url"], d.get("image_url"))]
        for i in matched:
            labels[i] = hit
        counts["decisions_used"] += 1 if matched else 0
    by_sha = {}
    for i, (e, _) in enumerate(entries):
        if e["_sha"]:
            by_sha.setdefault(str(e["_sha"]).lower(), []).append(i)
    for idxs in by_sha.values():
        known = {labels[i][0] for i in idxs if i in labels}
        if len(known) == 1:
            label = next(iter(known))
            for i in idxs:
                labels.setdefault(i, (label, "same image file as a reviewed candidate"))

    sku_id = f"{DB_SET_PREFIX}-{int(row_text):05d}-{re.sub(r'[^0-9a-z]+', '', key.lower())[:16] or 'nokey'}"
    candidates, readings = [], {}
    pick = None
    for i, (e, vlm) in enumerate(entries):
        cid = f"c{i + 1}"
        sha = e.pop("_sha")
        cand = dict(e, id=cid, rank=i + 1, surfaced_by=["text"], image_recipe=None,
                    label=labels.get(i, ("", ""))[0], note=labels.get(i, ("", ""))[1])
        path = stored_image(store_dir, sha)
        blob = small_blob(path, out_dir, max_side) if path is not None else None
        if blob is not None:
            rel, mime, size = blob
            cand.update(image_file=rel, mime=mime, download="ok", recorded_size=list(size))
            cand["width"] = cand["width"] or size[0]
            cand["height"] = cand["height"] or size[1]
            counts["images"] += 1
        else:
            cand["download"] = "not_recorded"
            counts["images_missing"] += 1
        if vlm:
            fields = {k: vlm.get(k) for k in VERDICT_FIELDS if k in vlm}
            reading = dict(fields)
            if vlm.get("decision"):
                reading["recorded_decision"] = str(vlm["decision"])
            if reading:
                readings[cid] = reading
                counts["readings"] += 1
        if cand["recorded_status"] == "preselected" and pick is None:
            pick = cid
        candidates.append(cand)
        counts["candidates"] += 1
        counts["labelled"] += 1 if cand["label"] else 0
    if pick is None and winner:
        pick = next((c["id"] for c in candidates if _same_image(c["image_url"], winner)), None)
    pick_reasons = next((c["recorded_reasons"] for c in candidates if c["id"] == pick), [])
    engine_decision = outcome.get("decision") or next((d.get("engine_decision") for d in reversed(decisions)
                                                       if d.get("engine_decision")), None)
    lane = _pick_lane(pick_reasons) if pick_reasons else next(
        (d.get("lane") for d in reversed(decisions) if d.get("lane")), None)
    sku = {
        "id": sku_id, "stratum": "recorded_db", "named_case": None, "named_case_title": None,
        "name_en": sheet.get("name", ""), "name_ar": sheet.get("name_ar", ""), "brand": sheet.get("brand", ""),
        "brand_ar": sheet.get("brand_ar", ""), "barcode": str(sheet.get("barcode") or ""),
        "category": sheet.get("category", ""), "size": sheet.get("size", ""),
        "no_correct_candidate": not any(c["label"] == "correct_exact" for c in candidates), "expected_v2": None,
        "row_number": int(row_text), "sku_key": key if not key.startswith("row:") else "",
        "recorded": {"decision": engine_decision, "pick": pick if engine_decision in ("AUTO_PUBLISH",
                                                                                      "REVIEW_PRESELECTED") else None,
                     "lane": lane, "queries": list(outcome.get("queries") or []),
                     "reviews": [{"action": d.get("action"), "reason_code": d.get("reason_code"),
                                  "candidate": next((c["id"] for c in candidates
                                                     if _same_image(c["image_url"], d.get("image_url"))), None),
                                  "was_preselected": d.get("was_preselected"), "lane": d.get("lane")}
                                 for d in decisions]},
        "candidates": candidates,
    }
    return sku, readings


def _eval_metrics():
    """tests/eval/metrics.py loaded by path (the held-out split), without putting tests/eval on sys.path."""
    import importlib.util

    name = "_laqta_eval_metrics"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(REPO_ROOT) / "tests" / "eval" / "metrics.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod                      # dataclasses resolve their module through sys.modules
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


def _next_free(folder):
    folder = Path(folder)
    if not folder.exists():
        return folder
    n = 2
    while Path(f"{folder}-{n}").exists():
        n += 1
    return Path(f"{folder}-{n}")


def load_cached_mappings():
    """The Brands Mapping tab as the dashboard last read it (google_sheets' local cache), without asking Google."""
    try:
        import google_sheets
        cached = google_sheets._read_cache("brand_mappings_cache.json", float("inf"), google_sheets.BRAND_CACHE_VERSION)
        mappings = (cached or {}).get("mappings")
        return mappings if isinstance(mappings, dict) else {}
    except Exception:
        return {}


def export_from_db(out_dir=None, reader=None, store_dir=None, mappings=None, max_side=DB_SET_MAX_SIDE,
                   zip_path=None, today=None, secrets=None):
    """Write the reviewed products of the database as a labelled set; returns the manifest (with 'folder', 'zip').

    Read only: the database, the candidate store (temp/candidates) and the Brands Mapping cache are only read.
    """
    reader = reader or DbReader()
    today = today or dt.date.today().isoformat()
    out_dir = Path(out_dir) if out_dir else _next_free(RECORDED_DIR / today)
    if store_dir is None:
        try:
            from catalog_match import fetch
            store_dir = fetch._store_dir(None)
        except Exception:
            store_dir = Path(REPO_ROOT) / "temp" / "candidates"
    decisions = [d for d in (reader.review_decisions() or []) if str(d.get("action") or "").lower()
                 in ("approved", "rejected")]
    queue = list(reader.queue_rows() or [])
    curation = list(reader.curation_rows() or [])
    q_by_key, q_by_row = {}, {}
    for row in queue:
        for k in (row.get("sku_key"), row.get("alt_sku_key")):
            if k:
                q_by_key.setdefault(str(k), row)
        q_by_row.setdefault(_row_text(row.get("row_number")), row)

    def resolved_key(d):
        """sku_key of the review, else the key of the queued row of its sheet row, else 'row:<n>'."""
        key = _product_key(d)
        if key.startswith("row:"):
            row = q_by_row.get(_row_text(d.get("row_number")))
            if row and row.get("sku_key"):
                return str(row["sku_key"])
        return key

    products = {}                                   # key -> decisions, oldest first
    ordered = sorted(enumerate(decisions), key=lambda p: (str(p[1].get("created_at") or ""),
                                                          int(_row_text(p[1].get("id")) or 0), p[0]))
    for _i, d in ordered:
        products.setdefault(resolved_key(d), []).append(d)

    out_dir.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    golden = {"version": 1, "labels": list(LABELS), "named_cases": {}, "skus": [],
              "description": (f"Reviewed products of the dashboard's database on {today} (scripts/eval_record.py "
                              "--from-db): the sheet row as it was queued, the engine's pick and the alternatives it "
                              "kept for review, each labelled by the reviewers' approvals and rejections, and the "
                              "label reader's recorded readings. Images are downscaled copies of the candidate store "
                              f"(long side <= {max_side} px). Every candidate is served to every text query about the "
                              "product (the database does not keep which query found it).")}
    cassette = {"version": 1, "description": "Label-reader readings recorded in the database with each candidate",
                "schema": list(VERDICT_FIELDS), "missing": "UNKNOWN",
                "scenarios": {"gemini_down": {"decision": "UNKNOWN"}}, "verdicts": {}}
    for key, decs in products.items():
        row_text = _row_text(decs[-1].get("row_number"))
        queue_row = q_by_key.get(key) if not key.startswith("row:") else None
        queue_row = queue_row or q_by_row.get(row_text)
        # the product's stored candidates: its sku_key, or its sheet row for a candidate saved without one
        cur = [c for c in curation if (str(c.get("sku_key") or "") == key) or
               (not c.get("sku_key") and _row_text(c.get("row_number")) == row_text)]
        sku, readings = build_db_product(key, decs, queue_row, cur, store_dir, out_dir, max_side, counts)
        if not sku["candidates"]:
            continue
        golden["skus"].append(sku)
        cassette["verdicts"][sku["id"]] = readings
    golden["skus"].sort(key=lambda s: (s["row_number"], s["id"]))

    secrets = _secret_values() if secrets is None else list(secrets)
    golden, cassette = _redact(golden, secrets), _redact(cassette, secrets)
    mappings = load_cached_mappings() if mappings is None else mappings
    splits = Counter()
    try:
        metrics = _eval_metrics()
        for sku in golden["skus"]:
            splits[metrics.split_of(sku)] += 1
    except Exception:
        pass
    manifest = {
        "format": "laqta_eval_set/1", "source": "database", "created": dt.datetime.now().isoformat(timespec="seconds"),
        "products": len(golden["skus"]), "review_decisions": len(decisions),
        "decisions_used": counts["decisions_used"], "decisions_without_label": counts["decisions_without_label"],
        "candidates": counts["candidates"], "labelled": counts["labelled"], "images": counts["images"],
        "images_missing": counts["images_missing"], "readings": counts["readings"],
        "labels": dict(Counter(c["label"] or "unlabelled" for s in golden["skus"] for c in s["candidates"])),
        "split": dict(splits), "max_side": max_side, "brand_mappings": len(mappings),
        "stripped": ["configured secret values (keys, tokens, the database and proxy credentials) -> [hidden]",
                     "link query parameters that can carry a credential or a signed session"],
        "not_included": ["reviewer identity (the database keeps none)", "full-size images", "page bodies",
                         "settings, keys and the Google credentials"],
        "replay": "python scripts/eval_report.py --engine v2 --golden <folder>/golden_skus.json",
        "files": ["golden_skus.json", "vlm_cassette.json", "brand_mappings.json", "labels.csv", "manifest.json",
                  "blobs/"],
    }
    for name, doc in (("golden_skus.json", golden), ("vlm_cassette.json", cassette),
                      ("brand_mappings.json", {"version": 1, "description": "Brands Mapping (the dashboard's cache)",
                                               "mappings": mappings})):
        (out_dir / name).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    write_labelling_csv(golden, cassette, out_dir / "labels.csv")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                                           encoding="utf-8")
    manifest["folder"] = str(out_dir)
    if zip_path:
        manifest["zip"] = str(zip_folder(out_dir, zip_path))
    return manifest


def zip_folder(folder, zip_path):
    """One file to send: the set's folder as a zip (written next to it first, then moved into place)."""
    import zipfile

    folder, zip_path = Path(folder), Path(zip_path)
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_name(zip_path.name + ".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                zf.write(path, Path(folder.name) / path.relative_to(folder))
    os.replace(tmp, zip_path)
    return zip_path


def run_from_db(args):
    try:
        manifest = export_from_db(args.out, max_side=args.max_side, zip_path=args.zip)
    except Exception as exc:
        print(f"could not read the dashboard's database ({type(exc).__name__}): is MariaDB running, and is "
              "DB_DATABASE in .env the dashboard's database? Nothing was written", file=sys.stderr)
        return 1
    print(f"{manifest['products']} reviewed products, {manifest['candidates']} candidates ({manifest['labelled']} "
          f"labelled, {manifest['images']} images, {manifest['readings']} recorded readings) in {manifest['folder']}")
    if manifest.get("zip"):
        print(f"one file to send: {manifest['zip']}")
    print(f"replay: python scripts/eval_report.py --engine v2 --golden {manifest['folder']}/golden_skus.json")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", action="append", help="sheet rows to record, e.g. 2-201 (repeatable)")
    parser.add_argument("--from-db", action="store_true",
                        help="write the dashboard's reviewed products as a labelled set (no search, no paid call)")
    parser.add_argument("--max-side", type=int, default=DB_SET_MAX_SIDE,
                        help=f"--from-db: long side of the stored image copies (default {DB_SET_MAX_SIDE} px)")
    parser.add_argument("--zip", help="--from-db: also write the folder as one zip file to send")
    parser.add_argument("--out", help="output folder (default tests/eval/fixtures/recorded/<today>)")
    parser.add_argument("--import-labels", metavar="CSV", help="merge a filled-in labels.csv and exit")
    parser.add_argument("--prefill-labels-from-db", metavar="CSV",
                        help="fill the empty labels of a labels.csv from the dashboard's review decisions and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Arabic names on a Windows console
        except Exception:
            pass
    if args.from_db:
        return run_from_db(args)
    if args.prefill_labels_from_db:
        return run_prefill(args.prefill_labels_from_db)
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
