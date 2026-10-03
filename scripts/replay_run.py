"""Replay a recorded live dry run offline: the same products, the recorded answers, this checkout's code.

Usage (from the repository root; no key, no network, no database, no cost):

    python3 scripts/replay_run.py runs/cassette_2026-10 --json runs/replayed.json
    python3 scripts/replay_run.py runs/cassette_2026-10.zip --rows 2-10 --json runs/replayed.json
    python3 scripts/compare_runs.py runs/after.json runs/replayed.json     # the live run vs this code

The cassette comes from the owner's machine (a folder or a zip of it):

    .venv\\Scripts\\python.exe scripts\\smoke_live.py --rows-file runs\\2026-10-03\\rows_2_61.csv ^
        --brands-file runs\\2026-10-03\\brands_mapping_suggested.csv --dry-run --json runs\\after.json ^
        --record runs\\cassette_2026-10 --record-shadow

Every row runs through scripts/smoke_live.py's own run_row(), so the --json file has the dry run's format
(compare_runs.py reads it) with one more entry per row:

    "replay": {"complete": true|false, "misses": [...], "approximate": true|false, "approximations": [...]}

complete   every answer the code asked for was in the cassette;
misses     what it asked for and the cassette does not hold (a provider call fails with NotRecorded, a
           download or page read with 'not_recorded', a label-reader call fails closed): the row's decision
           may differ from what the live services would say. Never silent: each miss is printed and listed;
approximate a label reading came from another recorded call of the same row and model (another batch or
           prompt), or an index lookup or deadline was not recorded for exactly this request.

When a row missed answers, the misses are also written to a manifest (--misses, by default next to --json as
<name>.misses.json). On the machine with the keys,
    smoke_live.py --record runs\\cassette_2026-10 --fill-misses replayed.misses.json
runs those rows again with this same code, reuses every recorded answer and records only the missing ones.

What a replay neutralises: the settings are the recorded ones (keys replaced by dummies, so the same
providers and readers are built), the brand mappings are the recorded ones (after learning), the rate limits
do not wait, the local index answers from its recorded rows and deadline, the circuit breakers, the strong
reader's month spend and the index size are the recorded values, the CSE sunset check uses the recording
day, every process-wide switch starts as in a fresh worker, and the page cache is emptied for every row.
Every socket and database connection is refused for the whole replay.
"""

