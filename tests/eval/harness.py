"""Run a whole golden set through one engine and write a JSON report.

    report = run_all("v1")                  # legacy search, offline
    report = run_all("v2", "gemini_down")   # catalog_match with the verifier down
    path = write_report(report)             # <tmp>/image_search_eval/<engine>-<scenario>-<stamp>.json

A report holds the per-SKU outcomes, the metrics from metrics.compute and the
network attempts the run made (it must make none). baseline_v1.json is a
trimmed report of the legacy engine, written once by
scripts/eval_report.py --engine v1 --write-baseline.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
for _p in (str(REPO_ROOT), str(EVAL_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import metrics  # noqa: E402
import runners  # noqa: E402

log = logging.getLogger(__name__)

FIXTURES = EVAL_DIR / "fixtures"
GOLDEN_PATH = FIXTURES / "golden_skus.json"
CASSETTE_PATH = FIXTURES / "vlm_cassette.json"
MAPPINGS_PATH = FIXTURES / "brand_mappings.json"
BASELINE_PATH = FIXTURES / "baseline_v1.json"
# The ORIGINAL v1 (before any fix) stays in baseline_v1.json and is never regenerated; the
# v2 gate compares against it. The legacy replay after the v1 rollback hot-fixes (WP-4a)
# is recorded separately, so a live v1 replay can still be pinned exactly.
HOTFIXED_BASELINE_PATH = FIXTURES / "baseline_v1_hotfixed.json"

ENGINES = ("v1", "v2")
# vlm_noisy: the cassette with the recorded misreads of VLM_NOISY_PATH laid over it (see load_cassette)
SCENARIOS = ("normal", "gemini_down", "vlm_noisy")
PROVIDER_SETS = runners.PROVIDER_SETS
VLM_NOISY_PATH = FIXTURES / "vlm_noisy.json"
ADVERSARIAL_PATH = FIXTURES / "adversarial_skus.json"

# Source files whose behaviour the v1 baseline records. When any of them
# changes (for example the v1 rollback hot-fixes), the live v1 run is no
# longer expected to reproduce the stored numbers exactly.
LEGACY_SOURCES = ("image_search.py", "image_quality_gatekeeper.py", "aesthetics_engine.py", "image_dedup_bktree.py")
FIXTURE_FILES = ("golden_skus.json", "vlm_cassette.json", "brand_mappings.json")


def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def load_golden(path: Optional[os.PathLike] = None) -> Dict[str, Any]:
    path = Path(path or GOLDEN_PATH)
    golden = _read_json(path)
    for sku in golden["skus"]:
        sku.setdefault("_base_dir", str(path.parent))   # recorded sets keep their blobs next to the JSON
    return golden


def load_cassette(path: Optional[os.PathLike] = None) -> Dict[str, Any]:
    return _read_json(Path(path or CASSETTE_PATH))


def overlay_readings(cassette: Mapping[str, Any], readings: Mapping[str, Mapping[str, Mapping[str, Any]]]
                     ) -> Dict[str, Any]:
    """A copy of the cassette with {sku_id: {cand_id: {field: value}}} merged into its verdicts."""
    out = json.loads(json.dumps(cassette))
    verdicts = out.setdefault("verdicts", {})
    for sku_id, per_cand in readings.items():
        for cand_id, fields in per_cand.items():
            verdicts.setdefault(sku_id, {}).setdefault(cand_id, {}).update(fields)
    return out


def noisy_cassette(cassette: Mapping[str, Any], path: Optional[os.PathLike] = None) -> Dict[str, Any]:
    """The 'vlm_noisy' scenario: the recorded readings with the misreads of vlm_noisy.json laid over them."""
    return overlay_readings(cassette, _read_json(Path(path or VLM_NOISY_PATH))["misreads"])


def correct_absent(golden: Mapping[str, Any], cassette: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(set, cassette): every golden SKU that has a correct candidate, with its correct_exact candidates removed.

    Nothing correct is left, so any auto-publish is wrong: what the identity rules (D4) and the
    routing (D10) must hold when the right listing is simply not on the web. The cassette
    carries the same readings under the derived SKU ids.
    """
    skus, readings = [], {}
    for sku in golden["skus"]:
        if sku["no_correct_candidate"]:
            continue
        derived = json.loads(json.dumps(sku))
        derived["id"] = sku["id"] + "~no-correct"
        derived["named_case"] = None
        derived["expected_v2"] = None
        derived["no_correct_candidate"] = True
        derived["candidates"] = [c for c in derived["candidates"] if c["label"] != "correct_exact"]
        skus.append(derived)
        readings[derived["id"]] = dict(cassette.get("verdicts", {}).get(sku["id"], {}))
    return {"skus": skus}, overlay_readings({k: v for k, v in cassette.items() if k != "verdicts"}, readings)


