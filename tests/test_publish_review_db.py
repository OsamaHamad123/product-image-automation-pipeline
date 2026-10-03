"""Publish & review backend on the real MariaDB test database (skips when MariaDB is down).

Sockets may reach the MariaDB server only; the sheet, the image processing and the upload are recorders.
Rows and keys are shifted clear of the other database tests.
"""

import json
import os
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest

ROWS = (930001, 930002, 930003, 930004)
SKUS = ("p4-sku-p", "p4-sku-q", "p4-sku-r", "p4-sku-s")
ROOT = Path(__file__).resolve().parents[1]
PHP = shutil.which("php")


@pytest.fixture
def db_only(monkeypatch):
    real_connect = socket.socket.connect
    hosts = {"127.0.0.1", "::1", "localhost", os.getenv("DB_HOST", "127.0.0.1")}
    port = int(os.getenv("DB_PORT", "3306"))

    def guarded(sock, address):
        if isinstance(address, tuple) and address[0] in hosts and address[1] == port:
            return real_connect(sock, address)
        raise OSError(f"network access is blocked in this test: {address!r}")

    monkeypatch.setattr(socket.socket, "connect", guarded)
    return guarded


def _cleanup(db):
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        marks = ", ".join(["%s"] * len(ROWS))
        keys = ", ".join(["%s"] * len(SKUS))
        cur.execute(f"DELETE FROM automation_queue WHERE `row_number` IN ({marks})", ROWS)
        cur.execute(f"DELETE FROM curation_candidates WHERE `row_number` IN ({marks}) OR sku_key IN ({keys})",
                    ROWS + SKUS)
        cur.execute(f"DELETE FROM resolved_products WHERE sku_key IN ({keys})", SKUS)
        cur.execute(f"DELETE FROM rejected_images WHERE sku_key IN ({keys})", SKUS)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def db(db_only, mariadb_or_skip):
    db = mariadb_or_skip
    _cleanup(db)
    yield db
    _cleanup(db)


def _urls(db, row, sku):
    return sorted(c["image_url"] for c in db.get_curation_candidates(row, sku_key=sku))


# ---------------------------------------------------------------------------
# Item 6: saving candidates never wipes another product's candidates after a row shift
# ---------------------------------------------------------------------------

def test_worker_save_after_a_row_shift_keeps_the_other_products_candidates(db):
    """P was searched at row 1; the owner inserted a row, so Q is now at row 1 and P at row 2. Q's search saves
    its candidates at row 1: P's candidates (stored at row 1 under P's key) must survive."""
    p_row, p_sku, q_sku = ROWS[0], SKUS[0], SKUS[1]
    assert db.save_curation_candidates(p_row, "Product P", "Brand", [{"url": "https://x/p.jpg"}], sku_key=p_sku)
    assert db.save_curation_candidates(p_row, "Product Q", "Brand", [{"url": "https://x/q.jpg"}], sku_key=q_sku)
    assert _urls(db, ROWS[1], p_sku) == ["https://x/p.jpg"]
    assert _urls(db, p_row, q_sku) == ["https://x/q.jpg"]


def test_worker_save_replaces_the_products_own_and_legacy_candidates(db):
    sku = SKUS[2]
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        # a legacy candidate (no key) stored at this row before the upgrade
        cur.execute("INSERT INTO curation_candidates (`row_number`, product_name, image_url, status) "
                    "VALUES (%s, 'Product R', 'https://x/legacy.jpg', 'eligible')", (ROWS[2],))
        conn.commit()
    finally:
        conn.close()
    assert db.save_curation_candidates(ROWS[3], "Product R", "Brand", [{"url": "https://x/r-old.jpg"}], sku_key=sku)
    # the product moved to ROWS[2]: its new run replaces its own rows (at any row) and the legacy row there
    assert db.save_curation_candidates(ROWS[2], "Product R", "Brand", [{"url": "https://x/r-new.jpg"}], sku_key=sku)
    assert _urls(db, ROWS[2], sku) == ["https://x/r-new.jpg"]
    assert db.get_curation_candidates(ROWS[2]) == db.get_curation_candidates(ROWS[2], sku_key=sku)


# ---------------------------------------------------------------------------
# Item 6: the dashboard's save-candidates endpoint uses the same delete rule (run under PHP with a fake DB)
# ---------------------------------------------------------------------------

