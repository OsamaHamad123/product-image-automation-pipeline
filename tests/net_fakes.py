"""A DNS stand-in for net_guard (the SSRF guard). tests/conftest.py installs it for the whole session, so no test looks
a name up for real; tests of the guard build their own mapping with fake_getaddrinfo(names).

(A module of its own: `from conftest import ...` would find tests/eval/conftest.py in a full run.)
"""

import ipaddress
import re
import socket

# What every host name resolves to: a public address (no test reaches it, the HTTP clients are fakes). An IP literal
# resolves to itself and 'localhost' to 127.0.0.1, so the guard still refuses those.
PUBLIC_TEST_ADDRESS = "93.184.216.34"


def fake_getaddrinfo(names=None):
    """A stand-in for net_guard.getaddrinfo: names maps a host to its addresses (a str or a list) or to an exception
    to raise; every other name is PUBLIC_TEST_ADDRESS. Never looks anything up."""
    names = {k.lower(): v for k, v in (names or {}).items()}

    def literal(host):
        try:
            return str(ipaddress.ip_address(host.split("%", 1)[0]))
        except ValueError:
            pass
        if re.fullmatch(r"[0-9a-fA-Fx.]+", host):       # 2130706433, 0x7f.1, 127.1: what inet_aton (and curl) accept
            try:
                return socket.inet_ntoa(socket.inet_aton(host))
            except OSError:
                pass
        return None

    def getaddrinfo(host):
        key = str(host).lower().rstrip(".")
        if key in names:
            value = names[key]
            if isinstance(value, BaseException):
                raise value
            addresses = [value] if isinstance(value, str) else list(value)
        else:
            found = literal(key)
            addresses = [found] if found else (["127.0.0.1"] if key == "localhost" else [PUBLIC_TEST_ADDRESS])
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                 (a, 0, 0, 0) if ":" in a else (a, 0)) for a in addresses]

    return getaddrinfo