def load_adversarial(path: Optional[os.PathLike] = None, golden: Optional[Mapping[str, Any]] = None,
                     cassette: Optional[Mapping[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """(golden-shaped set, cassette) of the adversarial cases in adversarial_skus.json.

    Each case starts from a golden SKU ('base'), drops candidates ('drop'), overrides SKU
    fields ('set') and candidate fields ('candidates', labels included, each with its reason)
    and the recorded model readings ('readings': the VLM misreads the case is about). The
    committed golden_skus.json / vlm_cassette.json stay untouched (the v1 baseline pins them).
    """
    golden = golden or load_golden()
    cassette = cassette or load_cassette()
    doc = _read_json(Path(path or ADVERSARIAL_PATH))
    by_id = {s["id"]: s for s in golden["skus"]}
    skus, readings = [], {}
    for case in doc["cases"]:
        base = by_id[case["base"]]
        sku = json.loads(json.dumps(base))
        sku.update(case.get("set", {}))
        sku["id"] = case["id"]
        sku["stratum"] = "adversarial"
        sku["named_case"] = None
        sku["expected_v2"] = case["expected_v2"]
        sku["adversarial_rule"] = case["rule"]
        dropped = set(case.get("drop", []))
        unknown = (dropped | set(case.get("candidates", {})) | set(case.get("readings", {}))) - {
            c["id"] for c in base["candidates"]}
        if unknown:
            raise ValueError(f"adversarial case {case['id']}: unknown candidate ids {sorted(unknown)}")
        sku["candidates"] = [c for c in sku["candidates"] if c["id"] not in dropped]
        for c in sku["candidates"]:
            c.update({k: v for k, v in case.get("candidates", {}).get(c["id"], {}).items() if k != "why"})
        sku["no_correct_candidate"] = not any(c["label"] == "correct_exact" for c in sku["candidates"])
        skus.append(sku)
        base_readings = cassette.get("verdicts", {}).get(case["base"], {})
        readings[sku["id"]] = {c["id"]: dict(base_readings.get(c["id"], {}),
                                             **{k: v for k, v in case.get("readings", {}).get(c["id"], {}).items()
                                                if k != "why"})
                               for c in sku["candidates"] if c["id"] in base_readings}
    return {"skus": skus}, overlay_readings({k: v for k, v in cassette.items() if k != "verdicts"}, readings)


def load_mappings(path: Optional[os.PathLike] = None) -> Dict[str, Any]:
    return runners.load_mappings(path or MAPPINGS_PATH)


def load_baseline(path: Optional[os.PathLike] = None) -> Dict[str, Any]:
    return _read_json(Path(path or BASELINE_PATH))


def load_hotfixed_baseline(path: Optional[os.PathLike] = None) -> Dict[str, Any]:
    return _read_json(Path(path or HOTFIXED_BASELINE_PATH))


def _normalised_sha256(paths: Iterable[Path]) -> Dict[str, str]:
    """sha256 per file with CRLF folded to LF, so a Windows checkout hashes the same."""
    out = {}
    for path in paths:
        data = path.read_bytes().replace(b"\r\n", b"\n") if path.exists() else b""
        out[path.name] = hashlib.sha256(data).hexdigest()
    return out


def _combined(digests: Mapping[str, str]) -> str:
    return hashlib.sha256(json.dumps(dict(sorted(digests.items()))).encode("utf-8")).hexdigest()


def legacy_fingerprint() -> Dict[str, Any]:
    files = _normalised_sha256(REPO_ROOT / name for name in LEGACY_SOURCES)
    return {"sha256": _combined(files), "files": files}


def fixture_fingerprint() -> Dict[str, Any]:
    files = _normalised_sha256(FIXTURES / name for name in FIXTURE_FILES)
    files["imagegen.py"] = _normalised_sha256([EVAL_DIR / "imagegen.py"])["imagegen.py"]
    return {"sha256": _combined(files), "files": files}


def git_commit() -> Optional[str]:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), capture_output=True, text=True,
                             timeout=10, check=False)
        return out.stdout.strip() or None
    except Exception:
        return None


def run_all(engine: str = "v1", scenario: str = "normal", *, golden: Optional[Mapping[str, Any]] = None,
            cassette: Optional[Mapping[str, Any]] = None, mappings: Optional[Dict[str, Any]] = None,
            sku_ids: Optional[Iterable[str]] = None,
            progress: Optional[Callable[[int, int, metrics.Outcome], None]] = None,
            provider_set: str = "serper") -> Dict[str, Any]:
    """Replay every golden SKU through one engine with the network blocked; return a report dict.

    provider_set ("serper" | "bing_only") picks the production provider set v2 runs with.
    The 'vlm_noisy' scenario lays vlm_noisy.json over the cassette (the committed one
    unless a cassette is passed) and then replays like 'normal'.
    """
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}")
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {SCENARIOS}")
    if provider_set not in PROVIDER_SETS:
        raise ValueError(f"provider_set must be one of {PROVIDER_SETS}")
    golden = golden or load_golden()
    cassette = cassette or load_cassette()
    if scenario == "vlm_noisy":
        cassette = noisy_cassette(cassette)
    mappings = mappings if mappings is not None else load_mappings()
    wanted = set(sku_ids) if sku_ids else None
    skus = [s for s in golden["skus"] if wanted is None or s["id"] in wanted]
    if engine == "v1":
        run_one = runners.run_legacy
    else:
        def run_one(sku, cassette, scenario, mappings):  # type: ignore[no-untyped-def]
            return runners.run_v2(sku, cassette, scenario=scenario, mappings=mappings, provider_set=provider_set)

    outcomes: List[metrics.Outcome] = []
    attempts: List[str] = []
    t0 = time.perf_counter()
    if engine == "v2":
        # no store spelling another run proved: the scorecard depends on this run's rows only
        from catalog_match import brand_discovery
        brand_discovery.forget_all()
    with runners.network_blocked(attempts):
        for i, sku in enumerate(skus, 1):
            outcome = run_one(sku, cassette, scenario=scenario, mappings=mappings)
            outcomes.append(outcome)
            if progress:
                progress(i, len(skus), outcome)
    seconds = time.perf_counter() - t0

    labels = metrics.labels_from_golden({"skus": skus})
    report = {
        "engine": engine,
        "scenario": scenario,
        "provider_set": provider_set if engine == "v2" else None,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(seconds, 2),
        "n_skus": len(skus),
        "network_attempts": runners.outbound_attempts(attempts),
        "metrics": metrics.compute(outcomes, labels),
        "outcomes": [o.to_dict() for o in outcomes],
    }
    if engine == "v1":
        report["legacy_fingerprint"] = legacy_fingerprint()
    report["fixture_fingerprint"] = fixture_fingerprint()
    return report


