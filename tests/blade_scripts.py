"""Inline <script> blocks of a Blade view, for the tests that run a page's JavaScript under node.

An HTML parser finds the blocks instead of a regular expression: it matches the tag in any letter case and
reads the script body up to its real end tag, as a browser does. Only attribute-less <script> tags are
returned (inline page code); <script src=...> includes and typed blocks are skipped.
"""
import re
from html.parser import HTMLParser

_BLADE_ECHO = re.compile(r"\{\{.*?\}\}")


class _ScriptCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._current = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and not attrs:
            self._current = []

    def handle_endtag(self, tag):
        if tag == "script" and self._current is not None:
            self.blocks.append("".join(self._current))
            self._current = None

    def handle_data(self, data):
        if self._current is not None:
            self._current.append(data)


def inline_scripts(text: str) -> list:
    """Bodies of the attribute-less <script> blocks in the view source, in page order."""
    parser = _ScriptCollector()
    parser.feed(text)
    parser.close()
    return parser.blocks


def without_blade_echo(script: str) -> str:
    """Blade echo tags replaced by a string literal, as they would read after rendering."""
    return _BLADE_ECHO.sub("''", script)
