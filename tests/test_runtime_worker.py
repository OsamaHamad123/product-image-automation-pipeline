"""The worker on an Ubuntu server: stop signals, product deadlines and row leases (runtime package, items 1 and 2).

- SIGTERM / SIGHUP (systemctl stop, a timeout, a reboot) raise KeyboardInterrupt once, in the main thread only, into the
  worker's existing "stopped" path: no new row, the products in progress get SHUTDOWN_GRACE_S seconds, the rows still
  running go back to 'pending' at once, the lock is released, the report is written, exit code 3 (stop reason
  'shutdown'). Cleanup in progress is never cut by a second signal. Proven on a real subprocess.
- A product still running after PRODUCT_DEADLINE_MINUTES is given up: its row goes back to the queue like a provider
  outage (PROVIDER_DOWN), its late result is never written, the worker takes the next row and never waits for the
  hung thread, at the end of the run or at exit.
- While a product runs, the worker renews the lease of its row (LEASE_RENEW_SECONDS), so a long product is not
  claimed a second time (a second paid search) and its result is not thrown away.
"""

import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from test_queue_scheduling import worker  # noqa: F401  (the offline worker-loop fixture)

ROOT = Path(__file__).resolve().parents[1]
POSIX = os.name == "posix"


# ---------------------------------------------------------------------------
# stop_signals
# ---------------------------------------------------------------------------

@pytest.fixture
def stops():
    import stop_signals

    stop_signals.reset()
    yield stop_signals
    stop_signals.reset()


def test_the_first_stop_signal_raises_keyboard_interrupt_once(stops):
    with pytest.raises(KeyboardInterrupt):
        stops._handler(signal.SIGTERM, None)
    assert stops.requested() == "SIGTERM"
    stops._handler(signal.SIGTERM, None)              # a second signal: the cleanup in progress goes on
    assert stops.requested() == "SIGTERM"


def test_a_signal_during_cleanup_is_recorded_and_does_not_cut_it(stops):
    with stops.deferred():
        stops._handler(getattr(signal, "SIGHUP", signal.SIGTERM), None)
        with stops.deferred():                          # nested cleanups
            pass
        still_deferred = stops._STATE["deferred"]
    assert still_deferred == 1 and stops._STATE["deferred"] == 0
    assert stops.requested() in ("SIGHUP", "SIGTERM")  # the caller checks it once the cleanup is done
    with pytest.raises(KeyboardInterrupt):            # outside a cleanup the next signal stops the process
        stops._handler(signal.SIGTERM, None)
    stops._handler(signal.SIGTERM, None)


def test_the_handler_is_installed_from_the_main_thread_only(stops):
    seen = []
    thread = threading.Thread(target=lambda: seen.append(stops.install()))
    thread.start()
    thread.join()
    assert seen == [[]]
    before = {name: signal.getsignal(getattr(signal, name)) for name in stops.STOP_SIGNALS if hasattr(signal, name)}
    try:
        installed = stops.install()
        assert "SIGTERM" in installed and ("SIGHUP" in installed or not hasattr(signal, "SIGHUP"))
        assert signal.getsignal(signal.SIGTERM) is stops._handler
    finally:
        for name, handler in before.items():
            signal.signal(getattr(signal, name), handler)


def test_the_shutdown_reason_is_a_stop_with_its_own_text():
    import run_report

    assert run_report.exit_code("shutdown") == 3 and run_report.outcome_of("shutdown") == "stopped"
    assert "السيرفر" in run_report.reason_text("shutdown")


def test_the_settings_and_their_bounds(monkeypatch):
    from catalog_match import settings

    monkeypatch.setattr(settings, "_config", None)
    for name in ("PRODUCT_DEADLINE_MINUTES", "SHUTDOWN_GRACE_S"):
        monkeypatch.delenv(name, raising=False)
    assert settings.product_deadline_s() == 8 * 60 and settings.shutdown_grace_s() == 45
    monkeypatch.setenv("PRODUCT_DEADLINE_MINUTES", "0")
    monkeypatch.setenv("SHUTDOWN_GRACE_S", "9999")
    assert settings.product_deadline_s() == 60 and settings.shutdown_grace_s() == 600
    monkeypatch.setenv("PRODUCT_DEADLINE_MINUTES", "2.5")
    assert settings.product_deadline_s() == 150


# ---------------------------------------------------------------------------
# the product pool
# ---------------------------------------------------------------------------

@pytest.fixture
def released_threads():
    """Hung test products wait on this event; it is set at the end of the test so no thread outlives it."""
    event = threading.Event()
    yield event
    event.set()


