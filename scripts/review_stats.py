"""Reviewer evidence for auto-publish: how often the engine's pre-checked image was the one approved.

Usage (from the repository root, with the database settings of .env):

    python scripts/review_stats.py
    python scripts/review_stats.py --json review_stats.json

Every approve, reject and manual upload in the dashboard writes one row to
review_decisions. Per brand the script prints the reviewed SKUs, the reviewed
pre-checks, how many were accepted, the precision and its 95% Wilson lower bound;
per page domain the approvals and rejections. A brand is ready for auto-publish
when at least AUTO_PUBLISH_MIN_REVIEWED pre-checks were reviewed and the lower
bound is at least AUTO_PUBLISH_MIN_LOWER_BOUND (both in local_cache_db).

Per lane (catalog_match.decide.pick_lane: 'strict' = every auto-publish rule passed but
the brand setting, 'unsure' = tier 1 the label reader was unsure of, 'other') the same
numbers with the replaced and rejected pre-checks; lane 'strict' is ready for
AUTO_PUBLISH_STRICT_LANE on the same thresholds as a brand.

Read-only: the script only runs SELECT and prints a suggested AUTO_PUBLISH_BRANDS
value; it never changes a setting (paste the value in the Settings page yourself).
"""

import argparse
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

STATUS_TEXT = {"ready": "ready", "low_precision": "precision below target", "needs_reviews": "needs more reviews"}


def _pct(value):
    return "n/a" if value is None else f"{100 * value:.1f}%"


def _table(headers, rows):
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    lines = [headers, ["-" * w for w in widths]] + [list(row) for row in rows]
    return "\n".join("  ".join(str(x).ljust(w) for x, w in zip(line, widths)).rstrip() for line in lines)


def _status(brand):
    text = STATUS_TEXT[brand["status"]]
    if brand["status"] == "needs_reviews":
        needed = brand["reviews_needed"]
        text += f" ({brand['prechecked']}/{needed if needed is not None else '?'})"
    return text


def format_report(stats):
    """The printed report for local_cache_db.review_stats(rows)."""
    overall = stats["overall"]
    rule = stats["thresholds"]
    out = [
        f"Reviewer decisions: {overall['actions']} (approved {overall['approved']}, rejected {overall['rejected']}, "
        f"manual uploads {overall['manual_upload']}) on {overall['reviewed_skus']} SKUs",
        f"Reviewed pre-checks: {overall['prechecked']}, accepted {overall['accepted']} -> precision "
        f"{_pct(overall['precision'])} (Wilson 95% lower bound {_pct(overall['lower_bound'])})",
        f"Ready for auto-publish: >= {rule['min_reviewed']} reviewed pre-checks and lower bound "
        f">= {_pct(rule['min_lower_bound'])} (with every pre-check accepted that takes "
        f"{rule['perfect_record_reviews']} reviews)",
    ]
    if not overall["actions"]:
        out += ["", "No reviewer decisions yet: approve or reject images in the dashboard first."]
    else:
        out += ["", "Per brand", _table(
            ("brand", "SKUs", "pre-checks", "accepted", "precision", "lower bound", "status", "top reject reasons"),
            [(b["brand"] or "(no brand)", b["reviewed_skus"], b["prechecked"], b["accepted"], _pct(b["precision"]),
              _pct(b["lower_bound"]), _status(b),
              ", ".join(f"{code} {n}" for code, n in b["top_reject_reasons"]) or "-")
             for b in stats["brands"]])]
        lanes = stats.get("lanes") or {}
        out += ["", "Per lane (decisions recorded before the lanes count in none: "
                    f"{stats.get('unlaned_prechecked', 0)})", _table(
            ("lane", "pre-checks", "accepted", "replaced", "rejected", "precision", "lower bound", "status"),
            [(name, lane["prechecked"], lane["accepted"], lane["replaced"], lane["rejected"], _pct(lane["precision"]),
              _pct(lane["lower_bound"]), _status(lane) if name == "strict" else "display only")
             for name, lane in lanes.items()])]
        if stats["domains"]:
            out += ["", "Per page domain", _table(
                ("domain", "approved", "rejected"),
                [(d["domain"], d["approved"], d["rejected"]) for d in stats["domains"]])]
    out.append("")
    if stats["ready_brands"]:
        out.append(f"Suggested AUTO_PUBLISH_BRANDS={stats['suggested_auto_publish_brands']}")
    else:
        out.append("Suggested AUTO_PUBLISH_BRANDS= (empty: no brand is ready yet)")
    if (stats.get("lanes") or {}).get("strict", {}).get("ready"):
        out.append("Lane 'strict' is ready: with AUTO_PUBLISH_STRICT_LANE on (the default) the worker now auto-publishes "
                   "its picks; switch it off in the Settings page to keep reviewing them.")
    out.append("This script changes nothing; set the value in the dashboard Settings page.")
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", metavar="PATH", help="also write the stats as JSON to PATH")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # Arabic brand names on a cp1256 console
    except Exception:
        pass

    import local_cache_db

    stats = local_cache_db.review_stats(local_cache_db.get_review_decisions())
    print(format_report(stats))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(stats, fh, ensure_ascii=False, indent=2, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