def report_dir() -> Path:
    path = Path(os.environ.get("EVAL_REPORT_DIR") or Path(tempfile.gettempdir()) / "image_search_eval")
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_report(report: Mapping[str, Any], path: Optional[os.PathLike] = None) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(path) if path else report_dir() / f"{report['engine']}-{report['scenario']}-{stamp}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return target


HOTFIXED_DESCRIPTION = (
    "Legacy v1 (image_search.search_best_product_image with SEARCH_ENGINE=v1) replayed offline on the golden "
    "fixtures AFTER the v1 rollback hot-fixes (no unverified pick counted as success, fail-closed Gemini, no "
    "hard exposure/blur gates). The live legacy replay must reproduce it while the legacy sources keep this "
    "fingerprint. Regenerate only with: python scripts/eval_report.py --engine v1 --write-hotfixed-baseline. "
    "The ORIGINAL pre-fix v1 stays in baseline_v1.json.")


def baseline_payload(report: Mapping[str, Any], golden: Optional[Mapping[str, Any]] = None,
                     description: Optional[str] = None) -> Dict[str, Any]:
    """The committed v1 baseline: aggregate metrics plus a per-SKU record of what v1 did."""
    if report.get("engine") != "v1" or report.get("scenario") != "normal":
        raise ValueError("the baseline is the legacy engine on the normal scenario")
    golden = golden or load_golden()
    labels = metrics.labels_from_golden(golden)
    per_sku = {}
    for o in report["outcomes"]:
        lab = labels[o["sku_id"]]
        per_sku[o["sku_id"]] = {
            "decision": o["decision"],
            "status": o["status"],
            "chosen_id": o["chosen_id"],
            "chosen_label": lab["candidates"].get(o["chosen_id"]) if o["chosen_id"] else None,
            "auto": o["auto"],
            "needs_review": o["needs_review"],
            "kills": o["kills"],
            "queries": o["queries"],
        }
    return {
        "description": description or (
            "Legacy v1 (image_search.search_best_product_image) replayed offline on the golden fixtures before any "
            "fix. Aggregate metrics and what v1 did per SKU, kept as data so the 'before' state stays on record "
            "after v1 is hot-fixed. Regenerate only with: python scripts/eval_report.py --engine v1 "
            "--write-baseline (from a checkout of legacy_commit when v1 has changed since)."),
        "engine": "v1",
        "scenario": "normal",
        "generated_at": report["generated_at"],
        "legacy_commit": git_commit(),
        "legacy_fingerprint": report["legacy_fingerprint"],
        "fixture_fingerprint": report["fixture_fingerprint"],
        "n_skus": report["n_skus"],
        "metrics": report["metrics"],
        "per_sku": per_sku,
    }


