"""Re-route a run export offline with this checkout's rules: what would the CURRENT decide.route pre-select?

Usage (from the repository root; no network, no database, no cost):

    python3 scripts/reroute_export.py laqta_run_2026-10-04_1619.json         # the rows whose decision or pick changed
    python3 scripts/reroute_export.py laqta_run_2026-10-04_1619.json --all   # every row
    python3 scripts/reroute_export.py laqta_run_2026-10-04_1619.json --json rerouted.json

The export is scripts/export_run.py's file (format smoke_live/2, export laqta_run/1). For every row:
  * the SkuSpec is built again from the sheet columns (name, brand, size, barcode, name_ar, brand_ar, category)
    with the pipeline's own identity.build_sku_spec and no brand mappings (brand_conf 'sheet_raw'), plus the
    store spellings the run searched under (discovered_brands, or the 'brand_spelling:<spelling>' warnings;
    brand_discovery.apply);
  * every top[] candidate becomes a RankedCandidate with its RECORDED listing evidence: tier, size and pack
    status, conflicts, source class (and its trust), the url-only size conflict, a hard reject ('hard:*'), a
    failed download (download_error or 'download:*') and a failed hard quality gate ('quality:*'). The size each
    listing field states (title, URL slug, image file name) is parsed again from the recorded title and URLs;
  * the label reading (label_reader flags with the vlm brand / variant / size texts) is classified again with
    the CURRENT verify.classify;
  * the CURRENT decide.route decides, with the export's recorded auto-publish settings and provider answers.

APPROXIMATION, never a replay: the tiers are the recorded ones (nothing is scored again), the export keeps only
each row's top list (8 candidates at most), page titles are not in the export (a size stated only there is not
seen), and relaxed queries and cache hits are not recorded. A changed row says what the current rules would do
with the same candidates; a live run can still differ (scripts/replay_run.py replays a recorded cassette).
"""

import argparse
import contextlib
import json
import os
import socket
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

APPROXIMATION = ("APPROXIMATION: recorded tiers and listing evidence, the top candidates of each row only, no page "
                 "titles; the label readings are classified and every row routed again with this checkout's rules.")
PICK_DECISIONS = ("AUTO_PUBLISH", "REVIEW_PRESELECTED")
NONE = "-"
# the export's recorded settings that routing reads (everything else stays at its default: no DB, no .env)
ROUTE_SETTINGS = ("AUTO_PUBLISH_ENABLED", "AUTO_PUBLISH_BRANDS", "GTIN_POLICY")


# ---------------------------------------------------------------------------
# Offline environment
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def offline(meta: Mapping[str, Any]):
    """The export's routing settings, the dashboard settings (DB) unread and every outbound connection refused."""
    from catalog_match import settings

    def refuse(*args, **kwargs):
        raise OSError(f"reroute_export is offline: connection refused {args[1:2]!r}")

    values = ((meta.get("settings") or {}).get("values") or {}) if isinstance(meta, Mapping) else {}
    saved_env = {name: os.environ.get(name) for name in ROUTE_SETTINGS}
    saved_config, saved_connect = settings._config, socket.socket.connect
    try:
        settings._config = None
        socket.socket.connect = refuse
        for name in ROUTE_SETTINGS:
            value = values.get(name, settings.DEFAULTS.get(name))
            if isinstance(value, (list, tuple)):
                value = ",".join(str(v) for v in value)
            elif isinstance(value, bool):
                value = "true" if value else "false"
            os.environ[name] = "" if value is None else str(value)
        yield
    finally:
        settings._config, socket.socket.connect = saved_config, saved_connect
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


# ---------------------------------------------------------------------------
# One row
# ---------------------------------------------------------------------------

def store_spellings(row: Mapping[str, Any]) -> List[str]:
    """The store spellings of the sheet brand the run searched under: the row's discovered_brands, else the ones
    its 'brand_spelling:<spelling>' review warnings name (an export may carry the warning only)."""
    found = [str(d) for d in row.get("discovered_brands") or () if str(d).strip()]
    entries = [row] + [e for e in row.get("top") or () if isinstance(e, Mapping)]
    for entry in entries:
        codes = list(entry.get("warnings") or ()) + [str(r)[len("warn:"):] for r in entry.get("reasons") or ()
                                                     if str(r).startswith("warn:")]
        for code in codes:
            name, _, spelling = str(code).partition(":")
            if name == "brand_spelling" and spelling.strip():
                found.append(spelling.strip())
    return list(dict.fromkeys(found))