import argparse
import contextlib
import datetime as dt
import json
import logging
import os
import sys
import tempfile
from unittest import mock

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPTS)
for _p in (REPO_ROOT, SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import smoke_live  # noqa: E402  (same folder: the dry run's row runner, report and summary)

log = logging.getLogger("replay_run")

DUMMY_PROXY = "http://replay-proxy.invalid:9"
LIST_SECRETS = ("GOOGLE_SEARCH_API_KEYS", "GOOGLE_SEARCH_CX_LIST")


# ---------------------------------------------------------------------------
# Environment of the recording
# ---------------------------------------------------------------------------

def replay_settings(meta, candidate_dir):
    """Setting name -> value for the replay: the recorded values, dummies for the secrets that were set."""
    snap = meta.get("settings") or {}
    values = dict(snap.get("values") or {})
    for name, present in (snap.get("secrets") or {}).items():
        if name in LIST_SECRETS:
            values[name] = [f"replay-{name.lower()}-{i + 1}" for i in range(int(present or 0))]
        elif name == "PROXY_URL":
            values[name] = DUMMY_PROXY if present else ""
        else:
            values[name] = f"replay-{name.lower().replace('_', '-')}" if present else ""
    values["CANDIDATE_STORE_DIR"] = candidate_dir
    return values


def _env_text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return "" if value is None else str(value)


class RecordedProviders:
    """providers_mod for run_row(): default_providers() as on the recording day (the CSE sunset check)."""

    def __init__(self, module, today):
        self._module = module
        self._today = today

    def default_providers(self):
        return self._module.default_providers(today=self._today)

    def __getattr__(self, name):
        return getattr(self._module, name)


def _reset_process_state():
    """What a fresh worker process starts with (process-wide caches and switches)."""
    from catalog_match import learning, local_index, pages, verify
    from catalog_match.verifiers import claude

    local_index.reset_blocked_hosts()
    local_index.DbCatalogStore._count_cache.clear()
    pages.clear_cache()
    learning.clear_cache()
    verify.BREAKER.reset()
    claude.CLAUDE_BREAKER.reset()


@contextlib.contextmanager
def replay_environment(meta, candidate_dir):
    """The recorded settings, dummy keys, fresh process state, no rate-limit waits, every connection refused."""
    from catalog_match import cassette, fetch, ratelimit, settings as cm_settings
    from catalog_match.providers import bing_html
    from catalog_match.providers.cse_legacy import CseLegacyProvider
    from catalog_match.providers.lens import SerperLensProvider
    from catalog_match.providers.serper import SerperImagesProvider

    values = replay_settings(meta, candidate_dir)
    env = {name: _env_text(value) for name, value in values.items()}
    env.update(GOOGLE_SEARCH_API_KEY=_env_text(values.get("GOOGLE_SEARCH_API_KEYS", [])),
               GOOGLE_SEARCH_CX=_env_text(values.get("GOOGLE_SEARCH_CX_LIST", [])))
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, env))
        cfg = getattr(cm_settings, "_config", None)
        if cfg is not None:
            for name, value in values.items():
                stack.enter_context(mock.patch.object(cfg, name, value, create=True))
            for name in ("GOOGLE_SEARCH_API_KEY", "GOOGLE_SEARCH_CX"):
                stack.enter_context(mock.patch.object(cfg, name, env[name], create=True))
        stack.enter_context(mock.patch.object(SerperImagesProvider, "operators_blocked", False))
        stack.enter_context(mock.patch.object(SerperLensProvider, "unsupported", False))
        stack.enter_context(mock.patch.object(CseLegacyProvider, "disabled_reason", None))
        stack.enter_context(mock.patch.object(ratelimit, "get_bucket", lambda *a, **k: ratelimit.UNLIMITED))
        # libcurl opens its own sockets: a request that slipped past the hooks goes through `requests`, where the
        # socket guard below refuses (and counts) it
        stack.enter_context(mock.patch.object(fetch, "_curl_requests", None))
        stack.enter_context(mock.patch.object(bing_html, "_cffi_requests", None))
        _reset_process_state()
        stack.callback(_reset_process_state)
        attempts = stack.enter_context(cassette.offline())
        yield attempts


def read_meta(path):
    """meta.json of a cassette folder or zip."""
    from catalog_match import cassette
    store = cassette._Store(path, writable=False)
    try:
        text = store.read_text(cassette.META_FILE)
    finally:
        store.close()
    if not text:
        raise ValueError(f"{path} holds no {cassette.META_FILE}: not a cassette")
    return json.loads(text)


def version_warnings(meta):
    """Plain lines for the differences that can move a replayed decision by themselves."""
    from catalog_match import cassette
    lines = []
    recorded, here = meta.get("versions") or {}, cassette.library_versions()
    for name, why in (("pillow", "image decoding and quality scores"), ("numpy", "quality scores and pHashes"),
                      ("scipy", "pHashes")):
        if recorded.get(name) and here.get(name) and recorded[name] != here[name]:
            lines.append(f"{name} {recorded[name]} recorded, {here[name]} here: {why} may differ slightly")
    if bool(recorded.get("curl_cffi")) != bool(here.get("curl_cffi")):
        lines.append("curl_cffi differs between the recording and here (only the client; answers are recorded)")
    return lines


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _miss_text(miss):
    request = miss.get("request") or {}
    what = request.get("url") or request.get("image") or ""
    if miss.get("kind") == "verifier":
        what = f"{request.get('provider')}:{request.get('model')} on {len(request.get('images') or [])} image(s)"
    elif miss.get("kind") == "local_index":
        what = f"rows for {request.get('groups')}"
    return f"{miss.get('kind')} {str(what)[:110]}"


