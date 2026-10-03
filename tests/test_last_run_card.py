"""The health page's «آخر تشغيل» card (package P4b, item 4): the last run_history row, in plain Arabic.

HealthController::lastRunCard is a pure function run under the PHP CLI; the page is rendered through the Laravel
kernel with a row written by local_cache_db.save_run_history (skipped without php, dashboard/vendor or MariaDB).
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
RUN = "p4ops-card-"


def _cards(rows):
    health = str(HEALTH_PHP).replace("\\", "/")
    script = ("<?php\nnamespace App\\Http\\Controllers { abstract class Controller {} }\nnamespace {\n"
              f"require '{health}';\nuse App\\Http\\Controllers\\HealthController;\n"
              f"$rows = json_decode({json.dumps(json.dumps(rows, ensure_ascii=False))}, true);\n"
              "echo json_encode(array_map(fn ($r) => HealthController::lastRunCard($r), $rows), JSON_UNESCAPED_UNICODE);\n}\n")
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


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
    out = _cards([done, outage, skipped, budget, None])
    assert out[0] == {"title": "التشغيل الليلي: خلص", "tone": "success", "when": "2026-10-03 02:00",
                      "summary": "بانتظار المراجعة 90 · ما انلقت 20 · فشل 8"}
    assert out[1]["title"] == "التشغيل الليلي: انقطاع (قاعدة البيانات لا ترد)" and out[1]["tone"] == "danger"
    assert out[1]["summary"] == "انعاد التشغيل مرتين بعد انقطاع"
    assert out[2] == {"title": "التشغيل الليلي: ما بلّش لأنو في تشغيل تاني شغّال", "tone": "muted",
                      "when": "2026-10-03 02:00", "summary": ""}
    assert out[3]["title"] == "تشغيل من اللوحة: وقف قبل ما يخلص الطابور (BUDGET_REACHED)"
    assert out[3]["summary"] == "بانتظار المراجعة 0 · بقي بالانتظار 40" and out[3]["when"] == ""
    assert out[4] is None


@pytest.mark.skipif(PHP is None or not (DASH / "vendor" / "autoload.php").exists(),
                    reason="php or dashboard/vendor is not installed")
def test_the_health_page_shows_the_last_run(mariadb_or_skip, tmp_path):
    db = mariadb_or_skip
    entry = {"run_id": RUN + "1", "run_trigger": "nightly", "started_at": "2026-10-03 02:00:00",
             "ended_at": "2026-10-03 03:12:00", "outcome": "done", "exit_code": 0, "attempts": 2,
             "enqueued": 120, "searched": 118, "auto_published": 0, "ready_for_review": 90, "not_found": 20,
             "failed": 8, "provider_down": 0, "pending_left": 0,
             "report_json": {"reason_text": "", "outcome": "done"}}
    row_id = db.save_run_history(entry)
    assert row_id
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
        assert "data-health-last-run" in body and "التشغيل الليلي: خلص" in body and "2026-10-03 02:00" in body
        assert "بانتظار المراجعة 90 · ما انلقت 20 · فشل 8 · انعاد التشغيل مرة بعد انقطاع" in body
    finally:
        conn = db.get_db_connection()
        try:
            conn.cursor().execute("DELETE FROM run_history WHERE run_id LIKE %s", (RUN + "%",))
            conn.commit()
        finally:
            conn.close()