def build_spec(row: Mapping[str, Any]):
    """The SkuSpec of the export row, built like a sheet row with no brand mappings (brand_conf 'sheet_raw'), with
    the store spellings the run searched under (store_spellings)."""
    from catalog_match import brand_discovery
    from catalog_match.identity import build_sku_spec
    from catalog_match.text_norm import match_string

    sheet = {key: str(row.get(key) or "") for key in ("name", "brand", "size", "name_ar", "brand_ar", "category")}
    sheet["barcode"] = row.get("barcode") or None
    spec = build_sku_spec(sheet, None)
    for display in store_spellings(row):
        found = brand_discovery.Discovery(phrase=match_string(display), display=str(display), kind="spelling",
                                          sheet_phrase="", domains=())
        spec = brand_discovery.apply(spec, found)
    return spec


def _evidence_bits(text: Any) -> Dict[str, str]:
    """'tier=2 size=match brand=True coverage=1.0 source_class=generic' -> {'tier': '2', ...}."""
    out: Dict[str, str] = {}
    for part in str(text or "").split():
        key, sep, value = part.partition("=")
        if sep:
            out[key] = value
    return out


def _size_fields(spec, title: str, page_url: str, image_url: str) -> Dict[str, str]:
    """The size each listing field states compared with the SKU's (score.score_candidate's size_fields): the
    recorded title stands for the title (the page title is not in the export)."""
    from catalog_match.sizes import compare, parse_sizes
    from catalog_match.text_norm import url_path_text

    if spec.size is None:
        return {}
    fields = {"title": title, "page_slug": url_path_text(page_url),
              "image_file": url_path_text(image_url, filename_only=True)}
    out = {}
    for name, text in fields.items():
        found = parse_sizes(text, name)
        if found:
            out[name] = compare(spec.size, found)
    return out


def _verdict(spec, index: int, entry: Mapping[str, Any]):
    from catalog_match.models import VlmImageVerdict
    from catalog_match.verify import UNKNOWN, make_verdict

    lr = entry.get("label_reader")
    if not isinstance(lr, Mapping) or not lr:
        return None
    if lr.get("decision") == UNKNOWN:
        return VlmImageVerdict(index=index, decision=UNKNOWN)      # the reader never saw it: stays unread
    vlm = entry.get("vlm") if isinstance(entry.get("vlm"), Mapping) else {}
    return make_verdict(spec, index, {
        "brand_text": vlm.get("brand") or "", "variant_text": vlm.get("variant") or "",
        "size_text": vlm.get("size") or "", "pack_count": lr.get("pack_count"),
        "view": lr.get("view") or vlm.get("view") or "", "brand_match": lr.get("brand_match"),
        "variant_match": lr.get("variant_match"), "size_match": lr.get("size_match")})


