"""scripts/preview_normalizer.py: the owner's before/after look at the reading of abbreviated sheet names.

The model is a fake and every socket is blocked: the preview reads rows (a CSV here; the sheet through the read-only
wrapper of smoke_live), asks the reading once per new product, prints today's queries and the queries with the
reading, and ends with the calls, tokens and cost. It never searches and never writes to the sheet.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from catalog_match import normalizer
from catalog_match.verifiers import spend as spend_mod

REPO = Path(__file__).resolve().parents[1]
READINGS = {
    "SUP/T": {"brand": "Super Tasty", "product_type": "tuna", "variant": "white meat", "size": "185g", "pack": "",
              "expanded_name": "Super Tasty White Meat Solid Tuna in Salt Water 185g", "confidence": 0.9},
    "AMERICAN GOLD": {"brand": "American Gold", "product_type": "tuna", "variant": "light meat", "size": "160g",
                      "pack": "", "expanded_name": "American Gold Light Meat Tuna Flakes in Sunflower Oil 160g",
                      "confidence": 0.8},
}


def _load():
    spec = importlib.util.spec_from_file_location("preview_normalizer", REPO / "scripts" / "preview_normalizer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeClient:
    model = normalizer.MODEL
    prompts = []

    def __init__(self, *a, **k):
        self.api_key = "-".join(("tst", "gem", "1b2c"))

    def generate(self, prompt):
        FakeClient.prompts.append(prompt)
        usage = {"provider": "gemini", "model": self.model, "input_tokens": 340, "output_tokens": 70,
                 "estimated": False}
        for brand, reading in READINGS.items():
            if f'Sheet brand column: "{brand}"' in prompt:
                return dict(reading), usage
        raise normalizer.NormalizerError("bad_reply", usage)


@pytest.fixture
def wired(monkeypatch, offline, tmp_path):
    FakeClient.prompts = []
    monkeypatch.setattr(normalizer, "GeminiNormalizerClient", FakeClient)
    monkeypatch.setattr(spend_mod, "MariaDbSpendStore", spend_mod.MemorySpendStore)
    rows = tmp_path / "rows.csv"
    rows.write_text("row,name,brand,size\n"
                    "2,SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM,SUP/T,\n"
                    "3,AMERICAN GOLD LGT MEAT TUNA FLAKE IN SUNFLOWER OIL 160GM,AMERICAN GOLD,\n"
                    "4,SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM,SUP/T,\n"
                    "5,PLAIN WATER 500ML,AQUA,\n", encoding="utf-8")
    return {"script": _load(), "rows": rows}


def test_the_preview_prints_before_and_after_and_the_cost(wired, tmp_path, capsys):
    out_file = tmp_path / "preview.json"
    code = wired["script"].main(["--rows", "4", "--rows-file", str(wired["rows"]), "--no-cache",
                                 "--json", str(out_file)])
    assert code == 0
    text = capsys.readouterr().out
    assert "=== row 3: AMERICAN GOLD LGT MEAT TUNA FLAKE IN SUNFLOWER OIL 160GM" in text
    assert "Q3  AMERICAN GOLD LGT MEAT TUNA FLAKE IN SUNFLOWER OIL 160g (" in text and "stores)" in text
    assert "N1  AMERICAN GOLD Light Meat Tuna Flakes in Sunflower Oil 160g (" in text and "<- new" in text
    assert "NB  Super Tasty White Meat Solid Tuna in Salt Water 185g   <- only when no listing names the brand" in text
    assert "reading: none (bad_reply)" in text                                   # row 5: today's queries only
    doc = json.loads(out_file.read_text(encoding="utf-8"))
    s = doc["summary"]
    # rows 2 and 4 are the same product: one call, the second reading comes from the memo
    assert len(FakeClient.prompts) == 3 and s["new_readings"] == 2 and s["cached"] == 1
    assert s["billed_calls"] == 3 and (s["input_tokens"], s["output_tokens"]) == (1020, 210)
    assert s["usd"] == pytest.approx(3 * normalizer.usd_of(normalizer.MODEL, 340, 70))
    assert s["n1_rows"] == 1 and s["rescue_rows"] == 2
    assert f"estimated cost ${s['usd']:.4f}" in text
    assert [r["row"] for r in doc["rows"]] == [2, 3, 4, 5]


def test_the_preview_stops_at_its_budget_and_without_a_key(wired, capsys, monkeypatch):
    code = wired["script"].main(["--rows", "4", "--rows-file", str(wired["rows"]), "--no-cache", "--max-usd", "0"])
    assert code == 0 and FakeClient.prompts == []
    assert "reading: none (budget)" in capsys.readouterr().out
    monkeypatch.setattr(FakeClient, "__init__", lambda self, *a, **k: setattr(self, "api_key", ""))
    assert wired["script"].main(["--rows", "2", "--rows-file", str(wired["rows"]), "--no-cache"]) == 2
    assert "No Gemini key" in capsys.readouterr().out


def test_the_sheet_is_opened_read_only(wired, monkeypatch, capsys):
    import smoke_live

    class Sheet:
        def get_all_values(self):
            return [["Product Name", "Brand"], ["SUP/T WT/MEAT SOLIDTUNA SALTWATER 185GM", "SUP/T"]]

        def update_cell(self, *a):            # the ReadOnly wrapper refuses it before it is reached
            raise AssertionError("the preview wrote to the sheet")

    class Book:
        def worksheet(self, title):
            raise KeyError(title)

    opened = []
    monkeypatch.setattr(smoke_live, "open_sheet_read_only",
                        lambda: opened.append(1) or (smoke_live.ReadOnly(Book()), smoke_live.ReadOnly(Sheet())))
    assert wired["script"].main(["--rows", "1"]) == 0 and opened == [1]
    out = capsys.readouterr().out
    assert "sheet rows 2-2" in out and "NB  Super Tasty" in out
    with pytest.raises(PermissionError):
        smoke_live.ReadOnly(Sheet()).update_cell(1, 1, "x")


def test_the_site_clause_is_shortened_in_one_pass():
    """The store clause the query plan appends reads '(N stores)'. It is parsed by hand: the regular expression it
    replaced (an optional ' OR ' between items) backtracked exponentially on a long clause that does not match
    (CodeQL py/redos on PR #30)."""
    import time

    pn = _load()
    assert pn.short("tomex fries 6mm (site:carrefouruae.com OR site:noon.com OR site:luluhypermarket.com)") \
        == "tomex fries 6mm (3 stores)"
    assert pn.short("almarai milk 1l  (site:noon.com)") == "almarai milk 1l (1 stores)"
    for unchanged in ("no clause", "", None, "x (site:)", "x (site:a b)", "x (site:a.com OR site:b.com",
                      "x (site:a.com OR site:b.com) tail", "x (site:a.com OR )"):
        assert pn.short(unchanged) == unchanged
    started = time.monotonic()
    assert pn.short("(site:" + "!site:" * 20000) == "(site:" + "!site:" * 20000
    assert time.monotonic() - started < 1.0
