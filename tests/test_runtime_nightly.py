"""The nightly run on a server (runtime package, items 1, 5 and 6).

- SIGTERM / SIGHUP (stop_signals) during the enqueue, the worker or the wait before a retry stop the night at once:
  no retry, the run state is settled, one report is written (stop reason 'shutdown', exit 3).
- After the report, the storage cleanup (scripts/prune_storage.py --apply) runs when NIGHTLY_PRUNE_ENABLED, within
  PRUNE_MAX_SECONDS and the time left; it never changes the night.
- HEALTHCHECK_URL: <url>/start at the start, <url> for exit codes 0 and 3, <url>/fail for 1 and 2; 5-second timeout;
  a failed ping never changes the night and the URL is never logged.
"""

import os
import signal

import pytest

from test_nightly_runner import _last_report, nightly  # noqa: F401  (the runner with main's entry points recorded)


@pytest.fixture
def stops():
    import stop_signals

    stop_signals.reset()
    yield stop_signals
    stop_signals.reset()


def _sigterm(stops):
    stops._handler(signal.SIGTERM, None)       # what the installed handler does in the main thread


# ---------------------------------------------------------------------------
# stop signals
# ---------------------------------------------------------------------------

def test_sigterm_during_the_worker_writes_the_report_and_never_retries(nightly, monkeypatch, stops):
    runner, main, _, rec = nightly

    def worker(trigger="manual", report=True, deadline_ts=None):
        rec["calls"].append(("worker",))
        main.LAST_WORKER.update(stop_reason="shutdown", run_id="run-1", worker_id="host:1")
        os.remove(runner.LOCK_FILE)
        _sigterm(stops)

    monkeypatch.setattr(main, "run_worker_mode", worker)
    assert runner.run() == 3
    assert [c[0] for c in rec["calls"]] == ["enqueue", "worker"] and rec["sleeps"] == []
    report = _last_report()
    assert report["stop_reason"] == "shutdown" and report["outcome"] == "stopped" and report["exit_code"] == 3
    assert "السيرفر" in report["reason_text"]
    assert not os.path.exists(runner.LOCK_FILE)


def test_a_worker_stopped_before_it_wrote_its_result_is_still_a_shutdown(nightly, monkeypatch, stops):
    runner, main, _, rec = nightly

    def worker(trigger="manual", report=True, deadline_ts=None):
        _sigterm(stops)                       # nothing in LAST_WORKER yet

    monkeypatch.setattr(main, "run_worker_mode", worker)
    assert runner.run() == 3
    assert rec["reports"][-1]["stop_reason"] == "shutdown"
    assert not os.path.exists(runner.LOCK_FILE)


def test_sigterm_during_the_enqueue_settles_the_state_and_starts_no_worker(nightly, monkeypatch, stops):
    runner, main, _, rec = nightly
    import local_cache_db

    settled = []
    monkeypatch.setattr(local_cache_db, "stop_run", lambda worker_active=False: settled.append(worker_active))
    monkeypatch.setattr(main, "run_enqueue_mode", lambda: _sigterm(stops))
    assert runner.run() == 3
    assert rec["calls"] == [] and settled == [False] and rec["sleeps"] == []
    assert rec["reports"][-1]["stop_reason"] == "shutdown"
    assert not os.path.exists(runner.LOCK_FILE)


def test_sigterm_while_waiting_for_a_retry_ends_the_night(nightly, stops):
    runner, _, _, rec = nightly
    rec["workers"] = [{"stop_reason": "provider_down", "run_id": "run-1"}]

    def sleep(seconds):
        rec["sleeps"].append(seconds)
        _sigterm(stops)

    assert runner.run(sleep=sleep) == 3
    assert rec["sleeps"] == [15 * 60] and [c[0] for c in rec["calls"]] == ["enqueue", "worker"]
    assert _last_report()["stop_reason"] == "shutdown"


def test_a_signal_recorded_during_the_workers_cleanup_means_no_retry(nightly, monkeypatch, stops):
    """The signal arrived while the worker wrote its final state (stop_signals.deferred): it was not raised, but the
    night does not start a retry 15 minutes later."""
    runner, _, _, rec = nightly
    rec["workers"] = [{"stop_reason": "provider_down", "run_id": "run-1"}]
    with stops.deferred():
        stops._handler(signal.SIGTERM, None)
    assert runner.run() == 2
    assert rec["sleeps"] == [] and len(rec["calls"]) == 2


# ---------------------------------------------------------------------------
# the storage cleanup at the end of the night
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_prune(nightly, monkeypatch):
    runner, _, _, rec = nightly
    calls = []

    class Module:
        @staticmethod
        def prune(**kwargs):
            calls.append(dict(kwargs, reports_before=len(rec["reports"])))
            return {"apply": True}

    monkeypatch.setattr(runner, "_load_prune", lambda: Module)
    monkeypatch.setenv("NIGHTLY_PRUNE_ENABLED", "1")
    monkeypatch.delenv("PRUNE_MAX_SECONDS", raising=False)
    return calls


