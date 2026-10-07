"""Local background-removal fallback (BG_FALLBACK) when a cloud method's credit, key or quota is gone, offline.

PhotoRoom's credit ran out and every approval failed until the owner pressed «تجاوز عزل الخلفية». Now, with the setting
BG_FALLBACK = 'local' (default), image_processor isolates the same image with rembg (the BiRefNet model; never GrabCut) when a cloud method fails with a billing / key / quota code (the codes of
publish_check.BG_SKIP_CODE_RE), and the result goes through the same crop gate as a normal isolation:

- never for a timeout or any other error, never to «no isolation» (that stays the owner's button);
- 'off', or rembg not installed, changes nothing;
- after a billing failure the cloud method is skipped for 30 minutes in the process (CloudBreaker), off under a cassette;
- the result says which method isolated it (provider) and where it fell back from (fallback_from); publish_image,
  the approval response, the worker's run report and «فحص النشر» say so.

The fallback is rembg with the BiRefNet model (REMBG_MODEL, one session per process); GrabCut and the small u2net-style
models stay a manual Settings choice. rembg itself is a stub here: nothing is installed or downloaded.
"""

import io
import os
import re
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
if sys.path[0] != str(ROOT):
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import image_processor  # noqa: E402
import processing_profile  # noqa: E402
import publish_check as pc  # noqa: E402
from catalog_match import settings  # noqa: E402
from laqta_review_harness import NODE  # noqa: E402
from test_cli_bridge_contract import SELECT_PARAMS, V2_RESULT, _canvas, bridge, select_env  # noqa: E402,F401 - fixtures
from test_laqta_health import (DASH, PHP, _kernel, _php, _put, _settings, app_env,  # noqa: E402,F401
                               php_value)
from test_laqta_review_guards import fixture, page, picked  # noqa: E402
from test_publish_check import _canvas as _check_canvas, steps, world  # noqa: E402,F401 - fixture

NEEDS_PHP = pytest.mark.skipif(PHP is None, reason="php is not installed")
NEEDS_NODE = pytest.mark.skipif(NODE is None, reason="node is not installed")

BG = (225, 225, 225)
BLUE = (40, 70, 200)
CLOUD_LINK = "https://res.cloudinary.com/demo/image/upload/q_auto,f_auto/products/dairy/abc.png"
BILLING = ["photoroom_no_key", "photoroom_401", "photoroom_402", "photoroom_403", "photoroom_429"]
NOT_BILLING = ["photoroom_timeout", "photoroom_connection_error", "photoroom_500", "photoroom_502",
               "photoroom_bad_output", "photoroom_404", "photoroom_400", "photoroom_empty_cutout"]


# ---------------------------------------------------------------------------
# Stubs: cloud providers and local methods that count their calls; a clock the tests move
# ---------------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def segment(img):
    """What a good local isolation returns for the synthetic product: the grey backdrop turned transparent."""
    arr = np.asarray(img.convert("RGB")).astype(int)
    alpha = np.where(np.abs(arr - np.array(BG)).sum(axis=2) <= 30, 0, 255).astype(np.uint8)
    rgba = np.dstack([arr.astype(np.uint8), alpha])
    rgba[alpha == 0, :3] = 0
    return Image.fromarray(rgba, "RGBA")


def untouched(img):
    """A local isolation that removed nothing: the whole frame stays opaque (the crop gate refuses it)."""
    return img.convert("RGBA")


def empty(img):
    return Image.new("RGBA", img.size, (0, 0, 0, 0))


