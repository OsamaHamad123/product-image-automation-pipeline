"""Reprocess the published pictures whose stored master is still a white (opaque) canvas into transparent ones.

    python scripts/reprocess_transparent.py                          # dry run (the default): what would change + cost
    python scripts/reprocess_transparent.py --apply --max 50 --max-usd 2
    python scripts/reprocess_transparent.py --apply --max 50 --max-usd 2 --no-flush   # queue only; the timer writes

Why: the pictures published before the transparent output (OUTPUT_BACKGROUND=transparent) are white JPEG/PNG canvases;
the native app shows them as white boxes in dark mode.

Which pictures: every published product (resolved_products, human_approved / auto_verified) whose master is not
transparent: resolved_products.master_background (recorded with every approval since this script exists), else the
stored master itself is checked once (its WebP delivery keeps alpha; a free download, no paid call). Only masters that
are in the sheet right now are redone: a link cell holding exactly that link (old or new delivery form). A cell the
owner changed by hand since we wrote it holds another value and is never touched (and the outbox re-checks the cell when
it writes: expect_value).

How each one is redone (recut.py): re-isolated from its best stored source (the cached candidate bytes of the approval,
or its original URL once it is shown to be the same picture; the white master only when there is no other source) with
the configured background removal (PhotoRoom, its local fallback rules unchanged), then cutout_finish. A clean cut is
uploaded as a NEW versioned asset (the app's cache refreshes), the approval follows it, and the new delivery link is
queued in the sheet outbox for the cells that still hold the old one. A cut with review flags is not published: it
waits for the owner on the «فحص القص» page. Every replacement is logged (recut_log) for undo.

Costs: the dry run prints the estimate (pictures x PHOTOROOM_PRICE_USD; a picture whose first cut needs a retry can
take up to 3 calls). --apply stops before a picture that could pass --max-usd (counting the calls really sent), after
--max pictures, and at the first error (credit, network, upload, database): the state is saved in
temp/reprocess_state.json and the next --apply goes on where this one stopped (an upload whose sheet write was not
queued yet is finished first). --apply refuses while an automation run holds the lock or another batch is running.

Exit code: 0, 1 on an unexpected error, 2 when refused (run active, batch running, background removal off), 3 when the
batch stopped on an error.
"""

import argparse
import os
import re
import sys
import time
import uuid

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

DEFAULT_MAX = 25
DEFAULT_MAX_USD = 1.0
SAMPLES = 8
STATE_PATH = os.path.join(REPO_ROOT, "temp", "reprocess_state.json")
LOG_PATH = os.path.join(REPO_ROOT, "temp", "reprocess.log")
STALE_S = 600           # a 'running' state not updated for 10 minutes is a batch that died
# errors that say the service, the network or the setup is down: the batch stops (the next run retries the picture)
SYSTEMIC = ("no_key", "_401", "_402", "_403", "_429", "timeout", "connection_error", "processing_failed",
            "bg_removal_off", "unknown_bg_method", "_unsupported", "rembg_not_installed", "rembg_failed",
            "upload_", "outbox_unreadable", "db_error")


def _log(message):
    print(message, flush=True)


def systemic(code) -> bool:
    """Does this failure stop the batch (service / network / setup), rather than skip one picture?"""
    import recut

    text = str(code or "")
    if recut.is_source_error(text):
        return True                  # the master itself could not be read (Cloudinary down): stop
    return any(part in text for part in SYSTEMIC) or bool(re.search(r"_5\d\d$", text))


# ---------------------------------------------------------------------------
# state (temp/reprocess_state.json, read by the Health card)
# ---------------------------------------------------------------------------

def read_state(path=STATE_PATH):
    import json

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(state, path=STATE_PATH):
    import atomic_file

    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_file.write_json(path, dict(state, updated_at=time.time()), ensure_ascii=False)


def running(state, now=None) -> bool:
    """Is a batch running now (state starting / running, updated in the last STALE_S seconds)?"""
    now = time.time() if now is None else now
    return state.get("state") in ("starting", "running") and now - float(state.get("updated_at") or 0) < STALE_S