def print_replay(report):
    if report["complete"] and not report["approximate"]:
        print(f"  REPLAY complete ({report['answers']} recorded answers)")
        return
    state = "complete" if report["complete"] else f"INCOMPLETE: {len(report['misses'])} answer(s) not recorded"
    print(f"  REPLAY {state}{', approximate' if report['approximate'] else ''}")
    for miss in report["misses"]:
        print(f"    miss    {_miss_text(miss)}")
    for note in report["approximations"]:
        print(f"    approx  {note}")


def replay_summary(results):
    rows = [r for r in results if isinstance(r.get("replay"), dict)]
    by_kind = {}
    for r in rows:
        for miss in r["replay"]["misses"]:
            by_kind[miss.get("kind")] = by_kind.get(miss.get("kind"), 0) + 1
    return {
        "rows": len(rows),
        "complete": sum(1 for r in rows if r["replay"]["complete"]),
        "incomplete_rows": [r["row"] for r in rows if not r["replay"]["complete"]],
        "approximate_rows": [r["row"] for r in rows if r["replay"]["approximate"]],
        "misses": sum(by_kind.values()),
        "misses_by_kind": dict(sorted(by_kind.items())),
        "crashed_rows": [r["row"] for r in results if "error" in r],
    }


def format_replay_summary(s):
    lines = ["", "=== replay ===",
             f"rows {s['rows']} | complete {s['complete']} | incomplete {len(s['incomplete_rows'])}"
             f"{' (' + smoke_live._rows_text(s['incomplete_rows']) + ')' if s['incomplete_rows'] else ''}"
             f" | approximate {len(s['approximate_rows'])}"
             f"{' (' + smoke_live._rows_text(s['approximate_rows']) + ')' if s['approximate_rows'] else ''}"]
    if s["misses"]:
        lines.append(f"answers not in the cassette: {s['misses']} ({smoke_live._counts_text(s['misses_by_kind'])}); "
                     "the decisions of those rows may differ from the live services'")
    else:
        lines.append("every answer the code asked for was in the cassette")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(args):
    from catalog_match import cassette

    meta = read_meta(args.cassette)
    if meta.get("format") != cassette.FORMAT:
        raise SystemExit(f"{args.cassette}: cassette format {meta.get('format')!r}, this code reads {cassette.FORMAT}")
    identity, pipeline, providers_mod, settings, verify_mod = smoke_live.load_v2()
    wanted = set(smoke_live.parse_rows(args.rows)) if args.rows else None
    rows = [r for r in meta.get("rows") or [] if wanted is None or int(r["row_number"]) in wanted]
    mappings = meta.get("mappings") or {}
    run_info = meta.get("run") or {}
    expansion = bool(run_info.get("expansion", True))
    serp_cost = float(run_info.get("serp_cost", smoke_live.DEFAULT_SERP_COST))
    vlm_cost = float(run_info.get("vlm_cost", smoke_live.DEFAULT_VLM_COST))
    today = dt.date.fromisoformat(meta["today"]) if meta.get("today") else None
    recorded_git = (meta.get("git") or {}).get("commit") or "?"
    here_git = smoke_live.git_info().get("commit") or "?"
    print(f"replaying {len(rows)} rows of {args.cassette} (recorded {meta.get('created_at', '?')} at commit "
          f"{recorded_git[:10]}, this checkout {here_git[:10]}) | offline, no key, no cost")
    for line in version_warnings(meta):
        print(f"WARNING: {line}")

    providers = RecordedProviders(providers_mod, today)
    results = []
    candidate_dir = tempfile.mkdtemp(prefix="replay_candidates_")
    with replay_environment(meta, candidate_dir) as attempts:
        prices = smoke_live.provider_prices(serp_cost)
        secrets = smoke_live.secret_values()
        cas = cassette.install("replay", args.cassette)
        try:
            for row in rows:
                with cas.row_context(int(row["row_number"])):
                    try:
                        r = smoke_live.run_row(row, mappings, identity, pipeline, providers, verify_mod, serp_cost,
                                               vlm_cost, expansion=expansion, prices=prices, secrets=secrets)
                    except Exception as exc:
                        r = {"row": row["row_number"], "name": row.get("name", ""), "brand": row.get("brand", ""),
                             "error": f"{type(exc).__name__}: {exc}"}
                        log.exception("row %s crashed in the replay", row["row_number"])
                        print(f"\n=== row {row['row_number']}: {row.get('name', '')} -> ERROR {r['error']}")
                    else:
                        smoke_live.print_row(r)
                r["replay"] = cas.row_report(int(row["row_number"]))
                if "error" not in r:
                    print_replay(r["replay"])
                results.append(r)
            manifest = cas.misses_manifest([int(r["row_number"]) for r in rows])
        finally:
            cassette.uninstall()
        outbound = [a for a in attempts if not a.startswith("pymysql")]
    if outbound:
        print(f"WARNING: the replay tried to reach the network {len(outbound)} time(s) (refused): {outbound[:3]}")

    summary = smoke_live.summarize(results)
    print(smoke_live.format_summary(summary))
    rsum = replay_summary(results)
    rsum["outbound_attempts"] = len(outbound)
    print(format_replay_summary(rsum))
    manifest.update(created_at=dt.datetime.now().isoformat(timespec="seconds"), git_commit=here_git)
    misses_path = args.misses or (os.path.splitext(args.json)[0] + ".misses.json" if args.json else None)
    if manifest["misses"] and misses_path:
        with open(misses_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
        print(f"misses manifest written to {misses_path}: on the machine with the keys, with this same code,\n"
              f"    .venv\\Scripts\\python.exe scripts\\smoke_live.py --record <the cassette folder> "
              f"--fill-misses {os.path.basename(misses_path)}\nadds the missing answers to the cassette")
    if args.json:
        doc = {"format": smoke_live.JSON_FORMAT,
               "meta": {"replay_of": os.path.abspath(args.cassette), "recorded": {
                   "created_at": meta.get("created_at"), "git": meta.get("git"), "run_meta": meta.get("run_meta")},
                   "git": smoke_live.git_info(), "started_at": dt.datetime.now().isoformat(timespec="seconds"),
                   "versions_recorded": meta.get("versions"), "versions_here": cassette.library_versions()},
               "summary": summary, "replay": rsum, "rows": results}
        with open(args.json, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, ensure_ascii=False, indent=1, default=str))
        print(f"results written to {args.json}")
    return 1 if args.strict and (rsum["incomplete_rows"] or outbound) else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("cassette", help="the cassette folder (smoke_live.py --record) or a zip of it")
    parser.add_argument("--json", help="write the replayed run (smoke_live's --json format) to this file")
    parser.add_argument("--rows", action="append", help="replay only these sheet rows, e.g. 2-10 (repeatable)")
    parser.add_argument("--misses", help="where to write the misses manifest (default: <--json name>.misses.json)")
    parser.add_argument("--strict", action="store_true", help="exit with 1 when a row missed answers")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    smoke_live._utf8_stdout()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if not os.path.exists(args.cassette):
        parser.error(f"{args.cassette}: no such cassette folder or zip")
    if args.json:
        target = os.path.abspath(args.json)
        if os.path.isdir(target) or not os.path.isdir(os.path.dirname(target)):
            parser.error(f"--json {args.json}: give a file name in a folder that exists")
    try:
        return run(args)
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    sys.exit(main())
