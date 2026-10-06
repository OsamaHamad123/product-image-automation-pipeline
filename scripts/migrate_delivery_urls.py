"""Rewrite the image links already in the sheet from the old delivery form to the new one. No re-upload.

Usage (from the repository root, on the machine with the database and the sheet credentials):

    python scripts/migrate_delivery_urls.py              # dry run (the default): counts and samples, nothing written
    python scripts/migrate_delivery_urls.py --apply      # queue the writes in the sheet outbox, then flush it
    python scripts/migrate_delivery_urls.py --apply --no-flush   # queue only; sync_worker or the next run writes them

Why: the old links (.../image/upload/q_auto,f_auto/...) let Cloudinary pick the format per client, and the native
app's HTTP clients (okhttp with `Accept: image/*`, iOS CFNetwork, Dart) were sent a JPEG without transparency, a
white box in dark mode. The new form (.../image/upload/c_limit,w_1200,f_webp,q_auto/...) is the same asset and
version, delivered as WebP with its alpha and at most 1200 px wide (delivery_urls.migrated_delivery_url).

What is rewritten: a link cell holding exactly one of OUR delivery links in the old form (https://res.cloudinary.com/
<account>/image/upload/q_auto,f_auto/<version and id>; with CLOUDINARY_CLOUD_NAME set, that account only). Nothing
else is touched: a store link, someone else's Cloudinary link or transformation, an already new link, a cell with
spaces around the link, and an old pending review cell (needs_review:<link>: approving or rejecting it in the
review screen writes a clean link or empties it).

How --apply writes: one write per row through the existing sheet outbox (google_sheets.queue_link_writes), with
the row's identity as the sheet holds it (barcode, name, size, brand): at flush time a row whose product changed
is skipped (CONFLICT) and nothing is written over another product. --apply refuses while an automation run holds
the lock, and skips a row with a link write of its own still in the outbox or queued since the sheet was read
(a newer image wins). The approvals in the database keep their old links; the code treats both forms as the same
image (url_norm, the reviewer guards, find_image_owners), and a stored old link is written in the new form.
"""

import argparse
import os
import sys
from collections import Counter

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import delivery_urls  # noqa: E402

SAMPLES = 5
REASONS = ("migrate", "already_new", "pending_review", "other_account", "not_ours", "empty")


def _cell(row, idx):
    return str(row[idx]).strip() if 0 <= idx < len(row) and row[idx] is not None else ""


def classify(value, cloud=""):
    """(reason, new link or None) for one link cell. reason is one of REASONS."""
    raw = "" if value is None else str(value)
    text = raw.strip()
    if not text:
        return "empty", None
    if text.startswith(delivery_urls.REVIEW_PREFIX):
        return "pending_review", None
    new = delivery_urls.migrated_delivery_url(raw)
    if new:
        if cloud and delivery_urls.cloud_name(raw) != cloud:
            return "other_account", None
        return "migrate", new
    parts = delivery_urls.split_delivery_url(text)
    if parts is not None and parts[1] == delivery_urls.DELIVERY_TRANSFORMATION:
        return "already_new", None
    return "not_ours", None


def plan(values, cloud=""):
    """
    The sheet values (header row first, as worksheet.get_all_values) -> {link_col, counts, items, samples}.
    items: [{row_number, old, new, barcode, product_name, size, brand}] for the cells to rewrite.
    """
    import google_sheets

    out = {"link_col": -1, "counts": Counter({r: 0 for r in REASONS}), "items": [], "samples": []}
    if not values:
        return out
    cols = google_sheets.resolve_columns(values[0])
    out["link_col"] = link = cols.get("link", -1)
    if link == -1:
        return out
    for number, row in enumerate(values[1:], start=2):
        reason, new = classify(row[link] if link < len(row) else "", cloud)
        out["counts"][reason] += 1
        if reason != "migrate":
            continue
        item = {"row_number": number, "old": str(row[link]), "new": new,
                "barcode": _cell(row, cols.get("barcode", -1)), "product_name": _cell(row, cols.get("name", -1)),
                "size": _cell(row, cols.get("size", -1)) or None, "brand": _cell(row, cols.get("brand", -1)) or None}
        out["items"].append(item)
        if len(out["samples"]) < SAMPLES:
            out["samples"].append(item)
    return out


def busy_rows(row_numbers, since_id):
    """
    Rows that must not be rewritten now: a link write still in the outbox (PENDING / FAILED), or any link write
    queued after since_id (the outbox's top id before the sheet was read). None when the outbox cannot be read.
    """
    import google_sheets

    if not row_numbers:
        return set()
    try:
        records = google_sheets.outbox_outcomes(sorted(row_numbers), limit=len(row_numbers) * 4 + 500)
        if since_id is not None:
            records += google_sheets.outbox_outcomes(sorted(row_numbers), since_id=since_id,
                                                     limit=len(row_numbers) * 4 + 500)
    except Exception:
        return None
    busy = set()
    for rec in records:
        if rec.get("column_key") not in (None, "", google_sheets.LINK_KEY):
            continue
        status = str(rec.get("status") or "").upper()
        newer = since_id is not None and int(rec.get("id") or 0) > int(since_id)
        if status in ("PENDING", "FAILED") or newer:
            busy.update(int(r) for r in (rec.get("row"), rec.get("queued_row")) if r is not None)
    return busy


