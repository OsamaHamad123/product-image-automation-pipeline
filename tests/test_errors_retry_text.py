"""Failed-rows retry: the rows are only re-queued, and every message says so.

Before this fix the errors page told the owner that a retried row "will start running immediately", although
ApiController::retryFailures only upserts the rows into automation_queue; no worker is started. The errors page is
now the «أعطال» filter of the review screen (/errors redirects to /catalog?filter=failed); its retry says the rows
went back to the queue and that the run is started from «التشغيل».
"""

import re
from pathlib import Path

import pytest

from laqta_review_harness import NODE, run

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "dashboard"
REVIEW_JS = DASH / "public" / "js" / "review"
API = DASH / "app" / "Http" / "Controllers" / "ApiController.php"


def read(path):
    return path.read_text(encoding="utf-8")


def _method(text, name):
    start = text.index(f"function {name}(")
    nxt = re.search(r"\n    (?:public|private|protected)(?: static)? function ", text[start + 1:])
    return text[start:start + 1 + nxt.start()] if nxt else text[start:]


def _js_function(text, name):
    start = text.index(f"function {name}(")
    return text[start:text.index("\n    }\n", start)]


def test_retry_texts_do_not_promise_an_immediate_run():
    single = read(REVIEW_JS / "single.js")
    retry = _js_function(single, "retryFailures")
    for claim in ("فوراً", "سيبدأ تشغيله", "تصفير وجدولة", "بلشت", "عم تنعالج"):
        assert claim not in retry, claim
    # the confirmation and the success message both say: back to the queue, nothing starts from here
    assert "رح نرجّع" in retry and "ما رح يبلش أي تشغيل من هون" in retry
    assert "للطابور" in retry and "ما بتبلش معالجتها لحالها" in retry and "شغّل التشغيل" in retry
    # the list-level button and the workspace alert say it too
    app = read(REVIEW_JS / "app.js")
    assert "إعادة المحاولة بترجّعها للطابور، وما بتبلش معالجتها لحالها." in app
    assert "إعادة المحاولة بترجّعه للطابور، وبينبحث عنه بالتشغيل الجاي." in single

    retry_api = _method(read(API), "retryFailures")
    assert "طابور الأتمتة" in retry_api and "من صفحة «التشغيل»" in retry_api
    assert "التحكم والأتمتة الجماعية" not in retry_api          # the old page name is gone
    assert "فوراً" not in retry_api


FAILED = {"row_number": 6, "product_name": "Healthy Farms Fresh Eggs 30 pcs", "brand": "Healthy Farms", "barcode": "",
          "sku_key": "key-eggs", "size": "30 pcs", "product_name_ar": "بيض", "brand_ar": "", "category": "Fresh",
          "existing_image_link": "", "needs_review": False, "has_error": True,
          "error_message": "NO_RESULTS: No acceptable image found (NO_RESULTS)"}
BROKEN = dict(FAILED, row_number=8, product_name="Broken Juice 1L", sku_key="key-juice", barcode="6291003000017",
              error_message="SEARCH_ERROR: search raised an exception")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_single_retry_says_the_row_went_back_to_the_queue(tmp_path):
    fixture = {"products": [FAILED, BROKEN],
               "queue": {"status": "success", "ready_for_review": 0, "rows": [
                   {"row_number": 6, "sku_key": "key-eggs", "status": "failed", "failure_code": "NO_RESULTS",
                    "product_name": FAILED["product_name"], "brand": FAILED["brand"]},
                   {"row_number": 8, "sku_key": "key-juice", "status": "failed", "failure_code": "SEARCH_ERROR",
                    "product_name": BROKEN["product_name"], "brand": BROKEN["brand"]}]},
               "retry": {"status": "success", "requeued": 1, "not_found": 0,
                         "message": "أُضيفت المنتجات إلى طابور الأتمتة (العدد: 1)، ولا تبدأ معالجتها من هنا"}}
    out = run(r"""
await boot({ filter: 'failed' });
out.visible = S().items.filter(it => R.filterItems([it], 'failed', '').length).map(it => it.product.row_number);
out.text = wsText();
ws().querySelector('.rv-retry').click();
await flush();
out.body = requests('/api/failures/retry')[0].body;
out.toasts = toasts.map(t => t.text);
out.confirms = confirms.length;
""", tmp_path, fixture)
    assert out["visible"] == [8]                       # NO_RESULTS is «ما انلقت»; SEARCH_ERROR is «أعطال»
    assert "عطل:" in out["text"] and "صار خطأ أثناء البحث." in out["text"]
    assert "SEARCH_ERROR" not in out["text"].replace("SEARCH_ERROR: search raised an exception", "")
    assert out["body"] == {"barcodes": ["6291003000017"]}
    assert out["confirms"] == 0                        # one explicit button for one row
    assert out["toasts"] == ["رجعت منتج واحد للطابور. ما بتبلش معالجتها لحالها: شغّل التشغيل من صفحة «التشغيل»."]
