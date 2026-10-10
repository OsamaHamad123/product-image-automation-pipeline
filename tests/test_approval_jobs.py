"""approval_jobs + scripts/approval_worker.py + bridge approval_enqueue: approvals run on the server, so the reviewer
can leave the page (owner, 2026-10-08).

* a job keeps the page's parameters; one product (sku_key) has at most one queued or running job: the same image gets
  that job back, another image (a second tab) is refused as busy;
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
    same = approval_jobs.enqueue("select", dict(PARAMS))
    assert again == {"job_id": first["job_id"], "existing": True, "busy": True}
    assert same == {"job_id": first["job_id"], "existing": True}
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
    monkeypatch.delenv("LAQTA_RUN_LAUNCHER", raising=False)
    calls = []
    monkeypatch.setattr(approval_jobs, "LOG_PATH", tmp_path / "approval_worker.log")
    assert approval_jobs.start_worker(python="py", popen=lambda cmd, **kw: calls.append((cmd, kw))) is True
    cmd, kw = calls[0]
    assert cmd[0] == "py" and cmd[-1].endswith("approval_worker.py")
    assert kw.get("start_new_session") or kw.get("creationflags")


def test_a_second_tab_approving_another_image_is_told_busy(jobs, monkeypatch):
    import cli_bridge

    monkeypatch.setattr(approval_jobs, "start_worker", lambda *a, **k: True)
    first = cli_bridge.action_approval_enqueue(dict(PARAMS))
    other = cli_bridge.action_approval_enqueue(dict(PARAMS, image_url="https://other.example/x.jpg"))
    assert other["status"] == "busy" and other["job_id"] == first["job_id"] and other["error"]
    assert len(sql_rows(jobs)) == 1


def sql_rows(db):
    from laqta_kernel import sql

    return sql(db, "SELECT id FROM approval_jobs")


def test_a_running_job_s_heartbeat_keeps_it_from_being_taken_again(jobs):
    from laqta_kernel import sql

    approval_jobs.enqueue("select", PARAMS)
    job = approval_jobs.claim("h" * 32)
    sql(jobs, "UPDATE approval_jobs SET updated_at = NOW() - INTERVAL 20 MINUTE WHERE id = %s", (job["id"],))
    assert approval_jobs.heartbeat(job) is True
    assert approval_jobs.claim("i" * 32) is None                        # alive: not stale any more
    assert approval_jobs.heartbeat(dict(job, claim_token="x" * 32)) is False


def test_the_worker_beats_while_the_job_runs_and_stops_after():
    import threading

    from scripts import approval_worker

    beats, release = [], threading.Event()

    def run(job):
        for _ in range(200):
            if len(beats) >= 2:
                break
            release.wait(0.01)
        return {"status": "success"}, 200

    out = approval_worker.run_with_heartbeat({"id": 7}, run=run, beat=lambda j: beats.append(j["id"]), every=0.01)
    assert out == ({"status": "success"}, 200) and beats[:2] == [7, 7]
    after = len(beats)
    release.wait(0.05)
    assert len(beats) == after                                         # the thread stopped with the job


def test_a_result_is_stored_even_when_the_database_hiccups_once():
    from scripts import approval_worker

    calls = []

    def flaky(job, result, http):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("gone away")
        return "done"

    assert approval_worker.finish_with_retry({"id": 1}, {}, 200, finish=flaky, pause=0) == "done"

    def down(job, result, http):
        raise ConnectionError("gone away")

    assert approval_worker.finish_with_retry({"id": 1}, {}, 200, finish=down, tries=2, pause=0) is None


def test_on_the_server_the_bridge_only_asks_systemd(monkeypatch, tmp_path):
    kick = tmp_path / "temp" / "approval_request"
    monkeypatch.setattr(approval_jobs, "KICK_PATH", kick)
    monkeypatch.setenv("LAQTA_RUN_LAUNCHER", "systemd")
    calls = []
    assert approval_jobs.start_worker(python="py", popen=lambda cmd, **kw: calls.append(cmd)) is True
    assert kick.exists() and calls == []                               # laqta-approvals.path starts the worker


def test_every_timestamp_comes_from_the_database_clock_whatever_the_session_time_zone(jobs, monkeypatch):
    """The bridge (php-fpm) adds a job and the worker (systemd) finishes it, maybe with other session time zones: job 1
    on the server showed finished_at two hours before created_at. Every timestamp is the database's NOW(), so the
    instants stay ordered and close together whatever each connection's time_zone is."""
    from laqta_kernel import sql

    real_connect = approval_jobs.db_connect.connect
    zone = {"now": "+00:00"}

    def connect(*a, **k):
        conn = real_connect(*a, **k)
        conn.cursor().execute("SET time_zone = %s", (zone["now"],))
        return conn

    monkeypatch.setattr(approval_jobs.db_connect, "connect", connect)
    job_id = approval_jobs.enqueue("select", PARAMS)["job_id"]
    zone["now"] = "+04:00"                          # the worker's session: Dubai
    job = approval_jobs.claim("d" * 32)
    zone["now"] = "-02:00"
    assert approval_jobs.finish(job, {"status": "success"}, 200) == "done"
    row = sql(jobs, "SELECT UNIX_TIMESTAMP(created_at) AS c, UNIX_TIMESTAMP(started_at) AS s, "
                    "UNIX_TIMESTAMP(finished_at) AS f, UNIX_TIMESTAMP(updated_at) AS u, "
                    "UNIX_TIMESTAMP(NOW()) AS now FROM approval_jobs WHERE id = %s", (job_id,))[0]
    assert row["c"] <= row["s"] <= row["f"] <= row["u"] <= row["now"]
    assert row["now"] - row["c"] < 60
    # the dashboard's «failed in the last 24 hours» window (ApiController FAILED_OPEN_SQL) compares with NOW() too
    assert sql(jobs, "SELECT COUNT(*) AS n FROM approval_jobs WHERE finished_at > NOW() - INTERVAL 24 HOUR "
                     "AND finished_at >= created_at")[0]["n"] == 1