def test_an_overdue_product_is_given_up_and_its_slot_freed(released_threads):
    import main
    from concurrent.futures import ThreadPoolExecutor

    made = []

    def factory(max_workers):
        made.append(ThreadPoolExecutor(max_workers=max_workers))
        return made[-1]

    pool = main._ProductPool(factory, 1, deadline_s=0.2)
    hung = {"id": 1, "row_number": 11}
    pool.submit(lambda t: released_threads.wait(30), hung)
    time.sleep(0.05)
    assert pool.overdue() == [] and len(pool.live()) == 1
    time.sleep(0.25)
    assert pool.overdue() == [hung] and hung["_given_up"] is True
    assert pool.live() == [] and len(made) == 2                    # a fresh executor for the next product
    quick = {"id": 2, "row_number": 12}
    assert pool.submit(lambda t: "done", quick).result(timeout=5) == "done"
    assert any(not f.done() for f in main._LEFT_RUNNING)
    started = time.monotonic()
    assert main._drain_products(pool) == [] and time.monotonic() - started < 1


def test_a_product_queued_behind_others_is_timed_from_its_own_start(released_threads):
    import main
    from concurrent.futures import ThreadPoolExecutor

    pool = main._ProductPool(ThreadPoolExecutor, 1, deadline_s=0.3)
    first = {"id": 1}
    second = {"id": 2}
    pool.submit(lambda t: time.sleep(0.2), first)
    pool.submit(lambda t: time.sleep(0.2), second)                 # waits for the only thread
    time.sleep(0.35)
    assert pool.overdue() == []                                    # 0.15 s of its own: not overdue
    assert main._drain_products(pool) == []


def test_the_grace_drain_leaves_what_still_runs(released_threads):
    import main
    from concurrent.futures import ThreadPoolExecutor

    pool = main._ProductPool(ThreadPoolExecutor, 2, deadline_s=600)
    hung = {"id": 1, "worker_id": "h:1#a"}
    quick = {"id": 2, "worker_id": "h:1#b"}
    pool.submit(lambda t: released_threads.wait(30), hung)
    pool.submit(lambda t: time.sleep(0.1), quick)
    started = time.monotonic()
    assert main._drain_products(pool, grace_s=0.5) == [hung]
    assert 0.4 < time.monotonic() - started < 3


# ---------------------------------------------------------------------------
# the worker loop (offline, recorders)
# ---------------------------------------------------------------------------

@pytest.fixture
def statuses(worker, monkeypatch):
    main, ldb, _, rec = worker
    rec["finished"] = []
    rec["renewed"] = []
    rec["released"] = []
    rec["stop_run"] = []
    monkeypatch.setattr(ldb, "update_task_status", lambda task_id, status, error_message=None, **kw:
                        rec["finished"].append((task_id, status, kw.get("failure_code"), error_message)) or True)
    monkeypatch.setattr(ldb, "renew_leases", lambda claims, **kw: rec["renewed"].append(list(claims)) or len(claims))
    monkeypatch.setattr(ldb, "release_claims", lambda claims: rec["released"].append(list(claims)) or len(claims))
    monkeypatch.setattr(ldb, "stop_run", lambda worker_active=False: rec["stop_run"].append(worker_active))
    for i, task in enumerate(rec["tasks"]):
        task["worker_id"] = f"host:1#c{i}"
    return worker


def test_a_hung_product_is_given_up_and_the_worker_goes_on(statuses, monkeypatch, released_threads):
    main, _, _, rec = statuses
    from catalog_match import settings

    monkeypatch.setattr(settings, "product_deadline_s", lambda: 0.4)
    rec["tasks"] = rec["tasks"][:4]

    def precache(task, *a, report=None, **k):
        if task["row_number"] == 101:
            released_threads.wait(60)          # a provider that never answers
            main._finish_task(task, "ready_for_review")   # the late result: never written
            return "success"
        rec["worked"].append(task["row_number"])
        main._finish_task(task, "ready_for_review")
        return "success"

    monkeypatch.setattr(main, "pre_cache_product_candidates", precache)
    started = time.monotonic()
    main.run_worker_mode(report=False)
    assert time.monotonic() - started < 10, "the worker waited for the hung product"
    assert sorted(rec["worked"]) == [100, 102, 103]
    given_up = [f for f in rec["finished"] if f[0] == 1]
    assert given_up == [(1, "pending", "PROVIDER_DOWN", given_up[0][3])]
    assert given_up[0][3].startswith("PRODUCT_TIMEOUT: still running after")
    assert main.LAST_WORKER["stop_reason"] is None and rec["released"] == []
    released_threads.set()
    time.sleep(0.2)
    assert [f for f in rec["finished"] if f[0] == 1] == given_up, "a given-up product's late result was counted"


