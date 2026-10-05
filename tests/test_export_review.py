"""The run export says what the reviewers did (scripts/export_run.py: review, approval, page_gtin, summary.review).

Every reviewer decision is stored in review_decisions; the export did not carry it, and once a row is approved its
candidates are gone, so nothing showed whether the reviewer took the pre-checked pick. Now each row has its latest
decision, a row with a current approval its published link (and the store page's barcode for a row without one), and
summary.review counts the decisions and splits the pre-checked picks into accepted / replaced / rejected / pending,
also by the pick's warning set. A rejection taken back («تراجع عن الرفض») is left out; an old database gives {}.
"""

import json

import pytest

from test_export_run import (  # noqa: F401  (the export's database fixtures)
    MAPPINGS, ROW, RUN_ID, SKU, _insert, _save_candidates, _sql, db, run_rows,
)

PICK = "https://cdn.x.ae/tuna-pick.jpg"
OTHER = "https://cdn.x.ae/tuna-other.jpg"
GTIN = "6281007035309"


def _wipe(db):
    _sql(db, "DELETE FROM review_decisions WHERE sku_key LIKE %s", (SKU + "%",))
    _sql(db, "DELETE FROM resolved_products WHERE sku_key LIKE %s", (SKU + "%",))


@pytest.fixture
def reviewed(run_rows):
    db = run_rows
    _wipe(db)
    preselected = {"outcome": {"decision": "REVIEW_PRESELECTED", "failure_code": None, "winner_url": PICK,
                               "searched_at": "2026-10-03T09:03:00+00:00",
                               "provider_health": [{"provider": "serper", "status": "ok", "http_status": 200,
                                                    "query_id": "Q1"}]}}
    _insert(db, 5, "ready_for_review", preselected, name="W2E GOLDEN PRIZE TUNA 185G", brand="GOLDEN PRIZE",
            run_id=RUN_ID)
    _save_candidates(db, 5, [
        {"url": PICK, "status": "preselected", "domain": "x.ae", "reasons": ["vlm:UNSURE", "warn:vlm_unsure"],
         "evidence": {"tier": 1}, "vlm": {"decision": "UNSURE"}},
        {"url": OTHER, "status": "eligible", "domain": "x.ae", "reasons": [], "evidence": {"tier": 2}}],
        name="W2E GOLDEN PRIZE TUNA 185G")
    # row 2 (auto-published): the reviewer approved the pick; row 5: the pick rejected; row 0: a rejection taken back
    db.add_review_decision("approved", sku_key=f"{SKU}2", row_number=ROW + 2, image_url="https://cdn.x.ae/milk.jpg",
                           page_domain="x.ae", engine_decision="AUTO_PUBLISH", was_preselected=True,
                           vlm_decision="MATCH")
    db.add_review_decision("rejected", sku_key=f"{SKU}5", row_number=ROW + 5, image_url=PICK, page_domain="x.ae",
                           engine_decision="REVIEW_PRESELECTED", was_preselected=True, vlm_decision="UNSURE",
                           reason_code="WRONG_PACK")
    db.add_review_decision("rejected", sku_key=f"{SKU}0", row_number=ROW, image_url="https://cdn.x.ae/1.jpg",
                           reason_code="WRONG_SIZE")
    _sql(db, "UPDATE review_decisions SET undone_at = NOW() WHERE sku_key = %s", (f"{SKU}0",))
    db.save_product_resolution("", "W2E ALMARAI FRESH MILK 1L", "ALMARAI", "https://cdn.x.ae/milk.jpg",
                               "https://res.cloudinary.com/demo/milk.png", verification_status="human_approved",
                               approved_by="human", sku_key=f"{SKU}2", page_gtin=GTIN,
                               page_gtin_url="https://www.x.ae/milk")
    yield db
    _wipe(db)