def write_baseline(report: Mapping[str, Any], path: Optional[os.PathLike] = None,
                   description: Optional[str] = None) -> Path:
    payload = baseline_payload(report, description=description)
    target = Path(path or BASELINE_PATH)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return target


def write_hotfixed_baseline(report: Mapping[str, Any], path: Optional[os.PathLike] = None) -> Path:
    return write_baseline(report, path or HOTFIXED_BASELINE_PATH, description=HOTFIXED_DESCRIPTION)


def fmt_rate(value: Any) -> str:
    return "  -  " if value is None else f"{100 * float(value):5.1f}%"


def stratum_table(m: Mapping[str, Any]) -> str:
    """Plain-text per-stratum table of the headline rates."""
    cols = (("n", "n_skus"), ("auto", "n_auto"), ("pre", "n_preselected"), ("auto prec", "auto_accept_precision"),
            ("wrong auto", "wrong_auto_rate"), ("correct pick", "correct_pick_rate"),
            ("presel prec", "preselect_precision"), ("review", "review_rate"), ("not found", "not_found_rate"),
            ("false NF", "false_not_found_rate"), ("pool recall", "pool_recall"))
    width = max([len("stratum"), len("ALL")] + [len(s) for s in m.get("per_stratum", {})]) + 2
    head = "stratum".ljust(width) + "".join(c[0].rjust(13) for c in cols)
    lines = [head, "-" * len(head)]

    def row(name: str, data: Mapping[str, Any]) -> str:
        cells = []
        for _, key in cols:
            v = data.get(key)
            cells.append((str(v) if key.startswith("n_") else fmt_rate(v)).rjust(13))
        return name.ljust(width) + "".join(cells)

    for stratum, data in m.get("per_stratum", {}).items():
        lines.append(row(stratum, data))
    lines.append("-" * len(head))
    lines.append(row("ALL", m))
    return "\n".join(lines)


def lane_table(m: Mapping[str, Any]) -> str:
    """Plain-text per-lane table (metrics.lane_table) for ALL and each held-out split half."""
    head = (f"{'rows':15s}{'lane':9s}{'picks':>7s}{'coverage':>10s}{'precision':>11s}{'Wilson 95% low':>16s}"
            f"{'auto':>6s}{'wrong auto':>12s}")
    lines = [head, "-" * len(head)]
    groups = [("ALL", m.get("n_skus"), m.get("per_lane", {}))]
    for split, data in (m.get("per_split") or {}).items():
        groups.append((split, data.get("n_skus"), data.get("per_lane", {})))
    for name, n, lanes in groups:
        for lane, row in lanes.items():
            if lane == metrics.NO_LANE and not row.get("n_picks"):
                continue
            lines.append(f"{(name + f' ({n})') if lane == 'strict' else '':15s}{lane:9s}{row['n_picks']:7d}"
                         f"{fmt_rate(row['coverage']):>10s}{fmt_rate(row['precision']):>11s}"
                         f"{fmt_rate(row['precision_wilson_lower']):>16s}{row['n_auto']:6d}"
                         f"{row['n_auto_wrong']:6d} {fmt_rate(row['wrong_auto_rate']):>5s}")
    return "\n".join(lines)
