"""«فحص النشر» from the terminal: the publish chain rehearsed on a test image, step by step (publish_check.py).

Usage (from the repository root, with the settings of .env and the dashboard database):

    python scripts/publish_check.py          # one line per step, in Arabic
    python scripts/publish_check.py --json   # one JSON document (what the Health page shows)
    python scripts/publish_check.py --no-save   # keep the last result the Health page shows

Steps: the image of a product waiting for review (candidate store, then the real download: direct, then the proxy),
background removal with the current processing profile, an upload to Cloudinary at laqta_selftest/publish_check that
is deleted right after, and a value-preserving write of the link column's header cell in the products tab. No product
row, queue row or setting is touched. It may cost one background-removal call (and one Gemini read when its key is
saved). The result is saved to temp/publish_check_last.json (the Health page's «آخر فحص») unless --no-save is given.
Exit code 0 when every step passed, 1 otherwise.
"""

import io
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

ICONS = {"ok": "✅", "warn": "⚠️", "fail": "❌", "skipped": "⏭"}


def format_report(result):
    """The printed report: one line per step with its time, what happened and what to do."""
    lines = ["فحص النشر", "=" * 40]
    for step in result.get("steps") or []:
        seconds = (step.get("ms") or 0) / 1000
        status = step.get("status")
        label = "ما انفحصت" if status == "skipped" else f"{seconds:.1f} ث"
        lines.append(f"{ICONS.get(status, '?')} {step.get('title_ar')} ({label})")
        if step.get("detail_ar"):
            lines.append(f"   {step['detail_ar']}")
        if step.get("action_ar") and status in ("fail", "warn"):
            lines.append(f"   ← {step['action_ar']}")
    lines.append("=" * 40)
    lines.append(f"{ICONS.get(result.get('overall'), '?')} {result.get('summary_ar', '')}")
    for note in result.get("notes") or []:
        lines.append(f"ℹ️ {note}")
    return "\n".join(lines)


def main(argv=None, out=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out = out or sys.stdout
    as_json = "--json" in argv
    real_stdout = sys.stdout
    if as_json:
        # what the libraries print (config at import, warnings) never mixes with the JSON document
        sys.stdout = io.StringIO()
    try:
        os.chdir(REPO_ROOT)
        import publish_check
        result = publish_check.run_publish_check(save="--no-save" not in argv)
    finally:
        sys.stdout = real_stdout
    try:
        out.reconfigure(encoding="utf-8", errors="replace")     # Arabic on a Windows console code page
    except Exception:
        pass
    if as_json:
        out.write(json.dumps(result, ensure_ascii=False) + "\n")
    else:
        out.write(format_report(result) + "\n")
    out.flush()
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    code = main()
    sys.stderr.flush()
    # a step still hanging after its timeout must not keep the process alive
    os._exit(code)
