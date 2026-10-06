"""scripts/migrate_delivery_urls.py: rewrite OUR old q_auto,f_auto delivery links in the sheet to the WebP form.

Fakes only: a recorded worksheet (test_sheet_write_integrity.Sheet) and the local MariaDB outbox. The real sheet and
Cloudinary are never reached; the dry run writes nothing; --apply goes through the outbox and its identity guard.
"""

import importlib.util
import os

import pytest

# loaded by path: putting scripts/ on sys.path would shadow root modules (scripts/publish_check.py vs publish_check.py)
_SPEC = importlib.util.spec_from_file_location(
    "migrate_delivery_urls",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "migrate_delivery_urls.py"))
mig = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mig)

from test_sheet_write_integrity import (HEADERS, JUICE, LABAN, MILK, WATER, Sheet, gs, local_only,  # noqa: E402,F401
                                        outbox, statuses)

BASE = "https://res.cloudinary.com/laqta/image/upload/"
OLD_MILK = BASE + "q_auto,f_auto/v1700000001/products/dairy/aaaa1111"
OLD_LABAN = BASE + "q_auto,f_auto/v1700000002/products/dairy/bbbb2222"
NEW_JUICE = BASE + "c_limit,w_1200,f_webp,q_auto/v1700000003/products/juice/cccc3333"


def new(link):
    return link.replace("/q_auto,f_auto/", "/c_limit,w_1200,f_webp,q_auto/")


def the_sheet():
    return Sheet([HEADERS,
                  [MILK, "Almarai Fresh Milk", "Almarai", "1L", "KSA", OLD_MILK],                      # row 2
                  [LABAN, "Almarai Laban", "Almarai", "1L", "KSA", OLD_LABAN],                         # row 3
                  [JUICE, "Al Rawabi Juice", "Al Rawabi", "500ml", "UAE", NEW_JUICE],                  # row 4
                  [WATER, "Mai Dubai Water", "Mai Dubai", "500ml", "UAE", "needs_review:" + OLD_MILK],   # row 5
                  ["", "Lulu Bread", "Lulu", "", "UAE", "https://www.luluhypermarket.com/bread.jpg"],   # row 6
                  ["", "Other Account Tea", "Tea", "", "UAE",
                   OLD_MILK.replace("/laqta/", "/someone/")],                                          # row 7
                  ["", "Empty Row", "X", "", "UAE", ""]])                                              # row 8


def test_classify_rewrites_only_our_old_delivery_links():
    assert mig.classify(OLD_MILK, "laqta") == ("migrate", new(OLD_MILK))
    assert mig.classify(OLD_MILK, "") == ("migrate", new(OLD_MILK))            # no account set: any account
    assert mig.classify(OLD_MILK, "someone") == ("other_account", None)
    assert mig.classify(NEW_JUICE, "laqta") == ("already_new", None)
    assert mig.classify("needs_review:" + OLD_MILK, "laqta") == ("pending_review", None)
    assert mig.classify(" " + OLD_MILK, "laqta") == ("not_ours", None)          # not the exact link: left alone
    assert mig.classify("https://www.lulu.com/x.jpg", "laqta") == ("not_ours", None)
    assert mig.classify("", "laqta") == ("empty", None)


def test_plan_counts_samples_and_carries_each_rows_identity():
    found = mig.plan(the_sheet().get_all_values(), "laqta")
    assert found["link_col"] == 5
    assert dict(found["counts"]) == {"migrate": 2, "already_new": 1, "pending_review": 1, "other_account": 1,
                                     "not_ours": 1, "empty": 1}
    assert [(i["row_number"], i["new"]) for i in found["items"]] == [(2, new(OLD_MILK)), (3, new(OLD_LABAN))]
    first = found["items"][0]
    assert (first["barcode"], first["product_name"], first["size"], first["brand"]) == (
        MILK, "Almarai Fresh Milk", "1L", "Almarai")
    assert found["samples"] == found["items"]


def test_a_sheet_without_a_link_column_is_left_alone(capsys, monkeypatch):
    monkeypatch.setattr(mig, "run_active", lambda: False)
    ws = Sheet([["Barcode", "Product Name"], [MILK, "Milk"]])
    assert mig.main([], worksheet=ws) == 1
    assert "No image link column" in capsys.readouterr().out


