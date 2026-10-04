"""scripts/reroute_export.py on a two-row export (laqta_run/1 shape): every row is routed again offline with the
current rules from its recorded candidates; the report lists the rows that changed, says it is an approximation,
and the recorded settings are applied only while it runs."""

import importlib.util
import json
import os
import socket
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def reroute():
    spec = importlib.util.spec_from_file_location("reroute_export_under_test", REPO / "scripts" / "reroute_export.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError(f"network access attempted: {args!r}")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)


def entry(rank, title, page_url, image_url, reading, tier=2, source_class="generic", status="eligible",
          reasons=None, download_error=None, size="match"):
    lr = None if reading is None else {
        "decision": reading["decision"], "brand_match": reading["brand_match"], "size_match": reading["size_match"],
        "variant_match": reading["variant_match"], "pack_count": 1, "view": reading["view"]}
    vlm = None if reading is None else {"decision": reading["decision"], "view": reading["view"],
                                        "brand": reading["brand"], "variant": reading["variant"],
                                        "size": reading["size"]}
    return {
        "rank": rank, "status": status, "reasons": list(reasons or ()), "warnings": [], "tier": tier,
        "provider": "", "domain": page_url.split("/")[2].replace("www.", ""), "query_id": "", "sanctioned": None,
        "title": title, "image_url": image_url, "page_url": page_url,
        "evidence": f"tier={tier} size={size} brand=True variant_status=match coverage=1.0 source_class={source_class}",
        "vlm": vlm, "label_reader": lr,
        "size_evidence": {"size": size, "pack": "unknown", "gtin": None, "url_only_size_conflict": False},
        "variant_evidence": {"status": "match", "matched": ["protein", "flavour"], "found": {}},
        "source_class": source_class, "conflicts": [], "download_error": download_error,
        "width": None if download_error else 1000, "height": None if download_error else 1000,
    }


ZWAN_UNSURE = {"decision": "UNSURE", "brand_match": "yes", "size_match": "unsure", "variant_match": "yes",
               "view": "front_packshot", "brand": "ZWAN", "variant": "LUNCHEON MEAT BEEF HOT & SPICY", "size": ""}
AIDA_MATCH = {"decision": "MATCH", "brand_match": "yes", "size_match": "yes", "variant_match": "yes",
              "view": "front_packshot", "brand": "AIDA", "variant": "French Fries", "size": "1kg"}
AIDA_IMAGE = "https://bf1af2.akinoncloudcdn.com/products/2025/10/10/602053/07be3cde_size1920x1920_cropCenter.jpg"


def export():
    zwan = {
        "row": 71, "name": "ZWAN LUNCHEON MEAT BEEF HOT&SPICY 340GM", "brand": "ZWAN", "size": "", "barcode": "",
        "name_ar": "", "brand_ar": "", "category": "", "discovered_brands": [],
        "provider_calls": [{"provider": "serper", "query_id": "Q1", "status": "ok", "http_status": 200}],
        "decision": "REVIEW_UNSELECTED", "failure_code": None, "winner": None, "vlm_calls": 1,
        "top": [
            entry(1, "Buy Zwan Luncheon Meat Beef Hot & Spicy 340G - Shop On The Fresh Market",
                  "https://www.thefreshmarketdubai.com/products/zwan-luncheon-meat-beef-hot-spicy-340g",
                  "https://www.thefreshmarketdubai.com/cdn/shop/files/8714555001239.jpg", ZWAN_UNSURE,
                  reasons=["vlm:UNSURE"]),
            entry(2, "Zwan Luncheon Meat Beef Hot And Spicy 340g",
                  "https://palmyraorders.com/products/zwan-luncheon-meat-beef-hot-and-spicy-340-g",
                  "https://palmyraorders.com/cdn/shop/files/zwan-luncheon-meat.jpg", ZWAN_UNSURE,
                  reasons=["vlm:UNSURE"]),
            entry(3, "Zwan Luncheon Meat Beef Hot & Spicy 340g", "https://shop.example.com/products/zwan-beef-340g",
                  "https://shop.example.com/zwan.jpg", None, status="rejected", reasons=["download:timeout"],
                  download_error="timeout"),
        ],
    }
    aida = {
        "row": 2, "name": "AIDA FRENCH FRIES 1KG", "brand": "AIDA", "size": "", "barcode": "", "name_ar": "",
        "brand_ar": "", "category": "", "discovered_brands": [],
        "provider_calls": [{"provider": "serper", "query_id": "Q1", "status": "ok", "http_status": 200}],
        "decision": "REVIEW_PRESELECTED", "failure_code": None, "winner": AIDA_IMAGE, "vlm_calls": 1,
        "top": [entry(1, "Aida French Fries 1 kg", "https://gcc.luluhypermarket.com/en-ae/aida-french-fries-1-kg/p/1",
                      AIDA_IMAGE, AIDA_MATCH, tier=1, source_class="uae_retailer", status="preselected",
                      reasons=["vlm:MATCH", "preselected:vlm_match", "auto_blocked:auto_publish_disabled"])],
    }
    return {"format": "smoke_live/2", "export": "laqta_run/1",
            "meta": {"settings": {"values": {"AUTO_PUBLISH_ENABLED": False, "AUTO_PUBLISH_BRANDS": [],
                                             "GTIN_POLICY": "evidence"}}},
            "summary": {}, "rows": [zwan, aida]}


def test_each_row_is_routed_again_with_the_current_rules(reroute):
    results = {r["row"]: r for r in reroute.reroute(export())}
    zwan, aida = results[71], results[2]
    assert (zwan["old_decision"], zwan["new_decision"]) == ("REVIEW_UNSELECTED", "REVIEW_PRESELECTED")
    assert zwan["changed"] and zwan["new_reason"] == "preselected:tier2_corroborated"
    assert zwan["new_domain"] == "thefreshmarketdubai.com" and "vlm_unsure" in zwan["new_warnings"]
    assert (aida["old_decision"], aida["new_decision"]) == ("REVIEW_PRESELECTED", "REVIEW_PRESELECTED")
    assert not aida["changed"] and aida["new_winner"] == AIDA_IMAGE and aida["new_reason"] == "preselected:vlm_match"


def test_recorded_failures_stay_failures(reroute):
    from catalog_match import decide

    row = export()["rows"][0]
    spec = reroute.build_spec(row)
    ranked = [reroute.ranked_candidate(spec, e, i) for i, e in enumerate(row["top"])]
    assert spec.brand_conf == "sheet_raw" and spec.size.base_value == 340.0
    assert [rc.score.tier for rc in ranked] == [2, 2, 2]
    assert ranked[0].score.matched["size_fields"] == {"title": "match", "page_slug": "match"}
    with reroute.offline({}):
        out = decide.route(spec, ranked, reroute._verification(row, ranked), reroute._health(row), set())
    assert ranked[2].status == "rejected" and "download:timeout" in ranked[2].reasons
    assert out.winner is ranked[0]


def test_the_page_trust_is_read_again_from_this_checkouts_domain_lists(reroute):
    # live run 2026-10-04 19:33: the export recorded sharjahcoop.ae as 'generic'; it is a listed UAE retailer now.
    # A brand-official or a reviewed source depends on the brand's mappings: it stays as recorded.
    spec = reroute.build_spec(export()["rows"][0])
    sharjah = entry(1, "Zwan Luncheon Meat Beef Hot & Spicy 340g | Sharjah Co-operative Society",
                    "https://www.sharjahcoop.ae/en/zwan-luncheon-meat-beef-hot-spicy-340g/p/8714555001239",
                    "https://www.sharjahcoop.ae/medias/8714555001239-1200Wx1200H-001.jpg", ZWAN_UNSURE)
    official = entry(2, "Zwan Luncheon Meat Beef Hot & Spicy 340g", "https://www.zwan.example/beef-hot-spicy-340g",
                     "https://www.zwan.example/beef.jpg", ZWAN_UNSURE, source_class="official")
    scored = [reroute.ranked_candidate(spec, e, i).score.matched for i, e in enumerate((sharjah, official))]
    assert [(m["source_class"], m["source_trust"]) for m in scored] == [("uae_retailer", 3), ("official", 4)]


def test_report_names_the_approximation_and_the_changed_rows_only(reroute):
    results = reroute.reroute(export())
    text = reroute.format_report(results)
    assert text.startswith("APPROXIMATION")
    assert "ZWAN LUNCHEON MEAT BEEF HOT&SPICY 340GM" in text and "AIDA FRENCH FRIES" not in text
    assert "pre-selected 1 -> 2" in text and "gained a pick: 71" in text and "lost a pick: -" in text
    assert "AIDA FRENCH FRIES" in reroute.format_report(results, show_all=True)


def test_the_recorded_settings_apply_only_while_it_runs(reroute, monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "true")
    monkeypatch.delenv("GTIN_POLICY", raising=False)
    data = export()
    data["meta"]["settings"]["values"]["AUTO_PUBLISH_BRANDS"] = ["*"]
    seen = {}

    def spy(row):
        seen["auto"] = os.environ["AUTO_PUBLISH_ENABLED"]
        seen["brands"] = os.environ["AUTO_PUBLISH_BRANDS"]
        with socket.socket() as sock, pytest.raises(OSError):
            sock.connect(("127.0.0.1", 3306))                 # the database is never reached
        return {"row": row["row"], "changed": False}

    monkeypatch.setattr(reroute, "reroute_row", spy)
    reroute.reroute(data)
    assert seen == {"auto": "false", "brands": "*"}
    assert os.environ["AUTO_PUBLISH_ENABLED"] == "true" and "GTIN_POLICY" not in os.environ


def test_main_writes_the_json(reroute, tmp_path, capsys):
    src, out = tmp_path / "export.json", tmp_path / "rerouted.json"
    src.write_text(json.dumps(export()), encoding="utf-8")
    assert reroute.main([str(src), "--json", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "APPROXIMATION" in printed and "preselected:tier2_corroborated" in printed
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["totals"]["gained"] == [71] and data["note"].startswith("APPROXIMATION")
