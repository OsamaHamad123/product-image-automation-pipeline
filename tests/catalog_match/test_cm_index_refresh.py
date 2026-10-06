"""catalog_match.index_refresh: the local catalog index refreshes itself in the background.

No test reaches a network: the harvester and the database are fakes (a clock the fake harvester advances, or a
short real sleep where the point is that the caller does not wait). Covered: the staleness decision (fresh, stale,
never harvested), the time budget, a refresh thread that never blocks the caller, a blocked store left alone for 7
days, the single-flight lock, the button's detached job, the stores file (Sharjah Co-op), the settings, and the two
database queries (harvest ages, how many products the index answered).
"""

import json
import os
import socket
import sys
import threading
import time

import pytest

from catalog_match import index_refresh, settings, sitemaps
from catalog_match.index_refresh import BLOCKED, FORCED, FRESH, NEVER, RETRY_LATER, STALE, decide, plan
from catalog_match.local_index import DbCatalogStore
from catalog_match.sitemaps import HarvestReport, load_stores

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DAY, HOUR = 86400, 3600


@pytest.fixture(autouse=True)
def _no_network(request, monkeypatch):
    if "mariadb_or_skip" in request.fixturenames:       # the two database tests talk to the local test database only
        return

    def _blocked(*args, **kwargs):
        raise RuntimeError("network access is blocked in catalog_match unit tests")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture(autouse=True)
