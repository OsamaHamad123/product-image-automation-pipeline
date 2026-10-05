"""Barcodes the store pages stated for approved products whose sheet row has none: a CSV to paste into the sheet.

Usage (from the repository root, with the database settings of .env):

    python scripts/export_barcodes.py                       # -> laqta_barcodes_<date>.csv
    python scripts/export_barcodes.py --out runs/barcodes.csv

When an approval (or an auto-publish) used an image whose store page stated a valid, globally unique GTIN
(checksum-valid, not an in-store code: catalog_match.gtin.barcode_from_page) and the sheet row had no valid
barcode, the GTIN is kept with the approval (resolved_products.page_gtin, and the page in page_gtin_url).
This script lists them, one line per approved product: row, name, brand, page_gtin, source_page, approved
(human / auto) and duplicate_gtin ('yes' when another approved product got the same GTIN: check both rows
before pasting, one of the two pages is wrong).

Read-only: the script only runs SELECT. Nothing is ever written to the sheet automatically; paste the
column yourself (format the barcode column as plain text first, so a leading zero is kept).
"""

import argparse
import csv
import datetime as dt
import os
import sys
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

COLUMNS = ("row", "name", "brand", "page_gtin", "source_page", "approved", "duplicate_gtin")
APPROVED_BY = {"human_approved": "human", "auto_verified": "auto"}


def barcode_rows(records):
    """CSV rows (dicts of COLUMNS) for local_cache_db.get_page_barcodes() records, sheet row order; a GTIN that
    two or more approved products got is flagged duplicate_gtin='yes' on every one of them."""
    from catalog_match.gtin import display_gtin

    rows = []
    for r in records or ():
        gtin = display_gtin(r.get("page_gtin"))
        if not gtin:
            continue
        rows.append({"row": r.get("row_number") if r.get("row_number") is not None else "",
                     "name": str(r.get("product_name") or ""), "brand": str(r.get("brand") or ""),
                     "page_gtin": gtin, "source_page": str(r.get("page_gtin_url") or ""),
                     "approved": APPROVED_BY.get(str(r.get("verification_status") or ""), ""),
                     "duplicate_gtin": ""})
    counts = Counter(r["page_gtin"] for r in rows)
    for r in rows:
        r["duplicate_gtin"] = "yes" if counts[r["page_gtin"]] > 1 else ""
    rows.sort(key=lambda r: (r["row"] == "", r["row"] if r["row"] != "" else 0, r["name"]))
    return rows


def write_csv(path, rows):
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    # utf-8-sig: Excel and Google Sheets open the Arabic and Latin names correctly
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def default_name(now=None):
    now = now or dt.datetime.now()
    return f"laqta_barcodes_{now.strftime('%Y-%m-%d_%H%M')}.csv"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", metavar="PATH", help="the CSV file to write (default laqta_barcodes_<date>.csv)")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Arabic names on a cp1256 console
    except Exception:
        pass

    import local_cache_db

    rows = barcode_rows(local_cache_db.get_page_barcodes())
    path = write_csv(args.out or default_name(), rows)
    duplicates = sum(1 for r in rows if r["duplicate_gtin"])
    print(f"{len(rows)} barcodes from store pages -> {path}"
          + (f" ({duplicates} rows share a barcode with another product: check them)" if duplicates else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
