"""Build the local catalog index: product pages listed in the UAE stores' own sitemaps.

Usage (from the repository root):

    python scripts/build_catalog_index.py --discover                  # look first: no database writes
    python scripts/build_catalog_index.py --dry-run                   # read every sitemap, count, write nothing
    python scripts/build_catalog_index.py                             # build / refresh the index
    python scripts/build_catalog_index.py --stores lulu,spinneys      # some stores only (disabled ones too)
    python scripts/build_catalog_index.py --prune                     # also drop pages a store no longer lists
    python scripts/build_catalog_index.py --stats                     # what the index holds
    python scripts/build_catalog_index.py --refresh                   # read again only the stores that are stale
    python scripts/build_catalog_index.py --refresh --force           # ... every enabled store, fresh or not
    python scripts/build_catalog_index.py --discover --json runs/catalog_discover.json

The stores, their product page patterns and (once known) their sitemap URLs are in
catalog_match/data/catalog_stores.json. Only what a store publishes for crawlers is read:
robots.txt rules and its Crawl-delay are kept, and a store that answers 401 / 403 / 429 or a bot
check is reported BLOCKED and skipped (catalog_match/sitemaps.py). Product pages themselves are read
later, a few per product, while the pipeline searches (catalog_match/local_index.py).

--refresh is what the nightly run and the worker start in the background by themselves (catalog_match/index_refresh.py,
LOCAL_INDEX_REFRESH_DAYS / LOCAL_INDEX_REFRESH_MAX_S) and what the dashboard's «حدّث الفهرس هلق» button runs.

Writes go to the local MariaDB only (tables catalog_products, catalog_tokens, catalog_harvests);
nothing is written to the sheet or to Cloudinary.
"""

