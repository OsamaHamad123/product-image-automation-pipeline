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
    assert json.loads(counts["body"])["approvals"] == {"queued": 0, "running": 1, "failed_recent": 1}


def test_no_ids_and_no_table_answer_an_empty_list(env):
    environ, _calls, db = env
    empty, = run(environ, [["GET", "/api/approval-jobs?ids="]])
    assert json.loads(empty["body"]) == {"status": "success", "jobs": []}
