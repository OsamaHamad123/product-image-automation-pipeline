"""The health page's «آخر تشغيل» card (package P4b, item 4): the last run, in plain Arabic.

run_report.py writes temp/nightly/last_report.json after every run (the same report as the run_history row, and
written even when the database does not answer); HealthController reads that file, never a table (the health
controller reads data only through files and the Python bridge). HealthController::lastRunRow / lastRunCard run
under the PHP CLI; the page is rendered through the Laravel kernel (skipped without php or dashboard/vendor).
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
HEALTH_PHP = DASH / "app" / "Http" / "Controllers" / "HealthController.php"
PHP = shutil.which("php")


def _php(code):
    health = str(HEALTH_PHP).replace("\\", "/")
    script = ("<?php\nnamespace App\\Http\\Controllers { abstract class Controller {} }\nnamespace {\n"
              f"require '{health}';\nuse App\\Http\\Controllers\\HealthController;\n$out = null;\n{code}\n"
              "echo json_encode($out, JSON_UNESCAPED_UNICODE);\n}\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def _cards(rows):
    return _php(f"$rows = json_decode({json.dumps(json.dumps(rows, ensure_ascii=False))}, true);\n"
                "$out = array_map(fn ($r) => HealthController::lastRunCard($r), $rows);")


def _night_report(**extra):
    """A night report as run_report.py writes it (offline: counts come from a stand-in database)."""
    import run_report

    class Db:
        def run_outcome_counts(self, **kw):
            return {"enqueued": 120, "searched": 118, "auto_published": 0, "ready_for_review": 90, "not_found": 20,
                    "failed": 8, "provider_down": 0, "pending_left": 0}

    attempts = extra.pop("attempts", [{"stop_reason": "provider_down", "run_id": "r1"},
                                      {"stop_reason": None, "run_id": "r2"}])
    report = run_report.build_report("nightly", attempts, 1_790_000_000, 1_790_004_320, db=Db(), sheets=object())
    report.update(extra)
    return report


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_last_run_card_texts():
    done = {"run_trigger": "nightly", "started_at": "2026-10-03 02:00:05", "outcome": "done", "attempts": 1,
            "ready_for_review": 90, "auto_published": 0, "not_found": 20, "failed": 8, "pending_left": 0,
            "report_json": json.dumps({"reason_text": ""})}
    outage = {"run_trigger": "nightly", "started_at": "2026-10-03 02:00:05", "outcome": "outage", "attempts": 3,
              "stop_reason": "db_unavailable", "ready_for_review": None,
              "report_json": json.dumps({"reason_text": "قاعدة البيانات لا ترد"}, ensure_ascii=False)}
    skipped = {"run_trigger": "nightly", "started_at": "2026-10-03 02:00:05", "outcome": "skipped", "attempts": 1,
               "report_json": {"reason_text": "تشغيل آخر يعمل الآن"}}
    budget = {"run_trigger": "dashboard", "started_at": None, "outcome": "stopped", "stop_reason": "BUDGET_REACHED",
              "ready_for_review": 0, "pending_left": 40}
    handed = {"run_trigger": "nightly", "started_at": "2026-10-03 02:00:05", "outcome": "handed_over", "attempts": 2,
              "ready_for_review": 4, "pending_left": 0,
              "report_json": {"reason_text": "توقف على انقطاع، وأثناء انتظار إعادة المحاولة بدأ تشغيل آخر وتولى إكمال الطابور"}}
    out = _cards([done, outage, skipped, budget, None, handed])
    assert out[0] == {"title": "التشغيل الليلي: خلص", "tone": "success", "when": "2026-10-03 02:00",
                      "summary": "بانتظار المراجعة 90 · ما انلقت 20 · فشل 8"}
    assert out[1]["title"] == "التشغيل الليلي: انقطاع (قاعدة البيانات لا ترد)" and out[1]["tone"] == "danger"
    assert out[1]["summary"] == "انعاد التشغيل مرتين بعد انقطاع"
    assert out[2] == {"title": "التشغيل الليلي: ما بلّش لأنو في تشغيل تاني شغّال", "tone": "muted",
                      "when": "2026-10-03 02:00", "summary": ""}
    assert out[3]["title"] == "تشغيل من اللوحة: وقف قبل ما يخلص الطابور (BUDGET_REACHED)"
    assert out[3]["summary"] == "بانتظار المراجعة 0 · بقي بالانتظار 40" and out[3]["when"] == ""
    assert out[4] is None
    assert out[5]["title"].startswith("التشغيل الليلي: سلّم الطابور لتشغيل تاني بعد انقطاع (توقف على انقطاع")
    assert out[5]["tone"] == "warning" and out[5]["summary"] == "بانتظار المراجعة 4"      # the hand-over is no re-run


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_the_card_reads_the_report_python_writes(tmp_path):
    import run_report

    path = tmp_path / "last_report.json"
    run_report.write_last_report(_night_report(), str(path))
    card = _php(f"$out = HealthController::lastRunCard(HealthController::lastRunRow({json.dumps(str(path))}));")
    assert card["title"] == "التشغيل الليلي: خلص" and card["tone"] == "success"
    assert card["summary"] == "بانتظار المراجعة 90 · ما انلقت 20 · فشل 8 · انعاد التشغيل مرة بعد انقطاع"
    assert _php(f"$out = HealthController::lastRunRow({json.dumps(str(tmp_path / 'none.json'))});") is None
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert _php(f"$out = HealthController::lastRunRow({json.dumps(str(tmp_path / 'bad.json'))});") is None


def test_the_health_controller_reads_no_table():
    text = HEALTH_PHP.read_text(encoding="utf-8")
    assert "DB::" not in text and "select(" not in text.lower()


@pytest.mark.skipif(PHP is None or not (DASH / "vendor" / "autoload.php").exists(),
                    reason="php or dashboard/vendor is not installed")
def test_the_health_page_shows_the_last_run(tmp_path):
    import run_report

    real = ROOT / "temp" / "nightly" / "last_report.json"
    saved = real.read_bytes() if real.exists() else None
    run_report.write_last_report(_night_report(), str(real))
    try:
        compiled = tmp_path / "views"
        compiled.mkdir()
        env = dict(os.environ, APP_ENV="testing", APP_KEY="base64:" + "A" * 43 + "=", APP_DEBUG="true",
                   SESSION_DRIVER="array", CACHE_STORE="array", LOG_CHANNEL="stderr",
                   VIEW_COMPILED_PATH=str(compiled), DB_CONNECTION="mariadb",
                   DB_HOST=os.getenv("DB_HOST", "127.0.0.1"), DB_PORT=os.getenv("DB_PORT", "3306"),
                   DB_DATABASE=os.environ["DB_DATABASE"], DB_USERNAME=os.getenv("DB_USERNAME", "root"),
                   DB_PASSWORD=os.getenv("DB_PASSWORD", ""))
        dash = str(DASH).replace("\\", "/")
        script = f"""<?php
require '{dash}/vendor/autoload.php';
$app = require '{dash}/bootstrap/app.php';
$kernel = $app->make(Illuminate\\Contracts\\Http\\Kernel::class);
$response = $kernel->handle(Illuminate\\Http\\Request::create('/system-diagnostics', 'GET'));
echo json_encode(['status' => $response->getStatusCode(), 'body' => $response->getContent()], JSON_UNESCAPED_UNICODE);
"""
        with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
            fh.write(script)
            path = fh.name
        try:
            result = subprocess.run([PHP, path], cwd=DASH, env=env, capture_output=True, text=True, timeout=240,
                                    encoding="utf-8")
        finally:
            os.unlink(path)
        assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
        page = json.loads(result.stdout)
        assert page["status"] == 200, page["body"][:2000]
        body = page["body"]
        assert "data-health-last-run" in body and "التشغيل الليلي: خلص" in body
        assert "بانتظار المراجعة 90 · ما انلقت 20 · فشل 8 · انعاد التشغيل مرة بعد انقطاع" in body
    finally:
        if saved is None:
            real.unlink()
        else:
            real.write_bytes(saved)