def _index_on(monkeypatch):
    """The refresh switches as the owner has them by default (the test suite itself runs with the refresh off)."""
    import config
    monkeypatch.setattr(config, "LOCAL_INDEX_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "LOCAL_INDEX_REFRESH_DAYS", "7", raising=False)
    monkeypatch.setattr(config, "LOCAL_INDEX_REFRESH_MAX_S", "300", raising=False)
    monkeypatch.setattr(index_refresh, "_THREAD", None)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Store:
    def __init__(self, key):
        self.key = key
        self.name = key.title()


class FakeDb:
    """The index's database as index_refresh uses it; a harvest recorded here changes the next harvest_ages()."""

    def __init__(self, ages=None, slow_ages_s=0.0):
        self.ages = dict(ages or {})
        self.slow_ages_s = slow_ages_s
        self.began, self.finished, self.rows = [], [], []

    def harvest_ages(self):
        if self.slow_ages_s:
            time.sleep(self.slow_ages_s)
        return dict(self.ages)

    def begin_harvest(self, key):
        self.began.append(key)
        return "t0"

    def upsert(self, key, batch):
        self.rows.extend((key, u) for u, _ in batch)
        return len(batch)

    def finish_harvest(self, key, started, report):
        self.finished.append((key, dict(report)))
        done = report["status"] in index_refresh.COMPLETE_STATUSES
        self.ages[key] = {"ok_age_s": 0 if done else (self.ages.get(key) or {}).get("ok_age_s"),
                          "last_status": report["status"], "last_age_s": 0}


class FakeHarvester:
    """Each harvest advances the fake clock by `cost` seconds (or really sleeps `sleep_s`); status per store."""

    def __init__(self, clock=None, cost=0.0, sleep_s=0.0, statuses=None, sitemaps_per_store=1):
        self.clock, self.cost, self.sleep_s = clock, cost, sleep_s
        self.statuses = statuses or {}
        self.sitemaps_per_store = sitemaps_per_store
        self.calls, self.stop_args = [], []

    def harvest(self, store, on_urls=None, max_urls=None, max_sitemaps=None, discover=False, should_stop=None):
        self.calls.append(store.key)
        self.stop_args.append(should_stop)
        if self.sleep_s:
            time.sleep(self.sleep_s)
        rep = HarvestReport(store=store.key, status=self.statuses.get(store.key, "ok"))
        for _ in range(self.sitemaps_per_store):
            if should_stop is not None and should_stop():
                rep.truncated, rep.error = True, "stopped at the time budget"
                rep.status = "partial"
                break
            if self.clock is not None:
                self.clock.t += self.cost
            rep.sitemaps_read += 1
            if rep.status in ("ok", "partial"):
                rep.product_urls += 2
                if on_urls is not None:
                    rep.new_urls += on_urls([(f"https://x.example/{store.key}/p/1", None),
                                             (f"https://x.example/{store.key}/p/2", None)])
        return rep


def paths(tmp_path):
    return {"state_path": tmp_path / "state.json", "lock_path": tmp_path / "state.lock"}


def ages(ok=None, last_status="ok", last=None):
    return {"ok_age_s": ok, "last_status": last_status, "last_age_s": ok if last is None else last}


# ---------------------------------------------------------------------------
# The staleness decision
# ---------------------------------------------------------------------------

def test_a_store_harvested_within_the_refresh_days_is_fresh():
    assert decide(ages(ok=2 * DAY), 7) == (False, FRESH)
    assert decide(ages(ok=7 * DAY - 1), 7) == (False, FRESH)


def test_a_store_whose_newest_complete_harvest_is_older_is_stale():
    assert decide(ages(ok=7 * DAY), 7) == (True, STALE)
    assert decide(ages(ok=30 * DAY), 7) == (True, STALE)
    assert decide(ages(ok=2 * DAY), 1) == (True, STALE)            # LOCAL_INDEX_REFRESH_DAYS decides


def test_a_store_never_harvested_is_due_at_once():
    assert decide(None, 7) == (True, NEVER)
    assert decide({}, 7) == (True, NEVER)
    # only a partial or failed harvest so far: no complete one, so still due (after the retry wait)
    assert decide(ages(ok=None, last_status="error", last=2 * DAY), 7) == (True, NEVER)


def test_a_partial_or_failed_harvest_is_retried_after_six_hours_not_at_every_run():
    assert decide(ages(ok=None, last_status="partial", last=HOUR), 7) == (False, RETRY_LATER)
    assert decide(ages(ok=10 * DAY, last_status="error", last=2 * HOUR), 7) == (False, RETRY_LATER)
    assert decide(ages(ok=10 * DAY, last_status="error", last=7 * HOUR), 7) == (True, STALE)


def test_force_ignores_freshness_and_the_retry_wait():
    assert decide(ages(ok=HOUR), 7, force=True) == (True, FORCED)
    assert decide(ages(ok=None, last_status="error", last=60), 7, force=True) == (True, FORCED)


def test_a_store_that_answered_blocked_is_left_alone_for_seven_days_even_when_forced():
    blocked = lambda age: ages(ok=20 * DAY, last_status="blocked", last=age)
    assert decide(blocked(HOUR), 7) == (False, BLOCKED)
    assert decide(blocked(6 * DAY), 7) == (False, BLOCKED)
    assert decide(blocked(6 * DAY), 7, force=True) == (False, BLOCKED)     # a click does not push a store that refused
    assert decide(blocked(7 * DAY), 7) == (True, STALE)                    # a week later it is asked again
    assert decide(ages(ok=None, last_status="blocked", last=DAY), 7) == (False, BLOCKED)


def test_the_plan_reads_never_harvested_stores_first_then_the_stalest():
    stores = [Store(k) for k in ("a", "b", "c", "d", "e")]
    state = {"a": ages(ok=9 * DAY), "b": ages(ok=30 * DAY), "c": None, "d": ages(ok=DAY),
             "e": ages(ok=None, last_status="blocked", last=DAY)}
    todo, skipped = plan(stores, {k: v for k, v in state.items() if v}, 7)
    assert [(s.key, why) for s, why in todo] == [("c", NEVER), ("b", STALE), ("a", STALE)]
    assert [(s.key, why) for s, why in skipped] == [("d", FRESH), ("e", BLOCKED)]


# ---------------------------------------------------------------------------
# The refresh, its budget and its records
# ---------------------------------------------------------------------------

def run(db, harvester, stores, tmp_path, clock=None, **kw):
    return index_refresh.refresh(db=db, harvester=harvester, stores=stores, clock=clock or Clock(),
                                 **paths(tmp_path), **kw)


def test_a_refresh_reads_the_due_stores_and_records_each_harvest(tmp_path):
    db = FakeDb({"fresh": ages(ok=DAY), "stale": ages(ok=30 * DAY)})
    harvester = FakeHarvester()
    result = run(db, harvester, [Store("fresh"), Store("stale"), Store("new")], tmp_path, budget_s=300)
    assert harvester.calls == ["new", "stale"]                         # never harvested first; the fresh one waits
    assert result["reason"] == "done" and [r["store"] for r in result["results"]] == ["new", "stale"]
    assert [k for k, _ in db.finished] == ["new", "stale"] and db.began == ["new", "stale"]
    assert all(rep["status"] == "ok" and rep["product_urls"] == 2 for _, rep in db.finished)
    assert len(db.rows) == 4                                           # the urls went to the index
    assert [s["reason"] for s in result["skipped"]] == [FRESH]
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["state"] == "idle" and state["ended"] == "done" and state["current"] is None
    assert not (tmp_path / "state.lock").exists()                      # the lock is released


def test_nothing_is_read_when_every_store_is_fresh(tmp_path):
    db = FakeDb({"a": ages(ok=HOUR), "b": ages(ok=2 * DAY)})
    harvester = FakeHarvester()
    result = run(db, harvester, [Store("a"), Store("b")], tmp_path, budget_s=300)
    assert harvester.calls == [] and result["reason"] == "nothing_due" and db.finished == []


def test_a_refresh_that_reads_nothing_keeps_the_last_real_refreshs_progress(tmp_path):
    index_refresh._write_json(tmp_path / "state.json", {"state": "idle", "ended": "done", "finished_at": 5,
                                                         "results": [{"store": "lulu"}]})
    db = FakeDb({"a": ages(ok=HOUR)})
    run(db, FakeHarvester(), [Store("a")], tmp_path, budget_s=300)                     # the worker's run: nothing due
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["results"] == [{"store": "lulu"}]
    run(db, FakeHarvester(), [Store("a")], tmp_path, budget_s=300, trigger="dashboard")   # the button waits for an answer
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))["ended"] == "nothing_due"


