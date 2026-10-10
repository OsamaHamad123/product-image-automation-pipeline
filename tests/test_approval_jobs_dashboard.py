"""The dashboard side of server approvals (ApiController, through the Laravel kernel with a stub bridge):

* POST /api/select_image with async=1 queues the approval (bridge approval_enqueue, with the signed-in user's name)
  and answers 202 at once; without async it is the old synchronous call;
* GET /api/approval-jobs?ids= reads approval_jobs straight from the database: a finished job carries the result
  document and http status select_image would have answered; the products cache is cleared once per approval;
* GET /api/approval-jobs?active=1 lists the queued and running ones (a page opened again);
* /api/batch-status carries the counts for the sidebar on every page.
"""

import json

import pytest

from laqta_kernel import NEEDS_LARAVEL, bridge_calls, sql, stub_env
from test_dashboard_login import run

pytestmark = NEEDS_LARAVEL

SELECT = {"row_number": "12", "sku_key": "ajd-key-12", "product_name": "Almarai Milk 1L", "brand": "Almarai",
          "image_url": "https://www.carrefouruae.com/p12.jpg"}


@pytest.fixture
def env(mariadb_or_skip, tmp_path):
    import approval_jobs

    db = mariadb_or_skip
    approval_jobs.ensure_schema()
    sql(db, "DELETE FROM approval_jobs")
    environ, calls = stub_env(tmp_path, extra={
        "approval_enqueue": {"status": "queued", "job_id": 41, "existing": False},
        "select_image": {"status": "success", "image_link": "https://res.cloudinary.com/x/sync.png"}})
    yield environ, calls, db
    sql(db, "DELETE FROM approval_jobs")


def test_async_select_queues_the_approval_and_answers_202(env):
    environ, calls, _db = env
    queued, sync = run(environ, [["POST", "/api/select_image", {**SELECT, "async": "1"}],
                                 ["POST", "/api/select_image", SELECT]])
    assert queued["status"] == 202 and json.loads(queued["body"]) == {"status": "queued", "job_id": 41, "existing": False}
    assert sync["status"] == 200 and json.loads(sync["body"])["image_link"].endswith("sync.png")
    sent = bridge_calls(calls)
    assert [a for a, _ in sent] == ["approval_enqueue", "select_image"]
    params = sent[0][1]
    assert params["sku_key"] == "ajd-key-12" and params["image_url"] == SELECT["image_url"] and "created_by" in params
    assert "async" not in params                                       # not passed on to select_image


def test_a_finished_job_carries_the_result_and_clears_the_cache_once(env):
    environ, _calls, db = env
    done = {"status": "success", "image_link": "https://res.cloudinary.com/x/1.png", "rows_written": [12]}
    sql(db, "INSERT INTO approval_jobs (id, kind, sku_key, `row_number`, label, params_json, status, result_json, "
            "http_status, finished_at) VALUES (501, 'select', 'k1', 12, 'Milk', '{}', 'done', %s, 200, NOW()), "
            "(502, 'select', 'k2', 13, 'Laban', '{}', 'running', NULL, NULL, NULL), "
            "(503, 'select', 'k3', 14, 'Juice', '{}', 'failed', %s, 500, NOW())",
        (json.dumps(done), json.dumps({"status": "failed", "error_code": "already_approved"})))
    first, second, active, counts = run(environ, [["GET", "/api/approval-jobs?ids=501,502,503"],
                                                  ["GET", "/api/approval-jobs?ids=501"],
                                                  ["GET", "/api/approval-jobs?active=1"],
                                                  ["GET", "/api/batch-status"]])
    jobs = {j["id"]: j for j in json.loads(first["body"])["jobs"]}
    assert (jobs[501]["status"], jobs[501]["http_status"], jobs[501]["result"]) == ("done", 200, done)
    assert (jobs[502]["status"], jobs[502]["result"]) == ("running", None)
    assert (jobs[503]["http_status"], jobs[503]["result"]["error_code"]) == (500, "already_approved")
    assert sql(db, "SELECT cache_cleared FROM approval_jobs WHERE id = 501")[0]["cache_cleared"] == 1
    assert json.loads(second["body"])["jobs"][0]["status"] == "done"
    assert [j["id"] for j in json.loads(active["body"])["jobs"]] == [502]
    assert json.loads(counts["body"])["approvals"] == {"queued": 0, "running": 1, "failed_recent": 1, "failed_open": 1}


