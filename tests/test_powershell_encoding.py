"""PowerShell scripts with Arabic text must start with a UTF-8 BOM.

Windows PowerShell 5.1 (the owner's default shell) reads a script without a BOM in the ANSI code page
(cp1256 or cp1252). Arabic UTF-8 bytes then turn into quote characters, and setup_and_launch.ps1 failed to
parse. PowerShell 7 reads the BOM too, so it is safe everywhere.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted(p for p in list(ROOT.glob("*.ps1")) + list(ROOT.glob("scripts/*.ps1")))


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_non_ascii_powershell_scripts_start_with_a_bom(path):
    raw = path.read_bytes()
    if all(b < 0x80 for b in raw):
        return
    assert raw.startswith(b"\xef\xbb\xbf"), f"{path.name} has non-ASCII text but no UTF-8 BOM"