def test_the_dry_run_is_the_default_and_writes_nothing(gs, monkeypatch, capsys):
    ws = the_sheet()
    monkeypatch.setattr(gs, "queue_link_writes", lambda items: pytest.fail("the dry run queued a write"))
    monkeypatch.setattr(gs, "flush_outbox", lambda *a, **k: pytest.fail("the dry run flushed"))
    monkeypatch.setattr(mig, "run_active", lambda: pytest.fail("the dry run needs no lock check"))
    assert mig.main(["--cloud", "laqta"], worksheet=ws) == 0
    out = capsys.readouterr().out
    assert "To rewrite: 2" in out and "Dry run: nothing was written" in out
    assert new(OLD_MILK) in out and "row 2" in out
    assert ws.value(2, "Drive Image Link") == OLD_MILK


def test_apply_refuses_while_a_run_holds_the_lock(gs, monkeypatch, capsys):
    monkeypatch.setattr(mig, "run_active", lambda: True)
    monkeypatch.setattr(gs, "queue_link_writes", lambda items: pytest.fail("queued during a run"))
    assert mig.main(["--apply", "--cloud", "laqta"], worksheet=the_sheet()) == 2
    assert "automation run is active" in capsys.readouterr().out


def test_apply_writes_through_the_outbox_and_its_identity_guard(gs, outbox, monkeypatch, capsys):
    ws = the_sheet()
    monkeypatch.setattr(mig, "run_active", lambda: False)
    real_plan = mig.plan

    def plan_then_edit(values, cloud=""):
        found = real_plan(values, cloud)
        ws.set(3, 1, "")                         # the owner puts another product in row 3 after it was read
        ws.set(3, 2, "Almarai Laban Up")
        return found

    monkeypatch.setattr(mig, "plan", plan_then_edit)
    assert mig.main(["--apply", "--cloud", "laqta"], worksheet=ws) == 0
    assert ws.value(2, "Drive Image Link") == new(OLD_MILK)                         # rewritten, same asset
    assert ws.value(3, "Drive Image Link") == OLD_LABAN                             # identity changed: refused
    assert ws.value(4, "Drive Image Link") == NEW_JUICE
    assert ws.value(5, "Drive Image Link") == "needs_review:" + OLD_MILK           # a pending review stays
    assert ws.value(7, "Drive Image Link") == OLD_MILK.replace("/laqta/", "/someone/")
    assert sorted(statuses(gs).values()) == ["CONFLICT", "SYNCED"]
    assert "written 1" in capsys.readouterr().out


def test_apply_skips_a_row_with_its_own_link_write_waiting(gs, outbox, monkeypatch, capsys):
    """A newer image queued for the row (an approval) must win: the rewrite of the old cell value is not queued."""
    ws = the_sheet()
    monkeypatch.setattr(mig, "run_active", lambda: False)
    approval = outbox.append_update(2, 5, "https://res.cloudinary.com/laqta/image/upload/c_limit,w_1200,f_webp,q_auto/"
                                          "v1800000000/products/dairy/dddd4444", col_key="link", key_barcode=MILK)
    assert mig.main(["--apply", "--no-flush", "--cloud", "laqta"], worksheet=ws) == 0
    queued = [o for o in gs.outbox_outcomes(limit=100) if o["id"] != approval]
    assert [(o["row"], o["value"]) for o in queued] == [(3, new(OLD_LABAN))]
    assert "Skipped 1 row" in capsys.readouterr().out
    assert ws.value(2, "Drive Image Link") == OLD_MILK                             # nothing flushed with --no-flush


def test_apply_says_so_when_the_outbox_cannot_be_read(gs, monkeypatch, capsys):
    import local_cache_db

    monkeypatch.setattr(mig, "run_active", lambda: False)
    monkeypatch.setattr(local_cache_db, "outbox_max_id", lambda: 0)

    def unreadable(*a, **k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(gs, "outbox_outcomes", unreadable)
    monkeypatch.setattr(gs, "queue_link_writes", lambda items: pytest.fail("queued without the outbox check"))
    assert mig.main(["--apply", "--cloud", "laqta"], worksheet=the_sheet()) == 1
    assert "outbox cannot be read" in capsys.readouterr().out
