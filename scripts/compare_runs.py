"""Compare two dry runs of scripts/smoke_live.py: row by row, and their summaries.

Usage (from the repository root):

    python scripts/compare_runs.py run2.json run3.json
    python scripts/compare_runs.py run2.json run3.json --md > compare.md      # Markdown tables to paste
    python scripts/compare_runs.py run2.json run3.json --out compare.txt     # write the report to a file

Rows whose decision changed come first (rows that gained a pick, then rows that lost one, then the
other decision changes), then rows with the same decision but another picked site, another image,
other review warnings or another reason for having no pick. Rows are matched by sheet row number; a
row that holds another product name in the two runs (the sheet changed in between) is listed first.

Both summaries are computed again with the same rules (smoke_live.summarize), so an older --json
file that holds only the list of rows compares with a new one. Rows that hit a connection outage
are marked "(outage)" and are left out of the coverage, as in the dry run's own summary.
Output is UTF-8: Arabic product names print as they are, at the end of each line.
"""

import argparse
import os
import sys

SCRIPTS = os.path.dirname(os.path.abspath(__file__))
for _p in (os.path.dirname(SCRIPTS), SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import smoke_live  # noqa: E402  (same folder; the run summary rules live there)

NONE = "-"
CHANGE_LABELS = {
    "gained": "gained a pick",
    "lost": "lost its pick",
    "decision": "decision changed",
    "site": "another site picked",
    "image": "another image, same site",
    "warnings": "warnings changed",
    "reason": "another reason for no pick",
}
DECISION_KINDS = ("gained", "lost", "decision")
OTHER_KINDS = ("site", "image", "warnings", "reason")

METRICS = (
    ("rows", "rows"),
    ("measured", "measured rows"),
    ("outage_rows", "outage rows excluded"),
    ("errors", "crashed rows excluded"),
    ("preselected", "pre-selected"),
    ("coverage_pct", "coverage %"),
    ("auto_publish", "auto-publish"),
) + tuple(("unselected." + key, "no pick: " + label) for key, label in smoke_live.UNSELECTED_REASONS) + (
    ("expansion.rows", "rows with an expansion round"),
    ("expansion.winners", "picks found by the expansion round"),
    ("expansion.calls", "expansion paid calls"),
    ("strong_calls", "strong-model calls"),
    ("vlm_calls", "label-reader calls"),
    ("search_calls", "paid search calls"),
    ("cost_usd.total", "estimated cost $ total"),
    ("cost_usd.per_product", "estimated cost $ per measured product"),
    ("picks_with_warnings", "picks with a warning"),
)


# ---------------------------------------------------------------------------
# Rows
# ---------------------------------------------------------------------------

def row_view(r):
    """What the comparison looks at for one row."""
    picked = r.get("decision") in smoke_live.PICK_DECISIONS
    _provider, domain, _query = smoke_live.winner_info(r) if picked else (None, None, None)
    outage = smoke_live.outage_reason(r)
    if outage:
        reason = "outage"
    elif "error" in r or picked:
        reason = None
    else:
        reason = smoke_live.unselected_reason(r)
    return {
        "row": r.get("row"),
        "name": str(r.get("name") or ""),
        "decision": "ERROR" if "error" in r else str(r.get("decision") or "?"),
        "outage": bool(outage),
        "picked": picked and not outage,
        "domain": domain if picked else None,
        "winner": r.get("winner") if picked else None,
        "warnings": sorted(r.get("warnings") or []) if picked else [],
        "reason": reason,
    }


def _change_kind(old, new):
    if old["decision"] != new["decision"] or old["outage"] != new["outage"]:
        if new["picked"] and not old["picked"]:
            return "gained"
        if old["picked"] and not new["picked"]:
            return "lost"
        return "decision"
    if old["domain"] != new["domain"]:
        return "site"
    if old["winner"] != new["winner"]:
        return "image"
    if old["warnings"] != new["warnings"]:
        return "warnings"
    if old["reason"] != new["reason"]:
        return "reason"
    return None


def _same_name(a, b):
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


def diff_rows(old_rows, new_rows):
    """{'changed': [...], 'unchanged': [row numbers], 'only_old': [...], 'only_new': [...], 'renamed': [...]}.

    changed is ordered: gained, lost, other decision changes, then site / image / warnings / reason
    changes; by row number within each group. renamed lists the row numbers that hold another product
    name in the two runs (the sheet changed in between: that row's comparison is not like for like).
    """
    old = {r.get("row"): row_view(r) for r in old_rows if isinstance(r, dict)}
    new = {r.get("row"): row_view(r) for r in new_rows if isinstance(r, dict)}
    order = DECISION_KINDS + OTHER_KINDS
    changed, unchanged = [], []
    for number in sorted(set(old) & set(new), key=_row_sort):
        kind = _change_kind(old[number], new[number])
        if kind is None:
            unchanged.append(number)
        else:
            changed.append({"row": number, "kind": kind, "old": old[number], "new": new[number]})
    changed.sort(key=lambda c: (order.index(c["kind"]), _row_sort(c["row"])))
    renamed = [{"row": n, "old": old[n]["name"], "new": new[n]["name"]}
               for n in sorted(set(old) & set(new), key=_row_sort)
               if old[n]["name"] and new[n]["name"] and not _same_name(old[n]["name"], new[n]["name"])]
    return {"changed": changed, "unchanged": unchanged,
            "only_old": [old[n] for n in sorted(set(old) - set(new), key=_row_sort)],
            "only_new": [new[n] for n in sorted(set(new) - set(old), key=_row_sort)],
            "renamed": renamed}


def _row_sort(number):
    return (0, number) if isinstance(number, (int, float)) else (1, str(number))


# ---------------------------------------------------------------------------
# Summary delta
# ---------------------------------------------------------------------------

def _pick(summary, dotted):
    value = summary
    for part in dotted.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    return value


def _delta(old, new, key):
    if old is None or new is None:
        return NONE
    diff = new - old
    if abs(diff) < 1e-12:
        return "="
    if key.startswith("cost_usd"):
        return f"{diff:+.4f}"
    if isinstance(diff, float):
        return f"{diff:+.1f}"
    return f"{diff:+d}"


def _fmt(value, key):
    if value is None:
        return NONE
    if key.startswith("cost_usd"):
        return f"{value:.4f}"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def summary_delta(old_s, new_s):
    """[(label, old text, new text, change text)] for the fixed metrics, providers and warnings."""
    lines = []
    for key, label in METRICS:
        old, new = _pick(old_s, key), _pick(new_s, key)
        lines.append((label, _fmt(old, key), _fmt(new, key), _delta(old, new, key)))
    for group, prefix in (("winner_providers", "picks by provider: "), ("warnings", "warning on picks: ")):
        names = sorted(set(old_s.get(group) or {}) | set(new_s.get(group) or {}))
        for name in names:
            old, new = (old_s.get(group) or {}).get(name, 0), (new_s.get(group) or {}).get(name, 0)
            lines.append((prefix + str(name), str(old), str(new), _delta(old, new, group)))
    return lines


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _arrow(old, new):
    old, new = old or NONE, new or NONE
    return old if old == new else f"{old} -> {new}"


def _warn_text(view):
    return " ".join(view["warnings"]) or NONE


def _decision_text(view):
    return view["decision"] + (" (outage)" if view["outage"] else "")


def _reason_text(view):
    if not view["reason"]:
        return None
    if view["reason"] == "outage":
        return "outage"
    return dict(smoke_live.UNSELECTED_REASONS).get(view["reason"], view["reason"])


def _row_line(change):
    old, new = change["old"], change["new"]
    parts = [_arrow(_decision_text(old), _decision_text(new)),
             "site " + _arrow(old["domain"], new["domain"]),
             "warnings " + _arrow(_warn_text(old), _warn_text(new))]
    reasons = _arrow(_reason_text(old), _reason_text(new))
    if reasons != NONE:
        parts.append("no pick: " + reasons)
    return (f"  row {str(change['row']):<5} {CHANGE_LABELS[change['kind']]:<26} " + " | ".join(parts)
            + f" | {new['name'] or old['name']}")


def format_plain(old_doc, new_doc, old_name, new_name):
    old_s, new_s = smoke_live.summarize(old_doc["rows"]), smoke_live.summarize(new_doc["rows"])
    diff = diff_rows(old_doc["rows"], new_doc["rows"])
    out = [f"old: {old_name} ({len(old_doc['rows'])} rows, {old_doc.get('format') or '?'})",
           f"new: {new_name} ({len(new_doc['rows'])} rows, {new_doc.get('format') or '?'})", ""]
    if diff["renamed"]:
        out += [f"ROWS THAT HOLD ANOTHER PRODUCT ({len(diff['renamed'])}): the sheet changed between the runs, "
                "so these rows compare different products"]
        out += [f"  row {str(v['row']):<5} {v['old']} -> {v['new']}" for v in diff["renamed"]]
        out.append("")
    delta = summary_delta(old_s, new_s)
    width = max(len(label) for label, *_ in delta) + 2
    out.append(f"{'SUMMARY':<{width}}{'old':>10}{'new':>10}{'change':>10}")
    for label, old, new, change in delta:
        out.append(f"{label:<{width}}{old:>10}{new:>10}{change:>10}")
    decision_changes = [c for c in diff["changed"] if c["kind"] in DECISION_KINDS]
    other_changes = [c for c in diff["changed"] if c["kind"] in OTHER_KINDS]
    out += ["", f"ROWS WHOSE DECISION CHANGED ({len(decision_changes)})"]
    out += [_row_line(c) for c in decision_changes] or ["  none"]
    out += ["", f"SAME DECISION, OTHER PICK, WARNINGS OR REASON ({len(other_changes)})"]
    out += [_row_line(c) for c in other_changes] or ["  none"]
    out += ["", f"UNCHANGED ROWS ({len(diff['unchanged'])}): " + (", ".join(str(n) for n in diff["unchanged"])
                                                                   or NONE)]
    for key, title in (("only_old", "ONLY IN THE OLD RUN"), ("only_new", "ONLY IN THE NEW RUN")):
        if diff[key]:
            out.append(f"{title} ({len(diff[key])}): " + ", ".join(f"{v['row']} {v['name']}" for v in diff[key]))
    return "\n".join(out) + "\n"


def _md(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def format_markdown(old_doc, new_doc, old_name, new_name):
    old_s, new_s = smoke_live.summarize(old_doc["rows"]), smoke_live.summarize(new_doc["rows"])
    diff = diff_rows(old_doc["rows"], new_doc["rows"])
    out = [f"## Dry run comparison: `{_md(old_name)}` -> `{_md(new_name)}`", ""]
    if diff["renamed"]:
        out += [f"**Rows that hold another product ({len(diff['renamed'])})**: the sheet changed between the runs, "
                "so these rows compare different products: "
                + "; ".join(f"row {v['row']}: {_md(v['old'])} -> {_md(v['new'])}" for v in diff["renamed"]), ""]
    out += ["| metric | old | new | change |", "|---|---:|---:|---:|"]
    for label, old, new, change in summary_delta(old_s, new_s):
        out.append(f"| {_md(label)} | {old} | {new} | {change} |")
    header = ["| row | change | decision | picked site | warnings | no pick because | product |",
              "|---:|---|---|---|---|---|---|"]
    groups = (("Rows whose decision changed", [c for c in diff["changed"] if c["kind"] in DECISION_KINDS]),
              ("Same decision, other pick, warnings or reason",
               [c for c in diff["changed"] if c["kind"] in OTHER_KINDS]))
    for title, changes in groups:
        out += ["", f"### {title} ({len(changes)})", ""]
        if not changes:
            out.append("None.")
            continue
        out += header
        for c in changes:
            old, new = c["old"], c["new"]
            out.append("| " + " | ".join(_md(x) for x in (
                c["row"], CHANGE_LABELS[c["kind"]], _arrow(_decision_text(old), _decision_text(new)),
                _arrow(old["domain"], new["domain"]), _arrow(_warn_text(old), _warn_text(new)),
                _arrow(_reason_text(old), _reason_text(new)), new["name"] or old["name"])) + " |")
    out += ["", f"Unchanged rows ({len(diff['unchanged'])}): " + (", ".join(str(n) for n in diff["unchanged"])
                                                                  or NONE)]
    for key, title in (("only_old", "Only in the old run"), ("only_new", "Only in the new run")):
        if diff[key]:
            out.append(f"{title}: " + ", ".join(f"{v['row']} {_md(v['name'])}" for v in diff[key]))
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", help="the earlier smoke_live --json file")
    parser.add_argument("new", help="the later smoke_live --json file")
    parser.add_argument("--md", action="store_true", help="Markdown tables instead of plain text")
    parser.add_argument("--out", help="write the report to this file (UTF-8) instead of the screen")
    args = parser.parse_args(argv)
    smoke_live._utf8_stdout()
    try:
        old_doc, new_doc = smoke_live.load_run(args.old), smoke_live.load_run(args.new)
    except (OSError, ValueError) as exc:
        print(f"cannot read the runs: {exc}", file=sys.stderr)
        return 2
    names = (os.path.basename(args.old), os.path.basename(args.new))
    report = (format_markdown if args.md else format_plain)(old_doc, new_doc, *names)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(report)
        print(f"comparison written to {args.out}")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