def test_the_heartbeat_renews_the_leases_of_the_rows_in_progress(statuses, monkeypatch):
    main, _, _, rec = statuses
    monkeypatch.setattr(main, "LEASE_RENEW_SECONDS", 0)
    rec["tasks"] = rec["tasks"][:2]

    def precache(task, *a, report=None, **k):
        threading.Event().wait(0.3)             # time.sleep is shortened by the fixture
        return "success"

    monkeypatch.setattr(main, "pre_cache_product_candidates", precache)
    main.run_worker_mode(report=False)
    renewed = {claim for claims in rec["renewed"] for claim in claims}
    assert renewed == {"host:1#c0", "host:1#c1"}
    assert all(claims for claims in rec["renewed"])               # no empty renewal (no connection for nothing)


def test_a_stop_signal_gives_the_products_their_grace_then_hands_the_rest_back(statuses, monkeypatch,
                                                                             released_threads, stops):
    """SIGTERM in the main thread (here: while it reads the run state) during two products: the quick one finishes in
    the grace period and keeps its result, the hung one's row goes back to 'pending' at once."""
    main, ldb, _, rec = statuses
    monkeypatch.setenv("SHUTDOWN_GRACE_S", "1")
    rec["tasks"] = rec["tasks"][:2]
    running = threading.Event()

    def precache(task, *a, report=None, **k):
        if task["row_number"] == 100:
            running.set()
            released_threads.wait(60)
            return "success"
        threading.Event().wait(0.3)
        main._finish_task(task, "ready_for_review")
        return "success"

    calls = {"n": 0}

    def state():
        calls["n"] += 1
        if running.is_set() and len(rec["tasks"]) == 0 and calls["n"] > 3:
            stops._handler(signal.SIGTERM, None)
        return dict(rec["state"])

    monkeypatch.setattr(main, "pre_cache_product_candidates", precache)
    monkeypatch.setattr(ldb, "get_automation_state", state)
    monkeypatch.setattr(ldb, "count_open_tasks", lambda: 1)          # rows still waiting: only the signal ends it
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        main.run_worker_mode(report=False)
    assert time.monotonic() - started < 10
    assert (1, "ready_for_review", None, None) in rec["finished"]       # finished inside the grace period
    assert rec["released"] == [["host:1#c0"]]                            # the hung one: back to the queue at once
    assert rec["stop_run"] == [False]                                    # the existing "stopped" path
    assert main.LAST_WORKER["stop_reason"] == "shutdown"
    assert not os.path.exists(main.LOCK_FILE)


def test_a_dashboard_stop_still_waits_for_the_products_in_progress(statuses, monkeypatch):
    """Only a stop signal has a grace period: the dashboard's stop button lets the products in progress finish."""
    main, ldb, _, rec = statuses
    monkeypatch.setenv("SHUTDOWN_GRACE_S", "0")
    rec["tasks"] = rec["tasks"][:1]

    def precache(task, *a, report=None, **k):
        rec["state"]["stop_requested"] = 1
        threading.Event().wait(0.5)
        main._finish_task(task, "ready_for_review")
        return "success"

    monkeypatch.setattr(main, "pre_cache_product_candidates", precache)
    main.run_worker_mode(report=False)
    assert main.LAST_WORKER["stop_reason"] == "stopped"
    assert [f[:2] for f in rec["finished"]] == [(0, "ready_for_review")]
    assert rec["released"] == []


# ---------------------------------------------------------------------------
# a real worker process receives SIGTERM
# ---------------------------------------------------------------------------

