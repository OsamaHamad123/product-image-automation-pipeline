"""Inline <script> blocks of a Blade view, for the tests that run a page's JavaScript under node.

An HTML parser finds the blocks instead of a regular expression: it matches the tag in any letter case and
reads the script body up to its real end tag, as a browser does. Only attribute-less <script> tags are
returned (inline page code); <script src=...> includes and typed blocks are skipped. asset_scripts lists the
page's own script files loaded with {{ asset('js/...') }}.
"""
import re
from html.parser import HTMLParser

_BLADE_ECHO = re.compile(r"\{\{.*?\}\}")


class _ScriptCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.sources = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag != "script":
            return
        if not attrs:
            self._current = []
        src = dict(attrs).get("src")
        if src:
            self.sources.append(src)

    def handle_endtag(self, tag):
        if tag == "script" and self._current is not None:
            self.blocks.append("".join(self._current))
            self._current = None

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)


def _parse(text: str) -> _ScriptCollector:
    parser = _ScriptCollector()
    parser.feed(text)
    parser.close()
    return parser


def script_sources(text: str) -> list:
    """The src attribute of every <script src=...> in the page, in page order."""
    return _parse(text).sources


def inline_scripts(text: str) -> list:
    """Bodies of the attribute-less <script> blocks in the view source, in page order."""
    return _parse(text).blocks


_ASSET_JS = re.compile(r"asset\(\s*'js/([A-Za-z0-9_./-]+\.js)'\s*\)")


def asset_scripts(text: str) -> list:
    """Paths under public/js/ of the scripts the view loads with {{ asset('js/...') }}, in page order."""
    names = []
    for src in _parse(text).sources:
        match = _ASSET_JS.search(src)
        if match:
            names.append(match.group(1))
    return names


def without_blade_echo(script: str) -> str:
    """Blade echo tags replaced by a string literal, as they would read after rendering."""
    return _BLADE_ECHO.sub("''", script)