def test_a_button_job_that_cannot_read_the_database_says_it_is_over(tmp_path):
    class Down(FakeDb):
        def harvest_ages(self):
            raise OSError("database is down")

    index_refresh._write_json(tmp_path / "state.json", {"state": "starting", "updated_at": time.time()})
    run(Down(), FakeHarvester(), [Store("a")], tmp_path, budget_s=300, trigger="dashboard")
    state = index_refresh.status(**paths(tmp_path))
    assert state["running"] is False and state["ended"] == "unavailable"


def test_the_time_budget_stops_the_refresh_between_stores(tmp_path):
    clock = Clock()
    db = FakeDb()
    harvester = FakeHarvester(clock=clock, cost=200.0)                 # each store takes 200 fake seconds
    result = run(db, harvester, [Store("a"), Store("b"), Store("c")], tmp_path, clock=clock, budget_s=300)
    assert harvester.calls == ["a", "b"]                               # c would start after the 300 s budget
    assert result["reason"] == "budget"
    assert {"store": "c", "reason": "budget"} in result["skipped"]
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["ended"] == "budget"


def test_the_time_budget_also_stops_the_store_being_read_and_that_harvest_is_recorded_partial(tmp_path):
    clock = Clock()
    db = FakeDb()
    harvester = FakeHarvester(clock=clock, cost=100.0, sitemaps_per_store=14)     # 14 sitemap files of 100 s
    result = run(db, harvester, [Store("big"), Store("later")], tmp_path, clock=clock, budget_s=300)
    assert harvester.calls == ["big"]                                  # the budget was spent inside the first store
    (key, report), = db.finished
    assert key == "big" and report["status"] == "partial" and report["truncated"] is True
    assert report["sitemaps_read"] == 3 and "time budget" in report["error"]
    assert result["reason"] == "budget" and {"store": "later", "reason": "budget"} in result["skipped"]


def test_a_real_harvester_stops_at_the_budget_before_the_next_sitemap_file():
    """should_stop is asked before every sitemap file (catalog_match.sitemaps)."""
    from test_cm_sitemaps import BASE, PRODUCT_1, FakeHttp, full_store, index_of, lulu, urlset

    pages = full_store()
    pages[BASE + "/sitemaps/index.xml"] = (200, index_of(BASE + "/sitemaps/p1.xml", BASE + "/sitemaps/p2.xml"))
    pages[BASE + "/sitemaps/p1.xml"] = (200, urlset(PRODUCT_1))
    pages[BASE + "/sitemaps/p2.xml"] = (200, urlset(PRODUCT_1))
    http = FakeHttp(pages)
    harvester = sitemaps.SitemapHarvester(http=http, sleep=lambda s: None, clock=lambda: 0.0)
    asked = []

    def stop():
        asked.append(len(http.calls))
        return len(asked) > 2            # the index and the first file are read; the second file is not

    rep = harvester.harvest(lulu(), should_stop=stop)
    assert rep.status == "partial" and rep.truncated and "time budget" in rep.error
    assert BASE + "/sitemaps/p2.xml" not in http.calls and BASE + "/sitemaps/p1.xml" in http.calls
    assert rep.product_urls == 1


def test_a_failing_store_is_that_stores_error_and_the_next_store_is_still_read(tmp_path):
    class Boom(FakeHarvester):
        def harvest(self, store, **kw):
            if store.key == "a":
                raise RuntimeError("boom")
            return super().harvest(store, **kw)

    db = FakeDb()
    result = run(db, Boom(), [Store("a"), Store("b")], tmp_path, budget_s=300)
    assert [(r["store"], r["status"]) for r in result["results"]] == [("a", "error"), ("b", "ok")]
    assert dict(db.finished)["a"]["status"] == "error" and "boom" in dict(db.finished)["a"]["error"]


def test_a_database_that_cannot_be_read_stops_the_refresh_quietly(tmp_path):
    class Down(FakeDb):
        def harvest_ages(self):
            raise OSError("database is down")

    harvester = FakeHarvester()
    result = run(Down(), harvester, [Store("a")], tmp_path, budget_s=300)
    assert result["reason"] == "unavailable" and harvester.calls == []
    assert not (tmp_path / "state.lock").exists()


