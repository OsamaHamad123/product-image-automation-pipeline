"""No secret in the logs.

* run_report.RedactingFilter (run_report.redact on every log line, its arguments and its traceback) sits on the root
  logger, on every root handler and on logging.lastResort; cli_bridge, the nightly runner and `python main.py` install
  it (install_log_redaction).
* config.log_error_to_laravel and config.log_and_fail (log, failure table, Telegram) redact; the Telegram message
  (parse_mode HTML) escapes its values.
* query_refiner sends the Gemini key in the x-goog-api-key header (it was ?key= in the URL) and redacts its error.
* a bare `python main.py` prints its usage: the old sequential mode that writes the Sheet directly needs
  --legacy-sequential.
"""

import importlib.util
import io
import logging
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SECRET = "-".join(("tst", "gm", "9d2e4c71aa"))


@pytest.fixture
def secret(monkeypatch):
    import config

    monkeypatch.setattr(config, "GEMINI_API_KEY", SECRET)
    return SECRET


@pytest.fixture
def clean_logging():
    """Whatever a test installs on the root logger, its handlers and lastResort is taken off afterwards."""
    import run_report

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield root
    for target in [root, *root.handlers, logging.lastResort]:
        for f in list(getattr(target, "filters", [])):
            if isinstance(f, run_report.RedactingFilter):
                target.removeFilter(f)
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
    root.setLevel(level)


def _stream_handler(root):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return stream


def test_a_child_loggers_line_its_arguments_and_traceback_are_redacted(secret, clean_logging):
    import run_report

    stream = _stream_handler(clean_logging)
    run_report.install_log_redaction()
    run_report.install_log_redaction()                       # twice: one filter per target
    log = logging.getLogger("catalog_match.providers.some_provider")
    log.warning("request failed: %s", f"https://api.example/v1?key={secret}&q=milk")
    try:
        raise RuntimeError(f"401 for https://generativelanguage.googleapis.com/x?key={secret}")
    except RuntimeError:
        log.exception("Gemini answered with an error")
    log.info("nothing secret here: %d rows", 3)
    text = stream.getvalue()
    assert secret not in text and text.count("[REDACTED]") >= 2
    assert "nothing secret here: 3 rows" in text and "Traceback" in text
    root = logging.getLogger()
    for target in [root, *root.handlers, logging.lastResort]:
        assert sum(isinstance(f, run_report.RedactingFilter) for f in target.filters) == 1


def test_without_any_handler_the_last_resort_output_is_redacted(secret, clean_logging, capsys):
    import run_report

    for handler in list(clean_logging.handlers):
        clean_logging.removeHandler(handler)
    run_report.install_log_redaction()
    logging.getLogger("main").error("worker stopped: %s", secret)
    err = capsys.readouterr().err
    assert secret not in err and "[REDACTED]" in err


def test_cli_bridge_logs_to_search_log_without_secrets(secret, clean_logging):
    import cli_bridge

    stream = io.StringIO()
    cli_bridge._configure_logging(stream)
    logging.getLogger("image_search").warning("Serper call failed with api_key=%s", secret)
    assert secret not in stream.getvalue() and "api_key=[REDACTED]" in stream.getvalue()