def test_the_cleanup_runs_after_the_report_within_its_cap(nightly, fake_prune):
    runner, _, _, rec = nightly
    assert runner.run() == 0
    (call,) = fake_prune
    assert call["apply"] is True and call["max_seconds"] == 300 and call["reports_before"] == 1
    assert call["log"] is runner.say


def test_the_cleanup_is_off_by_the_setting(nightly, fake_prune, monkeypatch):
    runner, _, _, _ = nightly
    monkeypatch.setenv("NIGHTLY_PRUNE_ENABLED", "0")
    assert runner.run() == 0
    assert fake_prune == []


def test_the_cleanup_stays_inside_the_time_left_and_never_fails_the_night(nightly, fake_prune):
    runner, _, _, _ = nightly
    assert runner.prune_storage(seconds_left=65) is None and fake_prune == []        # no time left
    assert runner.prune_storage(seconds_left=200) == {"apply": True}
    assert fake_prune[-1]["max_seconds"] == 140

    def broken(**kwargs):
        raise RuntimeError("disk")

    assert runner.prune_storage(prune=broken) is None


def test_no_cleanup_after_a_stop_signal(nightly, fake_prune, stops):
    runner, _, _, _ = nightly
    with stops.deferred():
        stops._handler(signal.SIGTERM, None)
    assert runner.prune_storage() is None and fake_prune == []


def test_the_suite_never_cleans_by_default():
    from catalog_match import settings

    assert os.environ["NIGHTLY_PRUNE_ENABLED"] == "0" and settings.nightly_prune_enabled() is False
    assert settings.DEFAULTS["NIGHTLY_PRUNE_ENABLED"] is True          # on for the owner


# ---------------------------------------------------------------------------
# the healthcheck ping
# ---------------------------------------------------------------------------

class _Answer:
    def __init__(self, status):
        self.status_code = status


def _check_url():
    return "https://hc-ping.example/" + "-".join(("tst", "x", "7f3c9a1e"))


def test_the_pings_and_their_urls(nightly, capsys):
    runner = nightly[0]
    sent = []

    def get(url, timeout=None):
        sent.append((url, timeout))
        return _Answer(200)

    base = _check_url()
    assert runner.ping_healthcheck("start", url=base, get=get) is True
    assert runner.ping_healthcheck("", url=base + "/", get=get) is True
    assert runner.ping_healthcheck("fail", url=base, get=get) is True
    assert sent == [(base + "/start", 5), (base, 5), (base + "/fail", 5)]
    assert base not in capsys.readouterr().out                         # the URL identifies the check


def test_a_ping_never_changes_the_night(nightly):
    runner = nightly[0]

    def down(url, timeout=None):
        raise OSError("connection refused")

    assert runner.ping_healthcheck("start", url=_check_url(), get=down) is False
    assert runner.ping_healthcheck("", url=_check_url(), get=lambda url, timeout=None: _Answer(503)) is False


def test_no_url_no_ping(nightly, monkeypatch):
    runner = nightly[0]
    from catalog_match import settings

    monkeypatch.setattr(settings, "_config", None)
    for value in ("", "   ", "not a url", "ftp://x.example/1"):
        monkeypatch.setenv("HEALTHCHECK_URL", value)
        assert settings.healthcheck_url() == ""
        assert runner.ping_healthcheck("start", get=lambda *a, **k: pytest.fail("no ping expected")) is False
    monkeypatch.setenv("HEALTHCHECK_URL", _check_url() + "/")
    assert settings.healthcheck_url() == _check_url()


@pytest.fixture
def nightly_main(nightly, monkeypatch, tmp_path):
    """runner.main() in a scratch folder with run() replaced; records the pings, keeps the test's signal handlers."""
    runner = nightly[0]
    import stop_signals

    pings = []
    monkeypatch.setattr(runner, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(stop_signals, "install", lambda *a: ["SIGTERM"])
    monkeypatch.setattr(runner, "ping_healthcheck", lambda kind="", **k: pings.append(kind) or True)
    return runner, pings


@pytest.mark.parametrize("code, last", [(0, ""), (3, ""), (2, "fail"), (1, "fail")])
def test_the_night_pings_start_then_its_result(nightly_main, monkeypatch, code, last):
    runner, pings = nightly_main
    monkeypatch.setattr(runner, "run", lambda **kw: code)
    assert runner.main([]) == code
    assert pings == ["start", last]


def test_a_crash_or_a_stop_signal_outside_run_still_pings_and_reports(nightly_main, monkeypatch, stops):
    runner, pings = nightly_main

    def crash(**kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "run", crash)
    assert runner.main([]) == 1 and pings == ["start", "fail"]

    pings.clear()
    monkeypatch.setattr(runner, "run", lambda **kw: _sigterm(stops))
    assert runner.main([]) == 3 and pings == ["start", ""]
    assert _last_report()["stop_reason"] == "shutdown"