def test_no_ids_and_no_table_answer_an_empty_list(env):
    environ, _calls, db = env
    empty, = run(environ, [["GET", "/api/approval-jobs?ids="]])
    assert json.loads(empty["body"]) == {"status": "success", "jobs": []}


# ---------------------------------------------------------------------------
# Failed approvals the reviewer may not have seen: ?failed=1, «تجاهل» (dismiss), the sidebar's failed_open count
# ---------------------------------------------------------------------------

def _failed_rows(db):
    quality = {"status": "failed", "error_code": "quality_flags", "error": "quality_flags", "quality_flags": ["halo"]}
    sql(db, "INSERT INTO approval_jobs (id, kind, sku_key, `row_number`, label, params_json, status, result_json, "
            "http_status, created_by, finished_at) VALUES "
            "(601, 'select', 'k1', 12, 'Milk', '{}', 'failed', %s, 500, 'owner', NOW() - INTERVAL 2 HOUR), "
            "(602, 'select', 'k2', 13, 'Laban', '{}', 'failed', %s, 409, 'owner', NOW() - INTERVAL 10 MINUTE), "
            "(603, 'select', 'k3', 14, 'Juice', '{}', 'failed', %s, 500, 'owner', NOW() - INTERVAL 30 HOUR), "
            "(604, 'select', 'k4', 15, 'Water', '{}', 'done', %s, 200, 'owner', NOW()), "
            "(605, 'select', 'k5', 16, 'Tea', '{}', 'running', NULL, NULL, 'owner', NULL)",
        (json.dumps({"status": "failed", "error": "photoroom_402", "error_code": "photoroom_402"}), json.dumps(quality),
         json.dumps({"status": "failed", "error": "old"}), json.dumps({"status": "success"})))


def test_failed_lists_the_last_day_s_open_failures_newest_first_with_their_reason(env):
    environ, _calls, db = env
    _failed_rows(db)
    failed, counts = run(environ, [["GET", "/api/approval-jobs?failed=1"], ["GET", "/api/batch-status"]])
    assert failed["status"] == 200
    jobs = json.loads(failed["body"])["jobs"]
    assert [j["id"] for j in jobs] == [602, 601]                    # 603 is older than a day; 604 / 605 did not fail
    assert (jobs[0]["label"], jobs[0]["row_number"], jobs[0]["sku_key"]) == ("Laban", 13, "k2")
    assert (jobs[0]["error_code"], jobs[0]["quality_flags"], jobs[0]["created_by"]) == ("quality_flags", ["halo"], "owner")
    assert (jobs[1]["error"], jobs[1]["error_code"]) == ("photoroom_402", "photoroom_402")
    assert jobs[1]["finished_at"]
    approvals = json.loads(counts["body"])["approvals"]
    assert (approvals["failed_open"], approvals["failed_recent"], approvals["running"]) == (2, 1, 1)


def test_a_dismissed_failure_leaves_the_list_and_the_count_and_an_unknown_id_is_404(env):
    environ, _calls, db = env
    _failed_rows(db)
    dismissed, missing, failed, counts = run(environ, [["POST", "/api/approval-jobs/602/dismiss"],
                                                       ["POST", "/api/approval-jobs/999999/dismiss"],
                                                       ["GET", "/api/approval-jobs?failed=1"],
                                                       ["GET", "/api/batch-status"]])
    assert dismissed["status"] == 200 and json.loads(dismissed["body"])["status"] == "success"
    assert missing["status"] == 404 and json.loads(missing["body"])["status"] == "error"
    assert [j["id"] for j in json.loads(failed["body"])["jobs"]] == [601]
    assert json.loads(counts["body"])["approvals"]["failed_open"] == 1
    assert sql(db, "SELECT dismissed_at FROM approval_jobs WHERE id = 602")[0]["dismissed_at"] is not None


def test_ensure_schema_adds_dismissed_at_to_an_existing_table(env):
    import approval_jobs
    import schema_mark

    _environ, _calls, db = env
    sql(db, "ALTER TABLE approval_jobs DROP COLUMN IF EXISTS dismissed_at")
    schema_mark.save("approval_jobs", "older-version")
    approval_jobs.ensure_schema()
    assert "dismissed_at" in [r["Field"] for r in sql(db, "SHOW COLUMNS FROM approval_jobs")]