def run_active():
    """Is an automation run holding the lock (main.read_lock / lock_verdict, as the dashboard reads it)?"""
    import main

    lock = main.read_lock(main.LOCK_FILE)
    if lock is None:
        return False
    if lock.get("kind") == "starting":
        return True
    return not main.lock_verdict(lock)["stale"]


def apply(worksheet, items, flush=True):
    """Queue the writes, flush (unless flush=False) and count the outcomes: {queued, written, pending, conflict}."""
    import google_sheets
    import local_cache_db

    since = local_cache_db.outbox_max_id()
    ids = google_sheets.queue_link_writes([{"row_number": i["row_number"], "value": i["new"], "barcode": i["barcode"],
                                            "product_name": i["product_name"], "size": i["size"],
                                            "brand": i["brand"]} for i in items])
    counts = Counter(queued=len(ids))
    if flush and ids:
        try:
            google_sheets.flush_outbox(worksheet, lock_timeout=30)
        except Exception as e:  # noqa: BLE001 - the writes stay in the outbox for sync_worker / the next run
            print(f"The flush failed ({type(e).__name__}); the writes stay in the outbox.")
    try:
        found = {o["id"]: o for o in google_sheets.outbox_outcomes(sorted(ids), since_id=since,
                                                                    limit=len(ids) * 4 + 200)}
    except Exception:
        found = {}
    bucket = {"SYNCED": "written", "PENDING": "pending", "FAILED": "pending"}
    for row, wid in ids.items():
        status = str((found.get(wid) or {}).get("status") or "PENDING").upper()
        counts[bucket.get(status, "conflict")] += 1
    return counts


def report(found, cloud):
    c = found["counts"]
    print(f"Account: {cloud or '(any: CLOUDINARY_CLOUD_NAME is not set)'}")
    print(f"To rewrite: {c['migrate']}  already new: {c['already_new']}  old pending reviews (left as they are): "
          f"{c['pending_review']}  other account: {c['other_account']}  other links: {c['not_ours']}  "
          f"empty: {c['empty']}")
    for s in found["samples"]:
        print(f"  row {s['row_number']}: {s['old']}\n      -> {s['new']}")


def open_sheet():
    import config
    import google_sheets

    client = google_sheets.get_sheets_client()
    if not client:
        raise RuntimeError("Could not open the Google Sheets client.")
    worksheet = google_sheets.open_worksheet(client, config.SPREADSHEET_NAME_OR_URL)
    if not worksheet:
        raise RuntimeError(f"Worksheet not found: {config.SPREADSHEET_NAME_OR_URL}")
    return worksheet


def main(argv=None, worksheet=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="queue the writes in the sheet outbox (default: dry run)")
    parser.add_argument("--no-flush", action="store_true", help="with --apply: queue only, do not flush now")
    parser.add_argument("--cloud", default=None, help="Cloudinary account to rewrite (default: CLOUDINARY_CLOUD_NAME)")
    args = parser.parse_args(argv)

    import config
    import google_sheets
    import local_cache_db

    cloud = (args.cloud if args.cloud is not None else str(getattr(config, "CLOUDINARY_CLOUD_NAME", "") or "")).strip()
    if args.apply and run_active():
        print("An automation run is active: run --apply after it ends (the dry run is safe at any time).")
        return 2
    since = local_cache_db.outbox_max_id() if args.apply else None
    worksheet = worksheet or open_sheet()
    values = google_sheets._retrying(worksheet.get_all_values)
    found = plan(values, cloud)
    if found["link_col"] == -1:
        print("No image link column in the sheet header; nothing to do.")
        return 1
    report(found, cloud)
    if not args.apply:
        print("Dry run: nothing was written. Add --apply to queue the writes.")
        return 0
    if not found["items"]:
        print("Nothing to rewrite.")
        return 0
    busy = busy_rows([i["row_number"] for i in found["items"]], since)
    if busy is None:
        print("The sheet outbox cannot be read; nothing was written.")
        return 1
    items = [i for i in found["items"] if i["row_number"] not in busy]
    if busy:
        print(f"Skipped {len(busy & {i['row_number'] for i in found['items']})} row(s) with a link write of their own "
              "in the outbox; run again later.")
    counts = apply(worksheet, items, flush=not args.no_flush)
    print(f"Queued {counts['queued']}: written {counts['written']}, still pending {counts['pending']}, "
          f"refused (row changed or a newer write) {counts['conflict']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