def ranked_candidate(spec, entry: Mapping[str, Any], index: int):
    """One top[] entry as a RankedCandidate with its recorded score facts and a reading classified again."""
    from catalog_match.models import Candidate, CandidateScore, FetchedImage, QualityReport, RankedCandidate
    from catalog_match.score import TRUST_NAMES, TRUST_UAE_RETAILER
    from catalog_match.text_norm import url_host

    reasons = [str(r) for r in entry.get("reasons") or ()]
    page_url, image_url = str(entry.get("page_url") or ""), str(entry.get("image_url") or "")
    title = str(entry.get("title") or "")
    cand = Candidate(image_url=image_url, page_url=page_url, title=title, domain=str(entry.get("domain") or ""),
                     provider=str(entry.get("provider") or ""), query_id=str(entry.get("query_id") or ""),
                     rank=int(entry.get("rank") or index + 1), sanctioned=entry.get("sanctioned") is not False)
    bits = _evidence_bits(entry.get("evidence"))
    size_ev = entry.get("size_evidence") if isinstance(entry.get("size_evidence"), Mapping) else {}
    var_ev = entry.get("variant_evidence") if isinstance(entry.get("variant_evidence"), Mapping) else {}
    hard = [r[len("hard:"):] for r in reasons if r.startswith("hard:")]
    hard += [x for x in bits.get("reject", "").split(",") if x]
    source_class = str(entry.get("source_class") or bits.get("source_class") or "generic")
    trust = {name: level for level, name in TRUST_NAMES.items()}
    trust["reviewed_source"] = TRUST_UAE_RETAILER
    tier = entry.get("tier")
    try:
        coverage = float(bits.get("coverage") or 0.0)
    except ValueError:
        coverage = 0.0
    size_status = str(size_ev.get("size") or bits.get("size") or "unknown")
    score = CandidateScore(
        tier=None if hard or tier is None else int(tier),
        hard_reject=tuple(dict.fromkeys(hard)),
        matched={"brand": bits.get("brand") == "True", "brand_fields": {}, "size": size_status,
                 "size_fields": _size_fields(spec, title, page_url, image_url),
                 "pack": size_ev.get("pack") or "unknown", "variants": list(var_ev.get("matched") or ()),
                 "variants_found": dict(var_ev.get("found") or {}), "gtin": size_ev.get("gtin"),
                 "coverage": coverage, "source_trust": trust.get(source_class, 0), "source_class": source_class,
                 "page_domain": url_host(page_url) or cand.domain},
        conflicts=tuple(str(c) for c in entry.get("conflicts") or ()),
        size_status=size_status,
        url_only_size_conflict=bool(size_ev.get("url_only_size_conflict")),
    )
    download = entry.get("download_error") or next(
        (r.split(":", 1)[1] for r in reasons if r.startswith("download:")), None)
    fetched = FetchedImage(candidate=cand, ok=download is None, error=download or None,
                           width=entry.get("width"), height=entry.get("height"))
    quality_reasons = [r.split(":", 1)[1] for r in reasons if r.startswith("quality:")]
    quality = None if download else QualityReport(hard_ok=not quality_reasons, hard_reasons=quality_reasons)
    status = str(entry.get("status") or "eligible")
    return RankedCandidate(candidate=cand, score=score, fetched=fetched, quality=quality,
                           verdict=_verdict(spec, index, entry),
                           status="eligible" if status == "preselected" else status, reasons=reasons)


def _health(row: Mapping[str, Any]):
    from catalog_match.models import ProviderHealth

    calls = [c for c in row.get("provider_calls") or () if isinstance(c, Mapping)]
    if not calls:
        return [ProviderHealth(provider="export", status="ok")]       # not recorded: the search answered
    return [ProviderHealth(provider=str(c.get("provider") or ""), status=str(c.get("status") or ""),
                           http_status=c.get("http_status"), error=c.get("error"),
                           query_id=str(c.get("query_id") or "")) for c in calls]


def _verification(row: Mapping[str, Any], ranked: Sequence[Any]):
    from catalog_match.models import VerificationResult

    calls = int(row.get("vlm_calls") or 0)
    read = any(rc.verdict is not None for rc in ranked)
    down = row.get("failure_code") == "VERIFIER_DOWN" or row.get("decision") == "VERIFIER_DOWN"
    if read and down:
        # one of two calls answered (decide.route's 'partial')
        return [VerificationResult(status="ok", calls=calls), VerificationResult(status="unknown")]
    if read:
        return VerificationResult(status="ok", calls=calls)
    if down:
        return VerificationResult(status="unknown", calls=calls)
    return None


def _pick_view(rc) -> Dict[str, Any]:
    if rc is None:
        return {"title": "", "domain": "", "image_url": "", "reason": "", "warnings": []}
    from catalog_match.decide import warning_codes

    return {"title": rc.candidate.title, "domain": rc.candidate.domain or "",
            "image_url": rc.candidate.image_url,
            "reason": next((r for r in rc.reasons if r.startswith(("preselected:", "auto_publish"))), ""),
            "warnings": warning_codes(rc.reasons)}


