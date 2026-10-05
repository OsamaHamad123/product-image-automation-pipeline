"""The barcode a store page stated, kept with the approval of a sheet row that has none.

None of the 120 products of the owner's last runs has a barcode in the sheet; store pages often state one in their
JSON-LD (pages.py: gtin_on_page). When an approval or an auto-publish uses an image whose page stated a valid,
globally unique GTIN and the sheet row has no valid barcode, the GTIN is stored with the approval
(resolved_products.page_gtin / page_gtin_url), shown on the review card with a copy button, exported per row and
listed by scripts/export_barcodes.py (two approved products with the same GTIN are both flagged). The sheet is
never written.
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from catalog_match import facade
from catalog_match.gtin import barcode_from_page, display_gtin
from catalog_match.models import Candidate, CandidateScore, RankedCandidate

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.append(str(ROOT / "scripts"))
DASH = ROOT / "dashboard"
NODE = shutil.which("node")
CORE_JS = DASH / "public" / "js" / "review" / "core.js"
SINGLE_JS = DASH / "public" / "js" / "review" / "single.js"

GTIN = "6281007035309"                 # valid check digit
OTHER_GTIN = "6281007035316"
IN_STORE = "2001234567893"             # restricted circulation (in-store code): never kept
BAD_CHECK = "6281007035308"
SKU_A, SKU_B, SKU_C = "pgtin-test-a", "pgtin-test-b", "pgtin-test-c"
PAGE = "https://www.carrefouruae.com/mafuae/en/tuna/golden-prize-tuna-185g/p/123"


def test_only_a_valid_global_gtin_is_kept_and_only_for_a_row_without_one():
    assert barcode_from_page("", GTIN) == GTIN
    assert barcode_from_page(None, "0" + GTIN) == GTIN                 # GTIN-14 with indicator 0 -> EAN-13
    assert barcode_from_page("N/A", GTIN) == GTIN                      # not a valid GTIN in the sheet: kept
    assert barcode_from_page("12345", GTIN) == GTIN
    assert barcode_from_page(OTHER_GTIN, GTIN) is None                 # the sheet has a valid barcode
    assert barcode_from_page("", BAD_CHECK) is None
    assert barcode_from_page("", IN_STORE) is None
    assert barcode_from_page("", "") is None and barcode_from_page("", None) is None
    assert display_gtin("96385074") == "96385074"                       # GTIN-8 stays 8 digits


def _rc(gtin):
    return RankedCandidate(candidate=Candidate(image_url="https://a.ae/x.jpg", page_url=PAGE, gtin_on_page=gtin),
                           score=CandidateScore(tier=1))


def test_the_candidate_evidence_carries_the_pages_gtin():
    assert facade.evidence(_rc(GTIN))["page_gtin"] == GTIN
    assert facade.evidence(_rc("0" + GTIN))["page_gtin"] == GTIN
    assert facade.evidence(_rc(IN_STORE))["page_gtin"] is None
    assert facade.evidence(_rc(BAD_CHECK))["page_gtin"] is None
    assert facade.evidence(_rc(None))["page_gtin"] is None


def test_the_worker_finds_the_published_candidates_page_barcode():
    import main

    best = {"url": "https://a.ae/x.jpg", "page_url": PAGE,
            "candidates": [{"url": "https://a.ae/y.jpg", "evidence": {"page_gtin": OTHER_GTIN}},
                           {"url": "https://a.ae/x.jpg", "page_url": PAGE, "evidence": {"page_gtin": GTIN}}]}
    assert main.page_barcode(best, "") == (GTIN, PAGE)
    assert main.page_barcode(best, OTHER_GTIN) == (None, None)
    assert main.page_barcode(dict(best, candidates=[]), "") == (None, None)


def test_an_auto_publish_stores_it_with_the_approval(race):
    from test_worker_wiring import _best, _task

    main, rec = race
    best = _best("AUTO_PUBLISH", page_url=PAGE)
    best["candidates"][0]["evidence"]["page_gtin"] = GTIN
    task = dict(_task(), worker_id="w1#claim")
    assert main.auto_approve_product(task, best, object(), 5, sku_key="sku-laban-up") == "published"
    saved = rec["resolution"][0]
    assert (saved["page_gtin"], saved["page_gtin_url"]) == (GTIN, PAGE)
    assert rec["sheet"] == [("link", "https://res/a.png"), ("md",)]      # the sheet gets the link only

    rec["resolution"].clear()
    task = dict(_task(), worker_id="w1#claim", barcode=OTHER_GTIN)      # the sheet has a barcode: nothing kept
    assert main.auto_approve_product(task, best, object(), 5, sku_key="sku-laban-up") == "published"
    assert "page_gtin" not in rec["resolution"][0]


from test_worker_wiring import race  # noqa: E402,F401  (the real auto-publish path with stage doubles)


# ---------------------------------------------------------------------------
# The database, the bridge and scripts/export_barcodes.py
# ---------------------------------------------------------------------------

def _sql(db, statement, params=()):
    conn = db.get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement, params)
            rows = cur.fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


@pytest.fixture
def db(mariadb_or_skip):
    def wipe():
        _sql(mariadb_or_skip, "DELETE FROM resolved_products WHERE sku_key IN (%s, %s, %s)", (SKU_A, SKU_B, SKU_C))
        _sql(mariadb_or_skip, "DELETE FROM automation_queue WHERE sku_key IN (%s, %s, %s)", (SKU_A, SKU_B, SKU_C))
        _sql(mariadb_or_skip, "DELETE FROM curation_candidates WHERE sku_key IN (%s, %s, %s)", (SKU_A, SKU_B, SKU_C))
    wipe()
    yield mariadb_or_skip
    wipe()


def _approve(db, sku, gtin, status="human_approved", name="GOLDEN PRIZE TUNA 185G"):
    db.save_product_resolution("", name, "GOLDEN PRIZE", "https://a.ae/" + sku + ".jpg", "https://res/" + sku + ".png",
                               verification_status=status, approved_by="human", sku_key=sku,
                               page_gtin=gtin, page_gtin_url=PAGE if gtin else None)


def test_the_approval_keeps_the_barcode_and_the_script_flags_duplicates(db, tmp_path):
    import export_barcodes

    _approve(db, SKU_A, GTIN)
    _approve(db, SKU_B, GTIN, status="auto_verified", name="GOLDEN PRIZE TUNA 185G X2")
    _approve(db, SKU_C, OTHER_GTIN)
    _sql(db, "INSERT INTO automation_queue (`row_number`, product_name, brand, status, sku_key) "
             "VALUES (%s, %s, %s, 'completed', %s)", (947012, "GOLDEN PRIZE TUNA 185G", "GOLDEN PRIZE", SKU_A))
    found = {r["sku_key"]: r for r in db.get_page_barcodes() if r["sku_key"] in (SKU_A, SKU_B, SKU_C)}
    assert (found[SKU_A]["page_gtin"], found[SKU_A]["page_gtin_url"], found[SKU_A]["row_number"]) == (GTIN, PAGE, 947012)
    rows = [r for r in export_barcodes.barcode_rows(found.values())]
    by_name = {(r["name"], r["page_gtin"]): r for r in rows}
    assert by_name[("GOLDEN PRIZE TUNA 185G", GTIN)]["duplicate_gtin"] == "yes"
    assert by_name[("GOLDEN PRIZE TUNA 185G X2", GTIN)]["duplicate_gtin"] == "yes"
    assert by_name[("GOLDEN PRIZE TUNA 185G", OTHER_GTIN)]["duplicate_gtin"] == ""
    assert rows[0]["row"] == 947012 and {r["approved"] for r in rows} == {"human", "auto"}

    # a later approval of another image without a page barcode clears it; a superseded approval is not listed
    _approve(db, SKU_C, None)
    assert SKU_C not in {r["sku_key"] for r in db.get_page_barcodes()}
    db.supersede_resolution(SKU_B)
    assert SKU_B not in {r["sku_key"] for r in db.get_page_barcodes()}

    out = tmp_path / "barcodes.csv"
    assert export_barcodes.main(["--out", str(out)]) == 0
    with open(out, encoding="utf-8-sig", newline="") as fh:
        lines = list(csv.DictReader(fh))
    assert list(lines[0].keys()) == list(export_barcodes.COLUMNS)
    mine = [r for r in lines if r["name"].startswith("GOLDEN PRIZE")]
    assert mine == [{"row": "947012", "name": "GOLDEN PRIZE TUNA 185G", "brand": "GOLDEN PRIZE", "page_gtin": GTIN,
                     "source_page": PAGE, "approved": "human", "duplicate_gtin": ""}]


def test_the_bridge_reads_the_stored_candidate_or_what_the_screen_showed(db):
    import cli_bridge

    row = 947013
    cands = [{"url": "https://a.ae/one.jpg", "title": "Golden Prize Tuna 185g", "status": "preselected",
              "page_url": PAGE, "reasons": [], "evidence": {"tier": 1, "page_gtin": GTIN}}]
    assert db.save_curation_candidates(row, "GOLDEN PRIZE TUNA 185G", "GOLDEN PRIZE", cands, sku_key=SKU_A)
    assert cli_bridge._approval_page_gtin({}, row, SKU_A, "https://a.ae/one.jpg", "") == (GTIN, PAGE)
    assert cli_bridge._approval_page_gtin({}, row, SKU_A, "https://a.ae/one.jpg", OTHER_GTIN) == (None, None)
    # the catalog screen's live search stores no candidates: what it showed, checked again here
    sent = {"page_gtin": OTHER_GTIN, "page_url": PAGE + "?x=1"}
    assert cli_bridge._approval_page_gtin(sent, row, SKU_A, "https://a.ae/two.jpg", "") == (OTHER_GTIN, PAGE + "?x=1")
    assert cli_bridge._approval_page_gtin({"page_gtin": BAD_CHECK}, row, SKU_A, "https://a.ae/two.jpg", "") == (None, None)

    record = cli_bridge._human_decision("", "GOLDEN PRIZE TUNA 185G", "GOLDEN PRIZE", "https://a.ae/one.jpg", "human",
                                        SKU_A, row, [row], GTIN, PAGE)
    record({"status": "published", "link": "https://res/one.png"})
    approval = db.get_cached_product(sku_key=SKU_A)
    assert (approval["verification_status"], approval["page_gtin"], approval["page_gtin_url"]) == (
        "human_approved", GTIN, PAGE)


def test_the_published_response_names_it():
    import cli_bridge

    res = {"status": "published", "link": "https://res/x.png", "sheet_value": "https://res/x.png", "isolated": True}
    assert cli_bridge._published_response(res, "k", 3, page_gtin=GTIN)["page_gtin"] == GTIN
    assert "page_gtin" not in cli_bridge._published_response(res, "k", 3)


# ---------------------------------------------------------------------------
# The review screen
# ---------------------------------------------------------------------------

def test_the_screen_shows_it_with_a_copy_button_and_sends_it():
    api = (DASH / "app" / "Http" / "Controllers" / "ApiController.php").read_text(encoding="utf-8")
    assert "'page_gtin'," in api.split("SELECT_FIELDS = [", 1)[1].split("];", 1)[0]
    products = (DASH / "app" / "Http" / "Controllers" / "ProductController.php").read_text(encoding="utf-8")
    assert "$prod['page_gtin']" in products
    single = SINGLE_JS.read_text(encoding="utf-8")
    assert single.count("pageGtinLine(") >= 4          # the definition, the selected card, the approved and final ones
    assert "pageGtinLine(R.pageGtinOf(item.product, pick))" in single and "pageGtinLine(done.pageGtin)" in single
    assert "pageGtinLine(item.product.page_gtin)" in single and "navigator" in single
    assert "الباركود من صفحة المتجر: " in CORE_JS.read_text(encoding="utf-8")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_screen_rule_for_showing_it():
    script = (
        "globalThis.window = globalThis;\n" + CORE_JS.read_text(encoding="utf-8") + "\n"
        + "const R = globalThis.LaqtaReview;\n"
        + f"const c = {{ url: 'https://a.ae/x.jpg', evidence: {{ page_gtin: '{GTIN}' }}, reasons: [], warnings: [] }};\n"
        + "const out = {\n"
          "  none: R.pageGtinOf({ barcode: '' }, c),\n"
          "  gap: R.pageGtinOf({ barcode: '123', sheet_issues: [{ key: 'no_barcode' }] }, c),\n"
          "  has: R.pageGtinOf({ barcode: '6281007035316', sheet_issues: [] }, c),\n"
          "  empty: R.pageGtinOf({ barcode: '' }, { evidence: {} }),\n"
          "  sent: R.selectBody({ product_name: 'X', row_number: 2 }, c).page_gtin,\n"
          "  label: R.PAGE_GTIN_LABEL };\n"
        + "console.log(JSON.stringify(out));\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60, encoding="utf-8")
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stderr[-2000:]
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out == {"none": GTIN, "gap": GTIN, "has": "", "empty": "", "sent": GTIN,
                   "label": "الباركود من صفحة المتجر: "}
