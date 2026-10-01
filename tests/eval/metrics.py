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
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass, field
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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Outcome":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


def labels_from_golden(golden: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """sku_id -> ground truth: stratum, no_correct_candidate, named_case, expected_v2, auto_publish_allowed,
    and per candidate id its label, download result and provider."""
    out: Dict[str, Dict[str, Any]] = {}
    for sku in golden["skus"]:
        cands = sku.get("candidates", [])
        out[sku["id"]] = {
            "stratum": sku.get("stratum", ""),
            "no_correct_candidate": bool(sku.get("no_correct_candidate")),
            "named_case": sku.get("named_case"),
            "expected_v2": sku.get("expected_v2"),
            "auto_publish_allowed": sku.get("auto_publish_allowed", True),
            "candidates": {c["id"]: c["label"] for c in cands},
            "download": {c["id"]: c.get("download", "ok") for c in cands},
            "provider": {c["id"]: c.get("provider", "") for c in cands},
        }
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


def compute(outcomes: Iterable[Any], labels: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """All metrics, overall and per stratum. Accepts Outcome objects or their dicts."""
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