def test_no_timestamp_is_written_from_a_python_clock():
    from pathlib import Path

    source = Path(approval_jobs.__file__).read_text(encoding="utf-8")
    assert "import datetime" not in source and "from datetime" not in source and "time.time" not in source
    assert "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())" in source
    assert "started_at = NOW(), updated_at = NOW()" in source and "finished_at = NOW(), " in source


class _LockConn:
    def __init__(self, got):
        self.got = got

    def cursor(self):
        conn = self

        class _Cur:
            def execute(self, *a):
                pass

            def fetchone(self):
                return {"got": 1 if conn.got else 0}

        return _Cur()

    def close(self):
        pass


@pytest.mark.parametrize("slot_free", [True, False])
def test_an_idle_worker_writes_nothing_to_its_log(monkeypatch, capsys, slot_free):
    """laqta-approvals.timer starts a worker every minute: «queue empty» / «slots busy» are not logged."""
    from scripts import approval_worker

    monkeypatch.setattr(approval_jobs, "ensure_schema", lambda: True)
    monkeypatch.setattr(approval_jobs, "give_up_stale", lambda: None)
    monkeypatch.setattr(approval_jobs, "claim", lambda token: None)
    monkeypatch.setattr(approval_worker, "_drop_kick", lambda: None)
    monkeypatch.setattr(approval_worker.db_connect, "connect", lambda *a, **k: _LockConn(slot_free))
    assert approval_worker.main() == 0
    assert capsys.readouterr().out == ""


def test_a_worker_that_ran_jobs_logs_them(monkeypatch, capsys):
    from scripts import approval_worker

    queue = [{"id": 5, "row_number": 12, "params": {}, "claim_token": "t"}]
    monkeypatch.setattr(approval_jobs, "ensure_schema", lambda: True)
    monkeypatch.setattr(approval_jobs, "give_up_stale", lambda: None)
    monkeypatch.setattr(approval_jobs, "claim", lambda token: queue.pop() if queue else None)
    monkeypatch.setattr(approval_worker, "_drop_kick", lambda: None)
    monkeypatch.setattr(approval_worker.db_connect, "connect", lambda *a, **k: _LockConn(True))
    monkeypatch.setattr(approval_worker, "run_with_heartbeat", lambda job: ({"status": "success"}, 200))
    monkeypatch.setattr(approval_worker, "finish_with_retry", lambda job, result, http: "done")
    assert approval_worker.main() == 0
    out = capsys.readouterr().out
    assert "job 5 row 12 -> done" in out and "1 job(s), queue empty" in out


def test_the_approval_worker_log_is_rotated():
    """laqta-approvals.service appends to temp/approval_worker.log; logrotate's temp/*.log block covers it."""
    from pathlib import Path

    deploy = Path(approval_jobs.__file__).resolve().parent / "deploy" / "ubuntu"
    assert ">> @APP_DIR@/temp/approval_worker.log" in (deploy / "laqta-approvals.service").read_text(encoding="utf-8")
    rotate = (deploy / "laqta-logrotate").read_text(encoding="utf-8")
    block = rotate.split("@APP_DIR@/temp/*.log", 1)[1].split("}", 1)[0]
    assert "copytruncate" in block and "maxsize" in block
