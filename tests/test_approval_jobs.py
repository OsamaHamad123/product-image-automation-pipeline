"""approval_jobs + scripts/approval_worker.py + bridge approval_enqueue: approvals run on the server, so the reviewer
can leave the page (owner, 2026-10-08).

* a job keeps the page's parameters; one product (sku_key) has at most one queued or running job;
* a worker takes the oldest job atomically, runs the very same action_select_image and stores its result document and
  the http status the dashboard would have answered (200 / 500);
* a job whose worker died is taken again, at most MAX_ATTEMPTS times, then failed with a reason;
* approval_enqueue answers at once with the job id and starts a worker.
"""

import json

import pytest

import approval_jobs

PARAMS = {"row_number": 12, "sku_key": "aj-key-12", "product_name": "Almarai Milk 1L", "brand": "Almarai",
          "image_url": "https://www.carrefouruae.com/p12.jpg", "expected_state": {"queue_status": "ready_for_review"}}


@pytest.fixture
def jobs(mariadb_or_skip):
    from laqta_kernel import sql

    approval_jobs.ensure_schema()
    sql(mariadb_or_skip, "DELETE FROM approval_jobs")
    yield mariadb_or_skip
    sql(mariadb_or_skip, "DELETE FROM approval_jobs")


def _row(db, job_id):
    from laqta_kernel import sql

    return sql(db, "SELECT * FROM approval_jobs WHERE id = %s", (job_id,))[0]


def test_one_product_has_one_active_job(jobs):
    first = approval_jobs.enqueue("select", PARAMS, created_by="reviewer")
    again = approval_jobs.enqueue("select", dict(PARAMS, image_url="https://other.example/x.jpg"))
    other = approval_jobs.enqueue("select", dict(PARAMS, sku_key="aj-key-13", row_number=13))
    assert again == {"job_id": first["job_id"], "existing": True}
    assert other["existing"] is False and other["job_id"] != first["job_id"]
    row = _row(jobs, first["job_id"])
    assert (row["status"], row["created_by"], row["row_number"], row["label"]) == ("queued", "reviewer", 12, "Almarai Milk 1L")
    assert json.loads(row["params_json"]) == PARAMS                     # exactly what the page sent


def test_a_worker_runs_the_same_select_image_and_stores_what_the_page_would_have_received(jobs):
    from scripts import approval_worker

    approval_jobs.enqueue("select", PARAMS)
    job = approval_jobs.claim("t" * 32)
    assert job["status"] == "running" and job["params"] == PARAMS and job["attempts"] == 1
    assert approval_jobs.claim("u" * 32) is None                        # nothing else queued
    seen = []
    answer = {"status": "success", "image_link": "https://res.cloudinary.com/x/1.png", "rows_written": [12]}
    result, http = approval_worker.run_job(job, select_image=lambda p: seen.append(p) or answer)
    assert seen == [PARAMS] and (result, http) == (answer, 200)
    assert approval_jobs.finish(job, result, http) == "done"
    row = _row(jobs, job["id"])
    assert (row["status"], row["http_status"], json.loads(row["result_json"])) == ("done", 200, answer)


def test_a_refusal_and_an_exception_are_failed_jobs_with_the_page_s_reason(jobs):
    from scripts import approval_worker

    stale = {"status": "failed", "error_code": "already_approved", "current": {"queue_status": "completed"}}
    job = {"id": 1, "params": PARAMS}
    assert approval_worker.run_job(job, select_image=lambda p: stale) == (stale, 500)

    def boom(p):
        raise RuntimeError("secret detail")

    result, http = approval_worker.run_job(job, select_image=boom)
    assert http == 500 and result["status"] == "error" and "secret" not in json.dumps(result)


def test_a_job_whose_worker_died_is_taken_again_then_given_up(jobs):
    from laqta_kernel import sql

    approval_jobs.enqueue("select", PARAMS)
    job = approval_jobs.claim("a" * 32)
    sql(jobs, "UPDATE approval_jobs SET updated_at = NOW() - INTERVAL 20 MINUTE WHERE id = %s", (job["id"],))
    again = approval_jobs.claim("b" * 32)
    assert again["id"] == job["id"] and again["attempts"] == 2
    assert approval_jobs.finish(job, {"status": "success"}, 200) == "superseded"      # the dead worker's late answer
    assert _row(jobs, job["id"])["status"] == "running"                              # is not written over the new claim
    sql(jobs, "UPDATE approval_jobs SET attempts = %s, updated_at = NOW() - INTERVAL 20 MINUTE WHERE id = %s",
        (approval_jobs.MAX_ATTEMPTS, job["id"]))
    assert approval_jobs.claim("c" * 32) is None
    approval_jobs.give_up_stale()
    row = _row(jobs, job["id"])
    assert row["status"] == "failed" and json.loads(row["result_json"])["status"] == "error"


def test_approval_enqueue_answers_at_once_and_starts_a_worker(jobs, monkeypatch):
    import cli_bridge

    started = []
    monkeypatch.setattr(approval_jobs, "start_worker", lambda *a, **k: started.append(1) or True)
    out = cli_bridge.action_approval_enqueue(dict(PARAMS, created_by="osama"))
    assert out["status"] == "queued" and out["existing"] is False and started == [1]
    assert _row(jobs, out["job_id"])["created_by"] == "osama"
    assert "created_by" not in json.loads(_row(jobs, out["job_id"])["params_json"])   # not passed on to select_image
    missing = cli_bridge.action_approval_enqueue({"row_number": 3})
    assert missing["status"] == "failed" and len(started) == 1


def test_the_worker_starts_detached_with_this_python(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(approval_jobs, "LOG_PATH", tmp_path / "approval_worker.log")
    assert approval_jobs.start_worker(python="py", popen=lambda cmd, **kw: calls.append((cmd, kw))) is True
    cmd, kw = calls[0]
    assert cmd[0] == "py" and cmd[-1].endswith("approval_worker.py")
    assert kw.get("start_new_session") or kw.get("creationflags")
