"""Embed the approved pictures that have no vector yet (approved_embeddings): a dry run by default.

    python3 scripts/backfill_embeddings.py                     # says what it would embed, embeds nothing
    python3 scripts/backfill_embeddings.py --apply --max 300   # embeds at most 300 approvals, newest first
    python3 scripts/backfill_embeddings.py --setup dinov2      # download + check the model, then turn EMBEDDINGS on
    python3 scripts/backfill_embeddings.py --calibrate         # how the stored vectors spread (re-tuning the thresholds)

The brand look check (catalog_match.embeddings) compares a candidate with the approved pictures of its brand. The
approvals made while EMBEDDINGS was on store their vector at once; this fills in the others: the approvals from
before, and any the dashboard skipped (the model file was missing, the picture could not be read).

Each approval still served (resolved_products: human_approved or auto_verified) without a vector of the configured
model is read from its source picture (a download of the original URL), else from its Cloudinary copy; both downloads
go through image_processor._download_bytes (http_client with the SSRF guard, net_guard). --max caps how many
approvals one run embeds (default 500), --max-seconds how long it runs (default 1800); a later run goes on where this
one stopped. Nothing is ever deleted, and an approval whose picture cannot be read is only counted.

--setup MODE (dinov2 | siglip2): downloads the pinned model into EMBEDDINGS_MODEL_DIR (sha256 checked), proves it
loads and embeds one picture, then writes embeddings=MODE to the dashboard settings (system_settings), which every
process reads (config.load_db_config). deploy/ubuntu/install.sh --with-embeddings runs it. --setup off turns it off.

--calibrate prints, for the stored vectors of the configured model, the closest approved picture of the same brand
(leave one out) and of another brand, with the thresholds in use, and how many approvals the rule would have flagged:
the numbers to re-tune catalog_match.embeddings.THRESHOLDS with once real approvals accumulate.

Exit code: 0, 1 on an unexpected error or a failed --setup, 2 when EMBEDDINGS is off (nothing to embed with).
"""

import argparse
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

DEFAULT_MAX = 500
DEFAULT_MAX_SECONDS = 1800.0


def _log(message):
    print(message, flush=True)


# ---------------------------------------------------------------------------
# --setup
# ---------------------------------------------------------------------------

def save_mode(mode):
    """Write embeddings=<mode> to system_settings (what config.load_db_config reads into every process)."""
    import local_cache_db

    conn = local_cache_db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("INSERT INTO system_settings (`key`, `value`) VALUES ('embeddings', %s) "
                    "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)", (mode,))
        conn.commit()
    finally:
        conn.close()


def setup(mode, model_dir=None, save=save_mode):
    """0 when the model of `mode` is ready and the setting is saved; 1 otherwise ('off' only saves the setting)."""
    from PIL import Image

    from catalog_match import embeddings

    if mode != "off":
        spec = embeddings.MODELS[mode]
        if embeddings.onnxruntime_module() is None:
            _log("embeddings setup: onnxruntime is not installed (pip install onnxruntime)")
            return 1
        try:
            path = embeddings.ensure_model(spec, model_dir, allow_download=True)
        except embeddings.ModelUnavailable as exc:
            _log(f"embeddings setup: {exc}")
            return 1
        embedder = embeddings.OnnxEmbedder(spec, model_dir, allow_download=False)
        started = time.monotonic()
        vector = embedder.embed([Image.new("RGB", (64, 96), (200, 30, 30))])[0]
        if vector is None:
            _log(f"embeddings setup: the model does not load: {embedder.error}")
            return 1
        _log(f"    model ready: {spec.model_id} ({path}, {len(vector)}-d, first picture in "
             f"{time.monotonic() - started:.2f} s)")
    try:
        save(mode)
    except Exception as exc:  # noqa: BLE001 - shown by type only
        _log(f"embeddings setup: the setting was not saved ({type(exc).__name__}); set EMBEDDINGS={mode} in .env")
        return 1
    _log(f"    EMBEDDINGS={mode} saved in the dashboard settings")
    return 0


# ---------------------------------------------------------------------------
# The backfill
# ---------------------------------------------------------------------------

