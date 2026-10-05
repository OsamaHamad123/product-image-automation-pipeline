"""WORKER_CONCURRENCY (WP speed, change 2): how many products the worker searches at once.

* catalog_match.settings.worker_concurrency(): default 5, clamped to 1..8, a bad value is the default;
* config.load_db_config's loader takes system_settings.worker_concurrency (clamped), a bad value changes nothing;
* main.run_worker_mode runs exactly that many searches in parallel (it was a hard-coded 3);
* every provider draws from ONE process-wide token bucket per name, so more workers cannot exceed its limit;
* the dashboard saves the field (1..8, server side) and shows it on the «متقدم» tab.
"""

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from catalog_match import ratelimit, settings
from catalog_match.providers.serper import SerperImagesProvider
from catalog_match.providers.serper_web import SerperWebProvider
from test_laqta_health import _kernel, _put, _settings, app_env  # noqa: F401  (the PHP kernel and its fixture)
from test_worker_lock import _worker_env, main_mod  # noqa: F401  (the worker harness and its fixture)


@pytest.fixture
def plain_settings(monkeypatch):
    monkeypatch.setattr(settings, "_config", None)
    monkeypatch.delenv("WORKER_CONCURRENCY", raising=False)


@pytest.mark.parametrize("value, expected", [
    (None, 5), ("5", 5), ("1", 1), ("8", 8), ("3", 3), ("0", 1), ("-4", 1), ("9", 8), ("100", 8),
    ("abc", 5), ("", 5), ("2.5", 5), (" 6 ", 6),
])
def test_the_setting_is_clamped_to_one_through_eight_and_defaults_to_five(plain_settings, monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("WORKER_CONCURRENCY", value)
    assert settings.worker_concurrency() == expected


def test_the_dashboard_value_wins_over_the_environment(monkeypatch):
    import config

    monkeypatch.setattr(settings, "_config", config)
    monkeypatch.setenv("WORKER_CONCURRENCY", "2")
    monkeypatch.setattr(config, "WORKER_CONCURRENCY", "7", raising=False)
    assert settings.worker_concurrency() == 7


@pytest.mark.parametrize("stored, expected", [("6", "6"), ("12", "8"), ("0", "1"), ("-3", "1"), (" 4 ", "4"),
                                              ("abc", "5"), ("", "5"), (None, "5")])
def test_the_database_loader_clamps_and_ignores_a_bad_value(monkeypatch, stored, expected):
    import config

    monkeypatch.setattr(config, "WORKER_CONCURRENCY", "5", raising=False)
    config._load_queue_settings({"worker_concurrency": stored})
    assert config.WORKER_CONCURRENCY == expected


def test_main_reads_the_concurrency_from_the_settings(main_mod, monkeypatch):
    import config

    monkeypatch.setattr(settings, "_config", config)
    monkeypatch.setattr(config, "WORKER_CONCURRENCY", "4", raising=False)
    assert main_mod._worker_concurrency() == 4
    monkeypatch.setattr(config, "WORKER_CONCURRENCY", "99", raising=False)
    assert main_mod._worker_concurrency() == 8


def _peak_of_parallel_rows(main_mod, monkeypatch, concurrency, rows=12):
    """run_worker_mode over `rows` tasks whose search takes a moment; the most searches seen at once."""
    import config
    import local_cache_db

    monkeypatch.setattr(settings, "_config", config)
    monkeypatch.setattr(config, "WORKER_CONCURRENCY", str(concurrency), raising=False)
    _worker_env(main_mod, monkeypatch, [{"stop_requested": 0, "pause_requested": 0, "run_id": "r1"}])
    queue = [{"row_number": i, "product_name": f"p{i}"} for i in range(1, rows + 1)]
    lock = threading.Lock()
    now = {"n": 0, "peak": 0, "done": 0}

    def search(task, worksheet, link_column_index, brand_mappings, report=None):
        with lock:
            now["n"] += 1
            now["peak"] = max(now["peak"], now["n"])
        time.sleep(0.15)
        with lock:
            now["n"] -= 1
            now["done"] += 1
        return "ok"

    def fetch_next(worker_id):
        with lock:
            return queue.pop(0) if queue else None

    monkeypatch.setattr(main_mod, "pre_cache_product_candidates", search)
    monkeypatch.setattr(local_cache_db, "fetch_next_task", fetch_next)
    monkeypatch.setattr(local_cache_db, "count_open_tasks", lambda: len(queue))
    monkeypatch.setattr(main_mod, "_refresh_state", lambda *a, **k: None)
    main_mod.run_worker_mode(report=False)
    assert now["done"] == rows
    return now["peak"]


@pytest.mark.parametrize("concurrency", [1, 3, 5, 8])
def test_the_worker_runs_exactly_as_many_searches_at_once_as_the_setting(main_mod, monkeypatch, concurrency):
    assert _peak_of_parallel_rows(main_mod, monkeypatch, concurrency) == concurrency


# ---------------------------------------------------------------------------
# Rate limiting is process-wide: five workers share one budget per provider
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_buckets():
    ratelimit.reset_registry()
    yield
    ratelimit.reset_registry()


def test_every_instance_and_thread_of_a_provider_shares_one_bucket(clean_buckets):
    """Each row builds its own provider objects (pipeline._default_providers); they must not each get a bucket."""
    first, second = SerperImagesProvider(api_key="k"), SerperImagesProvider(api_key="k")
    assert first.bucket() is second.bucket() is ratelimit.get_bucket("serper", 1, 1)
    seen = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for b in ex.map(lambda _: SerperWebProvider(api_key="k").bucket(), range(16)):
            seen.append(b)
    assert len({id(b) for b in seen}) == 1
    assert SerperWebProvider(api_key="k").bucket() is not first.bucket()          # one bucket per provider name


def test_eight_threads_cannot_draw_more_than_the_burst_and_the_refill():
    """The shared bucket hands out distinct, evenly spaced slots: 8 callers on a 120/min, burst 5 bucket wait for
    3 refills (0.5 s each) in total, never all at once."""
    clock = {"t": 0.0}
    slept = []
    bucket = ratelimit.TokenBucket(120.0, 5, clock=lambda: clock["t"], sleeper=slept.append, name="serper")
    waits = [bucket.acquire() for _ in range(8)]
    assert waits[:5] == [0.0] * 5
    assert waits[5:] == pytest.approx([0.5, 1.0, 1.5])
    assert slept == pytest.approx([0.5, 1.0, 1.5])


# ---------------------------------------------------------------------------
# The dashboard field: saved 1..8 server side, shown on the «متقدم» tab (tests/test_laqta_health.py's kernel)
# ---------------------------------------------------------------------------

def test_the_dashboard_saves_validates_and_shows_the_field(app_env):
    db, env = app_env["db"], app_env["env"]
    _put(db, {"worker_concurrency": "5"})
    out = _kernel(env, [
        ["GET", "/settings?tab=advanced", {}],
        ["POST", "/settings", {"section": "speed", "worker_concurrency": "7"}],
        ["GET", "/settings?tab=advanced", {}],
        ["POST", "/settings", {"section": "speed", "worker_concurrency": "9"}],
        ["POST", "/settings", {"section": "speed", "worker_concurrency": "0"}],
        ["POST", "/settings", {"section": "speed", "worker_concurrency": "abc"}],
        ["POST", "/settings", {"section": "speed", "worker_concurrency": "2.5"}],
        ["POST", "/settings", {"section": "speed", "worker_concurrency": ""}],
        ["POST", "/settings", {"section": "speed", "worker_concurrency": "1"}],
    ])
    before, saved, page, too_many, zero, text, decimal, absent, one = out
    body = before["body"]
    assert before["status"] == 200 and 'name="section" value="speed"' in body
    assert "كم منتج بيشتغل بنفس الوقت" in body and "بيستهلك رصيد البحث أسرع" in body
    assert re.search(r'name="worker_concurrency"[^>]*min="1"[^>]*max="8"[^>]*value="5"', body)
    assert saved["location"].endswith("?tab=advanced") and "انحفظ" in saved["flash"]["success"]
    assert re.search(r'name="worker_concurrency"[^>]*value="7"', page["body"])
    for refused in (too_many, zero, text, decimal):
        assert len(refused["flash"]["warnings"]) == 1 and "من 1 لـ 8" in refused["flash"]["warnings"][0], refused["flash"]
    assert not absent["flash"].get("warnings") and one["flash"]["success"]
    assert _settings(db)["worker_concurrency"] == "1"


def test_a_refused_value_keeps_the_saved_one(app_env):
    db, env = app_env["db"], app_env["env"]
    _put(db, {"worker_concurrency": "6"})
    _kernel(env, [["POST", "/settings", {"section": "speed", "worker_concurrency": "12"}],
                  ["POST", "/settings", {"section": "speed", "worker_concurrency": ""}]])
    assert _settings(db)["worker_concurrency"] == "6"
    page = _kernel(env, [["GET", "/settings?tab=advanced", {}]])[0]["body"]
    assert re.search(r'name="worker_concurrency"[^>]*value="6"', page)

