"""One JSON file with every row of a run, to analyse it away from the owner's PC. No search, no cost.

Usage (from the repository root, on the machine with the database):

    python scripts/export_run.py                              # the latest run -> laqta_run_<date>.json
    python scripts/export_run.py --run-id 3f2a9c1d0b7e4a55     # one run
    python scripts/export_run.py --review                     # every product waiting for review, any run
    python scripts/export_run.py --out runs/laqta_run.json
    python scripts/compare_runs.py runs/after.json laqta_run_2026-10-04_1530.json   # against a dry run

The dashboard's «تصدير تقرير للتحليل» button (Run page) makes the same file through cli_bridge.py 'export_run'.

What a row holds (read from automation_queue, its stored trace and its stored review candidates; nothing is
searched again): the sheet row number, product name, brand, size, the barcode (and whether it is a valid GTIN),
the decision and failure code, why there is no pick (catalog_match.explain: the reason key and the Arabic
sentence) and the sheet's gaps, the top 8 candidates (image and page URL, domain, title, identity tier, status,
reasons and warnings, the label reader's reading, the size / variant evidence, the provider and query id that found
it and its pHash; read from curation_candidates, or for a published or approved row whose review candidates are
gone, from the stored outcome's own top list), the stores' spelling of the brand the search used
(discovered_brands: the trace's outcome, or an older pick's 'brand_spelling' warning), the provider calls and the
estimated cost when the trace says it, and the search's wall time per stage ('timings', milliseconds: retrieval,
fetch, quality, verify, expansion when the round ran, total; a row saved before the timings were recorded has an
empty one). The summary adds 'timings': p50 / p90 / total seconds per stage over the rows that have them.

What the reviewers did (review_decisions, a rejection taken back with «تراجع عن الرفض» left out): per row 'review',
the latest decision for the row's sku_key (else its row number): decision (approved | rejected | manual_upload), the
image and its page domain, was_preselected, the engine's decision, the label reader's decision, the reject reason and
the time, plus how many decisions the row has; None without one. A row with a current approval (resolved_products)
gets 'approval': the published link (the Cloudinary URL the sheet holds), the source image, human_approved or
auto_verified and when; and 'page_gtin' / 'page_gtin_page': the barcode the approved image's store page stated for a
row without one (scripts/export_barcodes.py lists them). Whether an approval went out without background removal
(bg_skipped) is not recorded per approval, only counted per run (run_report), so it is not in the row.
summary.review: counts of the rows' latest decisions (approved / rejected / manual_upload; pending = waiting for
review without one; no_decision = the rest; undone = rejections taken back), and for the rows whose engine pick was
pre-checked (AUTO_PUBLISH / REVIEW_PRESELECTED) whether the reviewer accepted it, replaced it (another image or an
upload), rejected it or not yet, also split by the pick's warning set (no_warning, vlm_unsure only, other, unknown
when the export no longer has the pick's warnings) and counted for the picks with the reason size_corroborated
(preselected_size_corroborated). A database without review rows gives an empty block.

The run's metadata: the code version (git commit), every catalog_match
setting without any key (catalog_match.cassette.settings_snapshot: secret settings only as set / not set) and the
run_history row of the run.

The file has the shape of scripts/smoke_live.py --json (format smoke_live/2: meta, summary, rows), so
scripts/compare_runs.py reads it. It never holds a key, a token, a password, a cookie, the Google credentials or
the Cloudinary secret: every configured secret value (smoke_live.secret_values and the cassette's hidden values,
the database password too) is replaced by [hidden] wherever it appears, and every error text loses its
'key=' / 'api_key=' / 'cx=' query values. No image or page body is stored, only links and evidence.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
from collections import Counter

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS)
for _p in (REPO_ROOT, SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import smoke_live  # noqa: E402  (same folder: the row and summary shape, redaction and prices)

EXPORT_FORMAT = "laqta_run/1"
TOP_N = 8
SCOPES = ("latest", "run", "review")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_EXPANSION_QUERY_RE = re.compile(r"^X(\d+|U)$")
RUN_HISTORY_COLUMNS = ("run_id", "run_trigger", "started_at", "ended_at", "outcome", "stop_reason", "attempts",
                       "enqueued", "searched", "auto_published", "ready_for_review", "not_found", "failed",
                       "provider_down", "pending_left", "spend_usd", "spend_source")


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

def secrets():
    """Every value that must never appear in the file: the configured keys (smoke_live and the cassette's lists),
    the CSE engine ids, the proxy credentials and the database password, longest first."""
    values = set(smoke_live.secret_values())
    try:
        from catalog_match import cassette
        values.update(cassette.configured_hidden_values())
    except Exception:
        pass
    for name in ("DB_PASSWORD", "TELEGRAM_BOT_TOKEN", "CLOUDINARY_API_SECRET", "CLOUDINARY_API_KEY"):
        value = str(os.getenv(name) or "").strip()
        if len(value) >= 4:
            values.add(value)
    return sorted((v for v in values if v), key=len, reverse=True)


# ---------------------------------------------------------------------------
# Reading the queue
# ---------------------------------------------------------------------------

def _loads(value, default):
    if isinstance(value, (dict, list)):
        return value
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _query(sql, params=()):
    import local_cache_db

    conn = local_cache_db.get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        return [dict(r) for r in cursor.fetchall() or []]
    finally:
        conn.close()


def latest_run_id():
    """The run the dashboard calls the last one (QueueStats::latestRunId): the current run of automation_state
    when it has rows, else the run that searched last (outcome.searched_at), else the last updated one."""
    try:
        state = _query("SELECT run_id FROM automation_state WHERE `key` = 'active_session'")
        current = str((state[0] if state else {}).get("run_id") or "").strip()
        if current and _query("SELECT 1 AS one FROM automation_queue WHERE run_id = %s LIMIT 1", (current,)):
            return current
    except Exception:
        pass
    rows = _query(
        "SELECT run_id, MAX(JSON_UNQUOTE(JSON_EXTRACT(trace_json, '$.outcome.searched_at'))) AS last_search, "
        "MAX(updated_at) AS last_update FROM automation_queue WHERE run_id IS NOT NULL AND run_id <> '' "
        "GROUP BY run_id ORDER BY last_search DESC, last_update DESC LIMIT 1")
    return str(rows[0]["run_id"]) if rows else None


def queue_rows(scope, run_id=None):
    """(rows, run_id) of the scope: 'latest' (run_id found here), 'run' (run_id given) or 'review'."""
    columns = ("id, `row_number`, barcode, product_name, brand, status, failure_code, sku_key, run_id, payload_json, "
               "trace_json, searched_at, updated_at")
    if scope == "review":
        return _query(f"SELECT {columns} FROM automation_queue WHERE status = 'ready_for_review' "
                      "ORDER BY `row_number`"), None
    if scope == "latest":
        run_id = latest_run_id()
    if not run_id:
        return [], None
    return _query(f"SELECT {columns} FROM automation_queue WHERE run_id = %s ORDER BY `row_number`", (run_id,)), run_id


def run_history(run_id):
    if not run_id:
        return None
    try:
        rows = _query(f"SELECT {', '.join(RUN_HISTORY_COLUMNS)} FROM run_history WHERE run_id = %s "
                      "ORDER BY id DESC LIMIT 1", (run_id,))
    except Exception:
        return None
    return {k: (str(v) if isinstance(v, (dt.datetime, dt.date)) else (float(v) if k == "spend_usd" and v is not None
                                                                         else v)) for k, v in rows[0].items()} \
        if rows else None


def stored_candidates(row):
    import local_cache_db

    try:
        return local_cache_db.get_curation_candidates(row["row_number"], row.get("sku_key"),
                                                      identity=local_cache_db.queue_row_identity(row))
    except Exception:
        return []


# ---------------------------------------------------------------------------
# One row, in scripts/smoke_live.py's row shape
# ---------------------------------------------------------------------------

def _warnings(reasons):
    return [str(r)[5:] for r in reasons or () if str(r).startswith("warn:")]


def _evidence_text(tier, ev):
    bits = [f"tier={tier}", f"size={ev.get('size') or 'unknown'}"]
    if ev.get("hard_reject"):
        bits.append("reject=" + ",".join(str(x) for x in ev["hard_reject"]))
    for key in ("brand", "variant_status", "coverage", "source_class", "gtin"):
        if ev.get(key) not in (None, "", [], {}):
            bits.append(f"{key}={ev[key]}")
    return " ".join(bits)


def describe(c, rank):
    """A stored candidate (curation_candidates row or a stored trace's candidate) as smoke_live's top entry, with
    the label reader's reading and the size / variant evidence."""
    from catalog_match.explain import stored_candidate_view

    view = stored_candidate_view(c)
    ev = _loads(c.get("evidence", c.get("evidence_json")), {})
    ev = ev if isinstance(ev, dict) else {}
    vlm = _loads(c.get("vlm", c.get("vlm_json")), None)
    vlm = vlm if isinstance(vlm, dict) else None
    reasons = view["reasons"]
    page_url = view["page_url"]
    return {
        "rank": rank, "status": view["status"], "reasons": reasons, "warnings": _warnings(reasons),
        "tier": view["tier"], "provider": str(c.get("provider") or c.get("source") or ""),
        "domain": view["domain"] or smoke_live._host(page_url or view["image_url"]),
        "query_id": str(c.get("query_id") or ""), "sanctioned": c.get("sanctioned"),
        "title": str(c.get("title") or c.get("page_title") or ""), "image_url": view["image_url"], "page_url": page_url,
        "evidence": _evidence_text(view["tier"], ev),
        "vlm": None if vlm is None else {"decision": vlm.get("decision"), "view": vlm.get("view"),
                                         "brand": vlm.get("brand_text", vlm.get("brand")),
                                         "variant": vlm.get("variant_text", vlm.get("variant")),
                                         "size": vlm.get("size_text", vlm.get("size"))},
        "label_reader": None if vlm is None else {k: vlm.get(k) for k in (
            "decision", "brand_match", "size_match", "variant_match", "pack_count", "view")},
        "size_evidence": {"size": ev.get("size"), "pack": ev.get("pack"), "gtin": ev.get("gtin"),
                          "url_only_size_conflict": ev.get("url_only_size_conflict")},
        "variant_evidence": {"status": ev.get("variant_status"), "matched": ev.get("variants_matched") or [],
                             "found": ev.get("variants_found") or {}},
        "source_class": ev.get("source_class"), "conflicts": ev.get("conflicts") or [],
        "download_error": c.get("download_error"), "phash": c.get("phash") or None,
        # the same picture under other URLs (retrieve.reader_queue) and the barcode the store wrote in a URL (url_gtin)
        "same_picture_domains": list(ev.get("same_picture_domains") or []), "copy_of": ev.get("copy_of"),
        "url_gtin": ev.get("url_gtin"),
        "width": c.get("width"), "height": c.get("height"),
    }


def provider_calls(outcome, secret_values):
    out = []
    for h in outcome.get("provider_health") or []:
        if not isinstance(h, dict):
            continue
        provider, query_id = str(h.get("provider") or ""), str(h.get("query_id") or "")
        call = {"provider": provider, "query": query_id or "?", "query_id": query_id, "hl": "",
                "status": h.get("status"), "http_status": h.get("http_status"), "count": None,
                "ms": h.get("latency_ms"), "error": smoke_live.redact(h["error"], secret_values) if h.get("error") else None}
        if provider in smoke_live.EXPANSION_PROVIDERS or _EXPANSION_QUERY_RE.match(query_id):
            call["round"] = "expansion"
        try:
            hedges = max(0, int(h.get("hedges") or 0))
        except (TypeError, ValueError):
            hedges = 0
        if hedges:                    # the request was sent a second time (one more credit, counted in the cost)
            call["hedged"] = True
            call["hedges"] = hedges
        out.append(call)
    return out


TIMING_STAGES = ("retrieval", "fetch", "quality", "verify", "expansion", "total")


def row_timings(outcome):
    """{stage: ms} the search stored in its outcome; {} for a row saved before timings were recorded."""
    raw = outcome.get("timings") if isinstance(outcome, dict) else None
    out = {}
    if isinstance(raw, dict):
        for stage, value in raw.items():
            try:
                ms = int(value)
            except (TypeError, ValueError):
                continue
            if ms >= 0:
                out[str(stage)] = ms
    return out


def _percentile(sorted_values, q):
    """Nearest-rank percentile (q in 0..100) of a sorted, non-empty list."""
    rank = max(1, -(-len(sorted_values) * q // 100))
    return sorted_values[int(rank) - 1]


def timings_summary(rows):
    """{rows, stages: {stage: {rows, p50_s, p90_s, total_s}}} over the rows that carry timings; rows without
    them (older exports and traces) are left out, and no row with timings gives {rows: 0, stages: {}}."""
    per_stage = {}
    n = 0
    for r in rows:
        t = r.get("timings") if isinstance(r, dict) else None
        if not isinstance(t, dict) or not t:
            continue
        n += 1
        for stage, ms in t.items():
            if isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms >= 0:
                per_stage.setdefault(str(stage), []).append(float(ms))
    order = [st for st in TIMING_STAGES if st in per_stage] + sorted(set(per_stage) - set(TIMING_STAGES))
    stages = {}
    for stage in order:
        values = sorted(per_stage[stage])
        stages[stage] = {"rows": len(values), "p50_s": round(_percentile(values, 50) / 1000.0, 2),
                         "p90_s": round(_percentile(values, 90) / 1000.0, 2),
                         "total_s": round(sum(values) / 1000.0, 1)}
    return {"rows": n, "stages": stages}


def export_row(row, candidates, mappings=None, vocab=None, prices=None, secret_values=()):
    """One queue row as a smoke_live --json row (the fields compare_runs and smoke_live.summarize read) plus the
    no-pick reason, the sheet's gaps and the queue state."""
    from catalog_match import explain
    from catalog_match.identity import build_sku_spec

    prices = prices or smoke_live.provider_prices()
    trace = _loads(row.get("trace_json"), {})
    trace = trace if isinstance(trace, dict) else {}
    outcome = trace.get("outcome") if isinstance(trace.get("outcome"), dict) else {}
    sheet = explain.queue_sheet_row(row)
    spec = build_sku_spec(sheet, mappings)
    record = explain.stored_record(trace, candidates, str(row.get("status") or ""), row.get("failure_code"))
    decision = record["decision"]
    steps = [c for step in trace.get("steps") or [] if isinstance(step, dict)
             for c in step.get("candidates") or [] if isinstance(c, dict)]
    # a published or approved row has no review candidates left: the outcome's own top list (facade) keeps them
    kept = [c for c in outcome.get("top") or [] if isinstance(c, dict)]
    source = (steps or list(candidates or ()) or kept)[:TOP_N]
    top = [describe(c, i) for i, c in enumerate(source, 1)]
    winner_url = outcome.get("winner_url") if decision in smoke_live.PICK_DECISIONS else None
    if decision in smoke_live.PICK_DECISIONS and not winner_url:
        winner_url = next((t["image_url"] for t in top if t["status"] == "preselected"), None)
    detail = next((t for t in top if winner_url and t["image_url"] == winner_url), None)
    calls = provider_calls(outcome, secret_values)
    search_cost = sum(smoke_live.call_cost(c, prices) for c in calls)
    usage = [dict(u) for u in outcome.get("vlm_usage") or [] if isinstance(u, dict)]
    vlm_calls = int(outcome.get("vlm_calls") or 0)
    verifier_cost = sum(float(u.get("usd") or 0.0) for u in usage) if usage else vlm_calls * smoke_live.DEFAULT_VLM_COST
    no_pick = outcome.get("explain") if isinstance(outcome.get("explain"), dict) else None
    if no_pick is None and "explain" not in outcome and decision not in smoke_live.PICK_DECISIONS and decision:
        no_pick = explain.explain_stored(row, candidates, mappings, vocab)       # saved before it was computed
    barcode = str(row.get("barcode") or "")
    # the stores' spelling of the brand the search used (outcome.discovered_brands, or an older pick's warning)
    discovered = explain.stored_discovered(trace, candidates)
    out = {
        "row": row.get("row_number"), "name": str(row.get("product_name") or ""), "brand": str(row.get("brand") or ""),
        "name_ar": sheet["name_ar"], "brand_ar": sheet["brand_ar"], "category": sheet["category"], "size": sheet["size"],
        "barcode": barcode, "has_barcode": bool(barcode.strip()), "barcode_valid": spec.gtin_status == "ok",
        "sku_key": row.get("sku_key") or spec.sku_key, "brand_conf": spec.brand_conf, "gtin_status": spec.gtin_status,
        "variants": dict(spec.variants), "queue_status": row.get("status"), "run_id": row.get("run_id"),
        "searched_at": outcome.get("searched_at") or (str(row["searched_at"]) if row.get("searched_at") else None),
        "queries": list(outcome.get("queries") or []), "provider_calls": calls, "timings": row_timings(outcome),
        "decision": decision, "failure_code": outcome.get("failure_code") or row.get("failure_code"),
        "winner": winner_url, "winner_detail": detail,
        "winner_provider": (detail or {}).get("provider") or None, "winner_domain": (detail or {}).get("domain") or None,
        "warnings": (detail or {}).get("warnings") or [],
        "top": top, "reject_counts": dict(outcome.get("reject_counts") or {}),
        "vlm_calls": vlm_calls, "vlm_usage": usage, "strong_calls": sum(1 for u in usage if u.get("role") == "strong"),
        "social_links": list(outcome.get("social_links") or []),
        "discovered_brands": discovered,
        "serp_calls": sum(smoke_live.credits(c) for c in calls if c["provider"] not in smoke_live.FREE_PROVIDERS
                          and c["status"] in smoke_live.ANSWERED_STATUSES),
        "cost": {"search": round(search_cost, 4), "verifier": round(verifier_cost, 4)},
        "cost_usd": round(search_cost + verifier_cost, 4), "cost_known": bool(calls or usage or vlm_calls),
        "no_pick": no_pick,
        "sheet_issues": explain.sheet_issues(sheet, spec=spec, vocab=vocab, discovered=discovered),
    }
    out["expansion"] = smoke_live.expansion_info(out)
    out["outage"] = smoke_live.outage_reason(out)
    out["unselected_reason"] = (None if out["outage"] or decision in smoke_live.PICK_DECISIONS or not decision
                                else smoke_live.unselected_reason(out))
    return out


# ---------------------------------------------------------------------------
# What the reviewers did (review_decisions) and the current approvals (resolved_products)
# ---------------------------------------------------------------------------

REVIEW_COLUMNS = ("id, created_at, action, sku_key, `row_number`, image_url, page_domain, engine_decision, "
                  "was_preselected, vlm_decision, reason_code")
REVIEW_ACTIONS = ("approved", "rejected", "manual_upload")
PICK_VERDICTS = ("accepted", "replaced", "rejected", "pending")
WARNING_SETS = ("no_warning", "vlm_unsure", "other", "unknown")
# a pick whose label left the size open while two trusted stores state it (catalog_match.retrieve.annotate_copies):
# recorded only, its approval rate is measured here before it may count for anything
SIZE_CORROBORATED = "size_corroborated"


def review_decisions():
    """(by_sku, by_row, undone) of the reviewers' decisions in time order: lists of rows keyed by sku_key, and by row
    number for the rows saved without one; undone counts the rejections taken back per key ('sku:<key>' / 'row:<n>').
    A database without the table (or without undone_at) answers what it has; nothing at all gives empty maps."""
    try:
        rows = _query(f"SELECT {REVIEW_COLUMNS}, undone_at FROM review_decisions ORDER BY created_at, id")
    except Exception:
        try:
            rows = _query(f"SELECT {REVIEW_COLUMNS} FROM review_decisions ORDER BY created_at, id")
        except Exception:
            rows = []
    by_sku, by_row, undone = {}, {}, Counter()
    for r in rows:
        sku = str(r.get("sku_key") or "").strip()
        key = f"sku:{sku}" if sku else f"row:{r.get('row_number')}"
        if r.get("undone_at"):
            undone[key] += 1
            continue
        if sku:
            by_sku.setdefault(sku, []).append(r)
        elif r.get("row_number") is not None:
            by_row.setdefault(int(r["row_number"]), []).append(r)
    return by_sku, by_row, undone


def current_approvals():
    """{sku_key: the current approval} (human_approved / auto_verified; the latest per key); {} when unreadable."""
    base = ("SELECT sku_key, cloudinary_url, original_url, verification_status, resolved_at{} FROM resolved_products "
            "WHERE verification_status IN ('human_approved', 'auto_verified') AND sku_key IS NOT NULL "
            "AND sku_key <> '' ORDER BY id")
    for extra in (", page_gtin, page_gtin_url", ""):
        try:
            return {str(r["sku_key"]).strip(): r for r in _query(base.format(extra))}
        except Exception:
            continue
    return {}


def _ts(value):
    return str(value) if value is not None else None


def row_decisions(out_row, by_sku, by_row):
    sku = str(out_row.get("sku_key") or "").strip()
    found = by_sku.get(sku) if sku else None
    return list(found or by_row.get(out_row.get("row")) or [])


def review_block(decisions):
    """The row's latest review decision (see the module docstring), or None."""
    if not decisions:
        return None
    last = decisions[-1]
    pre = last.get("was_preselected")
    return {"decision": last.get("action"), "image_url": last.get("image_url"), "domain": last.get("page_domain"),
            "was_preselected": None if pre is None else bool(pre), "engine_decision": last.get("engine_decision"),
            "vlm_decision": last.get("vlm_decision"), "reason_code": last.get("reason_code"),
            "at": _ts(last.get("created_at")), "decisions": len(decisions)}


def approval_block(approval):
    if not approval:
        return None
    return {"link": approval.get("cloudinary_url"), "image_url": approval.get("original_url"),
            "status": approval.get("verification_status"), "at": _ts(approval.get("resolved_at"))}


def pick_verdict(decisions, winner_url=None):
    """What the reviewer did with a pre-checked pick: accepted | replaced | rejected | pending (the latest that
    counts: an approval of the pick, an approval of another image or an upload, a rejection of the pick)."""
    verdict = "pending"
    for d in decisions:
        action, pre = d.get("action"), d.get("was_preselected")
        if action == "approved":
            verdict = "accepted" if pre is not None and int(pre) == 1 else "replaced"
        elif action == "manual_upload":
            verdict = "replaced"
        elif action == "rejected" and ((pre is not None and int(pre) == 1)
                                        or (winner_url and d.get("image_url") == winner_url)):
            verdict = "rejected"
    return verdict


def warning_set(out_row, decisions):
    """The pick's warning set: no_warning, vlm_unsure (that one only), other, or unknown when the export no longer
    has the pick (an approved row's candidates are gone) and the review rows cannot tell."""
    if out_row.get("winner_detail"):
        codes = {str(w).split(":", 1)[0] for w in out_row.get("warnings") or []}
        return "no_warning" if not codes else "vlm_unsure" if codes == {"vlm_unsure"} else "other"
    read = next((d.get("vlm_decision") for d in reversed(decisions) if d.get("vlm_decision")
                 and d.get("was_preselected") is not None and int(d["was_preselected"]) == 1), None)
    return "vlm_unsure" if read and read != "MATCH" else "unknown"


def review_summary(out_rows, by_row_decisions, undone=0):
    """summary.review (see the module docstring); {} when no exported row has a review decision."""
    if not any(by_row_decisions.values()) and not undone:
        return {}
    counts = Counter({k: 0 for k in REVIEW_ACTIONS + ("pending", "no_decision")})
    pre = Counter({k: 0 for k in PICK_VERDICTS})
    by_warning = {w: Counter({k: 0 for k in PICK_VERDICTS}) for w in WARNING_SETS}
    corroborated = Counter({k: 0 for k in PICK_VERDICTS})
    for out_row in out_rows:
        decisions = by_row_decisions.get(id(out_row)) or []
        if decisions:
            counts[decisions[-1].get("action")] += 1
        elif out_row.get("queue_status") == "ready_for_review":
            counts["pending"] += 1
        else:
            counts["no_decision"] += 1
        if out_row.get("decision") in smoke_live.PICK_DECISIONS:
            verdict = pick_verdict(decisions, out_row.get("winner"))
            pre[verdict] += 1
            by_warning[warning_set(out_row, decisions)][verdict] += 1
            if SIZE_CORROBORATED in ((out_row.get("winner_detail") or {}).get("reasons") or ()):
                corroborated[verdict] += 1
    return {"counts": dict(counts), "undone": int(undone), "preselected": dict(pre),
            "preselected_by_warning": {w: dict(c) for w, c in by_warning.items()},
            "preselected_size_corroborated": dict(corroborated)}


def add_review(out_rows, by_sku=None, by_row=None, undone=None, approvals=None):
    """Each exported row's 'review', 'approval' and 'page_gtin' (read once for the whole export); returns
    summary.review."""
    if by_sku is None:
        by_sku, by_row, undone = review_decisions()
    approvals = current_approvals() if approvals is None else approvals
    found, undone_here = {}, 0
    for out_row in out_rows:
        if "error" in out_row:
            continue
        decisions = row_decisions(out_row, by_sku, by_row or {})
        found[id(out_row)] = decisions
        sku = str(out_row.get("sku_key") or "").strip()
        undone_here += (undone or {}).get(f"sku:{sku}" if sku else f"row:{out_row.get('row')}", 0)
        out_row["review"] = review_block(decisions)
        approval = approvals.get(sku) if sku else None
        out_row["approval"] = approval_block(approval)
        out_row["page_gtin"] = (approval or {}).get("page_gtin") or None
        out_row["page_gtin_page"] = (approval or {}).get("page_gtin_url") or None if out_row["page_gtin"] else None
    return review_summary([r for r in out_rows if "error" not in r], found, undone_here)


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

def load_mappings():
    """The Brands Mapping as the worker reads it (google_sheets.get_brand_mappings: its 5-minute cache, else the
    sheet, with what the reviewers taught), as one brand index; {} when it cannot be read."""
    try:
        import config
        import google_sheets
        from catalog_match.brand_index import BrandIndex

        client = google_sheets.get_sheets_client()
        mappings = google_sheets.get_brand_mappings(client, config.SPREADSHEET_NAME_OR_URL) if client else {}
        return BrandIndex.from_mappings(mappings or {})
    except Exception:
        return {}


def build_export(scope="latest", run_id=None, mappings=None, now=None):
    """The export document {format, export, meta, summary, rows}. Raises ValueError for a bad scope or run id."""
    from catalog_match import explain

    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    if scope == "run" and not (run_id and RUN_ID_RE.match(str(run_id))):
        raise ValueError("a run id of letters, digits, '-', '_', '.' or ':' is needed")
    hidden = secrets()
    rows, run_id = queue_rows(scope, run_id)
    mappings = load_mappings() if mappings is None else mappings
    vocab = explain.default_vocabulary()
    prices = smoke_live.provider_prices()
    out_rows, not_searched = [], []
    for row in rows:
        trace = _loads(row.get("trace_json"), {})
        if not (isinstance(trace, dict) and isinstance(trace.get("outcome"), dict)):
            # still waiting (or written without a search, e.g. an approved link relinked): nothing to analyse
            not_searched.append(row.get("row_number"))
            continue
        try:
            out_rows.append(export_row(row, stored_candidates(row), mappings, vocab, prices, hidden))
        except Exception as exc:
            out_rows.append({"row": row.get("row_number"), "name": str(row.get("product_name") or ""),
                             "brand": str(row.get("brand") or ""),
                             "error": smoke_live.redact(f"{type(exc).__name__}: {exc}", hidden)})
    from catalog_match import cassette

    now = now or dt.datetime.now()
    meta = {
        "tool": "scripts/export_run.py", "exported_at": now.isoformat(timespec="seconds"), "scope": scope,
        "run_id": run_id, "rows": len(out_rows), "not_searched": not_searched,
        "statuses": dict(Counter(str(r.get("queue_status") or "error") for r in out_rows)),
        "git": smoke_live.git_info(), "settings": cassette.settings_snapshot(), "run_history": run_history(run_id),
        "note": "Read from the queue, its stored traces and review candidates: nothing was searched again.",
    }
    review = add_review(out_rows)
    summary = smoke_live.summarize(out_rows)
    summary["timings"] = timings_summary(out_rows)
    summary["review"] = review
    return {"format": smoke_live.JSON_FORMAT, "export": EXPORT_FORMAT, "meta": meta,
            "summary": summary, "rows": out_rows}, hidden


def export_text(doc, hidden):
    """The document as UTF-8 JSON text with every secret value hidden."""
    return smoke_live.redact(json.dumps(doc, ensure_ascii=False, indent=1, default=str), hidden, query_keys=False)


def default_name(now=None):
    now = now or dt.datetime.now()
    return f"laqta_run_{now.strftime('%Y-%m-%d_%H%M')}.json"


def write_export(path, scope="latest", run_id=None, mappings=None, now=None):
    """Build and write the export; returns (path, number of rows)."""
    doc, hidden = build_export(scope, run_id, mappings=mappings, now=now)
    text = export_text(doc, hidden)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path, len(doc["rows"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--run-id", help="export this run (the run_id of automation_queue)")
    which.add_argument("--review", action="store_true", help="export every product waiting for review, any run")
    parser.add_argument("--out", help="the JSON file to write (default laqta_run_<date>_<time>.json here)")
    args = parser.parse_args(argv)
    smoke_live._utf8_stdout()
    scope = "review" if args.review else "run" if args.run_id else "latest"
    path = args.out or default_name()
    try:
        path, n = write_export(path, scope, args.run_id)
    except ValueError as exc:
        parser.error(str(exc))
    except Exception as exc:
        print(f"cannot export: {smoke_live.redact(f'{type(exc).__name__}: {exc}', secrets())}", file=sys.stderr)
        return 2
    print(f"{n} rows written to {path}" + ("" if n else " (no row in this scope)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