def test_a_blocked_store_is_skipped_for_seven_days(tmp_path):
    db = FakeDb()
    harvester = FakeHarvester(statuses={"shop": "blocked"})
    first = run(db, harvester, [Store("shop"), Store("other")], tmp_path, budget_s=300)
    assert [(r["store"], r["status"]) for r in first["results"]] == [("shop", "blocked"), ("other", "ok")]
    assert dict(db.finished)["shop"]["status"] == "blocked"            # recorded in catalog_harvests like the script
    # the next run (a day later): the store that refused is not asked; the other one is fresh
    for key in db.ages:
        db.ages[key] = dict(db.ages[key], ok_age_s=DAY if key == "other" else None, last_age_s=DAY)
    harvester.calls.clear()
    second = run(db, harvester, [Store("shop"), Store("other")], tmp_path, budget_s=300, refresh_days=7)
    assert harvester.calls == [] and {"store": "shop", "reason": BLOCKED} in second["skipped"]
    # even a forced refresh leaves it alone; eight days later it is asked again
    forced = run(db, harvester, [Store("shop")], tmp_path, budget_s=300, force=True)
    assert "shop" not in harvester.calls
    assert {"store": "shop", "reason": BLOCKED} in forced["skipped"]
    db.ages["shop"]["last_age_s"] = 8 * DAY
    run(db, harvester, [Store("shop")], tmp_path, budget_s=300)
    assert harvester.calls == ["shop"]


def test_a_refresh_does_not_start_when_the_index_or_the_budget_is_off(tmp_path, monkeypatch):
    import config
    harvester = FakeHarvester()
    assert run(FakeDb(), harvester, [Store("a")], tmp_path, budget_s=0)["reason"] == "off"
    monkeypatch.setattr(config, "LOCAL_INDEX_ENABLED", False, raising=False)
    assert run(FakeDb(), harvester, [Store("a")], tmp_path)["reason"] == "off"
    assert harvester.calls == [] and not (tmp_path / "state.json").exists()


def test_only_one_refresh_runs_at_a_time_and_a_dead_ones_lock_is_taken_over(tmp_path):
    lock = tmp_path / "state.lock"
    lock.write_text(json.dumps({"pid": 1, "budget_s": 300}), encoding="utf-8")
    harvester = FakeHarvester()
    busy = run(FakeDb(), harvester, [Store("a")], tmp_path, budget_s=300)
    assert busy["started"] is False and busy["reason"] == "running" and harvester.calls == []
    old = time.time() - 300 - index_refresh.LIVE_MARGIN_S - 5            # nobody touched it for longer than its budget
    os.utime(lock, (old, old))
    assert run(FakeDb(), harvester, [Store("a")], tmp_path, budget_s=300)["reason"] == "done"
    assert harvester.calls == ["a"] and not lock.exists()


# ---------------------------------------------------------------------------
# The background thread never blocks the caller
# ---------------------------------------------------------------------------

def test_the_refresh_thread_never_blocks_the_worker(tmp_path):
    db = FakeDb(slow_ages_s=0.4)                                       # even the database read is in the thread
    harvester = FakeHarvester(sleep_s=0.6)                             # and each store takes a while
    began = time.monotonic()
    thread = index_refresh.start_background("worker", db=db, harvester=harvester, stores=[Store("a"), Store("b")],
                                            budget_s=30, **paths(tmp_path))
    returned = time.monotonic() - began
    assert isinstance(thread, threading.Thread) and thread.daemon       # a daemon: it never holds the process open
    assert returned < 0.2, f"start_background took {returned:.2f}s"
    assert harvester.calls == [] and db.finished == []                  # nothing is done yet: it runs on its own
    assert index_refresh.start_background("worker", db=db, harvester=harvester, stores=[Store("a")],
                                          budget_s=30, **paths(tmp_path)) is None     # one per process
    thread.join(10)
    assert not thread.is_alive() and harvester.calls == ["a", "b"] and len(db.finished) == 2


def test_the_thread_is_not_started_when_the_switches_are_off(tmp_path, monkeypatch):
    import config
    kw = dict(db=FakeDb(), harvester=FakeHarvester(), stores=[Store("a")], **paths(tmp_path))
    assert index_refresh.start_background("worker", budget_s=0, **kw) is None
    monkeypatch.setattr(config, "LOCAL_INDEX_REFRESH_MAX_S", "0", raising=False)
    assert index_refresh.start_background("worker", **kw) is None        # LOCAL_INDEX_REFRESH_MAX_S=0
    monkeypatch.setattr(config, "LOCAL_INDEX_REFRESH_MAX_S", "300", raising=False)
    monkeypatch.setattr(config, "LOCAL_INDEX_ENABLED", False, raising=False)
    assert index_refresh.start_background("worker", **kw) is None        # LOCAL_INDEX_ENABLED off
    assert not (tmp_path / "state.json").exists()


def test_a_thread_that_fails_never_raises_into_the_caller(tmp_path):
    class Down(FakeDb):
        def harvest_ages(self):
            raise RuntimeError("boom")

    thread = index_refresh.start_background("nightly", db=Down(), harvester=FakeHarvester(), stores=[Store("a")],
                                            budget_s=30, **paths(tmp_path))
    thread.join(5)
    assert not thread.is_alive()


