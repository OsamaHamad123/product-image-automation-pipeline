"""Every failure code the search engine can end with has an Arabic text on each page that shows one."""

import re
from pathlib import Path

import pytest

from catalog_match import models

ROOT = Path(__file__).resolve().parents[1]
MAPS = [
    "dashboard/public/js/health.js",           # Health page: what happened, what to try
    "dashboard/public/js/review/core.js",      # review screen: why there is no pick
    "dashboard/app/Services/QueueStats.php",   # run page and home: failure counts by reason
]


@pytest.mark.parametrize("path", MAPS)
def test_every_engine_failure_code_has_a_text(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    missing = [code for code in models.FAILURE_CODES
               if not re.search(r"['\"]?\b%s\b['\"]?\s*(:|=>)\s*[\['\"]" % re.escape(code), text)]
    assert missing == [], f"{path} has no text for {missing}"