def backfill(apply=False, limit=DEFAULT_MAX, max_seconds=DEFAULT_MAX_SECONDS, clock=time.monotonic, db=None,
             picture=None, embedder=None):
    """Summary {'missing', 'embedded', 'unreadable', 'skipped', 'stopped'} of one run (see the module docstring)."""
    from catalog_match import embeddings

    if db is None:
        import local_cache_db as db
    picture = picture or embeddings.approved_picture
    embedder = embedder or embeddings.get_embedder(allow_download=True)
    summary = {"missing": 0, "embedded": 0, "unreadable": 0, "skipped": 0, "stopped": None}
    rows = db.approvals_missing_embedding(embedder.model_id, limit=max(0, int(limit)))
    summary["missing"] = len(rows)
    if not apply:
        for row in rows[:20]:
            _log(f"    would embed {row.get('sku_key')} ({row.get('brand') or '-'}): {row.get('cloudinary_url')}")
        if len(rows) > 20:
            _log(f"    ... and {len(rows) - 20} more")
        return summary
    started = clock()
    for row in rows:
        if clock() - started >= max_seconds:
            summary["stopped"] = "max_seconds"
            break
        key = embeddings.brand_key(row.get("brand"))
        if not key:
            summary["skipped"] += 1          # no brand: never a reference
            continue
        img, source = picture(url=row.get("original_url"), cloudinary_url=row.get("cloudinary_url"))
        vector = embedder.embed([img])[0] if img is not None else None
        if vector is None:
            summary["unreadable"] += 1
            continue
        db.save_approved_embedding(embedder.model_id, row["sku_key"], key, row.get("brand"),
                                   row["cloudinary_url"], source, embeddings.to_blob(vector), len(vector))
        summary["embedded"] += 1
    embeddings.clear_references()
    return summary


# ---------------------------------------------------------------------------
# --calibrate
# ---------------------------------------------------------------------------

def calibration(refs, thresholds, min_refs=None):
    """Leave-one-out spread of the stored vectors: per approved picture, its closest approved picture of the same
    brand and of another brand, and whether the rule would flag it (each of them is a right-brand picture, so every
    flag is a false warning). Returns {'n', 'judged', 'flagged', 'same': [..], 'other': [..]}."""
    from catalog_match import embeddings

    min_refs = embeddings.MIN_BRAND_REFERENCES if min_refs is None else min_refs
    out = {"n": len(refs), "judged": 0, "flagged": 0, "same": [], "other": []}
    for i, ref in enumerate(refs):
        rest = embeddings.ReferenceSet([r for j, r in enumerate(refs) if j != i])
        verdict = embeddings.judge(ref.vector, rest, [ref.brand_key], [ref.brand_key], thresholds, min_refs)
        if verdict is None:
            continue
        out["judged"] += 1
        out["same"].append(verdict.same)
        if verdict.other is not None:
            out["other"].append(verdict.other)
        out["flagged"] += int(verdict.mismatch)
    return out


def _quantiles(values):
    if not values:
        return "-"
    values = sorted(values)
    pick = lambda q: values[min(len(values) - 1, int(q * (len(values) - 1) + 0.5))]  # noqa: E731
    return " ".join(f"p{int(q * 100)} {pick(q):.3f}" for q in (0.05, 0.25, 0.5, 0.75, 0.95))


def print_calibration(model_id, thresholds, refs):
    result = calibration(refs, thresholds)
    _log(f"embeddings calibration: {model_id}, {result['n']} approved pictures, {result['judged']} of brands with "
         "enough approved pictures")
    _log(f"    thresholds: same < {thresholds.same_max}, other >= {thresholds.other_min}, margin >= "
         f"{thresholds.margin}, near duplicate >= {thresholds.near_dup}")
    _log(f"    closest picture of the same brand:   {_quantiles(result['same'])}")
    _log(f"    closest picture of another brand:    {_quantiles(result['other'])}")
    _log(f"    approved pictures the rule would flag (false warnings): {result['flagged']} of {result['judged']}")
    return result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="embed (default: a dry run that embeds nothing)")
    mode.add_argument("--dry-run", action="store_true", help="say what would be embedded (the default)")
    mode.add_argument("--setup", choices=("dinov2", "siglip2", "off"),
                      help="download and check the model, then save EMBEDDINGS=<value> in the dashboard settings")
    mode.add_argument("--calibrate", action="store_true", help="print how the stored vectors spread")
    parser.add_argument("--max", type=int, default=DEFAULT_MAX, help=f"approvals per run (default {DEFAULT_MAX})")
    parser.add_argument("--max-seconds", type=float, default=DEFAULT_MAX_SECONDS,
                        help=f"time cap (default {DEFAULT_MAX_SECONDS:.0f})")
    args = parser.parse_args(argv)
    try:
        if args.setup:
            return setup(args.setup)
        from catalog_match import embeddings

        embedder = embeddings.get_embedder(allow_download=True)
        if embedder is None:
            _log("embeddings are off (EMBEDDINGS=off): nothing to do; --setup dinov2 turns them on")
            return 2
        if args.calibrate:
            print_calibration(embedder.model_id, embeddings._thresholds_of(embedder),
                              embeddings._db_references(embedder.model_id))
            return 0
        summary = backfill(apply=args.apply, limit=args.max, max_seconds=args.max_seconds, embedder=embedder)
        verb = "embedded" if args.apply else "would embed (dry run)"
        _log(f"embeddings backfill: {summary['missing']} approvals without a vector (cap {args.max}); {verb} "
             f"{summary['embedded'] if args.apply else summary['missing']}, unreadable {summary['unreadable']}, "
             f"without a brand {summary['skipped']}" + (f", stopped at the time cap" if summary["stopped"] else ""))
        return 0
    except Exception as exc:  # noqa: BLE001 - the message names the type only
        _log(f"embeddings backfill failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