def test_the_worker_and_the_nightly_run_start_the_refresh_in_the_background(monkeypatch):
    import main
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import run_nightly

    started = []
    monkeypatch.setattr(index_refresh, "start_background", lambda trigger, **kw: started.append(trigger) or None)
    main._start_local_index_refresh("manual")
    main._start_local_index_refresh("nightly")
    run_nightly.start_index_refresh()
    assert started == ["worker", "nightly", "nightly"]

    def boom(trigger, **kw):
        raise RuntimeError("never raises into the run")

    monkeypatch.setattr(index_refresh, "start_background", boom)
    main._start_local_index_refresh("worker")
    assert run_nightly.start_index_refresh() is None
    # and both runs really call it: the worker before the brand-site harvest, the nightly run before its enqueue
    source = open(main.__file__, encoding="utf-8").read()
    assert source.index("_start_local_index_refresh(trigger)      #") < source.index("        _harvest_pending_brand_sites()\n")
    nightly = open(run_nightly.__file__, encoding="utf-8").read()
    assert "pin_nightly_settings(main, config)\n        start_index_refresh()\n" in nightly


# ---------------------------------------------------------------------------
# Progress and the dashboard button's detached job
# ---------------------------------------------------------------------------

def test_the_status_says_running_while_the_lock_is_alive_and_idle_after(tmp_path):
    class Watch(FakeHarvester):
        def harvest(self, store, **kw):
            self.seen = index_refresh.status(**paths(tmp_path))
            return super().harvest(store, **kw)

    harvester = Watch()
    run(FakeDb(), harvester, [Store("a")], tmp_path, budget_s=300)
    assert harvester.seen["running"] is True and harvester.seen["current"] == "a" and harvester.seen["state"] == "running"
    after = index_refresh.status(**paths(tmp_path))
    assert after["running"] is False and after["ended"] == "done" and [r["store"] for r in after["results"]] == ["a"]


def test_the_button_job_is_a_detached_process_that_answers_at_once(tmp_path):
    spawned = []

    class Popen:
        def __init__(self, command, **kw):
            spawned.append((command, kw))

    result = index_refresh.start_detached(python="py", popen=Popen, log_path=tmp_path / "job.log", **paths(tmp_path))
    assert result == {"started": True, "running": True, "reason": "started"}
    (command, kw), = spawned
    assert command[0] == "py" and command[1].replace("\\", "/").endswith("scripts/build_catalog_index.py")
    assert command[2:] == ["--refresh", "--trigger", "dashboard", "--force"]
    assert kw["close_fds"] is True and kw["cwd"] == str(index_refresh.ROOT) and kw["stdin"] is not None
    assert kw.get("start_new_session") is True or "creationflags" in kw          # not tied to the bridge process
    state = index_refresh.status(**paths(tmp_path))
    assert state["state"] == "starting" and state["running"] is True            # the page shows progress at once
    # a second click while it runs spawns nothing
    again = index_refresh.start_detached(python="py", popen=Popen, log_path=tmp_path / "job.log", **paths(tmp_path))
    assert again == {"started": False, "running": True, "reason": "running"} and len(spawned) == 1


def test_a_job_that_never_took_the_lock_stops_counting_as_running_after_the_grace(tmp_path):
    index_refresh._write_json(tmp_path / "state.json", {"state": "starting", "updated_at": time.time() - 600})
    assert index_refresh.status(**paths(tmp_path))["running"] is False


def test_the_button_job_is_refused_when_the_index_is_off(tmp_path, monkeypatch):
    import config
    monkeypatch.setattr(config, "LOCAL_INDEX_ENABLED", False, raising=False)

    class Popen:
        def __init__(self, *a, **k):
            raise AssertionError("must not start")

    assert index_refresh.start_detached(popen=Popen, **paths(tmp_path))["reason"] == "disabled"


def test_the_bridge_action_starts_the_job_and_returns_at_once(monkeypatch):
    import cli_bridge

    monkeypatch.setattr(index_refresh, "start_detached", lambda trigger, force: {"started": True, "running": True,
                                                                                 "reason": "started"})
    out = cli_bridge.ACTIONS["local_index_refresh"]({})
    assert out["status"] == "success" and out["started"] is True and "بلّش تحديث الفهرس" in out["message_ar"]
    monkeypatch.setattr(index_refresh, "start_detached", lambda trigger, force: {"started": False, "running": True,
                                                                                 "reason": "running"})
    assert cli_bridge.ACTIONS["local_index_refresh"]({})["running"] is True


def test_the_script_refresh_mode_runs_the_same_refresh(monkeypatch, capsys):
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import build_catalog_index

    seen = {}
    monkeypatch.setattr(build_catalog_index, "_db_store", lambda: (FakeDb(), ""))
    monkeypatch.setattr(index_refresh, "refresh", lambda **kw: seen.update(kw) or {
        "started": True, "reason": "done", "elapsed_s": 1, "skipped": [],
        "results": [{"store": "lulu", "status": "ok", "product_urls": 3, "new_urls": 3, "error": ""}]})
    assert build_catalog_index.main(["--refresh", "--force", "--trigger", "dashboard"]) == 0
    assert seen["force"] is True and seen["trigger"] == "dashboard"
    assert "lulu" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The stores file and the settings