import argparse
import json
import logging
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
for _p in (ROOT, SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from catalog_match import sitemaps  # noqa: E402


def _utf8_stdout():
    stream = sys.stdout
    if (getattr(stream, "encoding", "") or "").lower().replace("-", "") != "utf8":
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _db_store():
    """DbCatalogStore after init_db() created the tables; (None, reason) when the database is not reachable."""
    import config  # noqa: F401  (loads .env into the environment before local_cache_db connects)
    import local_cache_db
    from catalog_match.local_index import DbCatalogStore

    if not local_cache_db.init_db():
        return None, "MariaDB is not reachable (see the [MariaDB] warning above); start it and run again"
    return DbCatalogStore(), ""


def print_stats(store) -> None:
    rows = store.stats()
    if not rows:
        print("the local index is empty: run  python scripts/build_catalog_index.py --discover  first")
        return
    print(f"{'store':18s} {'products':>9s} {'pages read':>11s} {'with image':>11s}  last harvest")
    for r in rows:
        print(f"{r['store']:18s} {r['products']:9d} {r['pages_read']:11d} {r['with_image']:11d}  "
              f"{r.get('last_harvest') or '-'} {r.get('last_status') or ''}")
    print(f"{'total':18s} {sum(r['products'] for r in rows):9d}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stores", help="comma-separated store keys (default: every enabled store)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--discover", action="store_true",
                      help="read robots.txt, the sitemap indexes and a few URL lists; print what they hold")
    mode.add_argument("--dry-run", action="store_true", help="read every sitemap and count; write nothing")
    mode.add_argument("--stats", action="store_true", help="print what the index holds and exit")
    mode.add_argument("--refresh", action="store_true",
                      help="read again the stores that are stale (see LOCAL_INDEX_REFRESH_DAYS), within "
                           "LOCAL_INDEX_REFRESH_MAX_S seconds; --stores / --max-urls do not apply")
    parser.add_argument("--force", action="store_true", help="with --refresh: every enabled store, stale or not")
    parser.add_argument("--trigger", default="manual", help="with --refresh: who asked (recorded in the progress file)")
    parser.add_argument("--max-urls", type=int, help="stop a store after this many product pages")
    parser.add_argument("--max-sitemaps", type=int, help="stop a store after reading this many sitemap files")
    parser.add_argument("--prune", action="store_true",
                        help="delete pages a store no longer lists (only after a complete, unblocked harvest)")
    parser.add_argument("--json", help="also write the reports to this JSON file")
    parser.add_argument("--config", help="another stores file instead of catalog_match/data/catalog_stores.json")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _utf8_stdout()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.stats:
        store, why = _db_store()
        if store is None:
            print(why, file=sys.stderr)
            return 2
        print_stats(store)
        return 0

    if args.refresh:
        return _refresh(args)

    try:
        all_stores = sitemaps.load_stores(args.config)
        keys = [k.strip() for k in (args.stores or "").split(",") if k.strip()]
        chosen = sitemaps.enabled_stores(all_stores, keys)
    except (OSError, ValueError, KeyError) as exc:
        print(f"cannot use the stores file: {exc}", file=sys.stderr)
        return 2
    if not chosen:
        print("no store is enabled in the stores file; pass --stores", file=sys.stderr)
        return 2

    db = None
    if not (args.discover or args.dry_run):
        db, why = _db_store()
        if db is None:
            print(why, file=sys.stderr)
            return 2

    harvester = sitemaps.SitemapHarvester()
    reports = []
    try:
        for store in chosen:
            reports.append(_harvest_one(harvester, store, db, args))
    finally:
        if args.json:      # what was read so far, even when the run stops
            with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
                json.dump([r.as_dict() for r in reports], fh, ensure_ascii=False, indent=2)

    blocked = [r.store for r in reports if r.status in ("blocked", "error")]
    waiting = [r.store for r in reports if r.status == "outside_visit_time"]
    if args.discover:
        print("Discovery only: nothing was written. Send this output (or the --json file) to adjust the store patterns.")
    elif args.dry_run:
        print(f"Dry run: {sum(r.product_urls for r in reports)} product pages found, nothing was written.")
    else:
        print(f"{sum(r.new_urls for r in reports)} new product pages indexed.")
        print_stats(db)
    if blocked:
        print(f"Skipped (blocked, unreachable or failed; never worked around): {', '.join(blocked)}")
    if waiting:
        print(f"Not read now (outside the Visit-time window of their robots.txt; not a failure): {', '.join(waiting)}")
    if args.json:
        print(f"reports written to {args.json}")
    return 1 if blocked and len(blocked) == len(reports) - len(waiting) else 0     # every store that was read failed


def _refresh(args) -> int:
    """The automatic refresh, in the foreground (catalog_match/index_refresh.py): 0 when it ran or had nothing to do."""
    from catalog_match import index_refresh

    db, why = _db_store()
    if db is None:
        print(why, file=sys.stderr)
        if args.trigger == "dashboard":
            index_refresh.record_end("unavailable")       # the dashboard's button job: say it is over
        return 2
    result = index_refresh.refresh(db=db, trigger=args.trigger, force=args.force)
    print(index_refresh.format_result(result))
    return 0 if result.get("reason") in ("done", "budget", "nothing_due") else 1


def _harvest_one(harvester, store, db, args):
    """One store's harvest and its record; an unexpected failure (a database error, a bug) is that store's
    'error' and the next store is still harvested."""
    started = db.begin_harvest(store.key) if db is not None else None
    on_urls = (lambda batch, key=store.key: db.upsert(key, batch)) if db is not None else None
    try:
        rep = harvester.harvest(store, on_urls=on_urls, max_urls=args.max_urls, max_sitemaps=args.max_sitemaps,
                                discover=args.discover)
    except Exception as exc:
        logging.getLogger(__name__).exception("harvest of %s failed", store.key)
        rep = sitemaps.HarvestReport(store=store.key, status="error", error=f"{type(exc).__name__}: {exc}"[:300])
    if db is not None and rep.status != "outside_visit_time":     # not a harvest: nothing to record, asked again later
        try:
            if args.prune and rep.status == "ok" and not rep.truncated:
                rep.pruned = db.prune(store.key, started)
            db.finish_harvest(store.key, started, rep.as_dict())
        except Exception as exc:
            rep.status, rep.error = "error", f"the index could not be updated: {type(exc).__name__}"
    print(sitemaps.format_report(rep, store, discover=args.discover))
    print()
    return rep


if __name__ == "__main__":
    sys.exit(main())
