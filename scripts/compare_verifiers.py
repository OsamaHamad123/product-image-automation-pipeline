"""A/B of the label reader: one set replayed under several verifier configurations, side by side.

    python scripts/compare_verifiers.py                              # the realistic set, the three configs
    python scripts/compare_verifiers.py --set golden
    python scripts/compare_verifiers.py --set tests/eval/fixtures/recorded/2026-10-06
    python scripts/compare_verifiers.py --configs current,strong_primary
    python scripts/compare_verifiers.py --live                       # fill the unrecorded answers with real calls

The configurations (tests/eval/verifier_configs.py), built from the current settings:

    current          VERIFIER_PRIMARY reads every batch, VERIFIER_STRONG takes its second looks / re-judges as set
    strong_primary   the strong model reads every batch, no second look
    strong_max2      the current setup with VERIFIER_STRONG_MAX_CALLS = 2

Each configuration runs the real CascadeVerifier (its own rules for the second look, the re-judge and the merge)
over readers that answer from the set's recorded readings: vlm_cassette.json 'verdicts' are the readings of its
'model', 'readings_by_model' {model id: ...} those of other models. An image a model never read answers UNKNOWN
and is counted ('answers recorded' below): a configuration is only as complete as its recorded answers. With
--live those images are read by the real model instead, after the cost estimate is printed and confirmed (--yes
answers it); nothing else is ever sent. No search, download or database is used.

For each configuration it prints: accuracy (correct picks), wrong picks and wrong auto-publishes, the per-lane
precision with its 95% Wilson lower bound, the reader's cost (USD per 100 products, from the priced usage of every
call: estimated tokens for a recorded answer, the billed ones for a live call), its calls, the time the reader
adds (calls x VERIFY_CALL_S, an estimate; a --live run also prints its wall time) and how many of the answers it
needed were recorded. The decision about a stronger reader is the owner's, on these numbers.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "tests" / "eval"
for _p in (str(REPO_ROOT), str(EVAL_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import harness  # noqa: E402
import metrics  # noqa: E402
import verifier_configs  # noqa: E402

VERIFY_CALL_S = {"primary": 5.0, "strong": 6.0}     # one reader call, an estimate (no recorded latency)


def resolve_set(name):
    """(golden path, label) of a named set, a folder or a golden_skus.json."""
    target = harness.SETS.get(name) or Path(name)
    target = Path(target)
    if target.is_dir():
        target = target / "golden_skus.json"
    if not target.is_file():
        raise FileNotFoundError(f"no such set: {name}")
    return target, (name if name in harness.SETS else target.parent.name)


def run_config(cfg, golden, cassette, mappings, set_name, scenario="normal", live=False):
    def factory(sku):
        return verifier_configs.replay_verifier(cfg, cassette, sku, scenario, live=live)

    t0 = time.perf_counter()
    report = harness.run_all("v2", scenario, golden=golden, cassette=cassette, mappings=mappings, set_name=set_name,
                             verifier_factory=factory, allow_network=live)
    return report, time.perf_counter() - t0


def summarize(cfg, report, wall_s):
    m = report["metrics"]
    outcomes = report["outcomes"]
    stats = [(o.get("verifier") or {}).get("stats") or {} for o in outcomes]
    usd = verifier_configs.usage_usd(outcomes)
    n = max(1, report["n_skus"])
    primary_calls = sum(int(s.get("primary_calls") or 0) for s in stats)
    strong_calls = sum(int(s.get("strong_calls") or 0) for s in stats)
    asked = sum(int(s.get("images_asked") or 0) for s in stats)
    missing = sum(int(s.get("not_recorded") or 0) for s in stats)
    wrong = (m["n_auto"] - m["n_auto_correct"]) + (m["n_preselected"] - m["n_preselected_correct"])
    return {
        "config": cfg.name, "reader": cfg.describe(), "n_skus": report["n_skus"],
        "correct_pick": m["n_correct_pick"], "with_correct": m["n_with_correct"],
        "correct_pick_rate": m["correct_pick_rate"], "wrong_picks": wrong, "wrong_auto": m["n_auto_wrong"],
        "auto": m["n_auto"], "per_lane": m["per_lane"], "per_split": m.get("per_split"),
        "usd": round(usd["primary"] + usd["strong"], 6), "usd_by_role": usd,
        "usd_per_100": round(100.0 * (usd["primary"] + usd["strong"]) / n, 4),
        "primary_calls": primary_calls, "strong_calls": strong_calls,
        "est_reader_s": round(primary_calls * VERIFY_CALL_S["primary"] + strong_calls * VERIFY_CALL_S["strong"], 1),
        "wall_s": round(wall_s, 1), "images_asked": asked, "answers_missing": missing,
        "answers_recorded": None if not asked else round(1.0 - missing / asked, 4),
        "live_calls": sum(int(s.get("live_calls") or 0) for s in stats),
    }


def _pct(v):
    return "  -  " if v is None else f"{100 * v:5.1f}%"


def _lane(row):
    if not row or not row.get("n_picks"):
        return "-"
    return f"{_pct(row['precision']).strip()} ({_pct(row['precision_wilson_lower']).strip()}) n={row['n_picks']}"


def print_table(rows, live):
    head = (f"{'config':15s}{'correct pick':>15s}{'wrong':>7s}{'wrong auto':>11s}{'$/100':>9s}{'calls p+s':>11s}"
            f"{'est. reader s':>15s}{'answers recorded':>18s}")
    print(head)
    print("-" * len(head))
    for r in rows:
        print(f"{r['config']:15s}{r['correct_pick']:>6d}/{r['with_correct']:<3d}{_pct(r['correct_pick_rate']):>6s}"
              f"{r['wrong_picks']:>7d}{r['wrong_auto']:>11d}{r['usd_per_100']:>9.4f}"
              f"{str(r['primary_calls']) + '+' + str(r['strong_calls']):>11s}{r['est_reader_s']:>15.1f}"
              f"{_pct(r['answers_recorded']):>18s}")
    print("\nprecision per lane of the pick (Wilson 95% lower bound), n = picks in the lane:")
    print(f"{'config':15s}{'strict':>28s}{'unsure':>28s}{'other':>28s}")
    for r in rows:
        print(f"{r['config']:15s}" + "".join(f"{_lane(r['per_lane'].get(lane)):>28s}" for lane in metrics.LANES))
    held = [r for r in rows if (r.get("per_split") or {}).get("held_out", {}).get("n_skus")]
    if held:
        print("\nheld-out rows only (no rule was tuned on them):")
        for r in held:
            h = r["per_split"]["held_out"]
            print(f"{r['config']:15s} correct pick {h['n_correct_pick']}/{h['n_with_correct']}, wrong auto "
                  f"{h['n_auto_wrong']}, strict {_lane(h['per_lane'].get('strict'))}")
    print()
    for r in rows:
        line = f"{r['config']:15s}{r['reader']}"
        if live:
            line += f" | {r['live_calls']} live calls, {r['wall_s']:.1f} s wall"
        print(line)
    if any(r["answers_missing"] for r in rows):
        print("\nanswers recorded < 100%: those images read as UNKNOWN (never picked), so that configuration looks "
              "weaker than it is. Record them (scripts/eval_record.py) or fill them with --live.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--set", default="realistic", help="golden, realistic (default) or a recorded folder")
    parser.add_argument("--configs", default=",".join(verifier_configs.CONFIG_NAMES),
                        help=f"comma-separated, from {', '.join(verifier_configs.CONFIG_NAMES)}")
    parser.add_argument("--scenario", default="normal", choices=("normal", "vlm_noisy"))
    parser.add_argument("--live", action="store_true",
                        help="read the images without a recorded answer with the real models (paid; asks first)")
    parser.add_argument("--yes", action="store_true", help="with --live: answer the cost question with yes")
    parser.add_argument("--sku", action="append", help="only this product id (repeatable)")
    parser.add_argument("--json", metavar="FILE", help="also write the comparison as JSON")
    args = parser.parse_args(argv)
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    try:
        golden_path, set_name = resolve_set(args.set)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    paths = harness.set_paths(golden_path)
    golden = harness.load_golden(paths["golden"])
    if args.sku:
        golden = dict(golden, skus=[s for s in golden["skus"] if s["id"] in set(args.sku)])
        if not golden["skus"]:
            parser.error("--sku: no such product in the set")
    cassette = harness.load_cassette(paths["cassette"])
    mappings = harness.load_mappings(paths["mappings"])
    if args.scenario == "vlm_noisy":
        cassette = harness.noisy_cassette(cassette, paths["noisy"])
    try:
        cfgs = verifier_configs.configs([c.strip() for c in args.configs.split(",") if c.strip()])
    except ValueError as exc:
        parser.error(str(exc))

    print(f"set {set_name}: {len(golden['skus'])} products, scenario {args.scenario}; recorded answers of "
          f"{verifier_configs.recorded_model(cassette)}"
          + (f" and {', '.join(sorted(cassette.get('readings_by_model') or {}))}"
             if cassette.get("readings_by_model") else ""))
    if args.live:
        total = 0.0
        for cfg in cfgs:
            gaps = verifier_configs.missing_answers(cfg, cassette, golden)
            est = verifier_configs.estimate_gap_usd(cfg, cassette, golden)
            total += est
            print(f"  {cfg.name}: {gaps['primary']} images without a recorded primary answer, {gaps['strong']} "
                  f"without a strong one: at most ${est:.4f}")
        print(f"live calls for every config together: at most ${total:.4f} (the model prices of the settings page)")
        if not verifier_configs.confirm("read them with the real models and pay for it? [yes/no]",
                                        "yes" if args.yes else None):
            print("nothing was called")
            return 1

    rows = []
    for cfg in cfgs:
        report, wall = run_config(cfg, golden, cassette, mappings, set_name, args.scenario, live=args.live)
        if report["network_attempts"] and not args.live:
            print(f"{cfg.name}: the replay tried to reach the network: {report['network_attempts'][:3]}")
            return 1
        rows.append(summarize(cfg, report, wall))
    print()
    print_table(rows, args.live)
    if args.json:
        Path(args.json).write_text(json.dumps({"set": set_name, "scenario": args.scenario, "live": args.live,
                                               "configs": rows}, ensure_ascii=False, indent=1) + "\n",
                                   encoding="utf-8")
        print(f"\nwritten: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