# ---------------------------------------------------------------------------

def test_the_stores_file_validates():
    stores = load_stores()                                                  # every pattern compiles
    keys = [s.key for s in stores]
    assert len(keys) == len(set(keys)) and all(0 < len(k) <= 32 for k in keys)   # catalog_products.store is VARCHAR(32)
    for s in stores:
        assert s.base_url.startswith("https://") and s.hosts and s.base_url.split("//")[1] in s.hosts
        assert all(h == h.lower() for h in s.hosts) and s.product_path.pattern.startswith("^")
        assert all(u.startswith("https://") for u in s.sitemaps)
        assert s.enabled or s.note                                            # a disabled store says why
    with open(sitemaps.STORES_PATH, encoding="utf-8") as fh:
        assert set(json.load(fh)) == {"_about", "stores"}


def test_sharjah_coop_is_enabled_with_the_product_pattern_discover_found():
    store = {s.key: s for s in load_stores()}["sharjahcoop"]
    assert store.enabled and store.hosts == ("www.sharjahcoop.ae", "sharjahcoop.ae")
    assert store.sitemaps == ("https://www.sharjahcoop.ae/en/sitemap.xml",)
    for url in ("https://www.sharjahcoop.ae/en/dahabi-slicesd-bread-brown-700g/p/9501100306746",
                "https://www.sharjahcoop.ae/en/olive-whole-green-spain---250g/p/D000000000002"):
        assert store.is_product(url)
    for url in ("https://www.sharjahcoop.ae/en", "https://www.sharjahcoop.ae/ar/x/p/123",
                "https://www.sharjahcoop.ae/en/some-category/c/12", "https://www.sharjahcoop.ae/en/cart"):
        assert not store.is_product(url)
    # only the English product sitemaps are read (the Arabic ones repeat the same products)
    inc = store.include
    assert inc.search("https://www.sharjahcoop.ae/sitemaps/Product-en-AED-13.xml")
    for other in ("Product-ar-AED-0.xml", "Category-en-AED-0.xml", "Homepage-en-AED.xml", "Store-en-AED.xml"):
        assert not inc.search("https://www.sharjahcoop.ae/sitemaps/" + other)


def test_the_refresh_settings_default_and_clamp(monkeypatch):
    import config
    assert (settings.local_index_refresh_days(), settings.local_index_refresh_max_s()) == (7, 300)
    for value, days, seconds in (("3", 3, 3), ("0", 1, 0), ("nonsense", 7, 300), ("999999", 365, 3600)):
        monkeypatch.setattr(config, "LOCAL_INDEX_REFRESH_DAYS", value, raising=False)
        monkeypatch.setattr(config, "LOCAL_INDEX_REFRESH_MAX_S", value, raising=False)
        assert (settings.local_index_refresh_days(), settings.local_index_refresh_max_s()) == (days, seconds)


# ---------------------------------------------------------------------------
# The two database queries (MariaDB test database)
# ---------------------------------------------------------------------------

def _sql(db, statement, params=()):
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(statement, params)
        rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def test_harvest_ages_come_from_the_harvest_log_in_the_databases_own_clock(mariadb_or_skip):
    db = mariadb_or_skip
    _sql(db, "DELETE FROM catalog_harvests")
    try:
        for store, ago, status in (("lulu", "10 DAY", "ok"), ("lulu", "2 DAY", "error"), ("spinneys", "1 DAY", "blocked"),
                                   ("carrefour_uae", "3 HOUR", "partial"), ("talabat_mart_uae", "4 DAY", "empty")):
            _sql(db, "INSERT INTO catalog_harvests (store, started_at, finished_at, status) "
                     f"VALUES (%s, NOW() - INTERVAL {ago}, NOW() - INTERVAL {ago}, %s)", (store, status))
        got = DbCatalogStore().harvest_ages()
        assert got["lulu"]["ok_age_s"] // DAY == 10 and got["lulu"]["last_status"] == "error" and got["lulu"]["last_age_s"] // DAY == 2
        assert got["spinneys"]["ok_age_s"] is None and got["spinneys"]["last_status"] == "blocked"
        assert got["carrefour_uae"]["ok_age_s"] is None and got["carrefour_uae"]["last_status"] == "partial"
        assert got["talabat_mart_uae"]["ok_age_s"] // DAY == 4                 # an empty harvest read the sitemaps to the end
        assert decide(got["spinneys"], 7) == (False, BLOCKED)
        assert decide(got["lulu"], 7) == (True, STALE) and decide(got["talabat_mart_uae"], 7) == (False, FRESH)
    finally:
        _sql(db, "DELETE FROM catalog_harvests")


