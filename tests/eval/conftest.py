"""Shared fixtures for the offline evaluation harness (tests/eval).

Every test here runs without network, API keys or MariaDB. Engine runs are
wrapped in a socket block: a monkeypatch of socket.socket.connect (and DNS)
that raises for any address that leaves the machine and records the attempt.
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parent.parent
for _p in (str(REPO_ROOT), str(EVAL_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import harness  # noqa: E402
import metrics  # noqa: E402


def install_socket_block(mp: pytest.MonkeyPatch) -> List[str]:
    """Make socket.socket.connect / connect_ex / getaddrinfo raise for non-loopback targets.

    Loopback stays open because asyncio on Windows connects a loopback pair for
    its self-pipe; nothing in the harness talks to a local service.
    """
    attempts: List[str] = []
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def is_local(host: Any) -> bool:
        return isinstance(host, str) and (host == "" or host.startswith(("127.", "::1", "localhost")))

    def check(address: Any) -> None:
        host = address[0] if isinstance(address, tuple) and address else address
        if not is_local(host):
            attempts.append(repr(address))
            raise OSError(f"network blocked in tests/eval: connect to {address!r}")

    def connect(self, address):  # type: ignore[no-untyped-def]
        check(address)
        return real_connect(self, address)

    def connect_ex(self, address):  # type: ignore[no-untyped-def]
        check(address)
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):  # type: ignore[no-untyped-def]
        if not is_local(host):
            attempts.append(f"dns {host!r}")
            raise OSError(f"network blocked in tests/eval: DNS lookup of {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    mp.setattr(socket.socket, "connect", connect)
    mp.setattr(socket.socket, "connect_ex", connect_ex)
    mp.setattr(socket, "getaddrinfo", getaddrinfo)
    return attempts


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    return install_socket_block(monkeypatch)


@pytest.fixture(scope="session")
def golden() -> Dict[str, Any]:
    return harness.load_golden()


@pytest.fixture(scope="session")
def cassette() -> Dict[str, Any]:
    return harness.load_cassette()


@pytest.fixture(scope="session")
def labels(golden: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return metrics.labels_from_golden(golden)


@pytest.fixture(scope="session")
def baseline() -> Dict[str, Any]:
    return harness.load_baseline()


def timed_run(engine: str, scenario: str = "normal", **kwargs: Any) -> Tuple[Dict[str, Any], float, List[str]]:
    """harness.run_all under the socket block; returns (report, seconds, blocked attempts)."""
    with pytest.MonkeyPatch.context() as mp:
        attempts = install_socket_block(mp)
        t0 = time.perf_counter()
        report = harness.run_all(engine, scenario, **kwargs)
        seconds = time.perf_counter() - t0
    return report, seconds, attempts


@pytest.fixture(scope="session")
def legacy_run() -> Tuple[Dict[str, Any], float, List[str]]:
    """One offline replay of legacy v1 over the whole golden set, shared by the baseline tests."""
    return timed_run("v1")


@pytest.fixture(scope="session")
def v2_run() -> Tuple[Dict[str, Any], float, List[str]]:
    """catalog_match over the golden set, auto-publish enabled for every brand."""
    return timed_run("v2")


@pytest.fixture(scope="session")
def v2_gemini_down_run() -> Tuple[Dict[str, Any], float, List[str]]:
    """catalog_match with the verifier returning UNKNOWN for every image."""
    return timed_run("v2", "gemini_down")


@pytest.fixture(scope="session")
def v2_noisy_run() -> Tuple[Dict[str, Any], float, List[str]]:
    """catalog_match with the recorded model misreads of vlm_noisy.json."""
    return timed_run("v2", "vlm_noisy")


@pytest.fixture(scope="session")
def v2_bing_only_run() -> Tuple[Dict[str, Any], float, List[str]]:
    """catalog_match with the no-key provider set: Bing HTML as the only search source."""
    return timed_run("v2", provider_set="bing_only")


@pytest.fixture(scope="session")
def adversarial() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    return harness.load_adversarial()


@pytest.fixture(scope="session")
def v2_adversarial_run(adversarial) -> Tuple[Dict[str, Any], float, List[str]]:
    """catalog_match over the adversarial cases (adversarial_skus.json)."""
    adv, cassette = adversarial
    return timed_run("v2", golden=adv, cassette=cassette)


@pytest.fixture(scope="session")
def v2_absent_runs(golden, cassette) -> Dict[str, Tuple[Dict[str, Any], float, List[str]]]:
    """catalog_match over every golden SKU with its correct candidates removed: recorded and noisy readings."""
    out = {}
    for name, readings in (("normal", cassette), ("vlm_noisy", harness.noisy_cassette(cassette))):
        absent, absent_cassette = harness.correct_absent(golden, readings)
        out[name] = timed_run("v2", golden=absent, cassette=absent_cassette)
    return out