def run_active():
    """Is an automation run holding the lock (main.read_lock / lock_verdict, as migrate_delivery_urls reads it)?"""
    import main

    lock = main.read_lock(main.LOCK_FILE)
    if lock is None:
        return False
    if lock.get("kind") == "starting":
        return True
    return not main.lock_verdict(lock)["stale"]


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


# ---------------------------------------------------------------------------
# the plan (also the dashboard's dry run: cli_bridge reprocess_plan)
# ---------------------------------------------------------------------------

def build_plan(worksheet, retry_skipped=False, probe_fn=None):
    """recut.plan for the sheet and the published masters, plus the method and the estimate."""
    import google_sheets
    import local_cache_db
    import processing_profile
    import recut

    values = google_sheets._retrying(worksheet.get_all_values)
    masters = local_cache_db.published_masters()
    skipped = [] if retry_skipped else [e["old_url"] for e in
                                        local_cache_db.recut_entries(statuses=("skipped",), limit=None)]
    found = recut.plan(values, masters, skipped, probe_fn=probe_fn)
    method = processing_profile.current().bg_method
    found["method"] = method
    found["estimate"] = recut.estimate(len(found["todo"]), method)
    return found


def summary(found, limit=SAMPLES):
    """The plan as plain data (the dashboard card, --apply's report)."""
    c = found["counts"]
    return {"link_col": found["link_col"], "todo": c["todo"], "transparent": c["transparent"],
            "not_in_sheet": c["not_in_sheet"], "skipped_before": c["skipped_before"], "probe_failed": c["probe_failed"],
            "pictures": sum(c.values()), "method": found.get("method"), "estimate": found.get("estimate"),
            "samples": [{"product_name": g.name, "brand": g.rows[0].get("brand"), "url": g.url,
                         "rows": [r["row_number"] for r in g.sheet_rows]} for g in found["todo"][:limit]]}


def report(found):
    s = summary(found)
    est = s["estimate"] or {}
    _log(f"Published pictures: {s['pictures']}  transparent already: {s['transparent']}  white to redo: {s['todo']}  "
         f"not in the sheet any more (or changed by hand): {s['not_in_sheet']}  skipped before (flags or source): "
         f"{s['skipped_before']}  could not be checked: {s['probe_failed']}")
    for item in s["samples"]:
        _log(f"  rows {', '.join(str(r) for r in item['rows'])}: {item['product_name']} ({item['brand'] or '-'})")
    if est.get("price"):
        _log(f"Estimated cost: about {est['calls']} {s['method']} calls x ${est['price']:.3f} = ${est['usd']:.2f} "
             f"(at most ${est['worst_usd']:.2f} if every first cut needed retries); --max-usd caps the real calls.")
    else:
        _log(f"Estimated cost: $0 ({s['method']} is a free local method).")


# ---------------------------------------------------------------------------
# --apply
# ---------------------------------------------------------------------------

