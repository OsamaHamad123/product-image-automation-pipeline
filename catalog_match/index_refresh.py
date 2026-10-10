"""Keeps the local catalog index fresh without the owner running anything.

Before this module the index was built by hand (scripts/build_catalog_index.py) and then never again. Now the
nightly run and every worker run start a refresh in a background thread (start_background): the stores whose newest
complete harvest is older than LOCAL_INDEX_REFRESH_DAYS (default 7), or that were never harvested, have their sitemaps
read again. The search never waits for it and uses whatever the index holds at that moment.

Rules (all of them are tested without a network, with a fake harvester):
  - One refresh at a time, across processes: temp/local_index_refresh.lock (its modification time is the heartbeat).
  - A time budget (LOCAL_INDEX_REFRESH_MAX_S, default 300 s per refresh): no store starts after it, and the store being
    read stops at its next sitemap file or batch of URLs (that harvest is recorded as 'partial').
  - Stores take turns (plan): never asked first, then the one asked longest ago, so a store the budget cut short goes
    to the back and one slow store never keeps the others waiting. A store that stops answering (3 sitemap files in a
    row without an answer) gives up its turn ('error').
  - A store bigger than one budget is read to the end over a few refreshes: the URL lists an unfinished harvest read
    are kept in temp/local_index_resume.json and the next one reads the rest (resume_from, at most
    LOCAL_INDEX_REFRESH_DAYS old: after that it starts again).
  - The refresh never holds a run up: a thread of the run's process. At that process's exit it is stopped cleanly and
    records what it read (a worker run waits at most EXIT_GRACE_S for that); the unattended nightly run waits for it
    within its own budget (_at_exit).
  - A store that answered BLOCKED (401 / 403 / 429 or a bot check) is left alone for 7 days; a store whose last
    harvest was partial or failed is retried after 6 hours, not at every run.
  - Every harvest is recorded in catalog_harvests exactly as scripts/build_catalog_index.py records it, so the
    dashboard's «فهرس المتاجر المحلي» card and the script read the same log.
  - robots.txt, Crawl-delay and Visit-time are kept by the harvester (catalog_match.sitemaps); nothing is written to
    the sheet or to Cloudinary. A store read outside its Visit-time window is skipped with status 'outside_visit_time':
    not recorded in catalog_harvests (it is neither blocked nor failed, so nothing waits and nothing is penalised) and
    asked again at the next refresh, the button's included. The windows seen are kept in temp/local_index_robots.json
    for the dashboard card. Sharjah Co-op has a window of our own (sitemaps.CRAWL_WINDOWS, 04:00-08:45 UTC, setting
    SHARJAHCOOP_CRAWL_WINDOW): outside it the store is skipped the same way without any request, and a harvest still
    running when it ends stops there (partial: the next refresh in the window goes on from there).
  - LOCAL_INDEX_ENABLED off, or LOCAL_INDEX_REFRESH_MAX_S = 0: nothing starts.

The dashboard's «حدّث الفهرس هلق» button runs the same refresh as a detached job (start_detached, through the bridge
action local_index_refresh): it ignores freshness but not a store's BLOCKED answer. temp/local_index_refresh.json holds
the progress the card shows on reload.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from . import settings

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "temp" / "local_index_refresh.json"
LOCK_PATH = ROOT / "temp" / "local_index_refresh.lock"
LOG_PATH = ROOT / "temp" / "local_index_refresh.log"
ROBOTS_PATH = ROOT / "temp" / "local_index_robots.json"      # {store: {visit_time: [[start, end], ...], checked_at}}
RESUME_PATH = ROOT / "temp" / "local_index_resume.json"      # {store: {read: [URL lists read], since}}: unfinished
OUTSIDE_VISIT_TIME = "outside_visit_time"
EXIT_GRACE_S = 10.0                   # at a worker's exit, the refresh still running gets this long to stop and record

BLOCKED_SKIP_S = 7 * 24 * 3600        # a store that answered BLOCKED is not asked again for this long
RETRY_AFTER_S = 6 * 3600              # a partial or failed harvest is retried after this long, not at every run
COMPLETE_STATUSES = ("ok", "empty")   # a harvest that read the store's sitemaps to the end
LIVE_MARGIN_S = 180                   # a lock not touched for budget + this is a crashed refresh's
STARTING_GRACE_S = 90                 # the button's job has this long to take the lock
MAX_URLS = 300_000                    # per store and refresh
MAX_SITEMAPS = 400

# why a store is (not) refreshed
NEVER, STALE, FORCED, FRESH, BLOCKED, RETRY_LATER = "never", "stale", "forced", "fresh", "blocked", "retry_later"


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

def decide(ages: Optional[Mapping[str, Any]], refresh_days: int, force: bool = False) -> Tuple[bool, str]:
    """(refresh?, why) for one store from its harvest log entry (DbCatalogStore.harvest_ages; None = never harvested).

    blocked within BLOCKED_SKIP_S: no, whatever else (not even for force: a store that refused is not pushed);
    force: yes; never harvested to the end: yes; newest complete harvest older than refresh_days: yes; else no.
    Except that a store whose newest harvest of any kind is younger than RETRY_AFTER_S waits (retry_later)."""
    ages = ages or {}
    last_age, last_status = ages.get("last_age_s"), str(ages.get("last_status") or "")
    ok_age = ages.get("ok_age_s")
    if last_status == "blocked" and last_age is not None and last_age < BLOCKED_SKIP_S:
        return False, BLOCKED
    if force:
        return True, FORCED
    if ok_age is not None and ok_age < max(1, int(refresh_days)) * 86400:
        return False, FRESH
    why = STALE if ok_age is not None else NEVER
    if last_age is not None and last_age < RETRY_AFTER_S:
        return False, RETRY_LATER
    return True, why


def plan(stores: Sequence[Any], ages: Mapping[str, Mapping[str, Any]], refresh_days: int, force: bool = False
         ) -> Tuple[List[Tuple[Any, str]], List[Tuple[Any, str]]]:
    """(to refresh, left alone), each a list of (StoreConfig, why). The stores never asked come first, then the one
    asked longest ago, whatever it answered: a store the budget cut short (or that keeps failing) goes to the back,
    so one slow store never keeps the others waiting, and the stores a refresh did not reach go first next time."""
    todo, skipped = [], []
    for store in stores:
        refresh, why = decide(ages.get(store.key), refresh_days, force)
        (todo if refresh else skipped).append((store, why))

    def asked_longest_ago(item):
        last_age = (ages.get(item[0].key) or {}).get("last_age_s")
        return (0, 0) if last_age is None else (1, -int(last_age))

    todo.sort(key=asked_longest_ago)         # stable: the stores file's order breaks ties
    return todo, skipped


# ---------------------------------------------------------------------------
# One refresh at a time (a lock file) and its progress (a state file the dashboard reads)
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.info("index refresh: the state could not be written (%s)", type(exc).__name__)


def lock_is_live(lock_path: Path = LOCK_PATH, now: Callable[[], float] = time.time) -> bool:
    """A refresh holds the lock and touched it recently (each store and each sitemap file touches it)."""
    try:
        age = now() - lock_path.stat().st_mtime
    except OSError:
        return False
    info = _read_json(lock_path) or {}
    try:
        budget = float(info.get("budget_s") or settings.DEFAULTS["LOCAL_INDEX_REFRESH_MAX_S"])
    except (TypeError, ValueError):
        budget = float(settings.DEFAULTS["LOCAL_INDEX_REFRESH_MAX_S"])
    return age < budget + LIVE_MARGIN_S


def _acquire(lock_path: Path, budget_s: float, now: Callable[[], float] = time.time) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if lock_is_live(lock_path, now):
                return False
            try:
                lock_path.unlink()          # a crashed refresh's lock
            except OSError:
                return False
            continue
        except OSError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "budget_s": budget_s, "started_at": now()}))
        return True
    return False


def _release(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except OSError:
        pass


def _touch(lock_path: Path) -> None:
    try:
        os.utime(str(lock_path), None)
    except OSError:
        pass


def read_state(state_path: Path = STATE_PATH) -> Optional[Dict[str, Any]]:
    return _read_json(state_path)


def record_end(reason: str, state_path: Path = STATE_PATH, now: Callable[[], float] = time.time) -> None:
    """The progress file says the job is over (it could not start): the dashboard stops waiting for it."""
    _write_json(state_path, {"state": "idle", "finished_at": now(), "ended": reason, "results": []})


def status(state_path: Path = STATE_PATH, lock_path: Path = LOCK_PATH, now: Callable[[], float] = time.time
           ) -> Dict[str, Any]:
    """{running, state, current, done, total, results, finished_at, ...}: what the dashboard shows."""
    state = read_state(state_path) or {}
    running = lock_is_live(lock_path, now)
    if not running and state.get("state") == "starting":
        running = now() - float(state.get("updated_at") or 0) < STARTING_GRACE_S
    out = dict(state)
    out["running"] = bool(running)
    out.setdefault("results", [])
    return out


# ---------------------------------------------------------------------------
# The refresh
# ---------------------------------------------------------------------------

def _enabled_stores() -> List[Any]:
    from . import sitemaps
    return sitemaps.enabled_stores(sitemaps.load_stores())


def _harvest_one(store: Any, db: Any, harvester: Any, should_stop: Callable[[], bool], skip: Sequence[str] = (),
                 ended: Callable[[], bool] = lambda: False) -> Dict[str, Any]:
    """One store's harvest and its catalog_harvests row (scripts/build_catalog_index.py does the same); an
    unexpected failure is that store's 'error' and the next store is still read. skip: the URL lists an unfinished
    refresh of the store already read (resume); ended(): the process is ending (the stop at its exit)."""
    from . import sitemaps

    started = None
    try:
        started = db.begin_harvest(store.key)
        resume = {"skip_urls": frozenset(skip)} if skip else {}     # a harvester without resume still works
        rep = harvester.harvest(store, on_urls=lambda batch, key=store.key: db.upsert(key, batch), max_urls=MAX_URLS,
                                max_sitemaps=MAX_SITEMAPS, should_stop=should_stop, **resume)
        if rep.truncated and ended():
            rep.error = "stopped before the end: the run ended (the next refresh goes on from there)"
    except Exception as exc:  # noqa: BLE001 - one store's failure must not stop the next one
        logger.warning("index refresh: %s failed (%s)", store.key, type(exc).__name__)
        rep = sitemaps.HarvestReport(store=store.key, status="error", error=f"{type(exc).__name__}: {exc}"[:240])
    if rep.status != OUTSIDE_VISIT_TIME:      # not a harvest: no row, so the store keeps its place in the queue
        try:
            db.finish_harvest(store.key, started, rep.as_dict())
        except Exception as exc:  # noqa: BLE001
            logger.warning("index refresh: the harvest of %s could not be recorded (%s)", store.key, type(exc).__name__)
            rep.status, rep.error = "error", f"the index could not be updated: {type(exc).__name__}"
    return {"store": store.key, "status": rep.status, "product_urls": int(rep.product_urls),
            "new_urls": int(rep.new_urls), "truncated": bool(rep.truncated), "error": str(rep.error or "")[:200],
            "visit_window": rep.visit_window,
            "lists_read": [n["url"] for n in rep.tree if n.get("kind") == "urlset" and not n.get("stopped")]}


def resume_from(store: str, refresh_days: int, resume_path: Path = RESUME_PATH,
                now: Callable[[], float] = time.time) -> List[str]:
    """The URL lists an unfinished refresh of the store read within the last refresh_days (none after that: the
    store is read from the start again, so a list read long ago is never left out of a complete harvest)."""
    entry = (_read_json(resume_path) or {}).get(store)
    if not isinstance(entry, dict):
        return []
    try:
        since = float(entry.get("since") or 0)
    except (TypeError, ValueError):
        return []
    if now() - since >= max(1, int(refresh_days)) * 86400:
        return []
    return [str(u) for u in entry.get("read") or [] if isinstance(u, str)]


def remember_resume(store: str, skip: Sequence[str], lists_read: Sequence[str], status: str,
                    resume_path: Path = RESUME_PATH, now: Callable[[], float] = time.time) -> None:
    """After a harvest: a complete one (ok / empty, with what earlier unfinished ones read) clears the store's resume;
    an unfinished one (the budget, the run's end, a failure on the way) adds the URL lists it read in full, so the
    next refresh reads only the rest. A store bigger than one budget is still read to the end over a few refreshes."""
    data = _read_json(resume_path) or {}
    if status in COMPLETE_STATUSES:
        if store in data:
            del data[store]
            _write_json(resume_path, data)
        return
    if status == OUTSIDE_VISIT_TIME or not lists_read:
        return
    entry = data.get(store) if skip else None              # no resume this time: a new one starts now
    since = (entry or {}).get("since") or now()
    data[store] = {"read": list(dict.fromkeys(list(skip) + list(lists_read))), "since": since}
    _write_json(resume_path, data)


def remember_visit_windows(store: str, windows: Optional[Sequence[Sequence[int]]], robots_path: Path = ROBOTS_PATH,
                           now: Callable[[], float] = time.time) -> None:
    """Keeps the Visit-time windows robots.txt gave (None: robots.txt was not read, the old ones stay) for the
    dashboard card, which says in Dubai time when the store may be read."""
    if windows is None:
        return
    data = _read_json(robots_path) or {}
    data[store] = {"visit_time": [list(w) for w in windows], "checked_at": now()}
    _write_json(robots_path, data)


def refresh(db: Any = None, harvester: Any = None, stores: Optional[Sequence[Any]] = None, *, trigger: str = "worker",
            force: bool = False, budget_s: Optional[float] = None, refresh_days: Optional[int] = None,
            clock: Callable[[], float] = time.monotonic, now: Callable[[], float] = time.time,
            state_path: Path = STATE_PATH, lock_path: Path = LOCK_PATH, robots_path: Path = ROBOTS_PATH,
            resume_path: Optional[Path] = None, stop: Optional[threading.Event] = None) -> Dict[str, Any]:
    """
    Reads again, one after the other, the stores that are due (decide / plan), within budget_s seconds. Blocks until it is
    done: callers that must not wait use start_background. Never raises. Returns {started, reason, results, skipped,
    elapsed_s}; reason: done | budget | nothing_due | running | off | unavailable.
    A store an unfinished refresh did not read to the end goes on from the URL lists it had not read (resume_from).
    stop: set by the process's exit (start_background): the store being read stops at its next file or batch and is
    recorded partial, no other store starts (reason 'budget', as for the time budget: the rest next time).
    resume_path: by default next to state_path.
    """
    if resume_path is None:
        resume_path = RESUME_PATH if state_path == STATE_PATH else state_path.with_name(RESUME_PATH.name)
    budget = float(settings.local_index_refresh_max_s() if budget_s is None else budget_s)
    if not settings.local_index_enabled() or budget <= 0:
        return {"started": False, "reason": "off", "results": [], "skipped": [], "elapsed_s": 0}
    days = settings.local_index_refresh_days() if refresh_days is None else refresh_days
    if not _acquire(lock_path, budget, now):
        return {"started": False, "reason": "running", "results": [], "skipped": [], "elapsed_s": 0}
    began = clock()
    deadline = began + budget
    state: Dict[str, Any] = {"state": "running", "trigger": trigger, "started_at": now(), "updated_at": now(),
                             "finished_at": None, "budget_s": budget, "current": None, "plan": [], "results": [],
                             "skipped": [], "ended": None}
    last_beat = [clock()]

    def beat() -> None:
        if clock() - last_beat[0] >= 10:
            last_beat[0] = clock()
            _touch(lock_path)

    def ended() -> bool:
        return stop is not None and stop.is_set()

    def should_stop() -> bool:
        beat()
        return clock() >= deadline or ended()

    def save() -> None:
        state["updated_at"] = now()
        _touch(lock_path)
        _write_json(state_path, state)

    try:
        try:
            if db is None:
                from .local_index import DbCatalogStore
                db = DbCatalogStore()
            todo, skipped = plan(stores if stores is not None else _enabled_stores(), db.harvest_ages(), days, force)
        except Exception as exc:  # noqa: BLE001 - a database that cannot be read must not stop the run
            logger.warning("index refresh: not available (%s)", type(exc).__name__)
            if trigger == "dashboard":      # the button was pressed: the page must not wait for a refresh that never starts
                record_end("unavailable", state_path, now)
            return {"started": False, "reason": "unavailable", "results": [], "skipped": [], "elapsed_s": 0}
        state["plan"] = [{"store": s.key, "reason": why} for s, why in todo]
        state["skipped"] = [{"store": s.key, "reason": why} for s, why in skipped]
        if not todo:
            # every run asks; only a refresh that read something replaces the progress file (the card keeps the
            # last real refresh), except for the button, whose page waits for an answer
            if trigger == "dashboard":
                state.update(state="idle", finished_at=now(), ended="nothing_due")
                save()
            return {"started": True, "reason": "nothing_due", "results": [], "skipped": state["skipped"],
                    "elapsed_s": 0}
        save()
        if harvester is None:
            from . import sitemaps
            harvester = sitemaps.SitemapHarvester()
        reason = "done"
        for store, why in todo:
            if clock() >= deadline or ended():
                reason = "budget"
                state["skipped"].append({"store": store.key, "reason": "budget"})
                continue
            state["current"] = store.key
            save()
            skip = resume_from(store.key, days, resume_path, now)
            result = _harvest_one(store, db, harvester, should_stop, skip, ended)
            result["reason"] = why
            if skip:
                result["resumed_lists"] = len(skip)
            remember_resume(store.key, skip, result.pop("lists_read", []), result["status"], resume_path, now)
            remember_visit_windows(store.key, result.pop("visit_window", None), robots_path, now)
            state["results"].append(result)
            if result["truncated"] and (clock() >= deadline or ended()):
                reason = "budget"
            save()
        state.update(state="idle", current=None, finished_at=now(), ended=reason)
        save()
        return {"started": True, "reason": reason, "results": list(state["results"]), "skipped": state["skipped"],
                "elapsed_s": round(clock() - began, 1)}
    except Exception as exc:  # noqa: BLE001 - the refresh is a convenience: it never breaks the run
        logger.warning("index refresh: stopped (%s)", type(exc).__name__)
        state.update(state="idle", current=None, finished_at=now(), ended="error")
        _write_json(state_path, state)
        return {"started": True, "reason": "error", "results": list(state["results"]), "skipped": state["skipped"],
                "elapsed_s": round(clock() - began, 1)}
    finally:
        _release(lock_path)


# ---------------------------------------------------------------------------
# The ways to start it
# ---------------------------------------------------------------------------

_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()                 # set at the process's exit (_at_exit): the thread's refresh stops cleanly
_EXIT: Dict[str, Optional[float]] = {"wait_until": None}   # the nightly's exit waits for its refresh until then


def _at_exit() -> None:
    """
    At the process's exit (atexit) a refresh still running in this process's thread is not cut off in the middle of a
    file (no harvest record, a lock left behind for minutes, the same files read again next time). The nightly run,
    which nobody waits for, gives it the rest of its own budget first. A worker run asks it to stop at once: it stops
    at its next sitemap file or batch of URLs, records the store's harvest as partial with what it read in full (the
    next refresh goes on from there, resume_from) and releases the lock; the exit waits at most EXIT_GRACE_S for that.
    """
    thread = _THREAD
    if thread is None or not thread.is_alive():
        return
    until = _EXIT.get("wait_until")
    if until is not None:
        thread.join(max(0.0, until - time.monotonic()))
    if thread.is_alive():
        _STOP.set()
        thread.join(EXIT_GRACE_S)


atexit.register(_at_exit)


def start_background(trigger: str = "worker", **kwargs: Any) -> Optional[threading.Thread]:
    """
    Starts refresh() in a daemon thread and returns at once: the caller (the nightly run, the worker) never waits for
    it, not even for the database read that decides what is stale. None when the automatic refresh is off
    (LOCAL_INDEX_ENABLED, LOCAL_INDEX_REFRESH_MAX_S = 0) or a refresh of this process still runs. Never raises.
    At the process's exit the refresh is stopped cleanly, or waited for by the nightly run (_at_exit).
    kwargs: refresh()'s (the tests' fakes).
    """
    global _THREAD
    try:
        budget = kwargs.get("budget_s")
        budget = settings.local_index_refresh_max_s() if budget is None else budget
        if not settings.local_index_enabled() or budget <= 0:
            return None
        if _THREAD is not None and _THREAD.is_alive():
            return None
        _STOP.clear()
        kwargs.setdefault("stop", _STOP)
        _EXIT["wait_until"] = time.monotonic() + float(budget) + EXIT_GRACE_S if trigger == "nightly" else None

        def run() -> None:
            try:
                result = refresh(trigger=trigger, **kwargs)
                if result.get("started"):
                    logger.info("index refresh (%s): %s, %d stores read", trigger, result.get("reason"),
                                len(result.get("results") or []))
            except Exception as exc:  # noqa: BLE001
                logger.warning("index refresh: thread stopped (%s)", type(exc).__name__)

        _THREAD = threading.Thread(target=run, name="local-index-refresh", daemon=True)
        _THREAD.start()
        return _THREAD
    except Exception as exc:  # noqa: BLE001
        logger.warning("index refresh: could not start (%s)", type(exc).__name__)
        return None


def start_detached(trigger: str = "dashboard", force: bool = True, python: Optional[str] = None,
                   popen: Callable[..., Any] = subprocess.Popen, state_path: Path = STATE_PATH,
                   lock_path: Path = LOCK_PATH, log_path: Path = LOG_PATH, now: Callable[[], float] = time.time
                   ) -> Dict[str, Any]:
    """
    The dashboard button's job (bridge action local_index_refresh): the same refresh in a process of its own, so the
    bridge answers at once. {started, running, reason}. Never raises.
    """
    if not settings.local_index_enabled():
        return {"started": False, "running": False, "reason": "disabled"}
    if status(state_path, lock_path, now)["running"]:
        return {"started": False, "running": True, "reason": "running"}
    try:
        _write_json(state_path, {"state": "starting", "trigger": trigger, "started_at": now(), "updated_at": now(),
                                 "finished_at": None, "results": [], "plan": [], "skipped": []})
        command = [python or sys.executable, str(ROOT / "scripts" / "build_catalog_index.py"), "--refresh",
                   "--trigger", trigger] + (["--force"] if force else [])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        options: Dict[str, Any] = {"cwd": str(ROOT), "stdin": subprocess.DEVNULL, "close_fds": True, "env": env}
        if os.name == "nt":      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console, not tied to the bridge
            options["creationflags"] = 0x00000008 | 0x00000200
        else:
            options["start_new_session"] = True
        with open(log_path, "w", encoding="utf-8") as log:
            popen(command, stdout=log, stderr=subprocess.STDOUT, **options)
        return {"started": True, "running": True, "reason": "started"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("index refresh: the job could not start (%s)", type(exc).__name__)
        record_end("unavailable", state_path, now)
        return {"started": False, "running": False, "reason": "unavailable"}


def format_result(result: Mapping[str, Any]) -> str:
    """The console text of a refresh (scripts/build_catalog_index.py --refresh)."""
    reason = str(result.get("reason") or "")
    if not result.get("started"):
        return {"off": "the automatic refresh is off (LOCAL_INDEX_ENABLED / LOCAL_INDEX_REFRESH_MAX_S)",
                "running": "another refresh is running; nothing started",
                "unavailable": "the index is not available (database?)"}.get(reason, f"nothing started ({reason})")
    lines = [f"refresh {reason} in {result.get('elapsed_s', 0)}s"]
    for r in result.get("results") or []:
        lines.append(f"  {r['store']:18s} {r['status']:8s} {r['product_urls']:7d} product pages, {r['new_urls']} new"
                     + (f"  ({r['error']})" if r.get("error") else ""))
    for s in result.get("skipped") or []:
        lines.append(f"  {s['store']:18s} left alone: {s['reason']}")
    return "\n".join(lines)