def test_the_nightly_runner_logs_without_secrets(secret, clean_logging, monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("run_nightly_under_redaction_test", ROOT / "scripts" / "run_nightly.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    log = io.StringIO()
    log.close = lambda: None                                       # the runner closes its log at the end
    monkeypatch.setattr(runner, "open_log", lambda *a, **k: (log, str(tmp_path / "nightly.log")))
    monkeypatch.setattr(runner, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(runner, "run", lambda **k: logging.getLogger("main").warning("search key %s", secret) or 0)
    assert runner.main([]) == 0
    assert secret not in log.getvalue() and "search key [REDACTED]" in log.getvalue()


# ---------------------------------------------------------------------------
# config: the Laravel log, the failure table and Telegram
# ---------------------------------------------------------------------------

def test_the_laravel_log_line_is_redacted(secret, monkeypatch, tmp_path):
    import config

    path = tmp_path / "laravel.log"
    monkeypatch.setattr(config, "LARAVEL_LOG_PATH", str(path))
    config.log_error_to_laravel(f"CLI exception: 403 https://x.example/?key={secret}", product_name="Milk",
                                brand="Almarai", barcode="6281007000024")
    line = path.read_text(encoding="utf-8")
    assert secret not in line and "local.ERROR: Python Pipeline - [Product: Milk" in line and line.endswith("\n")


def test_a_failure_is_redacted_everywhere_and_telegram_html_is_escaped(secret, monkeypatch, tmp_path):
    import config
    import local_cache_db

    monkeypatch.setattr(config, "LARAVEL_LOG_PATH", str(tmp_path / "laravel.log"))
    runner_lines, stored, sent = [], [], []
    monkeypatch.setattr(config, "log_runner", runner_lines.append)
    monkeypatch.setattr(local_cache_db, "save_product_failure", lambda *a: stored.append(a))
    monkeypatch.setattr(config, "send_telegram_alert", sent.append)
    config.log_and_fail("6281007000024", "Milk <b>1L</b> & Co", "A&W", f"401 unauthorized <key> {secret}")
    everything = " ".join(runner_lines) + str(stored) + " ".join(sent) + (tmp_path / "laravel.log").read_text("utf-8")
    assert secret not in everything
    [message] = sent
    assert "Milk &lt;b&gt;1L&lt;/b&gt; &amp; Co" in message and "A&amp;W" in message
    assert "<code>401 unauthorized &lt;key&gt; [REDACTED]</code>" in message
    assert message.count("<b>") == message.count("</b>")         # only the alert's own tags


# ---------------------------------------------------------------------------
# query_refiner: the Gemini key in a header
# ---------------------------------------------------------------------------

class _Answer:
    status_code = 200

    @staticmethod
    def json():
        return {"candidates": [{"content": {"parts": [{"text": '{"canonical_brand_en": "Almarai"}'}]}}]}


def test_the_gemini_key_goes_in_a_header_never_the_url(secret, monkeypatch, capsys):
    import query_refiner

    seen = []
    monkeypatch.setattr(query_refiner.requests, "post", lambda url, **kw: seen.append((url, kw)) or _Answer())
    assert query_refiner.QueryRefiner.refine_product_metadata("Milk 1L", "Almarai")["canonical_brand_en"] == "Almarai"
    [(url, kwargs)] = seen
    assert secret not in url and "key=" not in url and kwargs["headers"]["x-goog-api-key"] == secret

    def boom(url, **kw):
        raise ConnectionError(f"HTTPSConnectionPool: Max retries exceeded with url: {url}?key={secret}")

    monkeypatch.setattr(query_refiner.requests, "post", boom)
    fallback = query_refiner.QueryRefiner.refine_product_metadata("Milk 1L", "Almarai")
    assert fallback["raw_brand"] == "Almarai"
    out = capsys.readouterr().out
    assert secret not in out and "[REDACTED]" in out


# ---------------------------------------------------------------------------
# python main.py
# ---------------------------------------------------------------------------

def test_a_bare_main_py_prints_its_usage_and_writes_nothing(clean_logging, monkeypatch):
    import main

    printed = []
    monkeypatch.setattr(main, "print", lambda *a: printed.append(" ".join(map(str, a))))   # main's print (log_runner)
    monkeypatch.setattr(main, "run_automation_pipeline", lambda: pytest.fail("the legacy sequential run started"))
    monkeypatch.setattr(main, "run_enqueue_mode", lambda: pytest.fail("enqueue started"))
    monkeypatch.setattr(main, "run_worker_mode", lambda **k: pytest.fail("worker started"))
    assert main.cli(["main.py"]) == 2
    assert main.cli(["main.py", "--sequential"]) == 2
    assert len(printed) == 2 and printed[0] == main.USAGE
    assert "usage: python main.py --enqueue | --worker" in printed[0] and "--legacy-sequential" in printed[0]


def test_what_the_worker_prints_is_redacted():
    """main.py's print is config.log_runner (stdout = temp/pipeline.log, and the dashboard's live log); in its own
    process, as the worker runs (cli_bridge replaces log_runner in this one)."""
    import os
    import subprocess
    import sys

    code = ("import config\n"
            "config.log_runner('search failed: 403 for url https://x.example/?key=' + config.GEMINI_API_KEY, "
            "config.GEMINI_API_KEY)\n"
            "assert config.RUNNER_LOGS[-1].endswith('[REDACTED]'), config.RUNNER_LOGS[-1]\n")
    env = dict(os.environ, GEMINI_API_KEY=SECRET, REDIS_HOST="127.0.0.1", REDIS_PORT="1")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                            timeout=120, encoding="utf-8")
    assert result.returncode == 0, result.stderr[-2000:]
    assert SECRET not in result.stdout and result.stdout.count("[REDACTED]") == 2


def test_each_mode_needs_its_flag(clean_logging, monkeypatch):
    import main
    import run_report

    ran = []
    monkeypatch.setattr(main, "run_automation_pipeline", lambda: ran.append("legacy"))
    monkeypatch.setattr(main, "run_enqueue_mode", lambda: ran.append("enqueue"))
    monkeypatch.setattr(main, "run_worker_mode", lambda trigger="manual", **k: ran.append(("worker", trigger)))
    monkeypatch.setattr(main, "LAST_WORKER", {"stop_reason": "queue_empty"})
    assert main.cli(["main.py", "--legacy-sequential"]) == 0
    assert main.cli(["main.py", "--enqueue"]) == 0
    assert main.cli(["main.py", "--worker", "--trigger=dashboard"]) == 0
    assert ran == ["legacy", "enqueue", ("worker", "dashboard")]
    assert any(isinstance(f, run_report.RedactingFilter) for f in logging.lastResort.filters)


def test_no_launcher_or_doc_runs_main_py_without_a_mode():
    import re

    found = []
    for path in list(ROOT.glob("*.bat")) + list(ROOT.glob("*.ps1")) + list(ROOT.glob("*.md")) \
            + list((ROOT / "docs").glob("*.md")) + list((ROOT / "deploy").rglob("*")) + list((ROOT / "scripts").glob("*")) \
            + [ROOT / "dashboard" / "app" / "Http" / "Controllers" / "ApiController.php"]:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"main\.py[\"']?([^\n]{0,40})", text):
            if re.match(r"\s*(?:\\?[\"']\s*)?$", m.group(1)) and re.search(r"python[^\n]{0,80}main\.py", text):
                found.append((path.name, m.group(0)))
    assert found == []
