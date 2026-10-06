"""Evaluation metrics for the image-search harness.

compute(outcomes, labels) turns per-SKU engine outcomes plus the golden
labels into the numbers the merge gate (D2) is written against:

    auto_accept_precision   correct_exact among auto picks (None when there are none)
    wrong_auto_rate         auto picks that are not correct_exact / all SKUs
    correct_pick_rate       chosen or preselected is correct_exact / SKUs that have a correct candidate
    preselect_precision     correct_exact among picks pre-checked for a reviewer (None when there are none)
    review_rate             SKUs routed to a human / all SKUs
    not_found_rate          NOT_FOUND / all SKUs
    false_not_found_rate    NOT_FOUND although a correct candidate existed / SKUs that have a correct candidate
    pool_recall             a correct candidate reached the engine's pool / SKUs that have a correct candidate
    kill_attribution        rule -> number of correct_exact candidates that rule rejected
    auto_precision_wilson_lower   95% Wilson lower bound of auto_accept_precision

Everything is also broken down per stratum.

per_lane (lane_table) breaks the picks down by the lane the engine gave them (catalog_match.decide.pick_lane:
'strict' | 'unsure' | 'other'; an AUTO_PUBLISH pick is 'strict'), each with:

    n_picks / coverage      picks in the lane / all SKUs
    precision               correct_exact among the lane's picks, with its 95% Wilson lower bound
    n_auto_wrong / wrong_auto_rate   auto picks of the lane that are not correct_exact (/ all SKUs)

per_split repeats the headline numbers and the lanes on the fixed held-out split (split_of): rules tuned on live rows
are checked on rows nobody tuned them on.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

AUTO = "AUTO_PUBLISH"
PRESELECTED = "REVIEW_PRESELECTED"
UNSELECTED = "REVIEW_UNSELECTED"
NOT_FOUND = "NOT_FOUND"
PROVIDER_DOWN = "PROVIDER_DOWN"
VERIFIER_DOWN = "VERIFIER_DOWN"
ERROR = "ERROR"
REVIEW_DECISIONS = frozenset({PRESELECTED, UNSELECTED, VERIFIER_DOWN})

CORRECT = "correct_exact"

# Legacy (v1) image-quality gate reasons, as ImageQualityGatekeeper words them.
QUALITY_RULES_V1 = (
    "Image overexposed",
    "Image underexposed",
    "Low contrast detected",
    "Image too blurry",
    "Image over-compressed",
    "Aspect ratio drift",
    "Dimensions below minimum",
    "Heuristic Unified Score too low",
    "Rejected by Active Learning Quality Gate",
)


def is_quality_rule(rule: str) -> bool:
    """True for image-quality rules: the legacy gate reasons and every v2 'quality:*' reason."""
    return rule in QUALITY_RULES_V1 or rule.startswith("quality:")


@dataclass
class Outcome:
    """What one engine did for one SKU, in an engine-neutral shape."""

    sku_id: str
    engine: str
    decision: str                                   # AUTO_PUBLISH | REVIEW_* | NOT_FOUND | *_DOWN | ERROR
    chosen_id: Optional[str] = None                 # fixture candidate id of the pick shown as chosen/pre-checked
    chosen_url: Optional[str] = None
    needs_review: bool = True
    auto: bool = False                              # chosen and published without a human
    status: str = ""                                # engine-specific detail (v1: accepted/last_resort; v2: failure_code)
    failure_code: Optional[str] = None
    pool: List[str] = field(default_factory=list)   # candidate ids the engine considered
    kills: Dict[str, List[str]] = field(default_factory=dict)   # candidate id -> rules that rejected it
    queries: List[str] = field(default_factory=list)
    provider_calls: Dict[str, int] = field(default_factory=dict)
    vlm_calls: int = 0
    vlm_images_max: int = 0
    n_preselected: int = 0
    error: Optional[str] = None
    seconds: float = 0.0
    lane: Optional[str] = None                      # lane of the pick (decide.lane_of), None without a pick
    sources: Dict[str, Any] = field(default_factory=dict)   # the expansion round / local index (runners, sources.py)
    verifier: Dict[str, Any] = field(default_factory=dict)  # verifier usage of a run with a verifier config

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Outcome":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def labels_from_golden(golden: Mapping[str, Any], tuned: Optional[Iterable[str]] = None
                       ) -> Dict[str, Dict[str, Any]]:
    """sku_id -> ground truth: stratum, no_correct_candidate, named_case, expected_v2, auto_publish_allowed,
    the held-out split (split_of), and per candidate id its label, download result and provider."""
    tuned_keys = tuned_rows() if tuned is None else {row_key(t) for t in tuned}
    out: Dict[str, Dict[str, Any]] = {}
    for sku in golden["skus"]:
        cands = sku.get("candidates", [])
        out[sku["id"]] = {
            "stratum": sku.get("stratum", ""),
            "no_correct_candidate": bool(sku.get("no_correct_candidate")),
            "named_case": sku.get("named_case"),
            "expected_v2": sku.get("expected_v2"),
            "auto_publish_allowed": sku.get("auto_publish_allowed", True),
            "split": split_of(sku, tuned_keys),
            "candidates": {c["id"]: c["label"] for c in cands},
            "download": {c["id"]: c.get("download", "ok") for c in cands},
            "provider": {c["id"]: c.get("provider", "") for c in cands},
        }
    return out


# ---------------------------------------------------------------------------
# The fixed held-out split
# ---------------------------------------------------------------------------

# A row is held out when the sha256 of HOLDOUT_SALT + its normalised sheet name (row_key) falls in the first
# HOLDOUT_SHARE of the hash space, and no regression test is named after it (fixtures/tuned_rows.json). The salt and
# the share never change: the same row is held out in every set, every export and every run. A held-out row that a
# test later names moves to 'dev' and is reported (holdout_leaks), so a rule can never be tuned and checked on it.
HOLDOUT_SALT = "laqta-holdout-v1:"
HOLDOUT_SHARE = 0.3
SPLITS = ("dev", "held_out")
TUNED_ROWS_PATH = Path(__file__).resolve().parent / "fixtures" / "tuned_rows.json"


def row_key(name: Any) -> str:
    """The sheet name as the split reads it: upper case, one space between words, no punctuation."""
    text = re.sub(r"[^\w]+", " ", str(name or ""), flags=re.UNICODE)
    return " ".join(text.upper().split())


def sku_row_key(sku: Mapping[str, Any]) -> str:
    return row_key(sku.get("name_en") or sku.get("name") or sku.get("name_ar") or sku.get("id"))


def in_holdout_share(key: str) -> bool:
    digest = hashlib.sha256((HOLDOUT_SALT + key).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") / float(1 << 32) < HOLDOUT_SHARE


_tuned_cache: Dict[str, frozenset] = {}


def tuned_rows(path: Optional[Path] = None) -> frozenset:
    """row_key of every live row a regression test is named after (fixtures/tuned_rows.json; empty when missing)."""
    path = Path(path or TUNED_ROWS_PATH)
    key = str(path)
    if key not in _tuned_cache:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            names = doc.get("rows", []) if isinstance(doc, dict) else []
        except (OSError, ValueError):
            names = []
        _tuned_cache[key] = frozenset(row_key(n.get("name") if isinstance(n, dict) else n) for n in names)
    return _tuned_cache[key]


def split_of(sku: Mapping[str, Any], tuned: Optional[Iterable[str]] = None) -> str:
    """'held_out' or 'dev' (see HOLDOUT_SALT). A set may pin a row with "split" (a recorded export keeps the split
    it was exported with)."""
    pinned = sku.get("split")
    if pinned in SPLITS:
        return str(pinned)
    key = sku_row_key(sku)
    tuned_keys = tuned_rows() if tuned is None else tuned
    if key in tuned_keys:
        return "dev"
    return "held_out" if in_holdout_share(key) else "dev"


def holdout_leaks(skus: Iterable[Mapping[str, Any]], tuned: Optional[Iterable[str]] = None) -> List[str]:
    """Names in the held-out share that a regression test is named after (they count as 'dev')."""
    tuned_keys = tuned_rows() if tuned is None else set(tuned)
    out = []
    for sku in skus:
        key = sku_row_key(sku)
        if key in tuned_keys and in_holdout_share(key) and sku.get("split") not in SPLITS:
            out.append(key)
    return out


def wilson_lower_bound(successes: int, n: int, z: float = 1.96) -> Optional[float]:
    """Lower end of the Wilson score interval for a binomial proportion."""
    if n <= 0:
        return None
    p = successes / n
    z2 = z * z
    centre = p + z2 / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (centre - margin) / (1 + z2 / n)


def _ratio(num: int, den: int) -> Optional[float]:
    return None if den == 0 else num / den


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 4)


def _chosen_label(o: Outcome, lab: Mapping[str, Any]) -> Optional[str]:
    if not o.chosen_url and o.chosen_id is None:
        return None
    if o.chosen_id is None:
        return "unmapped"          # a URL that is not a fixture candidate is never correct
    return lab["candidates"].get(o.chosen_id, "unmapped")


def _core(outcomes: List[Outcome], labels: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    n = len(outcomes)
    has_correct = [o for o in outcomes if not labels[o.sku_id]["no_correct_candidate"]]
    n_with_correct = len(has_correct)

    n_auto = n_auto_correct = n_pre = n_pre_correct = 0
    n_review = n_not_found = n_false_nf = n_correct_pick = n_pool_hit = 0
    n_provider_down = n_error = 0
    for o in outcomes:
        lab = labels[o.sku_id]
        label = _chosen_label(o, lab)
        picked = label is not None
        if picked and o.auto:
            n_auto += 1
            n_auto_correct += label == CORRECT
        elif picked:
            n_pre += 1
            n_pre_correct += label == CORRECT
        if o.decision in REVIEW_DECISIONS or (picked and not o.auto):
            n_review += 1
        if o.decision == NOT_FOUND:
            n_not_found += 1
            if not lab["no_correct_candidate"]:
                n_false_nf += 1
        if o.decision == PROVIDER_DOWN:
            n_provider_down += 1
        if o.decision == ERROR:
            n_error += 1
        if not lab["no_correct_candidate"]:
            if label == CORRECT:
                n_correct_pick += 1
            if any(lab["candidates"].get(cid) == CORRECT for cid in o.pool):
                n_pool_hit += 1

    return {
        "n_skus": n,
        "n_with_correct": n_with_correct,
        "n_auto": n_auto,
        "n_auto_correct": n_auto_correct,
        "n_auto_wrong": n_auto - n_auto_correct,
        "n_preselected": n_pre,
        "n_preselected_correct": n_pre_correct,
        "n_correct_pick": n_correct_pick,
        "n_review": n_review,
        "n_not_found": n_not_found,
        "n_false_not_found": n_false_nf,
        "n_provider_down": n_provider_down,
        "n_error": n_error,
        "auto_accept_precision": _round(_ratio(n_auto_correct, n_auto)),
        "auto_precision_wilson_lower": _round(wilson_lower_bound(n_auto_correct, n_auto)),
        "wrong_auto_rate": _round(_ratio(n_auto - n_auto_correct, n)),
        "correct_pick_rate": _round(_ratio(n_correct_pick, n_with_correct)),
        "preselect_precision": _round(_ratio(n_pre_correct, n_pre)),
        "review_rate": _round(_ratio(n_review, n)),
        "not_found_rate": _round(_ratio(n_not_found, n)),
        "false_not_found_rate": _round(_ratio(n_false_nf, n_with_correct)),
        "pool_recall": _round(_ratio(n_pool_hit, n_with_correct)),
    }


def kill_attribution(outcomes: Iterable[Outcome], labels: Mapping[str, Mapping[str, Any]]) -> Dict[str, int]:
    """rule -> number of correct_exact candidates it rejected (the chosen candidate is never counted)."""
    counts: Counter = Counter()
    for o in outcomes:
        cands = labels[o.sku_id]["candidates"]
        for cid, rules in o.kills.items():
            if cid == o.chosen_id or cands.get(cid) != CORRECT:
                continue
            for rule in sorted(set(rules)):
                counts[rule] += 1
    return dict(sorted(counts.items()))


LANES = ("strict", "unsure", "other")
NO_LANE = "unknown"            # a pick whose lane the engine did not record (a v1 run, an old recording)


def lane_table(outcomes: Iterable[Outcome], labels: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per lane of the pick: picks, coverage (picks / all SKUs), precision with its 95% Wilson lower bound and the
    wrong auto-publishes. Every lane is listed, an empty one with n_picks 0 and precision None; 'unknown' only
    when a pick carries no lane."""
    outs = list(outcomes)
    n = len(outs)
    rows: Dict[str, Dict[str, int]] = {lane: {"n_picks": 0, "n_correct": 0, "n_auto": 0, "n_auto_wrong": 0}
                                       for lane in LANES}
    for o in outs:
        label = _chosen_label(o, labels[o.sku_id])
        if label is None:
            continue
        lane = o.lane if o.lane in LANES else NO_LANE
        row = rows.setdefault(lane, {"n_picks": 0, "n_correct": 0, "n_auto": 0, "n_auto_wrong": 0})
        row["n_picks"] += 1
        row["n_correct"] += label == CORRECT
        if o.auto:
            row["n_auto"] += 1
            row["n_auto_wrong"] += label != CORRECT
    out: Dict[str, Dict[str, Any]] = {}
    for lane, row in rows.items():
        out[lane] = dict(row, coverage=_round(_ratio(row["n_picks"], n)),
                         precision=_round(_ratio(row["n_correct"], row["n_picks"])),
                         precision_wilson_lower=_round(wilson_lower_bound(row["n_correct"], row["n_picks"])),
                         wrong_auto_rate=_round(_ratio(row["n_auto_wrong"], n)))
    return out