HARNESS = textwrap.dedent(r'''
    import json, os, socket, sys, threading, time
    sys.path.insert(0, os.environ["REPO_ROOT"])
    events_path = os.path.abspath("events.jsonl")
    _guard = threading.Lock()

    def event(kind, **data):
        with _guard, open(events_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(data, kind=kind)) + "\n")
            fh.flush()

    import config, google_sheets, local_cache_db, main, run_report

    def refuse(*a, **k):
        raise OSError("network access is blocked in this harness")

    socket.socket.connect = refuse
    socket.create_connection = refuse
    os.makedirs("temp", exist_ok=True)
    mode = os.environ["HARNESS_MODE"]
    tasks = [{"id": i, "row_number": 10 + i, "product_name": f"P{i}", "worker_id": f"h:1#c{i}"} for i in (1, 2, 3)]
    state = {"pause_requested": 0, "stop_requested": 0, "run_id": None, "status": "pre_caching"}
    config.DAILY_BUDGET_USD = 0
    main._another_worker_running = lambda lock: False
    main.load_run_config = lambda: None
    main.check_verifier = lambda: ""
    main._outage_notice = lambda worker_id, since, base=None, **k: base
    main._refresh_state = lambda *a, **k: None
    main._run_health = lambda *a, **k: None
    main._harvest_pending_brand_sites = lambda: None
    main._start_local_index_refresh = lambda trigger: None
    local_cache_db.resume_automation = lambda: True
    local_cache_db.get_automation_state = lambda: dict(state)
    local_cache_db.update_automation_state = lambda *a, **k: True
    local_cache_db.get_ready_for_review_count = lambda: 0
    local_cache_db.requeue_verifier_down = lambda run_id=None: 0
    local_cache_db.park_verifier_rechecks = lambda: 0
    local_cache_db.fetch_next_task = lambda worker_id: tasks.pop(0) if tasks else None
    local_cache_db.count_open_tasks = lambda: 1
    local_cache_db.renew_leases = lambda claims, **k: len(claims)
    local_cache_db.update_task_status = lambda task_id, status, *a, **k: event("finish", id=task_id, status=status) or True
    local_cache_db.release_claims = lambda claims: event("release", claims=sorted(claims)) or len(claims)
    local_cache_db.stop_run = lambda worker_active=False: event("stop_run", worker_active=worker_active)
    google_sheets.get_sheets_client = lambda: object()
    google_sheets.open_worksheet = lambda client, name: object()
    google_sheets.find_link_column = lambda ws: 7
    google_sheets.get_brand_mappings = lambda *a: {}
    google_sheets.init_async_queue = lambda *a: None
    google_sheets.stop_async_queue = lambda: event("stop_async_queue")
    run_report.report_worker_run = lambda info, trigger=None: event("report", stop_reason=info.get("stop_reason"))
    forever = threading.Event()

    def precache(task, *a, report=None, **k):
        event("started", id=task["id"])
        if mode == "hung" and task["id"] in (1, 2):
            forever.wait()                    # a provider that never answers
        else:
            time.sleep(1.5 if mode == "finish" else 0.05)
            main._finish_task(task, "ready_for_review")
        return "success"

    main.pre_cache_product_candidates = precache
    event("ready")
    main.cli(["main.py", "--worker"])
''')


def _events(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_and_terminate(tmp_path, mode, grace, wait_for):
    script = tmp_path / "harness.py"
    script.write_text(HARNESS, encoding="utf-8")
    env = dict(os.environ, REPO_ROOT=str(ROOT), HARNESS_MODE=mode, SHUTDOWN_GRACE_S=str(grace),
               PYTHONUNBUFFERED="1")
    events = tmp_path / "events.jsonl"
    log = tmp_path / "harness.log"

    def output():
        return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""

    with open(log, "wb") as sink:
        proc = subprocess.Popen([sys.executable, str(script)], cwd=tmp_path, env=env, stdout=sink,
                                stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 60
        while not wait_for(_events(events)):
            assert proc.poll() is None, output()
            assert time.monotonic() < deadline, (_events(events), output())
            time.sleep(0.05)
        sent = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=60)
        return proc.returncode, time.monotonic() - sent, _events(events), output()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


@pytest.mark.skipif(not POSIX, reason="needs POSIX signals")
def test_sigterm_hands_hung_products_back_and_the_process_exits(tmp_path):
    def ready(events):
        started = {e["id"] for e in events if e["kind"] == "started"}
        finished = {e["id"] for e in events if e["kind"] == "finish"}
        return {1, 2} <= started and 3 in finished

    code, took, events, out = _run_and_terminate(tmp_path, "hung", grace=1, wait_for=ready)
    assert code == 3, out                                            # stopped before the queue was empty
    assert took < 20, f"the process waited {took:.1f} s for its hung threads"
    kinds = [e["kind"] for e in events]
    assert {"kind": "release", "claims": ["h:1#c1", "h:1#c2"]} in events
    assert {"kind": "stop_run", "worker_active": False} in events     # every row left 'processing' back to pending
    assert "stop_async_queue" in kinds                               # the sheet writes in flight are flushed
    assert {"kind": "report", "stop_reason": "shutdown"} in events
    assert kinds.index("release") < kinds.index("stop_run") < kinds.index("report")
    assert not (tmp_path / "temp" / "pipeline.lock").exists()


@pytest.mark.skipif(not POSIX, reason="needs POSIX signals")
def test_sigterm_lets_the_products_in_progress_finish_within_the_grace(tmp_path):
    def ready(events):
        return len({e["id"] for e in events if e["kind"] == "started"}) == 3

    code, took, events, out = _run_and_terminate(tmp_path, "finish", grace=30, wait_for=ready)
    assert code == 3, out
    finished = {e["id"] for e in events if e["kind"] == "finish" and e["status"] == "ready_for_review"}
    assert finished == {1, 2, 3}                                     # nothing searched twice
    assert not any(e["kind"] == "release" for e in events)
    assert {"kind": "report", "stop_reason": "shutdown"} in events
    assert took < 25
