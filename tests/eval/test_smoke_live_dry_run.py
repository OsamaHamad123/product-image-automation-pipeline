"""scripts/smoke_live.py end to end, offline: the owner's pre-merge dry run must work on a real-shaped sheet.

The sheet, providers, fetcher and Gemini are the golden-fixture doubles; everything else (row reading with
the production header synonyms, brand-mapping tab parsing, the v2 pipeline, the report and --json) is the
real script. Its decisions must equal what the pipeline decides for the same SKUs, nothing may be written
to the sheet, and no socket may be opened.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

import runners
from catalog_match import pipeline as cm_pipeline
from catalog_match import providers as cm_providers
from catalog_match import verify as cm_verify

pytestmark = pytest.mark.eval

REPO = Path(__file__).resolve().parents[2]
SKU_IDS = ["uae-001-almarai-full-fat-milk-1l", "uae-006-al-rawabi-fresh-milk-full-fat-2l",
           "uae-041-arabic-only-al-foah-khalas-dates-1kg"]
# Real-sheet quirks: trailing spaces, mixed case, Arabic columns, an unrelated column in between.
HEADERS = ["Barcode ", "Product Name", "Brand", "Notes", "SIZE", "Category", "Product Name Arabic",
           "Brand Arabic", "Drive Image Link"]


def _load_script():
    spec = importlib.util.spec_from_file_location("smoke_live", REPO / "scripts" / "smoke_live.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeWorksheet:
    def __init__(self, values):
        self._values = values
        self.writes = []

    def get_all_values(self):
        return [list(r) for r in self._values]

    def update_cell(self, *a, **k):  # must never be reached through the ReadOnly wrapper
        self.writes.append(a)


class FakeSpreadsheet:
    def __init__(self, tabs):
        self._tabs = tabs

    def worksheet(self, title):
        if title not in self._tabs:
            raise KeyError(title)
        return self._tabs[title]


def _mapping_rows(mappings):
    rows = [["Brand", "Synonyms", "Excluded Competitors", "Sub-brands", "Official domains"]]
    for entry in mappings.values():
        rows.append([entry.get("brand", ""), ", ".join(entry.get("synonyms", [])),
                     ", ".join(entry.get("excluded_competitors", [])), ", ".join(entry.get("sub_brands", [])),
                     ", ".join(entry.get("official_domains", []))])
    return rows


class _BySku:
    """Routes each call to the fixture double of the SKU the spec belongs to."""

    def __init__(self, skus, make):
        self._by_name = {runners.sku_row(s)["name"]: make(s) for s in skus}

    def of(self, spec):
        return self._by_name[spec.raw_name]


@pytest.fixture
def wired(monkeypatch, golden, cassette):
    by_id = {s["id"]: s for s in golden["skus"]}
    skus = [by_id[i] for i in SKU_IDS]
    models = runners._v2_modules()[2]
    mappings = runners.load_mappings()

    values = [HEADERS]
    for sku in skus:
        r = runners.sku_row(sku)
        values.append([r["barcode"], r["name"], r["brand"], "note", r["size"], r["category"], r["name_ar"],
                       r["brand_ar"], ""])
    worksheet = FakeWorksheet(values)
    spreadsheet = FakeSpreadsheet({"Brands Mapping": FakeWorksheet(_mapping_rows(mappings))})

    script = _load_script()
    monkeypatch.setattr(script, "open_sheet_read_only",
                        lambda: (script.ReadOnly(spreadsheet), script.ReadOnly(worksheet)))

    provider_lists = _BySku(skus, lambda s: runners.build_providers(models, s))
    names = [p.name for p in runners.build_providers(models, skus[0])]

    class Dispatch:
        def __init__(self, index):
            first = runners.build_providers(models, skus[0])[index]
            self.name, self.sanctioned, self._i = first.name, first.sanctioned, index
            self.fallback = getattr(first, "fallback", False)

        def search(self, query, hl, spec):
            return provider_lists.of(spec)[self._i].search(query, hl, spec)

        def lookup(self, spec):
            return provider_lists.of(spec)[self._i].lookup(spec)

    fetchers = _BySku(skus, lambda s: runners.FixtureFetcher(models, s))
    verifiers = _BySku(skus, lambda s: runners.CassetteVerifier(models, s, cassette, "normal"))

    class Fetcher:
        def fetch(self, cands, spec):
            return fetchers.of(spec).fetch(cands, spec)

    class Verifier:
        def verify(self, spec, images):
            return verifiers.of(spec).verify(spec, images)

    monkeypatch.setattr(cm_providers, "default_providers", lambda *a, **k: [Dispatch(i) for i in range(len(names))])
    monkeypatch.setattr(cm_pipeline, "_default_fetcher", lambda: Fetcher())
    monkeypatch.setattr(cm_verify, "GeminiVerifier", lambda *a, **k: Verifier())
    return {"script": script, "skus": skus, "worksheet": worksheet, "mappings": mappings}


def test_dry_run_reads_the_sheet_and_matches_the_pipeline(wired, cassette, tmp_path, capsys):
    out = tmp_path / "smoke.json"
    with runners._v2_settings(False), runners.network_blocked() as attempts:
        code = wired["script"].main(["--rows", "2-4", "--dry-run", "--json", str(out)])
    assert code == 0
    assert runners.outbound_attempts(attempts) == []
    assert wired["worksheet"].writes == []

    results = json.loads(out.read_text(encoding="utf-8"))
    assert [r["row"] for r in results] == [2, 3, 4]
    assert not [r for r in results if "error" in r], results
    for r, sku in zip(results, wired["skus"]):
        expected = runners.run_v2(sku, cassette, mappings=wired["mappings"])
        assert r["decision"] == expected.decision, (sku["id"], r["decision"], expected.decision)
        assert r["name"] == runners.sku_row(sku)["name"]
    printed = capsys.readouterr().out
    assert "3 rows | decisions" in printed and "DECISION" in printed


def test_rows_are_read_with_the_production_header_synonyms(wired):
    rows = wired["script"].read_sheet_rows(wired["worksheet"], [2, 4])
    first = runners.sku_row(wired["skus"][0])
    assert rows[0]["barcode"] == first["barcode"] and rows[0]["size"] == first["size"]
    arabic = runners.sku_row(wired["skus"][2])
    assert rows[1]["name_ar"] == arabic["name_ar"] and rows[1]["brand_ar"] == arabic["brand_ar"]


def test_read_only_wrapper_refuses_writes(wired):
    ws = wired["script"].ReadOnly(wired["worksheet"])
    with pytest.raises(PermissionError):
        ws.update_cell(2, 9, "x")
    assert wired["worksheet"].writes == []


def test_sheet_without_a_name_column_fails_loudly(wired):
    ws = FakeWorksheet([["SKU", "Price"], ["1", "2"]])
    with pytest.raises(SystemExit):
        wired["script"].read_sheet_rows(ws, [2])