def run_batch(found, worksheet, max_pictures, max_usd, batch_id, state, state_path=STATE_PATH, flush=True,
              recut_fn=None, publish_fn=None):
    """Redo up to max_pictures of found['todo'] within max_usd; the state is written after every picture."""
    import google_sheets
    import image_processor
    import local_cache_db
    import recut
    from catalog_match import settings

    recut_fn = recut_fn or recut.recut
    publish_fn = publish_fn or recut.publish
    method = found["method"]
    price = settings.isolation_price_usd(method)
    worst = recut.MAX_CALLS_PER_PICTURE * price
    for group in found["todo"]:
        if state["done"] >= max_pictures:
            state["state"] = "max"
            break
        if state["spent_usd"] + worst > max_usd + 1e-9:
            state["state"] = "budget"
            break
        busy = recut.busy_rows([r["row_number"] for r in group.sheet_rows])
        if busy is None:
            state.update(state="stopped", last_error="outbox_unreadable", last_item=group.name)
            break
        rows = [r for r in group.sheet_rows if r["row_number"] not in busy]
        if not rows:
            state["busy"] += 1
            continue
        state.update(state="running", current=group.name)
        write_state(state, state_path)
        result = recut_fn(group.rows[0], method=method)
        state["calls"] += result.paid_calls
        state["spent_usd"] = round(state["spent_usd"] + result.paid_calls * price, 4)
        entry = {"batch_id": batch_id, "origin": "reprocess", "resolved_ids": [r["id"] for r in group.rows],
                 "sku_key": group.rows[0].get("sku_key"), "product_name": group.name,
                 "brand": group.rows[0].get("brand"), "old_url": group.url, "old_background": group.background,
                 "provider": result.provider or method, "source": result.source, "paid_calls": result.paid_calls,
                 "rows_json": rows}
        if result.path is None:
            if systemic(result.error):
                local_cache_db.log_recut(dict(entry, status="failed", code=result.error))
                state.update(state="stopped", last_error=result.error, last_item=group.name)
                break
            local_cache_db.log_recut(dict(entry, status="skipped", code=result.error))
            state["skipped"] += 1
            continue
        if not result.clean:
            image_processor.cleanup_processed_image(result.path)
            local_cache_db.log_recut(dict(entry, status="skipped",
                                          code="needs_look:" + ",".join(result.flags or ["not_isolated"])))
            state["needs_look"] += 1
            continue
        try:
            outcome = publish_fn(group.rows, result.path, result.facts(), "reprocess", rows, batch_id=batch_id,
                                 provider=result.provider, source=result.source, paid_calls=result.paid_calls,
                                 old_background=group.background)
        finally:
            image_processor.cleanup_processed_image(result.path)
        if outcome["status"] == "failed":
            local_cache_db.log_recut(dict(entry, status="failed", code=outcome.get("code")))
            state.update(state="stopped", last_error=outcome.get("code") or "upload_failed", last_item=group.name)
            break
        if outcome["status"] == "done":
            state["done"] += 1
            state["queued"] += len(outcome.get("outbox") or {})
        else:
            if not outcome.get("log_id"):
                local_cache_db.log_recut(dict(entry, status="skipped", code=outcome.get("code")))
            state["skipped"] += 1
        write_state(state, state_path)
    else:
        state["state"] = "done"
    state.pop("current", None)
    if flush and state["queued"]:
        try:
            google_sheets.flush_outbox(worksheet, lock_timeout=30)
        except Exception as exc:  # noqa: BLE001 - the writes stay in the outbox for the timer / the next run
            _log(f"The flush failed ({type(exc).__name__}); the writes stay in the outbox.")
    return state


def apply(args, worksheet=None, probe_fn=None, recut_fn=None, publish_fn=None, state_path=STATE_PATH):
    import processing_profile
    import recut

    if processing_profile.current().skips_background:
        _log("Background removal is off in the settings (bg_removal_method = none): nothing can be cut out.")
        return 2
    previous = read_state(state_path)
    if running(previous) and previous.get("pid") != os.getpid():
        _log("Another reprocess batch is running; wait for it to end.")
        return 2
    if run_active():
        _log("An automation run is active: run --apply after it ends (the dry run is safe at any time).")
        return 2
    batch_id = uuid.uuid4().hex[:16]
    state = {"state": "running", "batch_id": batch_id, "pid": os.getpid(), "trigger": args.trigger,
             "started_at": time.time(), "finished_at": None, "max": args.max, "max_usd": args.max_usd,
             "planned": 0, "done": 0, "skipped": 0, "needs_look": 0, "busy": 0, "queued": 0, "calls": 0,
             "spent_usd": 0.0, "last_error": None, "last_item": None, "resumed": 0}
    write_state(state, state_path)
    try:
        resumed = recut.resume_unfinished()
        state["resumed"] = len(resumed)
        state["queued"] += sum(len(r.get("outbox") or {}) for r in resumed)
        worksheet = worksheet or open_sheet()
        found = build_plan(worksheet, retry_skipped=args.retry_skipped, probe_fn=probe_fn)
        recut.remember_measured(found["groups"])
        report(found)
        state["planned"] = len(found["todo"])
        state["method"] = found["method"]
        state["price"] = found["estimate"]["price"]
        write_state(state, state_path)
        state = run_batch(found, worksheet, args.max, args.max_usd, batch_id, state, state_path,
                          flush=not args.no_flush, recut_fn=recut_fn, publish_fn=publish_fn)
    except Exception as exc:
        state.update(state="stopped", last_error=f"error:{type(exc).__name__}")
        write_state(dict(state, finished_at=time.time()), state_path)
        raise
    state["finished_at"] = time.time()
    write_state(state, state_path)
    _log(f"Redone {state['done']} of {state['planned']} (queued {state['queued']} sheet writes), skipped "
         f"{state['skipped']}, waiting for a look {state['needs_look']}, rows busy {state['busy']}; "
         f"{state['calls']} paid calls, about ${state['spent_usd']:.2f}. Ended: {state['state']}"
         + (f" ({state['last_error']} at {state['last_item']})" if state.get("last_error") else ""))
    if state["state"] == "stopped":
        _log("Stopped on an error: run --apply again after it is fixed; it goes on where it stopped.")
        return 3
    return 0