def reroute_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """The export row's recorded decision next to what the current rules decide on the same candidates."""
    from catalog_match import decide

    spec = build_spec(row)
    ranked = [ranked_candidate(spec, entry, i) for i, entry in enumerate(row.get("top") or ())
              if isinstance(entry, Mapping)]
    outcome = decide.route(spec, ranked, _verification(row, ranked), _health(row), set())
    old_pick = str(row.get("winner") or "") if row.get("decision") in PICK_DECISIONS else ""
    new = _pick_view(outcome.winner)
    new_pick = new["image_url"] if outcome.decision in PICK_DECISIONS else ""
    return {
        "row": row.get("row"), "name": str(row.get("name") or ""),
        "old_decision": str(row.get("decision") or ""), "old_failure_code": row.get("failure_code"),
        "old_winner": old_pick,
        "new_decision": outcome.decision, "new_failure_code": outcome.failure_code,
        "new_winner": new_pick, "new_title": new["title"], "new_domain": new["domain"],
        "new_reason": new["reason"], "new_warnings": new["warnings"],
        "changed": outcome.decision != row.get("decision") or new_pick != old_pick,
    }


def reroute(export: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Every row of the export routed again (offline): see the module docstring."""
    with offline(export.get("meta") or {}):
        return [reroute_row(row) for row in export.get("rows") or () if isinstance(row, Mapping)]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def totals(results: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    def count(key, values):
        return sum(1 for r in results if r[key] in values)

    def picked(r, key):
        return r[key] in PICK_DECISIONS

    gained = [r["row"] for r in results if not picked(r, "old_decision") and picked(r, "new_decision")]
    lost = [r["row"] for r in results if picked(r, "old_decision") and not picked(r, "new_decision")]
    other = [r["row"] for r in results if r["changed"] and r["row"] not in gained and r["row"] not in lost]
    return {
        "rows": len(results),
        "preselected_before": count("old_decision", ("REVIEW_PRESELECTED",)),
        "preselected_after": count("new_decision", ("REVIEW_PRESELECTED",)),
        "auto_publish_before": count("old_decision", ("AUTO_PUBLISH",)),
        "auto_publish_after": count("new_decision", ("AUTO_PUBLISH",)),
        "unselected_before": count("old_decision", ("REVIEW_UNSELECTED",)),
        "unselected_after": count("new_decision", ("REVIEW_UNSELECTED",)),
        "gained": gained, "lost": lost, "other_changes": other,
    }


def _cut(text: str, width: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[:width - 1] + "~"


def format_report(results: Sequence[Mapping[str, Any]], show_all: bool = False) -> str:
    lines = [APPROXIMATION, ""]
    shown = [r for r in results if show_all or r["changed"]]
    header = (f"{'row':>4}  {'name':<42}  {'old decision':<18}  {'new decision':<18}  "
              "new winner (title | domain | reason)")
    lines += [header, "-" * len(header)]
    for r in shown:
        pick = NONE
        if r["new_winner"]:
            pick = f"{_cut(r['new_title'], 60)} | {r['new_domain'] or NONE} | {r['new_reason'] or NONE}"
            if r["new_warnings"]:
                pick += " | warn " + ",".join(r["new_warnings"])
        lines.append(f"{str(r['row']):>4}  {_cut(r['name'], 42):<42}  {r['old_decision']:<18}  "
                     f"{r['new_decision']:<18}  {pick}")
    if not shown:
        lines.append("(no row changed)")
    t = totals(results)
    lines += [
        "",
        f"rows {t['rows']} | pre-selected {t['preselected_before']} -> {t['preselected_after']} | "
        f"auto-publish {t['auto_publish_before']} -> {t['auto_publish_after']} | "
        f"no pick {t['unselected_before']} -> {t['unselected_after']}",
        f"gained a pick: {', '.join(map(str, t['gained'])) or NONE} | lost a pick: "
        f"{', '.join(map(str, t['lost'])) or NONE} | other changes: {', '.join(map(str, t['other_changes'])) or NONE}",
    ]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("export", help="the run export (scripts/export_run.py, laqta_run/1)")
    parser.add_argument("--all", action="store_true", help="list every row, not only the changed ones")
    parser.add_argument("--json", help="also write the before/after rows and totals to this file")
    args = parser.parse_args(argv)
    with open(args.export, encoding="utf-8") as fh:
        export = json.load(fh)
    results = reroute(export)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(format_report(results, show_all=args.all))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"note": APPROXIMATION, "totals": totals(results), "rows": results}, fh, ensure_ascii=False,
                      indent=1)
    return 0


if __name__ == "__main__":
    # the dashboard's settings live in its database: never loaded here (the export carries the routing settings)
    sys.modules.setdefault("config", None)
    sys.exit(main())
