"""The fixed held-out split of the evaluation (tests/eval/metrics.split_of): which rows rules may be tuned on.

    python scripts/eval_split.py --show tests/eval/fixtures/realistic/golden_skus.json
    python scripts/eval_split.py --show laqta_run_2026-10-05_1951.json          # a run export (smoke_live/2 shape)
    python scripts/eval_split.py --tuned-from laqta_run_*.json                  # refresh fixtures/tuned_rows.json

A row is 'held_out' when the sha256 of a fixed salt and its normalised sheet name falls in the first 30% of the hash
space and no regression test is named after it; every other row is 'dev'. The salt and the share never change, so
the same row is held out in every set, every export and every run. eval_report prints the per-lane numbers of both
halves: a rule tuned on live rows (the regression tests named after them) is checked on rows it was not tuned on.

--tuned-from reads run exports (laqta_run_*.json from «تصدير تقرير للتحليل», or smoke_live --json) and lists every
row a test under tests/ names, in tests/eval/fixtures/tuned_rows.json. A row counts as named when one line of a test
file holds its brand and its first two product words (ZWAN + CHICKEN + LUNCHEON): matching more rows than strictly
named only moves rows to 'dev', it never lets a tuned row into the held-out share. Only the names are written (no
image, link or reading), and only names a committed test already holds.
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "tests" / "eval"
for _p in (str(REPO_ROOT), str(EVAL_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import metrics  # noqa: E402

TESTS_DIR = REPO_ROOT / "tests"
MIN_WORD = 3


def _words(text):
    return [w for w in re.split(r"[^A-Za-z0-9؀-ۿ]+", str(text or "").upper()) if w]


def key_words(name, brand=""):
    """The words a test line must hold to name this row: the brand's words and the first two product words."""
    brand_words = [w for w in _words(brand) if len(w) >= 2]
    rest = [w for w in _words(name) if w not in brand_words and len(w) >= MIN_WORD and not any(c.isdigit() for c in w)]
    found = brand_words or _words(name)[:1]
    return tuple(dict.fromkeys(found + rest[:2]))


def test_lines(root=TESTS_DIR):
    """Every line of every test source, as a set of upper-case words (the eval's own fixtures left out)."""
    out = []
    for path in sorted(Path(root).rglob("*.py")):
        if "eval" in path.relative_to(root).parts[:1]:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for line in text.splitlines():
            words = set(_words(line))
            if words:
                out.append((rel, words))
    return out


def named_by_tests(rows, lines=None):
    """[{name, brand, tests}] for the rows a test line names (key_words all on one line)."""
    lines = test_lines() if lines is None else lines
    out = []
    seen = set()
    for row in rows:
        name, brand = str(row.get("name") or ""), str(row.get("brand") or "")
        key = metrics.row_key(name)
        if not key or key in seen:
            continue
        need = set(key_words(name, brand))
        if len(need) < 2:
            continue
        files = sorted({rel for rel, words in lines if need <= words})
        if files:
            seen.add(key)
            out.append({"name": name, "brand": brand, "tests": files[:5]})
    return out


def read_rows(path):
    """Sheet rows of a run export ({rows: [{name, brand}]}) or of a golden set ({skus: [{name_en, brand}]})."""
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    if isinstance(doc, dict) and isinstance(doc.get("skus"), list):
        return [{"name": s.get("name_en") or s.get("name_ar") or "", "brand": s.get("brand") or "",
                 "split": s.get("split")} for s in doc["skus"]]
    rows = doc.get("rows") if isinstance(doc, dict) else doc
    return [{"name": r.get("name") or "", "brand": r.get("brand") or ""} for r in rows or [] if isinstance(r, dict)]


def write_tuned(named, target=metrics.TUNED_ROWS_PATH):
    doc = {
        "version": 1,
        "description": ("Live sheet rows a regression test under tests/ is named after (scripts/eval_split.py "
                        "--tuned-from). They are never in the held-out split (tests/eval/metrics.split_of)."),
        "generated": dt.date.today().isoformat(),
        "rows": sorted(named, key=lambda r: metrics.row_key(r["name"])),
    }
    Path(target).write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return target


def show(path):
    rows = read_rows(path)
    tuned = metrics.tuned_rows()
    groups = {"dev": [], "held_out": []}
    for r in rows:
        groups[metrics.split_of({"name_en": r["name"], "split": r.get("split")}, tuned)].append(r["name"])
    leaks = metrics.holdout_leaks([{"name_en": r["name"]} for r in rows], tuned)
    print(f"{len(rows)} rows: {len(groups['dev'])} dev, {len(groups['held_out'])} held out "
          f"({len(tuned)} tuned rows listed; {len(leaks)} of them fall in the held-out share and count as dev)")
    for name in groups["held_out"]:
        print(f"  held out  {name}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--show", metavar="JSON", help="print the split of a golden set or a run export")
    parser.add_argument("--tuned-from", nargs="+", metavar="EXPORT",
                        help="run exports whose rows the tests may name; rewrites fixtures/tuned_rows.json")
    args = parser.parse_args(argv)
    if (getattr(sys.stdout, "encoding", "") or "").lower().replace("-", "") != "utf8":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if args.tuned_from:
        rows = [r for p in args.tuned_from for r in read_rows(p)]
        named = named_by_tests(rows)
        target = write_tuned(named)
        print(f"{len(named)} of {len({metrics.row_key(r['name']) for r in rows})} distinct rows are named by a "
              f"test; written to {os.path.relpath(target, REPO_ROOT)}")
        return 0
    if args.show:
        return show(args.show)
    parser.error("give --show or --tuned-from")
    return 2


if __name__ == "__main__":
    sys.exit(main())