def simple_product(path, size=(500, 700)):
    img = Image.new("RGB", size, BG)
    ImageDraw.Draw(img).rectangle([size[0] // 4, size[1] // 8, 3 * size[0] // 4, 7 * size[1] // 8], fill=BLUE)
    img.save(path, format="PNG")
    return str(path)


@pytest.fixture
def world_ip(monkeypatch, tmp_path, offline):
    """image_processor with a failing PhotoRoom and stubbed local methods; BG_FALLBACK='local', rembg (and GrabCut) installed."""
    w = types.SimpleNamespace()
    w.clock = Clock()
    w.cloud, w.local, w.models = [], [], []
    w.cloud_result = (None, "photoroom_402")
    w.remove_result = (None, "removebg_402")
    w.local_cutout = {"rembg": segment, "grabcut": segment}
    w.local_error = {}
    w.installed = {"rembg": True, "grabcut": True}
    w.real_photoroom = image_processor._isolate_photoroom
    w.real_rembg = image_processor._isolate_rembg

    def photoroom(img):
        w.cloud.append("photoroom")
        return w.cloud_result

    def remove_bg(img):
        w.cloud.append("remove_bg_api")
        return w.remove_result

    def local(name):
        def run(img, model=image_processor.MANUAL_REMBG_MODEL):
            w.local.append(name)
            w.models.append(model)
            if name in w.local_error:
                return None, w.local_error[name]
            return w.local_cutout[name](img), None
        return run

    monkeypatch.setattr(image_processor, "_isolate_photoroom", photoroom)
    monkeypatch.setattr(image_processor, "_isolate_remove_bg", remove_bg)
    monkeypatch.setattr(image_processor, "_isolate_rembg", local("rembg"))
    monkeypatch.setattr(image_processor, "_isolate_grabcut", local("grabcut"))
    monkeypatch.setattr(image_processor, "local_methods_available", lambda: dict(w.installed))
    monkeypatch.setattr(image_processor, "_CLOUD_BREAKER", image_processor.CloudBreaker(clock=w.clock))
    monkeypatch.setattr(config, "BG_FALLBACK", "local", raising=False)
    monkeypatch.setattr(config, "REMBG_MODEL", "birefnet-general", raising=False)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "PHOTOROOM_API_KEY", "unit-test-placeholder")
    monkeypatch.setattr(config, "REMOVE_BG_API_KEY", "")
    monkeypatch.setattr(config, "BG_REMOVAL_METHOD", "photoroom")
    monkeypatch.setattr(config, "ENABLE_STUDIO_SHADOWS", False)
    monkeypatch.setattr(config, "CANDIDATE_STORE_DIR", str(tmp_path / "candidates"), raising=False)
    monkeypatch.delenv("OUTPUT_CANVAS_SIZE", raising=False)
    work = tmp_path / "tmp"
    work.mkdir()
    monkeypatch.setattr(image_processor.tempfile, "tempdir", str(work))
    w.src = simple_product(tmp_path / "src.png")
    w.img = Image.open(w.src).convert("RGB")
    return w


def process(w, method="photoroom"):
    return image_processor.process_product_image_result(w.src, "Laban 1L", "Al Rawabi", 800, 800, bg_method=method)


# ---------------------------------------------------------------------------
# The setting flows like the other processing settings
# ---------------------------------------------------------------------------

def test_the_default_is_local_and_only_local_or_off_are_read(monkeypatch):
    assert settings.DEFAULTS["BG_FALLBACK"] == "local"
    assert re.search(r'^BG_FALLBACK = os\.getenv\("BG_FALLBACK", "local"\)$', (ROOT / "config.py").read_text("utf-8"),
                     re.M)
    assert config.BG_FALLBACKS == ("local", "off")
    monkeypatch.delattr(config, "BG_FALLBACK", raising=False)
    monkeypatch.delenv("BG_FALLBACK", raising=False)
    assert settings.bg_fallback() == "local"                    # nothing set: the default
    for value, expected in [("off", "off"), (" OFF ", "off"), ("local", "local"), ("LOCAL", "local"), ("", "local"),
                            ("none", "local"), ("magic", "local")]:           # never 'no isolation' by accident
        monkeypatch.setattr(config, "BG_FALLBACK", value, raising=False)
        assert settings.bg_fallback() == expected, value


def test_config_reads_the_saved_setting_and_ignores_an_unknown_value(monkeypatch, fake_connection):
    import pymysql

    monkeypatch.setattr(config, "BG_FALLBACK", "local")
    rows = [{"key": "bg_fallback", "value": " OFF "}]

    def responder(sql, params):
        return [{"t": "system_settings"}] if sql.upper().startswith("SHOW") else rows

    monkeypatch.setattr(pymysql, "connect", lambda **k: fake_connection(responder))
    config.load_db_config()
    assert config.BG_FALLBACK == "off"
    rows[:] = [{"key": "bg_fallback", "value": "magic"}]
    config.load_db_config()
    assert config.BG_FALLBACK == "off"                          # unknown value ignored, the earlier one stays
    rows[:] = [{"key": "bg_fallback", "value": "local"}]
    config.load_db_config()
    assert config.BG_FALLBACK == "local"


def test_the_rembg_model_is_birefnet_by_default_and_only_the_two_birefnet_models_are_read(monkeypatch):
    assert settings.DEFAULTS["REMBG_MODEL"] == "birefnet-general" and config.REMBG_MODELS == settings.REMBG_MODELS
    assert re.search(r'^REMBG_MODEL = os\.getenv\("REMBG_MODEL", "birefnet-general"\)$',
                     (ROOT / "config.py").read_text("utf-8"), re.M)
    assert config.REMBG_MODELS == ("birefnet-general", "birefnet-general-lite")
    monkeypatch.delattr(config, "REMBG_MODEL", raising=False)
    monkeypatch.delenv("REMBG_MODEL", raising=False)
    assert settings.rembg_model() == "birefnet-general"
    for value, expected in [("birefnet-general-lite", "birefnet-general-lite"), (" BiRefNet-General ", "birefnet-general"),
                            ("u2net", "birefnet-general"), ("isnet-general-use", "birefnet-general"), ("", "birefnet-general")]:
        monkeypatch.setattr(config, "REMBG_MODEL", value, raising=False)
        assert settings.rembg_model() == expected, value


def test_config_reads_a_saved_rembg_model_and_ignores_an_unknown_one(monkeypatch, fake_connection):
    import pymysql

    monkeypatch.setattr(config, "REMBG_MODEL", "birefnet-general")
    rows = [{"key": "rembg_model", "value": " birefnet-general-lite "}]

    def responder(sql, params):
        return [{"t": "system_settings"}] if sql.upper().startswith("SHOW") else rows

    monkeypatch.setattr(pymysql, "connect", lambda **k: fake_connection(responder))
    config.load_db_config()
    assert config.REMBG_MODEL == "birefnet-general-lite"
    rows[:] = [{"key": "rembg_model", "value": "u2net"}]
    config.load_db_config()
    assert config.REMBG_MODEL == "birefnet-general-lite"                       # unknown: the earlier value stays


def test_the_processing_profile_carries_it(monkeypatch):
    monkeypatch.setattr(config, "BG_FALLBACK", "off", raising=False)
    profile = processing_profile.current()
    assert profile.bg_fallback == "off" and profile.as_dict()["bg_fallback"] == "off"
    monkeypatch.setattr(config, "BG_FALLBACK", "local", raising=False)
    assert processing_profile.current().bg_fallback == "local"
    assert processing_profile.ProcessingProfile(canvas=800, enhance=False, bg_method="photoroom").bg_fallback == "local"


def test_the_billing_rule_is_one_regex_for_the_processor_and_the_dashboard():
    assert image_processor.BILLING_CODE_RE == pc.BG_SKIP_CODE_RE
    for code in BILLING + ["removebg_no_key", "removebg_401", "removebg_402", "removebg_403", "removebg_429"]:
        assert image_processor.is_billing_code(code) and pc.bg_skip_offered(code), code
    for code in NOT_BILLING + ["removebg_timeout", "removebg_500", "rembg_failed", "grabcut_empty", "", None]:
        assert not image_processor.is_billing_code(code) and not pc.bg_skip_offered(code), code


# ---------------------------------------------------------------------------
# The fallback is taken only for billing codes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", BILLING)
def test_a_billing_failure_of_photoroom_isolates_with_rembg_and_the_birefnet_model(world_ip, code):
    world_ip.cloud_result = (None, code)
    cutout, error, provider, fell = image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    assert cutout is not None and error is None and provider == "rembg"
    assert fell == {"method": "photoroom", "code": code}
    assert world_ip.cloud == ["photoroom"] and world_ip.local == ["rembg"]
    assert world_ip.models == ["birefnet-general"]                              # never the small u2net-style model


@pytest.mark.parametrize("code", ["removebg_no_key", "removebg_401", "removebg_402", "removebg_403", "removebg_429"])
def test_a_billing_failure_of_remove_bg_falls_back_the_same_way(world_ip, code):
    world_ip.remove_result = (None, code)
    cutout, error, provider, fell = image_processor._isolate_with_fallback(world_ip.img, "remove_bg_api")
    assert cutout is not None and provider == "rembg" and fell == {"method": "remove_bg_api", "code": code}


@pytest.mark.parametrize("code", NOT_BILLING)
def test_other_failures_never_fall_back(world_ip, code):
    world_ip.cloud_result = (None, code)
    assert image_processor._isolate_with_fallback(world_ip.img, "photoroom") == (None, code, "photoroom", None)
    assert world_ip.local == []                                              # no local method was even tried
    assert image_processor._CLOUD_BREAKER.paused_code("photoroom") is None   # and nothing was paused
    image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    assert world_ip.cloud == ["photoroom", "photoroom"]                      # the next image tries the cloud again


def test_a_network_timeout_through_the_real_provider_is_not_a_fallback(world_ip, monkeypatch):
    import requests

    monkeypatch.setattr(image_processor, "_isolate_photoroom", world_ip.real_photoroom)

    def timeout(*a, **k):
        raise requests.exceptions.Timeout("slow")

    monkeypatch.setattr(image_processor.requests, "post", timeout)
    assert image_processor._isolate_with_fallback(world_ip.img, "photoroom") == (None, "photoroom_timeout", "photoroom", None)
    assert world_ip.local == []

    def refused(*a, **k):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(image_processor.requests, "post", refused)
    assert image_processor._isolate_with_fallback(world_ip.img, "photoroom")[:3] == (None, "photoroom_connection_error",
                                                                                      "photoroom")
    assert world_ip.local == [] and image_processor._CLOUD_BREAKER.paused_code("photoroom") is None


def test_the_real_provider_codes_for_credit_do_fall_back(world_ip, monkeypatch):
    class Reply:
        status_code = 402
        content = b'{"detail": "no credits"}'

    monkeypatch.setattr(image_processor, "_isolate_photoroom", world_ip.real_photoroom)
    monkeypatch.setattr(image_processor.requests, "post", lambda *a, **k: Reply())
    cutout, error, provider, fell = image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    assert cutout is not None and provider == "rembg" and fell == {"method": "photoroom", "code": "photoroom_402"}


def test_the_local_methods_and_none_are_never_replaced_by_anything(world_ip):
    for method in ("rembg", "grabcut"):
        world_ip.local_error[method] = f"{method}_failed"
    assert image_processor._isolate_with_fallback(world_ip.img, "rembg")[:2] == (None, "rembg_failed")
    assert image_processor._isolate_with_fallback(world_ip.img, "grabcut")[:2] == (None, "grabcut_failed")
    assert world_ip.local == ["rembg", "grabcut"] and world_ip.cloud == []
    assert image_processor._isolate_with_fallback(world_ip.img, "none")[:2] == (None, "unknown_bg_method")


def test_grabcut_is_never_part_of_the_automatic_fallback(world_ip):
    world_ip.local_error["rembg"] = "rembg_failed"           # installed, but the model is not there / it crashed
    assert image_processor._isolate_with_fallback(world_ip.img, "photoroom") == (None, "photoroom_402", "photoroom", None)
    assert world_ip.local == ["rembg"]                        # GrabCut was not tried after it

    world_ip.local.clear()
    world_ip.local_error.clear()
    world_ip.cloud.clear()
    image_processor.reset_cloud_breaker()                     # (the first part paused PhotoRoom: the credit is out)
    world_ip.installed = {"rembg": False, "grabcut": True}   # only GrabCut (cv2) is installed: nothing changes
    for _ in range(3):
        assert image_processor._isolate_with_fallback(world_ip.img, "photoroom") == (None, "photoroom_402", "photoroom", None)
    assert world_ip.local == [] and world_ip.cloud == ["photoroom"] * 3
    assert image_processor._CLOUD_BREAKER.paused_code("photoroom") is None
    result = process(world_ip)
    assert (result.path, result.isolated, result.provider, result.error) == (None, False, "photoroom", "photoroom_402")


def test_when_rembg_fails_the_original_cloud_error_comes_back(world_ip):
    world_ip.local_error.update(rembg="rembg_failed")
    assert image_processor._isolate_with_fallback(world_ip.img, "photoroom") == (None, "photoroom_402", "photoroom", None)
    assert world_ip.local == ["rembg"]


def test_the_configured_model_is_the_one_used(world_ip, monkeypatch):
    monkeypatch.setattr(config, "REMBG_MODEL", "birefnet-general-lite", raising=False)
    image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    monkeypatch.setattr(config, "REMBG_MODEL", "u2net", raising=False)          # unsupported: the default, never u2net
    image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    assert world_ip.models == ["birefnet-general-lite", "birefnet-general"]


def test_rembg_chosen_by_hand_uses_the_same_birefnet_model(world_ip, monkeypatch):
    # the owner: the small rembg models eat white packaging; a manual rembg choice reads REMBG_MODEL too
    assert image_processor._isolate_with_fallback(world_ip.img, "rembg")[:2][1] is None
    monkeypatch.setattr(config, "REMBG_MODEL", "birefnet-general-lite", raising=False)
    image_processor._isolate_with_fallback(world_ip.img, "rembg")
    assert world_ip.models == ["birefnet-general", "birefnet-general-lite"]


# ---------------------------------------------------------------------------
# 'off' and «no local method installed» keep today's behaviour
# ---------------------------------------------------------------------------

def test_off_keeps_todays_behaviour(world_ip, monkeypatch):
    monkeypatch.setattr(config, "BG_FALLBACK", "off", raising=False)
    for _ in range(3):
        assert image_processor._isolate_with_fallback(world_ip.img, "photoroom") == (None, "photoroom_402", "photoroom", None)
    assert world_ip.local == [] and world_ip.cloud == ["photoroom"] * 3          # every image still calls the cloud
    assert image_processor._CLOUD_BREAKER.paused_code("photoroom") is None
    result = process(world_ip)
    assert (result.path, result.isolated, result.provider, result.error) == (None, False, "photoroom", "photoroom_402")
    assert result.fallback_from is None


def test_rembg_not_installed_keeps_todays_behaviour(world_ip):
    world_ip.installed = {"rembg": False, "grabcut": False}
    for _ in range(3):
        assert image_processor._isolate_with_fallback(world_ip.img, "photoroom") == (None, "photoroom_402", "photoroom", None)
    assert world_ip.local == [] and world_ip.cloud == ["photoroom"] * 3          # no breaker either: nothing to go to
    assert image_processor._CLOUD_BREAKER.paused_code("photoroom") is None
    result = process(world_ip)
    assert (result.path, result.isolated, result.provider, result.error) == (None, False, "photoroom", "photoroom_402")


def test_no_isolation_is_never_a_fallback(world_ip):
    world_ip.installed = {"rembg": False, "grabcut": False}
    result = process(world_ip)
    assert result.path is None and result.provider == "photoroom" and result.error == "photoroom_402"


# ---------------------------------------------------------------------------
# The billing breaker
# ---------------------------------------------------------------------------

def test_the_cloud_call_is_paid_once_then_skipped_for_30_minutes_then_retried(world_ip):
    clock = world_ip.clock
    for _ in range(100):                                                       # a run of 100 products
        assert image_processor._isolate_with_fallback(world_ip.img, "photoroom")[2] == "rembg"
    assert world_ip.cloud == ["photoroom"] and len(world_ip.local) == 100
    assert image_processor._CLOUD_BREAKER.paused_code("photoroom") == "photoroom_402"

    clock.now += 30 * 60 - 1
    assert image_processor._isolate_with_fallback(world_ip.img, "photoroom")[3]["code"] == "photoroom_402"
    assert world_ip.cloud == ["photoroom"]                                    # 1 s before the end: still skipped

    clock.now += 1                                                             # 30 minutes: the credit may be back
    assert image_processor._isolate_with_fallback(world_ip.img, "photoroom")[2] == "rembg"
    assert world_ip.cloud == ["photoroom", "photoroom"]                       # retried once, failed again, paused again
    clock.now += 60
    image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    assert world_ip.cloud == ["photoroom", "photoroom"]

    world_ip.cloud_result = (object(), None)                                   # topped up
    clock.now += 30 * 60
    cutout, error, provider, fell = image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    assert provider == "photoroom" and fell is None and error is None
    assert image_processor._CLOUD_BREAKER.paused_code("photoroom") is None


def test_a_paused_method_hands_back_its_own_code_when_the_local_methods_fail(world_ip):
    image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    world_ip.local_error.update(rembg="rembg_failed")
    assert image_processor._isolate_with_fallback(world_ip.img, "photoroom") == (None, "photoroom_402", "photoroom", None)
    assert world_ip.cloud == ["photoroom"]


def test_each_cloud_method_has_its_own_pause(world_ip):
    image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    assert image_processor._isolate_with_fallback(world_ip.img, "remove_bg_api")[3] == {"method": "remove_bg_api",
                                                                                         "code": "removebg_402"}
    assert world_ip.cloud == ["photoroom", "remove_bg_api"]
    image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    image_processor._isolate_with_fallback(world_ip.img, "remove_bg_api")
    assert world_ip.cloud == ["photoroom", "remove_bg_api"]


def test_a_missing_key_costs_nothing_so_it_is_not_paused(world_ip):
    world_ip.cloud_result = (None, "photoroom_no_key")
    for _ in range(3):
        assert image_processor._isolate_with_fallback(world_ip.img, "photoroom")[3]["code"] == "photoroom_no_key"
    assert world_ip.cloud == ["photoroom"] * 3 and image_processor._CLOUD_BREAKER.paused_code("photoroom") is None


def test_the_pause_does_not_apply_when_the_setting_is_off_and_the_reset_hook_clears_it(world_ip, monkeypatch):
    image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    monkeypatch.setattr(config, "BG_FALLBACK", "off", raising=False)
    image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    assert world_ip.cloud == ["photoroom", "photoroom"]                       # 'off': today's behaviour, always calls
    monkeypatch.setattr(config, "BG_FALLBACK", "local", raising=False)
    assert image_processor.cloud_breaker().paused_code("photoroom") == "photoroom_402"
    image_processor.reset_cloud_breaker()
    assert image_processor.cloud_breaker().paused_code("photoroom") is None
    image_processor._isolate_with_fallback(world_ip.img, "photoroom")
    assert world_ip.cloud == ["photoroom"] * 3                                # reset: the cloud is tried again


def test_no_breaker_under_a_cassette(world_ip, monkeypatch):
    monkeypatch.setattr(image_processor.cassette, "active", lambda: object())
    for _ in range(3):
        assert image_processor._isolate_with_fallback(world_ip.img, "photoroom")[2] == "rembg"
    assert world_ip.cloud == ["photoroom"] * 3                                # a recorded run sees every call
    assert image_processor._CLOUD_BREAKER.paused_code("photoroom") is None


def test_the_breaker_is_thread_safe_and_the_clock_is_injectable():
    clock = Clock()
    breaker = image_processor.CloudBreaker(clock=clock, pause_s=60)
    started = []
    threads = [threading.Thread(target=lambda: started.append(breaker.trip("photoroom", "photoroom_402")))
               for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert started.count(True) == 1 and started.count(False) == 15           # one call starts the pause
    assert breaker.paused_code("photoroom") == "photoroom_402" and breaker.paused_code("remove_bg_api") is None
    clock.now += 59
    assert breaker.paused_code("photoroom") == "photoroom_402"
    clock.now += 1
    assert breaker.paused_code("photoroom") is None                           # over: a clean record
    assert breaker.trip("photoroom", "photoroom_429") is True
    breaker.reset()
    assert breaker.paused_code("photoroom") is None
    assert image_processor.CLOUD_BREAKER_PAUSE_S == 30 * 60


def test_a_worker_run_starts_with_a_clean_breaker():
    source = (ROOT / "main.py").read_text(encoding="utf-8")
    assert re.search(r"bg_fallback_count\(reset=True\)\n\s+image_processor\.reset_cloud_breaker\(\)", source)


# ---------------------------------------------------------------------------
# The result goes through the same crop gate as a normal isolation
# ---------------------------------------------------------------------------

def test_a_good_local_result_is_published_and_says_where_it_fell_back_from(world_ip):
    result = process(world_ip)
    assert result.path and os.path.isfile(result.path)
    assert result.isolated is True and result.provider == "rembg" and result.error is None
    assert result.fallback_from == {"method": "photoroom", "code": "photoroom_402"}
    assert result.quality_flags == [] and (result.width, result.height) == (800, 800)
    assert world_ip.cloud == ["photoroom"] and world_ip.local == ["rembg"]
    image_processor.cleanup_processed_image(result.path)


def test_a_cloud_isolation_that_works_has_no_fallback(world_ip):
    world_ip.cloud_result = (segment(world_ip.img), None)
    result = process(world_ip)
    assert result.isolated and result.provider == "photoroom" and result.fallback_from is None
    assert world_ip.local == []
    image_processor.cleanup_processed_image(result.path)


def test_a_local_result_that_fails_the_crop_gate_is_refused_like_a_normal_isolation(world_ip):
    world_ip.local_cutout = {"rembg": untouched, "grabcut": untouched}
    fell_back = process(world_ip)
    direct = process(world_ip, "rembg")                                       # the same rembg, chosen as the method
    assert fell_back.isolated is False and fell_back.quality_flags, "the gate must flag the untouched frame"
    assert fell_back.quality_flags == direct.quality_flags and fell_back.isolated == direct.isolated
    assert fell_back.provider == "rembg" and fell_back.fallback_from["code"] == "photoroom_402"
    assert direct.fallback_from is None
    # an unisolated canvas is what main.publish_image refuses / sends to review, whatever produced it
    import main
    assert main.PRESENTATION_FLAGS.isdisjoint(fell_back.quality_flags)
    for r in (fell_back, direct):
        image_processor.cleanup_processed_image(r.path)


def test_a_local_cutout_with_nothing_in_it_leaves_the_original_cloud_error(world_ip):
    world_ip.local_cutout = {"rembg": empty, "grabcut": empty}
    result = process(world_ip)
    assert (result.path, result.isolated, result.provider, result.error) == (None, False, "photoroom", "photoroom_402")
    assert result.fallback_from is None                                        # the owner keeps «تجاوز عزل الخلفية»


def test_a_timeout_stays_a_failure_end_to_end(world_ip):
    world_ip.cloud_result = (None, "photoroom_timeout")
    result = process(world_ip)
    assert (result.path, result.provider, result.error) == (None, "photoroom", "photoroom_timeout")
    assert world_ip.local == []


def test_the_second_attempt_of_one_image_does_not_pay_the_cloud_again(world_ip, monkeypatch):
    # a flagged first cut triggers the retry on the full frame: the paused cloud method is not called a second time
    world_ip.local_cutout = {"rembg": untouched, "grabcut": untouched}
    process(world_ip)
    assert world_ip.cloud == ["photoroom"]


# ---------------------------------------------------------------------------
# main.publish_image, the approval response and the worker
# ---------------------------------------------------------------------------

@pytest.fixture
def publish(monkeypatch, tmp_path):
    import cloudinary_storage
    import google_sheets
    import local_cache_db
    import main

    sheet = []
    state = {"isolated": True, "flags": [], "fallback": {"method": "photoroom", "code": "photoroom_402"},
             "provider": "rembg"}

    def processing(*a, **k):
        return image_processor.ProcessResult(_canvas(tmp_path / "canvas.png"), state["isolated"], state["provider"],
                                             None, 800, 800, quality_flags=list(state["flags"]),
                                             fallback_from=state["fallback"])

    monkeypatch.setattr(image_processor, "process_product_image_result", processing)
    monkeypatch.setattr(image_processor, "extract_metadata_from_image", lambda *a, **k: {})
    monkeypatch.setattr(cloudinary_storage, "upload_product_image_to_cloudinary", lambda *a, **k: CLOUD_LINK)
    monkeypatch.setattr(google_sheets, "update_image_link", lambda *a, **k: sheet.append(a[3]) or True)
    monkeypatch.setattr(local_cache_db, "find_image_owners", lambda *a, **k: [])

    def run(**kwargs):
        profile = processing_profile.ProcessingProfile(canvas=800, enhance=False, bg_method="photoroom")
        return main.publish_image("https://x/milk.jpg", "Almarai Milk 1L", "Almarai", 4, object(), 3,
                                  profile=profile, **kwargs)

    return run, sheet, state


@pytest.mark.usefixtures("offline")
@pytest.mark.parametrize("unclean", ["review", "refuse"])
def test_publish_image_returns_bg_fallback_for_a_locally_isolated_canvas(publish, unclean):
    run, sheet, state = publish
    res = run(unclean=unclean)
    assert res["status"] == "published" and res["isolated"] is True and res["provider"] == "rembg"
    assert res["bg_fallback"] == {"provider": "rembg", "from": "photoroom", "code": "photoroom_402"}
    assert res["bg_skipped"] is False and sheet == [CLOUD_LINK]                # clean link, no needs_review


@pytest.mark.usefixtures("offline")
def test_publish_image_has_no_bg_fallback_without_one(publish):
    run, sheet, state = publish
    state.update(fallback=None, provider="photoroom")
    assert run(unclean="review")["bg_fallback"] is None
    state.update(fallback={"method": ""}, provider="photoroom")                # a malformed value is no fallback
    assert run(unclean="review")["bg_fallback"] is None


@pytest.mark.usefixtures("offline")
def test_a_locally_isolated_canvas_that_failed_the_gate_is_refused_and_still_says_where_it_came_from(publish):
    run, sheet, state = publish
    state.update(isolated=False, flags=["edge_clipped"])
    refused = run(unclean="refuse")
    assert refused["status"] == "quality_refused" and sheet == []
    assert refused["bg_fallback"] == {"provider": "rembg", "from": "photoroom", "code": "photoroom_402"}
    review = run(unclean="review")                    # the worker: a review in the queue, nothing in the image cell
    assert review["status"] == "needs_review" and sheet == []
    assert review["bg_fallback"] == {"provider": "rembg", "from": "photoroom", "code": "photoroom_402"}


@pytest.mark.usefixtures("offline")
def test_the_approval_response_says_the_background_was_isolated_locally(select_env, monkeypatch, tmp_path):
    bridge, events, state = select_env
    monkeypatch.setattr(image_processor, "process_product_image_result",
                        lambda *a, **k: image_processor.ProcessResult(
                            _canvas(tmp_path / "bgfb.png"), True, "rembg", None, 800, 800,
                            fallback_from={"method": "remove_bg_api", "code": "removebg_402"}))
    result = bridge.action_select_image(dict(SELECT_PARAMS))
    assert result["status"] == "success" and result["isolated"] is True
    assert result["bg_fallback"] == {"provider": "rembg", "from": "remove_bg_api", "code": "removebg_402"}
    assert "warning" not in result and "warnings" not in result               # a note, not a warning
    assert not result["sheet_value"].startswith("needs_review:")
    monkeypatch.setattr(image_processor, "process_product_image_result",
                        lambda *a, **k: image_processor.ProcessResult(_canvas(tmp_path / "bgfb2.png"), True,
                                                                      "photoroom", None, 800, 800))
    assert "bg_fallback" not in bridge.action_select_image(dict(SELECT_PARAMS))


@pytest.mark.usefixtures("offline")
def test_the_worker_counts_locally_isolated_publishes_into_the_run(select_env, monkeypatch, tmp_path):
    import local_cache_db
    import main

    bridge, events, state = select_env
    monkeypatch.setattr(local_cache_db, "get_cached_product", lambda **k: None)
    monkeypatch.setattr(local_cache_db, "find_image_owners", lambda *a, **k: [])
    monkeypatch.setattr(local_cache_db, "delete_product_failure", lambda *a, **k: True)
    fallback = {"method": "photoroom", "code": "photoroom_402"}
    monkeypatch.setattr(image_processor, "process_product_image_result",
                        lambda *a, **k: image_processor.ProcessResult(_canvas(tmp_path / "w.png"), True, "rembg", None,
                                                                      800, 800, fallback_from=fallback))
    main.bg_fallback_count(reset=True)
    task = {"id": 7, "row_number": 4, "product_name": SELECT_PARAMS["product_name"], "brand": "Almarai",
            "barcode": SELECT_PARAMS["barcode"], "payload_json": "{}", "sku_key": SELECT_PARAMS["sku_key"]}
    for _ in range(2):
        assert main.auto_approve_product(task, {"url": V2_RESULT["url"]}, object(), 3,
                                         sku_key=SELECT_PARAMS["sku_key"]) == "published"
    assert main.bg_fallback_count() == 2 and main.bg_fallback_count(reset=True) == 2 and main.bg_fallback_count() == 0
    assert main.bg_skipped_count(reset=True) == 0                              # a different count
    monkeypatch.setattr(image_processor, "process_product_image_result",
                        lambda *a, **k: image_processor.ProcessResult(_canvas(tmp_path / "w.png"), True, "photoroom",
                                                                      None, 800, 800))
    assert main.auto_approve_product(task, {"url": V2_RESULT["url"]}, object(), 3,
                                     sku_key=SELECT_PARAMS["sku_key"]) == "published"
    assert main.bg_fallback_count() == 0


def test_the_run_report_counts_the_fallbacks():
    import run_report

    class DB:
        @staticmethod
        def run_outcome_counts(**k):
            return {"enqueued": 5, "searched": 5, "auto_published": 3, "ready_for_review": 2}

        @staticmethod
        def db_available():
            return True

    report = run_report.build_report("nightly", [{"stop_reason": "provider_down", "run_id": "r1", "bg_fallback": 2},
                                                 {"stop_reason": None, "run_id": "r2", "bg_fallback": 4}],
                                     1000.0, 1600.0, db=DB, sheets=None)
    assert report["bg_fallback"] == 6 and report["bg_skipped"] == 0
    quiet = run_report.build_report("manual", [{"stop_reason": None, "run_id": "r3"}], 1000.0, 1600.0, db=DB)
    assert quiet["bg_fallback"] == 0


# ---------------------------------------------------------------------------
# «فحص النشر» step 2
# ---------------------------------------------------------------------------

def test_the_check_is_green_with_a_note_and_the_advice_when_the_fallback_isolated_it(world, tmp_path):
    world.process_result = lambda: image_processor.ProcessResult(
        _check_canvas(tmp_path), True, "rembg", None, 800, 800, fallback_from={"method": "photoroom", "code": "photoroom_402"})
    result = world.run()
    step = steps(result)["process"]
    assert step["status"] == "ok" and step["code"] == "bg_fallback"
    assert "رصيد PhotoRoom خلص، والعزل مشي بـ rembg" in step["detail_ar"]
    assert "اشحن رصيد PhotoRoom لأفضل جودة" in step["detail_ar"]
    assert "photoroom_402" not in step["detail_ar"]
    assert steps(result)["upload"]["status"] == "ok" and world.uploaded
    assert result["overall"] == "ok" and "رصيد مزوّد العزل خلص" in result["summary_ar"]


@pytest.mark.parametrize("code, why, advice", [
    ("photoroom_no_key", "مفتاح PhotoRoom مش محفوظ", "ضيف مفتاح PhotoRoom بالإعدادات"),
    ("photoroom_401", "PhotoRoom رفض المفتاح", "حدّث مفتاح PhotoRoom بالإعدادات"),
    ("photoroom_429", "حصة PhotoRoom خلصت", "استنى شوي أو زد حصة PhotoRoom"),
    ("removebg_402", "رصيد remove.bg خلص", "اشحن رصيد remove.bg")])
def test_the_note_names_the_cause_and_the_provider(world, tmp_path, code, why, advice):
    method = "remove_bg_api" if code.startswith("removebg") else "photoroom"
    world.process_result = lambda: image_processor.ProcessResult(
        _check_canvas(tmp_path), True, "rembg", None, 800, 800, fallback_from={"method": method, "code": code})
    step = steps(world.run())["process"]
    assert step["status"] == "ok" and f"{why}، والعزل مشي بـ rembg" in step["detail_ar"] and advice in step["detail_ar"]


def test_a_flagged_local_cut_is_a_warning_that_still_says_why_it_was_local(world, tmp_path):
    world.process_result = lambda: image_processor.ProcessResult(
        _check_canvas(tmp_path), False, "rembg", None, 800, 800, quality_flags=["edge_clipped"],
        fallback_from={"method": "photoroom", "code": "photoroom_402"})
    step = steps(world.run())["process"]
    assert step["status"] == "warn" and step["code"] == "quality_flags"
    assert "رصيد PhotoRoom خلص، والعزل مشي بـ rembg" in step["detail_ar"]


def test_without_a_local_method_the_check_keeps_todays_message_and_the_skip_button(world):
    world.process_result = lambda: image_processor.ProcessResult(None, False, "photoroom", "photoroom_402")
    result = world.run()
    step = steps(result)["process"]
    assert step["status"] == "fail" and step["code"] == "photoroom_402"
    assert "رصيد PhotoRoom خلص أو الاشتراك موقوف." in step["detail_ar"] and pc.SKIP_ACTION in step["action_ar"]
    assert pc.bg_skip_offered(step["code"]) and result["overall"] == "fail"


def test_a_normal_check_is_unchanged(world):
    step = steps(world.run())["process"]
    assert step["status"] == "ok" and step["code"] == "" and "رصيد" not in step["detail_ar"]


# ---------------------------------------------------------------------------
# The approval toast (review screen)
# ---------------------------------------------------------------------------

@NEEDS_NODE
@pytest.mark.parametrize("cloud, name", [("photoroom", "PhotoRoom"), ("remove_bg_api", "remove.bg")])
def test_the_approval_toast_says_the_background_was_isolated_locally(tmp_path, cloud, name):
    out = page(r"""
openRow(30);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png',
                                           sheet_value: 'https://res.cloudinary.com/demo/b.png', isolated: true,
                                           bg_fallback: { provider: 'rembg', from: '__CLOUD__', code: '__CODE__' },
                                           sheet: 'written' });
await flush();
openRow(31);
press('Enter');
await flush();
answer(requests('/api/select_image')[1], { status: 'success', image_link: 'https://res.cloudinary.com/demo/c.png',
                                           isolated: true, bg_fallback: { provider: 'rembg', from: '__CLOUD__',
                                           code: '__CODE__' }, sheet: 'written' });
await flush();
out.toasts = toasts.map(t => [t.variant, t.text]);
openRow(30);
out.text = wsText();
""".replace("__CLOUD__", cloud).replace("__CODE__", "photoroom_402"), tmp_path,
               fixture([picked(30, "Almarai Milk 1L"), picked(31, "Almarai Laban 1L")]), config={"row": 30})
    said = [(v, t) for v, t in out["toasts"] if "طريقة محلية" in t]
    assert len(said) == 1 and said[0][0] == "info"                              # once per session, as information
    assert said[0][1].startswith(f"انعزلت الخلفية بطريقة محلية لأن رصيد {name} خلص")
    assert "تم الاعتماد." in out["text"] and "عزل محلي:" in out["text"]
    assert f"لأن رصيد {name} خلص، فانعزلت بـ rembg" in out["text"]
    assert "الخلفية لم تُعزل" not in out["text"] and "بدون عزل الخلفية" not in out["text"]


@NEEDS_NODE
def test_an_approval_without_a_fallback_says_nothing_about_one(tmp_path):
    out = page(r"""
openRow(30);
press('Enter');
await flush();
answer(requests('/api/select_image')[0], { status: 'success', image_link: 'https://res.cloudinary.com/demo/b.png',
                                           sheet_value: 'https://res.cloudinary.com/demo/b.png', isolated: true,
                                           bg_fallback: 'rembg', sheet: 'written' });
await flush();
out.toasts = toasts.map(t => t.text);
out.text = wsText();
""", tmp_path, fixture([picked(30, "Almarai Milk 1L")]), config={"row": 30})
    assert not [t for t in out["toasts"] if "طريقة محلية" in t] and "عزل محلي:" not in out["text"]


# ---------------------------------------------------------------------------
# Settings → «معالجة الصور» and the Health last-run card (the Laravel app)
# ---------------------------------------------------------------------------

LABEL = "لما يخلص رصيد خدمة العزل: جرّب طريقة محلية مجانية"


@NEEDS_PHP
def test_the_processing_data_offers_the_fallback_only_when_rembg_is_installed():
    out = _php(
        "putenv('BG_FALLBACK=');"
        "$out['unknown'] = SettingsController::processingData([], null)['fallback'];"
        "$out['none'] = SettingsController::processingData([], ['grabcut' => false, 'rembg' => false])['fallback'];"
        "$out['grabcut'] = SettingsController::processingData([], ['grabcut' => true, 'rembg' => false])['fallback'];"
        "$out['rembg'] = SettingsController::processingData([], ['grabcut' => false, 'rembg' => true])['fallback'];"
        "$out['off'] = SettingsController::processingData(['bg_fallback' => ['value' => 'off']], ['grabcut' => true, 'rembg' => true])['fallback'];"
        "$out['local'] = SettingsController::processingData(['bg_fallback' => ['value' => ' LOCAL ']], ['grabcut' => true, 'rembg' => true])['fallback'];"
        "$out['bad'] = SettingsController::processingData(['bg_fallback' => ['value' => 'magic']], ['grabcut' => true, 'rembg' => true])['fallback'];"
        "putenv('BG_FALLBACK=off');"
        "$out['env_off'] = SettingsController::processingData([], ['grabcut' => true, 'rembg' => true])['fallback'];"
        "$out['saved_wins'] = SettingsController::processingData(['bg_fallback' => ['value' => 'local']], ['grabcut' => true, 'rembg' => true])['fallback'];")
    assert out["unknown"] == out["none"] == {"show": False, "on": True}        # not shown: no promise, 'local' by default
    assert out["rembg"] == {"show": True, "on": True}
    assert out["grabcut"] == {"show": False, "on": True}                       # GrabCut alone: the fallback has nothing to run
    assert out["off"] == {"show": True, "on": False} and out["local"] == {"show": True, "on": True}
    assert out["bad"]["on"] is True                                           # an unsupported value reads as the default
    assert out["env_off"]["on"] is False and out["saved_wins"]["on"] is True
    assert set(_php("$out = SettingsController::BG_FALLBACKS;")) == set(config.BG_FALLBACKS)


@NEEDS_PHP
def test_the_processing_tab_shows_the_switch_with_rembg_and_hides_it_without(app_env):
    db, env = app_env["db"], app_env["env"]
    _put(db, {"bg_fallback": "off"})
    (grabcut_only,) = _kernel(env, [["GET", "/settings?tab=processing", {}]])    # the stub bridge: only GrabCut installed
    assert LABEL not in grabcut_only["body"] and "bg_fallback" not in grabcut_only["body"]
    stub = Path(env["CLI_BRIDGE_PATH"])
    text = stub.read_text(encoding="utf-8")
    assert '\\"rembg\\": false' in text
    stub.write_text(text.replace('\\"rembg\\": false', '\\"rembg\\": true'), encoding="utf-8")   # rembg installed
    (shown,) = _kernel(env, [["GET", "/settings?tab=processing", {}]])
    body = shown["body"]
    assert "عزل محلي بموديل BiRefNet (لازم يكون منزّل)" in body and "GrabCut ما بيشتغل تلقائياً أبداً" in body
    assert LABEL in body and 'data-bg-fallback' in body and 'name="bg_fallback_shown" value="1"' in body
    switch = re.search(r'<input[^>]*name="bg_fallback"[^>]*>', body).group(0)
    assert 'value="local"' in switch and "checked" not in switch              # saved 'off': the switch is off
    _put(db, {"bg_fallback": "local"})
    (shown,) = _kernel(env, [["GET", "/settings?tab=processing", {}]])
    assert "checked" in re.search(r'<input[^>]*name="bg_fallback"[^>]*>', shown["body"]).group(0)
    (hidden,) = _kernel(dict(env, LQ_STUB_MODE="down"), [["GET", "/settings?tab=processing", {}]])   # no local info
    assert LABEL not in hidden["body"] and "bg_fallback" not in hidden["body"]


@NEEDS_PHP
def test_saving_the_processing_form_writes_local_or_off_and_never_without_the_switch(app_env):
    db, env = app_env["db"], app_env["env"]
    _put(db, {"bg_fallback": "off", "bg_removal_method": "photoroom"})
    base = {"section": "processing", "output_canvas_size": "800", "bg_removal_method": "photoroom"}
    _kernel(env, [["POST", "/settings", dict(base)]])                           # the switch was not on the form
    assert _settings(db)["bg_fallback"] == "off"                                # left alone
    _kernel(env, [["POST", "/settings", dict(base, bg_fallback_shown="1", bg_fallback="local")]])
    assert _settings(db)["bg_fallback"] == "local"
    _kernel(env, [["POST", "/settings", dict(base, bg_fallback_shown="1")]])    # shown, unchecked: off
    assert _settings(db)["bg_fallback"] == "off"
    _kernel(env, [["POST", "/settings", dict(base, bg_fallback="local")]])      # a crafted value without the marker
    assert _settings(db)["bg_fallback"] == "off"
    _kernel(env, [["POST", "/settings", {"section": "serper", "serper_api_key": "", "bg_fallback_shown": "1",
                                         "bg_fallback": "local"}]])              # another form never writes it
    assert _settings(db)["bg_fallback"] == "off"
    assert _settings(db)["bg_removal_method"] == "photoroom"


@NEEDS_PHP
def test_the_last_run_card_says_how_many_were_isolated_locally():
    row = {"outcome": "done", "run_trigger": "nightly", "started_at": "2026-10-05 02:00:00", "auto_published": 5,
           "ready_for_review": 2, "report_json": {"reason_text": "", "bg_fallback": 4}}
    out = _php(f"$out[] = HealthController::lastRunCard({php_value(row)});"
               f"$out[] = HealthController::lastRunCard({php_value(dict(row, report_json={'bg_fallback': 0}))});")
    assert "انعزل بطريقة محلية لأن رصيد مزوّد العزل خلص 4" in out[0]["summary"]
    assert "انعزل بطريقة محلية" not in out[1]["summary"]


def test_the_php_and_node_files_are_in_sync_with_the_python_texts():
    import run_report

    health = (DASH / "app" / "Http" / "Controllers" / "HealthController.php").read_text("utf-8")
    assert run_report.BG_FALLBACK_TEXT in health
    core = (DASH / "public" / "js" / "review" / "core.js").read_text("utf-8")
    assert "انعزلت الخلفية بطريقة محلية لأن رصيد ${cloud} خلص" in core
    view = (DASH / "resources" / "views" / "settings" / "processing.blade.php").read_text("utf-8")
    assert LABEL in view


# ---------------------------------------------------------------------------
# One rembg session per process and model (a stubbed rembg module: nothing is downloaded)
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_rembg(monkeypatch):
    fake = types.ModuleType("rembg")
    fake.created, fake.used, fake.fail = [], [], set()

    def new_session(model):
        fake.created.append(model)
        if model in fake.fail:
            raise RuntimeError("model file is not there")
        return {"model": model}

    def remove(data, session=None):
        fake.used.append(session["model"])
        img = Image.open(io.BytesIO(data)).convert("RGB")
        buf = io.BytesIO()
        segment(img).save(buf, format="PNG")
        return buf.getvalue()

    fake.new_session, fake.remove = new_session, remove
    monkeypatch.setitem(sys.modules, "rembg", fake)
    image_processor.reset_rembg_sessions(everything=True)
    yield fake
    image_processor.reset_rembg_sessions(everything=True)


def test_the_session_is_created_once_per_model_and_reused(fake_rembg):
    img = Image.new("RGB", (80, 80), BG)
    ImageDraw.Draw(img).rectangle([20, 20, 60, 60], fill=BLUE)
    for _ in range(5):
        cutout, error = image_processor._isolate_rembg(img, "birefnet-general")
        assert error is None and cutout is not None and cutout.mode == "RGBA"
    image_processor._isolate_rembg(img, "birefnet-general-lite")
    image_processor._isolate_rembg(img, "birefnet-general")
    assert fake_rembg.created == ["birefnet-general", "birefnet-general-lite"]
    assert fake_rembg.used == ["birefnet-general"] * 5 + ["birefnet-general-lite", "birefnet-general"]


def test_the_session_is_shared_by_threads_and_created_once(fake_rembg):
    img = Image.new("RGB", (80, 80), BG)
    ImageDraw.Draw(img).rectangle([20, 20, 60, 60], fill=BLUE)
    results = []
    threads = [threading.Thread(target=lambda: results.append(image_processor._isolate_rembg(img, "birefnet-general")[1]))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [None] * 8 and fake_rembg.created == ["birefnet-general"]


def test_a_model_that_cannot_be_prepared_fails_once_and_is_not_retried_until_the_next_run(fake_rembg):
    fake_rembg.fail.add("birefnet-general")
    img = Image.new("RGB", (80, 80), BG)
    for _ in range(3):
        assert image_processor._isolate_rembg(img, "birefnet-general") == (None, "rembg_failed")
    assert fake_rembg.created == ["birefnet-general"]                         # one attempt, not one per product
    image_processor.reset_rembg_sessions()                                    # the start of a worker run
    fake_rembg.fail.clear()
    cutout, error = image_processor._isolate_rembg(img, "birefnet-general")
    assert error is None and fake_rembg.created == ["birefnet-general"] * 2


def test_the_fallback_runs_through_the_cached_session_and_keeps_the_original_error_when_it_cannot(world_ip, fake_rembg,
                                                                                              monkeypatch):
    monkeypatch.setattr(image_processor, "_isolate_rembg", world_ip.real_rembg)
    for _ in range(3):
        cutout, error, provider, fell = image_processor._isolate_with_fallback(world_ip.img, "photoroom")
        assert provider == "rembg" and fell == {"method": "photoroom", "code": "photoroom_402"} and cutout is not None
    assert fake_rembg.created == ["birefnet-general"] and fake_rembg.used == ["birefnet-general"] * 3
    assert world_ip.cloud == ["photoroom"]                                    # and the cloud was paid once
    monkeypatch.setattr(config, "REMBG_MODEL", "birefnet-general-lite", raising=False)
    fake_rembg.fail.add("birefnet-general-lite")                              # this model is not on the machine
    assert image_processor._isolate_with_fallback(world_ip.img, "photoroom") == (None, "photoroom_402", "photoroom", None)
