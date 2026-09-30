"""Replay the golden SKU set through one search engine, offline, and print the scorecard.

Usage (from the repository root):

    python scripts/eval_report.py --engine v1
    python scripts/eval_report.py --engine v2
    python scripts/eval_report.py --engine v2 --scenario gemini_down
    python scripts/eval_report.py --engine v1 --write-baseline      # refresh tests/eval/fixtures/baseline_v1.json
    python scripts/eval_report.py --engine v1 --write-hotfixed-baseline   # refresh baseline_v1_hotfixed.json
    python scripts/eval_report.py --stored original                # print a stored baseline (original|hotfixed)
    python scripts/eval_report.py --engine v2 --golden tests/eval/fixtures/recorded/2026-10-02/golden_skus.json \
        --cassette tests/eval/fixtures/recorded/2026-10-02/vlm_cassette.json

No network, API key or database is used: providers answer from the fixture,
images are generated (or read from recorded blobs) and the vision model answers
from the recorded cassette. The full JSON report is written to the temp folder
(or --out) so runs can be compared.
"""

import argparse
import json
import logging
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVAL_DIR = os.path.join(REPO_ROOT, "tests", "eval")
for _p in (REPO_ROOT, EVAL_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import harness  # noqa: E402
import metrics  # noqa: E402

log = logging.getLogger("eval_report")


def _pct(value):
    return "n/a" if value is None else f"{100 * value:.1f}%"


def print_report(report, golden, baseline=None):
    m = report["metrics"]
    print(f"\nEngine {report['engine']} | scenario {report['scenario']} | {report['n_skus']} SKUs | "
          f"{report['seconds']:.1f}s | network attempts: {len(report['network_attempts'])}")
    print()
    print(harness.stratum_table(m))
    print()
    print(f"auto-accept precision  {_pct(m['auto_accept_precision'])}  "
          f"(Wilson 95% lower bound {_pct(m['auto_precision_wilson_lower'])}, {m['n_auto']} auto picks)")
    print(f"wrong auto-publish     {_pct(m['wrong_auto_rate'])}  ({m['n_auto_wrong']} SKUs)")
    print(f"correct pick           {_pct(m['correct_pick_rate'])}  ({m['n_correct_pick']}/{m['n_with_correct']})")
    print(f"preselect precision    {_pct(m['preselect_precision'])}  ({m['n_preselected']} preselected)")
    print(f"review rate            {_pct(m['review_rate'])}")
    print(f"not found              {_pct(m['not_found_rate'])}  (false NOT_FOUND {_pct(m['false_not_found_rate'])})")
    print(f"pool recall            {_pct(m['pool_recall'])}")
    print(f"VLM calls per SKU      mean {m['vlm_calls_mean']}, max {m['vlm_calls_max']}")
    print(f"provider calls max     {m['provider_calls_max']}")
    print(f"decisions              {m['decisions']}")
    print("kills of correct_exact images by rule:")
    for rule, count in sorted(m["kill_attribution"].items(), key=lambda kv: -kv[1]):
        flag = "  <- image-quality rule" if metrics.is_quality_rule(rule) else ""
        print(f"    {count:4d}  {rule}{flag}")
    if baseline and report["engine"] != "v1":
        base = baseline["metrics"]
        print(f"\nv1 baseline: correct pick {_pct(base['correct_pick_rate'])}, wrong auto "
              f"{_pct(base['wrong_auto_rate'])}, auto precision {_pct(base['auto_accept_precision'])}")

    labels = metrics.labels_from_golden(golden)
    by_id = {o["sku_id"]: o for o in report["outcomes"]}
    print("\nnamed regression cases:")
    for case, sku_ids in sorted(golden.get("named_cases", {}).items(), key=lambda kv: int(kv[0])):
        for sid in sku_ids:
            o = by_id.get(sid)
            if o is None:
                continue
            label = labels[sid]["candidates"].get(o["chosen_id"]) if o["chosen_id"] else "-"
            print(f"  ({case:>2}) {sid:48s} {o['decision']:19s} pick={o['chosen_id'] or '-':4s} {label:14s} "
                  f"expected={labels[sid]['expected_v2']}")
    errors = [o for o in report["outcomes"] if o.get("error")]
    if errors:
        print("\nengine errors:")
        for o in errors:
            print(f"  {o['sku_id']}: {o['error']}")


def print_stored(stored, which):
    """Metric table of a committed v1 baseline (no engine run)."""
    m = stored["metrics"]
    print(f"\nStored v1 baseline ({which}) | {stored['n_skus']} SKUs | recorded {stored['generated_at']} "
          f"from commit {stored.get('legacy_commit')}")
    print()
    print(harness.stratum_table(m))
    print()
    print(f"auto-accept precision  {_pct(m['auto_accept_precision'])}  ({m['n_auto']} auto picks)")
    print(f"wrong auto-publish     {_pct(m['wrong_auto_rate'])}  ({m['n_auto_wrong']} SKUs)")
    print(f"correct pick           {_pct(m['correct_pick_rate'])}  ({m['n_correct_pick']}/{m['n_with_correct']})")
    print(f"preselect precision    {_pct(m['preselect_precision'])}  ({m['n_preselected']} preselected)")
    print(f"review rate            {_pct(m['review_rate'])}")
    print(f"not found              {_pct(m['not_found_rate'])}  (false NOT_FOUND {_pct(m['false_not_found_rate'])})")
    print(f"decisions              {m['decisions']}")
    print("kills of correct_exact images by rule:")
    for rule, count in sorted(m["kill_attribution"].items(), key=lambda kv: -kv[1]):
        flag = "  <- image-quality rule" if metrics.is_quality_rule(rule) else ""
        print(f"    {count:4d}  {rule}{flag}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", choices=harness.ENGINES, default="v2")
    parser.add_argument("--scenario", choices=harness.SCENARIOS, default="normal")
    parser.add_argument("--write-baseline", action="store_true",
                        help="store this v1 run as tests/eval/fixtures/baseline_v1.json")
    parser.add_argument("--write-hotfixed-baseline", action="store_true",
                        help="store this v1 run as tests/eval/fixtures/baseline_v1_hotfixed.json (v1 after the "
                             "rollback hot-fixes); baseline_v1.json keeps the original v1")
    parser.add_argument("--stored", choices=("original", "hotfixed"),
                        help="print a stored v1 baseline's metric table instead of running an engine")
    parser.add_argument("--force", action="store_true",
                        help="with --write-baseline: overwrite even though the legacy code has changed")
    parser.add_argument("--golden", help="golden_skus.json to replay (default: the committed fixture)")
    parser.add_argument("--cassette", help="vlm_cassette.json to replay (default: the committed fixture)")
    parser.add_argument("--mappings", help="brand_mappings.json (default: the committed fixture)")
    parser.add_argument("--sku", action="append", help="only this SKU id (repeatable)")
    parser.add_argument("--out", help="where to write the JSON report (default: temp folder)")
    parser.add_argument("--json", action="store_true", help="print the metrics as JSON instead of tables")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    if args.stored:
        stored = harness.load_baseline() if args.stored == "original" else harness.load_hotfixed_baseline()
        print_stored(stored, args.stored)
        return 0
    if args.write_hotfixed_baseline and (args.engine != "v1" or args.scenario != "normal" or args.sku
                                         or args.golden or args.write_baseline):
        parser.error("--write-hotfixed-baseline needs --engine v1, the normal scenario and the full committed "
                     "golden set (and not --write-baseline)")
    if args.write_baseline and (args.engine != "v1" or args.scenario != "normal" or args.sku or args.golden):
        parser.error("--write-baseline needs --engine v1, the normal scenario and the full committed golden set")
    if args.write_baseline and not args.force:
        try:
            stored = harness.load_baseline()["legacy_fingerprint"]["sha256"]
        except (FileNotFoundError, KeyError):
            stored = None
        if stored and stored != harness.legacy_fingerprint()["sha256"]:
            parser.error("the legacy v1 code differs from the code baseline_v1.json was recorded from, so a new "
                         "baseline would overwrite the 'before' record with hot-fixed numbers. Re-record from a "
                         "checkout of the baseline's legacy_commit, or pass --force if that is really intended.")

    golden = harness.load_golden(args.golden)
    cassette = harness.load_cassette(args.cassette)
    mappings = harness.load_mappings(args.mappings)

    def progress(i, n, outcome):
        if args.verbose:
            log.info("%d/%d %s -> %s %s", i, n, outcome.sku_id, outcome.decision, outcome.chosen_id or "")

    try:
        report = harness.run_all(args.engine, args.scenario, golden=golden, cassette=cassette, mappings=mappings,
                                 sku_ids=args.sku, progress=progress)
    except Exception as exc:
        # pytest.importorskip raises its Skipped exception while catalog_match.pipeline does not exist yet
        if type(exc).__name__ == "Skipped":
            print(f"engine {args.engine} is not available yet: {exc}")
            return 2
        raise
    path = harness.write_report(report, args.out)

    try:
        baseline = harness.load_baseline()
    except FileNotFoundError:
        baseline = None
    if args.json:
        print(json.dumps(report["metrics"], ensure_ascii=False, indent=1))
    else:
        print_report(report, golden, baseline)
    print(f"\nfull report: {path}")

    if args.write_baseline:
        target = harness.write_baseline(report)
        print(f"baseline written: {target}")
    if args.write_hotfixed_baseline:
        target = harness.write_hotfixed_baseline(report)
        print(f"hot-fixed v1 baseline written: {target}")
    return 1 if report["network_attempts"] else 0


if __name__ == "__main__":
    sys.exit(main())