def test_the_run_counts_the_products_the_local_index_answered(mariadb_or_skip):
    db = mariadb_or_skip
    run_id = "idx-answers-test"

    def trace(*health):
        return json.dumps({"outcome": {"provider_health": [{"provider": p, "status": s} for p, s in health]}})

    rows = [(trace(("serper", "ok"), ("local_index", "ok")), run_id),             # the index answered
            (trace(("local_index", "ok"), ("local_index", "empty")), run_id),     # asked twice, answered once: one product
            (trace(("serper", "ok"), ("local_index", "empty")), run_id),          # asked, nothing found
            (trace(("serper", "ok")), run_id),                                    # not asked
            (trace(("local_index", "ok")), "another-run")]                        # another run
    try:
        for i, (trace_json, rid) in enumerate(rows):
            _sql(db, "INSERT INTO automation_queue (`row_number`, product_name, brand, search_query, status, trace_json, "
                     "sku_key, run_id) VALUES (%s, 'x', 'y', 'q', 'completed', %s, %s, %s)",
                 (990100 + i, trace_json, f"idx-answers-{i}", rid))
        assert db.run_local_index_answers(run_ids=[run_id]) == {"answered": 2, "asked": 3}
        assert db.run_local_index_answers() == {"answered": 0, "asked": 0}
    finally:
        _sql(db, "DELETE FROM automation_queue WHERE sku_key LIKE 'idx-answers-%%'")


def test_the_run_report_carries_the_index_answers():
    import run_report

    class Db:
        @staticmethod
        def run_outcome_counts(**kw):
            return {"enqueued": 58, "searched": 58, "auto_published": 0, "ready_for_review": 58, "not_found": 0,
                    "failed": 0, "provider_down": 0, "pending_left": 0}

        @staticmethod
        def run_local_index_answers(**kw):
            return {"answered": 14, "asked": 40}

        @staticmethod
        def db_available():
            return True

    report = run_report.build_report("nightly", [{"run_id": "r1", "worker_id": "w1"}], 1000.0, 1100.0, db=Db(),
                                     sheets=None)
    assert report["local_index"] == {"answered": 14, "asked": 40}
    del Db.run_local_index_answers                       # an older database module: the report is still written
    assert run_report.build_report("nightly", [{"run_id": "r1"}], 1000.0, 1100.0, db=Db())["local_index"] is None


# ---------------------------------------------------------------------------
# robots.txt Visit-time: outside the window the store is not read, and nothing is penalised
# ---------------------------------------------------------------------------

from datetime import datetime, timezone  # noqa: E402

from catalog_match.sitemaps import parse_robots, parse_visit_time  # noqa: E402


def utc(hour, minute=0):
    return datetime(2026, 10, 5, hour, minute, tzinfo=timezone.utc)


def robots_with(visit=None):
    lines = ["User-agent: *", "Disallow: /private/", "Crawl-delay: 2"]
    if visit:
        lines.append(f"Visit-time: {visit}")
    lines.append("Sitemap: https://gcc.luluhypermarket.com/sitemaps/index.xml")
    return "\n".join(lines) + "\n"


def test_visit_time_is_parsed_from_the_group_that_applies():
    assert parse_robots(robots_with("0400-0845")).visit_times == [(240, 525)]
    assert parse_robots(robots_with("04:00-08:45")).visit_times == [(240, 525)]
    assert parse_robots(robots_with("0000-2400")).visit_times == [(0, 1440)]
    for bad in ("soon", "2500-0100", "0460-0500", "0400", ""):
        assert parse_robots(robots_with(bad)).visit_times == [] and parse_visit_time(bad) is None
    named = ("User-agent: ProductImageAutomationPipeline\nVisit-time: 1000-1100\n\n"
             "User-agent: *\nVisit-time: 0400-0845\n")
    assert parse_robots(named).visit_times == [(600, 660)]             # the group naming us wins, as for the rules
    assert parse_robots("User-agent: Googlebot\nVisit-time: 0100-0200\n\nUser-agent: *\n").visit_times == []
    assert parse_robots(robots_with()).visit_times == []


@pytest.mark.parametrize("hour,minute,inside", [(4, 0, True), (5, 30, True), (8, 44, True), (8, 45, False),
                                                (3, 59, False), (12, 0, False), (22, 40, False), (0, 0, False)])
def test_inside_and_outside_a_visit_window(hour, minute, inside):
    assert parse_robots(robots_with("0400-0845")).visit_allowed(utc(hour, minute)) is inside


@pytest.mark.parametrize("hour,minute,inside", [(22, 0, True), (23, 0, True), (0, 30, True), (1, 59, True),
                                                (2, 0, False), (12, 0, False), (21, 59, False)])
def test_a_visit_window_that_crosses_midnight(hour, minute, inside):
    assert parse_robots(robots_with("2200-0200")).visit_allowed(utc(hour, minute)) is inside


def test_a_robots_file_without_visit_time_allows_every_hour_and_other_zones_are_converted():
    robots = parse_robots(robots_with())
    assert all(robots.visit_allowed(utc(h)) for h in range(24))
    from datetime import timedelta
    dubai = timezone(timedelta(hours=4))
    window = parse_robots(robots_with("0400-0845"))
    assert window.visit_allowed(datetime(2026, 10, 5, 9, 0, tzinfo=dubai)) is True       # 05:00 UTC
    assert window.visit_allowed(datetime(2026, 10, 5, 5, 0, tzinfo=dubai)) is False      # 01:00 UTC


