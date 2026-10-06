"""Run a whole golden set through the engine and write a JSON report.

    report = run_all("v2")                  # catalog_match, offline
    report = run_all("v2", "gemini_down")   # catalog_match with the verifier down
    path = write_report(report)             # <tmp>/image_search_eval/<engine>-<scenario>-<stamp>.json

A report holds the per-SKU outcomes, the metrics from metrics.compute and the
network attempts the run made (it must make none). baseline_v1.json is a
trimmed report of the removed v1 engine, recorded before it was deleted: data
only, the 'before' state the v2 gate compares against. No code can rewrite it.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import logging
import os
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
# The ORIGINAL v1 (before any fix) stays in baseline_v1.json; the v2 gate compares against it.
# baseline_v1_hotfixed.json is v1 after its rollback hot-fixes (WP-4a). The v1 engine itself is
# deleted: both files are records (eval_report --stored), never regenerated.
HOTFIXED_BASELINE_PATH = FIXTURES / "baseline_v1_hotfixed.json"

ENGINES = ("v2",)
# Engines that existed once: run_all and eval_report refuse them with this reason.
REMOVED_ENGINES = {
    "v1": "the v1 search engine was removed: catalog_match (v2) is the only engine. What v1 did on the golden "
          "set stays on record in tests/eval/fixtures/baseline_v1.json (scripts/eval_report.py --stored original).",
}
# vlm_noisy: the cassette with the recorded misreads of VLM_NOISY_PATH laid over it (see load_cassette)
SCENARIOS = ("normal", "gemini_down", "vlm_noisy")
PROVIDER_SETS = runners.PROVIDER_SETS
VLM_NOISY_PATH = FIXTURES / "vlm_noisy.json"
ADVERSARIAL_PATH = FIXTURES / "adversarial_skus.json"
# Named sets (eval_report --set): each folder holds golden_skus.json with its own vlm_cassette.json and
# brand_mappings.json (and vlm_noisy.json when it records misreads). 'golden' is the committed 63-SKU set whose
# numbers the merge gate pins; 'realistic' is modelled on the owner's live rows (fixtures/realistic).
REALISTIC_PATH = FIXTURES / "realistic" / "golden_skus.json"
SETS = {"golden": GOLDEN_PATH, "realistic": REALISTIC_PATH}

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


def set_paths(name_or_path: Any) -> Dict[str, Optional[Path]]:
    """{golden, cassette, mappings, noisy} of a named set (SETS) or of a golden_skus.json path; None = not there."""
    golden = SETS.get(str(name_or_path), None) if not isinstance(name_or_path, Path) else None
    golden = Path(golden or name_or_path)
    folder = golden.parent

    def near(name: str) -> Optional[Path]:
        path = folder / name
        return path if path.exists() else None

    return {"golden": golden, "cassette": near("vlm_cassette.json"), "mappings": near("brand_mappings.json"),
            "noisy": near("vlm_noisy.json")}


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


def fixture_fingerprint() -> Dict[str, Any]:
    files = _normalised_sha256(FIXTURES / name for name in FIXTURE_FILES)
    files["imagegen.py"] = _normalised_sha256([EVAL_DIR / "imagegen.py"])["imagegen.py"]
    return {"sha256": _combined(files), "files": files}


def run_all(engine: str = "v2", scenario: str = "normal", *, golden: Optional[Mapping[str, Any]] = None,
            cassette: Optional[Mapping[str, Any]] = None, mappings: Optional[Dict[str, Any]] = None,
            sku_ids: Optional[Iterable[str]] = None,
            progress: Optional[Callable[[int, int, metrics.Outcome], None]] = None,
            provider_set: str = "serper", set_name: str = "golden",
            noisy_path: Optional[os.PathLike] = None, sources: Optional[Mapping[str, Any]] = None,
            verifier_factory: Optional[Callable[[Mapping[str, Any]], Any]] = None,
            allow_network: bool = False) -> Dict[str, Any]:
    """Replay every golden SKU through one engine with the network blocked; return a report dict.

    provider_set ("serper" | "bing_only") picks the production provider set v2 runs with.
    The 'vlm_noisy' scenario lays vlm_noisy.json (noisy_path, default the committed one) over the
    cassette (the committed one unless a cassette is passed) and then replays like 'normal'.
    set_name only labels the report (SETS: 'golden', 'realistic', or a recorded folder's name).
    sources ({"expansion": bool, "index": bool}): run the expansion round / the local index through the fakes of
    tests/eval/sources.py (the set must already hold its sources overlay: sources.apply_overlay). None: neither,
    the default replay. verifier_factory(sku) -> a verifier instead of the cassette (compare_verifiers);
    allow_network only for a live reader the caller confirmed (the providers and downloads stay fixtures).
    """
    if engine in REMOVED_ENGINES:
        raise ValueError(REMOVED_ENGINES[engine])
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {ENGINES}")
    if scenario not in SCENARIOS:
        raise ValueError(f"scenario must be one of {SCENARIOS}")
    if provider_set not in PROVIDER_SETS:
        raise ValueError(f"provider_set must be one of {PROVIDER_SETS}")
    golden = golden or load_golden()
    cassette = cassette or load_cassette()
    if scenario == "vlm_noisy":
        cassette = noisy_cassette(cassette, noisy_path)
    mappings = mappings if mappings is not None else load_mappings()
    wanted = set(sku_ids) if sku_ids else None
    skus = [s for s in golden["skus"] if wanted is None or s["id"] in wanted]

    def run_one(sku, cassette, scenario, mappings):  # type: ignore[no-untyped-def]
        extra: Dict[str, Any] = {}
        if sources:
            extra["sources"] = sources
        if verifier_factory is not None:
            extra["verifier"] = verifier_factory(sku)
        return runners.run_v2(sku, cassette, scenario=scenario, mappings=mappings, provider_set=provider_set,
                              **extra)

    outcomes: List[metrics.Outcome] = []
    attempts: List[str] = []
    t0 = time.perf_counter()
    # no store spelling another run proved: the scorecard depends on this run's rows only
    from catalog_match import brand_discovery
    brand_discovery.forget_all()
    with (contextlib.nullcontext(attempts) if allow_network else runners.network_blocked(attempts)):
        for i, sku in enumerate(skus, 1):
            outcome = run_one(sku, cassette, scenario=scenario, mappings=mappings)
            outcomes.append(outcome)
            if progress:
                progress(i, len(skus), outcome)
    seconds = time.perf_counter() - t0

    labels = metrics.labels_from_golden({"skus": skus})
    report = {
        "engine": engine,
        "set": set_name,
        "scenario": scenario,
        "provider_set": provider_set,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(seconds, 2),
        "n_skus": len(skus),
        "network_attempts": runners.outbound_attempts(attempts),
        "metrics": metrics.compute(outcomes, labels),
        "outcomes": [o.to_dict() for o in outcomes],
    }
    if sources:
        import sources as sources_mod
        report["sources"] = dict(sources)
        report["sources_summary"] = sources_mod.summarize(report["outcomes"])
    if allow_network:
        report["network_allowed"] = True
    report["fixture_fingerprint"] = fixture_fingerprint()
    return report


def report_dir() -> Path:
    path = Path(os.environ.get("EVAL_REPORT_DIR") or Path(tempfile.gettempdir()) / "image_search_eval")
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_report(report: Mapping[str, Any], path: Optional[os.PathLike] = None) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    label = report["scenario"] if report.get("set", "golden") == "golden" else f"{report['set']}-{report['scenario']}"
    target = Path(path) if path else report_dir() / f"{report['engine']}-{label}-{stamp}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    return target


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