PHP_STUBS = r"""
namespace {
    class FakeQuery {
        public $table; public $conds = [];
        public function __construct($table) { $this->table = $table; }
        private function add($bool, $col, $op, $val) {
            if ($col instanceof \Closure) { $sub = new FakeQuery($this->table); $col($sub); $this->conds[] = [$bool, 'group', $sub]; }
            else { $this->conds[] = [$bool, $op, $col, $val]; }
            return $this;
        }
        public function where($col, $val = null) { return $this->add('and', $col, '=', $val); }
        public function orWhere($col, $val = null) { return $this->add('or', $col, '=', $val); }
        public function whereNull($col) { return $this->add('and', $col, 'null', null); }
        public function matches($row) {
            $groups = [[]];
            foreach ($this->conds as $c) { if ($c[0] === 'or' && $groups[count($groups) - 1]) { $groups[] = []; } $groups[count($groups) - 1][] = $c; }
            foreach ($groups as $g) {
                $ok = true;
                foreach ($g as $c) {
                    if ($c[1] === 'group') { $hit = $c[2]->matches($row); }
                    elseif ($c[1] === 'null') { $hit = !isset($row[$c[2]]) || $row[$c[2]] === null; }
                    else { $hit = isset($row[$c[2]]) && (string) $row[$c[2]] === (string) $c[3]; }
                    $ok = $ok && $hit;
                }
                if ($ok && $g) { return true; }
            }
            return false;
        }
        public function delete() {
            $kept = [];
            foreach (\Illuminate\Support\Facades\DB::$rows as $r) { if (!$this->matches($r)) { $kept[] = $r; } }
            \Illuminate\Support\Facades\DB::$rows = $kept;
        }
        public function insert($rows) { foreach ($rows as $r) { \Illuminate\Support\Facades\DB::$rows[] = $r; } }
    }
    function response() { return new class { public function json($data, $status = 200) { return new \Illuminate\Http\JsonResponse($data, $status); } }; }
    function now() { return '2026-10-03 12:00:00'; }
}
namespace Illuminate\Support\Facades {
    class DB {
        public static $rows = [];
        public static function table($t) { return new \FakeQuery($t); }
        public static function transaction($fn) { return $fn(); }
    }
    class Schema {
        public static function hasTable($t) { return true; }
        public static function hasColumn($t, $c) { return true; }
        public static function getColumnListing($t) {
            return ['id', 'row_number', 'product_name', 'brand', 'image_url', 'title', 'width', 'height', 'source_domain',
                    'is_selected', 'status', 'created_at', 'sku_key', 'run_id', 'reasons_json', 'evidence_json',
                    'vlm_json', 'identity_tier', 'content_sha256', 'page_url'];
        }
    }
}
namespace Illuminate\Http {
    class Request { public $data; public function __construct($d) { $this->data = $d; } public function validate($r) { return $this->data; } }
    class JsonResponse { public $data; public $status; public function __construct($d, $s) { $this->data = $d; $this->status = $s; } }
}
namespace App\Http\Controllers {
    class Controller {}
    class ProductController { public static function forgetProductCaches() {} }
}
"""


@pytest.mark.skipif(PHP is None, reason="php is not installed")
def test_dashboard_save_candidates_after_a_row_shift_keeps_the_other_products_candidates():
    controller = str(ROOT / "dashboard" / "app" / "Http" / "Controllers" / "CurationController.php").replace("\\", "/")
    service = str(ROOT / "dashboard" / "app" / "Services" / "CandidateRow.php").replace("\\", "/")
    stored = [
        # P's candidates, stored when P was at row 8 (P is now at row 9)
        {"row_number": 8, "sku_key": "sku-p", "image_url": "p1.jpg"},
        # Q's older candidates at another row, and a legacy (unkeyed) row at row 8
        {"row_number": 3, "sku_key": "sku-q", "image_url": "q-old.jpg"},
        {"row_number": 8, "sku_key": None, "image_url": "legacy.jpg"},
        # a legacy row elsewhere
        {"row_number": 5, "sku_key": None, "image_url": "other-legacy.jpg"},
    ]
    body = {"row_number": 8, "product_name": "Q", "brand": "B", "sku_key": "sku-q",
            "candidates": [{"url": "q-new.jpg", "status": "eligible"}]}
    unkeyed = {"row_number": 5, "product_name": "L", "brand": "B", "sku_key": "",
               "candidates": [{"url": "l-new.jpg", "status": "eligible"}]}
    script = f"""<?php
{PHP_STUBS}
namespace {{
    require '{service}';
    require '{controller}';
    \\Illuminate\\Support\\Facades\\DB::$rows = json_decode({json.dumps(json.dumps(stored))}, true);
    $c = new \\App\\Http\\Controllers\\CurationController();
    $first = $c->saveCandidates(new \\Illuminate\\Http\\Request(json_decode({json.dumps(json.dumps(body))}, true)));
    $second = $c->saveCandidates(new \\Illuminate\\Http\\Request(json_decode({json.dumps(json.dumps(unkeyed))}, true)));
    echo json_encode(['status' => [$first->status, $second->status],
                      'urls' => array_map(fn ($r) => $r['image_url'], \\Illuminate\\Support\\Facades\\DB::$rows)]);
}}
"""
    with tempfile.NamedTemporaryFile("w", suffix=".php", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        path = fh.name
    try:
        result = subprocess.run([PHP, path], capture_output=True, text=True, timeout=60)
    finally:
        os.unlink(path)
    assert result.returncode == 0, result.stdout + result.stderr
    out = json.loads(result.stdout)
    assert out["status"] == [200, 200]
    # P's candidates survive Q's save at P's old row; Q's own old row and the legacy row at row 8 are replaced;
    # without a key, only the legacy rows at that row number are replaced
    assert sorted(out["urls"]) == ["l-new.jpg", "p1.jpg", "q-new.jpg"]