def start_detached(max_pictures, max_usd, popen=None, python=None, state_path=STATE_PATH, log_path=LOG_PATH):
    """
    «ابدأ» on the Health card (cli_bridge reprocess_start): the same --apply in a process of its own, so the bridge
    answers at once; the card follows temp/reprocess_state.json. {started, reason: started | running | run_active |
    bg_off | unavailable}. Never raises.
    """
    import subprocess

    import processing_profile

    try:
        if processing_profile.current().skips_background:
            return {"started": False, "reason": "bg_off"}
        if running(read_state(state_path)):
            return {"started": False, "reason": "running"}
        if run_active():
            return {"started": False, "reason": "run_active"}
        write_state({"state": "starting", "trigger": "dashboard", "started_at": time.time(), "max": int(max_pictures),
                     "max_usd": float(max_usd), "done": 0, "planned": 0, "spent_usd": 0.0}, state_path)
        command = [python or sys.executable, os.path.abspath(__file__), "--apply", "--max", str(int(max_pictures)),
                   "--max-usd", f"{float(max_usd):.4f}", "--trigger", "dashboard"]
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        options = {"cwd": REPO_ROOT, "stdin": subprocess.DEVNULL, "close_fds": True, "env": env}
        if os.name == "nt":      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console, not tied to the bridge
            options["creationflags"] = 0x00000008 | 0x00000200
        else:
            options["start_new_session"] = True
        with open(log_path, "w", encoding="utf-8") as log:
            (popen or subprocess.Popen)(command, stdout=log, stderr=subprocess.STDOUT, **options)
        return {"started": True, "reason": "started"}
    except Exception as exc:  # noqa: BLE001
        _log(f"reprocess: the batch could not start ({type(exc).__name__})")
        try:
            write_state({"state": "stopped", "last_error": "unavailable", "finished_at": time.time()}, state_path)
        except Exception:  # noqa: BLE001
            pass
        return {"started": False, "reason": "unavailable"}


def main(argv=None, worksheet=None, probe_fn=None, recut_fn=None, publish_fn=None, state_path=STATE_PATH):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="redo the pictures (default: a dry run)")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX, help=f"pictures per batch (default {DEFAULT_MAX})")
    parser.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD,
                        help=f"cost cap of the batch in USD (default {DEFAULT_MAX_USD})")
    parser.add_argument("--retry-skipped", action="store_true",
                        help="also redo the pictures an earlier batch skipped (review flags, no source)")
    parser.add_argument("--no-flush", action="store_true", help="with --apply: queue the sheet writes only")
    parser.add_argument("--trigger", default="manual", choices=("manual", "dashboard"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    args.max = max(0, int(args.max))
    args.max_usd = max(0.0, float(args.max_usd))
    try:
        if args.apply:
            return apply(args, worksheet=worksheet, probe_fn=probe_fn, recut_fn=recut_fn, publish_fn=publish_fn,
                         state_path=state_path)
        found = build_plan(worksheet or open_sheet(), retry_skipped=args.retry_skipped, probe_fn=probe_fn)
        if found["link_col"] == -1:
            _log("No image link column in the sheet header; nothing to do.")
            return 1
        report(found)
        _log("Dry run: nothing was changed. Add --apply --max N --max-usd X to redo them.")
        return 0
    except Exception as exc:  # noqa: BLE001 - the message names the type only
        _log(f"reprocess failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
