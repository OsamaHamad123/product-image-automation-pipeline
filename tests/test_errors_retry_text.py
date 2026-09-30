"""Errors page retry: the rows are only re-queued, and every message says so.

Before this fix the page told the owner that a retried row "will start running immediately", although
ApiController::retryFailures only upserts the rows into automation_queue; no worker is started.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from blade_scripts import inline_scripts

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
ERRORS_VIEW = DASH / "resources" / "views" / "dashboard" / "errors.blade.php"
API = DASH / "app" / "Http" / "Controllers" / "ApiController.php"
NODE = shutil.which("node")


def read(path):
    return path.read_text(encoding="utf-8")


def _method(text, name):
    start = text.index(f"function {name}(")
    nxt = re.search(r"\n    (?:public|private|protected)(?: static)? function ", text[start + 1:])
    return text[start:start + 1 + nxt.start()] if nxt else text[start:]


def test_retry_texts_do_not_promise_an_immediate_run():
    page = read(ERRORS_VIEW)
    for claim in ("فوراً", "سيبدأ تشغيله", "تصفير وجدولة"):
        assert claim not in page, claim
    assert page.count("data.message ||") == 2
    assert "شغّل الأتمتة" in page

    retry = _method(read(API), "retryFailures")
    assert "طابور الأتمتة" in retry and "شغّل الأتمتة" in retry
    assert "فوراً" not in retry


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_single_retry_shows_the_servers_queue_message():
    script = inline_scripts(read(ERRORS_VIEW))[-1]
    message = "أُضيفت المنتجات إلى طابور الأتمتة (العدد: 1)، ولا تبدأ معالجتها من هنا"
    js = """
const alerts = [];
const rows = {};
function el(id) {
    if (!rows[id]) rows[id] = { id, style: {}, querySelectorAll: () => [], innerHTML: '', disabled: false };
    return rows[id];
}
globalThis.document = { getElementById: el, querySelector: () => ({ content: '' }), querySelectorAll: () => [] };
globalThis.alert = (text) => alerts.push(text);
""" + script + f"""
globalThis.fetch = async () => ({{ json: async () => ({{ status: 'success', requeued: 1, message: {json.dumps(message)} }}) }});
retrySingleFailure('ERR_1', {{ innerHTML: '', disabled: false }}).then(() => console.log(JSON.stringify(alerts)));
"""
    result = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    alerts = json.loads(result.stdout.strip().splitlines()[-1])
    assert alerts == ["✅ " + message]