def visit_harvester(visit, now):
    from test_cm_sitemaps import BASE, FakeHttp, full_store
    pages = full_store()
    pages[BASE + "/robots.txt"] = (200, robots_with(visit).encode())
    http = FakeHttp(pages)
    return http, sitemaps.SitemapHarvester(http=http, sleep=lambda s: None, clock=lambda: 0.0, utc_now=lambda: now)


def test_the_harvester_reads_nothing_but_robots_txt_outside_the_window():
    from test_cm_sitemaps import BASE, lulu
    http, harvester = visit_harvester("0400-0845", utc(22, 40))
    rep = harvester.harvest(lulu())
    assert rep.status == "outside_visit_time" and "04:00-08:45 UTC" in rep.error
    assert http.calls == [BASE + "/robots.txt"] and rep.sitemaps_read == 0 and rep.visit_window == [[240, 525]]
    assert "OUTSIDE_VISIT_TIME" in sitemaps.format_report(rep)


def test_the_harvester_reads_the_store_inside_the_window_and_when_robots_has_no_visit_time():
    from test_cm_sitemaps import lulu
    for visit, now in (("0400-0845", utc(5)), ("2200-0200", utc(23)), (None, utc(22, 40))):
        _, harvester = visit_harvester(visit, now)
        rep = harvester.harvest(lulu())
        assert rep.status == "ok" and rep.product_urls == 2, (visit, rep.status)
        assert rep.visit_window == ([[240, 525]] if visit == "0400-0845" else [[1320, 120]] if visit else [])


def test_a_store_outside_its_visit_window_is_skipped_unpenalised_and_read_at_the_next_refresh(tmp_path):
    from test_cm_sitemaps import lulu
    clock = {"now": utc(22, 40)}
    http, harvester = visit_harvester("0400-0845", utc(22, 40))
    harvester._utc_now = lambda: clock["now"]
    db = FakeDb()
    robots_path = tmp_path / "robots.json"
    first = index_refresh.refresh(db=db, harvester=harvester, stores=[lulu()], budget_s=300, clock=Clock(),
                                  robots_path=robots_path, **paths(tmp_path))
    assert [(r["store"], r["status"]) for r in first["results"]] == [("lulu", "outside_visit_time")]
    assert first["reason"] == "done" and db.finished == [] and db.rows == []       # not blocked, not failed: no row at all
    assert json.loads(robots_path.read_text(encoding="utf-8"))["lulu"]["visit_time"] == [[240, 525]]
    # nothing was recorded, so the store is still due (never harvested): no retry wait, no block
    assert decide(db.harvest_ages().get("lulu"), 7) == (True, NEVER)
    # the button follows the same rule
    forced = index_refresh.refresh(db=db, harvester=harvester, stores=[lulu()], budget_s=300, clock=Clock(), force=True,
                                   trigger="dashboard", robots_path=robots_path, **paths(tmp_path))
    assert forced["results"][0]["status"] == "outside_visit_time" and db.finished == []
    # inside the window the next refresh reads it
    clock["now"] = utc(6, 15)
    later = index_refresh.refresh(db=db, harvester=harvester, stores=[lulu()], budget_s=300, clock=Clock(),
                                  robots_path=robots_path, **paths(tmp_path))
    assert [(r["store"], r["status"]) for r in later["results"]] == [("lulu", "ok")]
    assert [k for k, _ in db.finished] == ["lulu"] and len(db.rows) == 2


def test_a_store_without_a_visit_time_clears_the_remembered_window(tmp_path):
    robots_path = tmp_path / "robots.json"
    index_refresh.remember_visit_windows("lulu", [[240, 525]], robots_path)
    index_refresh.remember_visit_windows("lulu", None, robots_path)                  # robots.txt not read: the window stays
    assert json.loads(robots_path.read_text(encoding="utf-8"))["lulu"]["visit_time"] == [[240, 525]]
    index_refresh.remember_visit_windows("lulu", [], robots_path)                    # read, no Visit-time any more
    assert json.loads(robots_path.read_text(encoding="utf-8"))["lulu"]["visit_time"] == []


def test_the_script_says_a_store_outside_its_window_is_not_a_failure(monkeypatch, capsys):
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import build_catalog_index
    from test_cm_sitemaps import lulu

    class Args:
        prune, discover, max_urls, max_sitemaps = False, False, None, None

    _, harvester = visit_harvester("0400-0845", utc(22, 40))
    db = FakeDb()
    rep = build_catalog_index._harvest_one(harvester, lulu(), db, Args())
    assert rep.status == "outside_visit_time" and db.finished == []                  # nothing recorded
    assert "OUTSIDE_VISIT_TIME" in capsys.readouterr().out