def compute(outcomes: Iterable[Any], labels: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """All metrics, overall, per stratum, per lane and per held-out split. Accepts Outcome objects or their dicts."""
    outs = [o if isinstance(o, Outcome) else Outcome.from_dict(o) for o in outcomes]
    missing = [o.sku_id for o in outs if o.sku_id not in labels]
    if missing:
        raise KeyError(f"outcomes for SKUs without labels: {missing[:5]}")

    result = _core(outs, labels)
    kills = kill_attribution(outs, labels)
    result["kill_attribution"] = kills
    result["quality_kills_on_correct"] = sum(v for k, v in kills.items() if is_quality_rule(k))
    result["decisions"] = dict(sorted(Counter(o.decision for o in outs).items()))

    vlm = [o.vlm_calls for o in outs]
    result["vlm_calls_mean"] = _round(sum(vlm) / len(vlm)) if vlm else None
    result["vlm_calls_max"] = max(vlm) if vlm else 0
    per_provider_max: Dict[str, int] = {}
    for o in outs:
        for name, calls in o.provider_calls.items():
            per_provider_max[name] = max(per_provider_max.get(name, 0), int(calls))
    result["provider_calls_max"] = dict(sorted(per_provider_max.items()))
    result["distinct_queries_max"] = max((len(set(o.queries)) for o in outs), default=0)

    per_stratum: Dict[str, Dict[str, Any]] = {}
    for stratum in sorted({labels[o.sku_id]["stratum"] for o in outs}):
        group = [o for o in outs if labels[o.sku_id]["stratum"] == stratum]
        core = _core(group, labels)
        per_stratum[stratum] = {k: core[k] for k in (
            "n_skus", "n_with_correct", "n_auto", "n_preselected", "auto_accept_precision", "wrong_auto_rate",
            "correct_pick_rate", "preselect_precision", "review_rate", "not_found_rate", "false_not_found_rate",
            "pool_recall")}
    result["per_stratum"] = per_stratum
    result["per_lane"] = lane_table(outs, labels)
    per_split: Dict[str, Dict[str, Any]] = {}
    for split in SPLITS:
        group = [o for o in outs if labels[o.sku_id].get("split", "dev") == split]
        core = _core(group, labels)
        per_split[split] = {k: core[k] for k in (
            "n_skus", "n_with_correct", "n_auto", "n_auto_wrong", "n_correct_pick", "auto_accept_precision",
            "auto_precision_wilson_lower", "wrong_auto_rate", "correct_pick_rate", "preselect_precision",
            "review_rate", "not_found_rate")}
        per_split[split]["per_lane"] = lane_table(group, labels)
    result["per_split"] = per_split
    return result


# Keys compared when checking a run against a stored baseline.
HEADLINE_KEYS = (
    "auto_accept_precision",
    "wrong_auto_rate",
    "correct_pick_rate",
    "preselect_precision",
    "review_rate",
    "not_found_rate",
    "false_not_found_rate",
    "pool_recall",
    "auto_precision_wilson_lower",
)


def diff_metrics(current: Mapping[str, Any], stored: Mapping[str, Any], tol: float = 0.01) -> List[str]:
    """Human-readable differences between two metric dicts, headline rates and kill counts."""
    problems = []
    for key in HEADLINE_KEYS:
        a, b = current.get(key), stored.get(key)
        if a is None or b is None:
            if a is not b:
                problems.append(f"{key}: now {a!r}, baseline {b!r}")
        elif abs(float(a) - float(b)) > tol:
            problems.append(f"{key}: now {a:.4f}, baseline {b:.4f}")
    ka, kb = current.get("kill_attribution", {}), stored.get("kill_attribution", {})
    for rule in sorted(set(ka) | set(kb)):
        if ka.get(rule, 0) != kb.get(rule, 0):
            problems.append(f"kill_attribution[{rule!r}]: now {ka.get(rule, 0)}, baseline {kb.get(rule, 0)}")
    for stratum, row in current.get("per_stratum", {}).items():
        base = stored.get("per_stratum", {}).get(stratum)
        if base is None:
            problems.append(f"per_stratum[{stratum}] missing from baseline")
            continue
        for key in ("correct_pick_rate", "wrong_auto_rate", "not_found_rate"):
            a, b = row.get(key), base.get(key)
            if (a is None) != (b is None) or (a is not None and abs(a - b) > tol):
                problems.append(f"per_stratum[{stratum}].{key}: now {a}, baseline {b}")
    return problems