def test_each_row_says_what_the_reviewer_did_and_the_summary_counts_it(reviewed, tmp_path):
    import export_run

    doc, _ = export_run.build_export("latest", mappings=MAPPINGS)
    rows = {r["row"]: r for r in doc["rows"]}
    approved = rows[ROW + 2]
    assert approved["review"] == {"decision": "approved", "image_url": "https://cdn.x.ae/milk.jpg", "domain": "x.ae",
                                  "was_preselected": True, "engine_decision": "AUTO_PUBLISH", "vlm_decision": "MATCH",
                                  "reason_code": None, "at": approved["review"]["at"], "decisions": 1}
    assert approved["approval"]["link"] == "https://res.cloudinary.com/demo/milk.png"
    assert approved["approval"]["status"] == "human_approved"
    assert (approved["page_gtin"], approved["page_gtin_page"]) == (GTIN, "https://www.x.ae/milk")
    rejected = rows[ROW + 5]
    assert (rejected["review"]["decision"], rejected["review"]["reason_code"]) == ("rejected", "WRONG_PACK")
    assert rejected["approval"] is None and rejected["page_gtin"] is None
    assert rows[ROW]["review"] is None                                  # its only rejection was taken back
    review = doc["summary"]["review"]
    assert review["counts"] == {"approved": 1, "rejected": 1, "manual_upload": 0, "pending": 1, "no_decision": 1}
    assert review["undone"] == 1
    assert review["preselected"] == {"accepted": 1, "replaced": 0, "rejected": 1, "pending": 0}
    by_warning = review["preselected_by_warning"]
    assert by_warning["vlm_unsure"]["rejected"] == 1 and sum(by_warning["vlm_unsure"].values()) == 1
    assert by_warning["no_warning"]["accepted"] + by_warning["unknown"]["accepted"] == 1
    # the written file carries the same, as JSON
    path, _ = export_run.write_export(str(tmp_path / "x.json"), "latest", mappings=MAPPINGS)
    assert json.loads(open(path, encoding="utf-8").read())["summary"]["review"] == json.loads(json.dumps(review))


def test_an_old_database_gives_an_empty_block(run_rows, monkeypatch):
    import export_run

    real = export_run._query

    def old(sql, params=()):
        if "review_decisions" in sql or "resolved_products" in sql:
            raise RuntimeError("Table 'review_decisions' doesn't exist")
        return real(sql, params)

    monkeypatch.setattr(export_run, "_query", old)
    doc, _ = export_run.build_export("latest", mappings=MAPPINGS)
    assert doc["summary"]["review"] == {}
    assert all(r.get("review") is None and r.get("approval") is None for r in doc["rows"])


def test_the_verdict_of_a_pick_and_its_warning_set():
    import export_run

    approve = {"action": "approved", "was_preselected": 1}
    other = {"action": "approved", "was_preselected": 0}
    upload = {"action": "manual_upload", "was_preselected": 0}
    reject_pick = {"action": "rejected", "was_preselected": 1}
    reject_alt = {"action": "rejected", "was_preselected": 0, "image_url": OTHER}
    assert export_run.pick_verdict([]) == "pending"
    assert export_run.pick_verdict([approve]) == "accepted"
    assert export_run.pick_verdict([reject_pick, other]) == "replaced"
    assert export_run.pick_verdict([upload]) == "replaced"
    assert export_run.pick_verdict([reject_alt]) == "pending"                # an alternative, not the pick
    assert export_run.pick_verdict([{"action": "rejected", "was_preselected": None, "image_url": PICK}], PICK) == \
        "rejected"
    assert export_run.pick_verdict([approve, reject_pick]) == "rejected"     # the approval was taken back later
    detail = {"winner_detail": {"image_url": PICK}}
    assert export_run.warning_set(dict(detail, warnings=[]), []) == "no_warning"
    assert export_run.warning_set(dict(detail, warnings=["vlm_unsure"]), []) == "vlm_unsure"
    assert export_run.warning_set(dict(detail, warnings=["vlm_unsure", "size_close:170g/185g"]), []) == "other"
    assert export_run.warning_set({}, [{"vlm_decision": "UNSURE", "was_preselected": 1}]) == "vlm_unsure"
    assert export_run.warning_set({}, [approve]) == "unknown"
