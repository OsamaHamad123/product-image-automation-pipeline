"""install.sh --with-embeddings and the EMBEDDINGS switch it turns on.

The flag installs onnxruntime, downloads and checks the pinned model through scripts/backfill_embeddings.py --setup
dinov2 and saves embeddings=dinov2 in system_settings, which config.load_db_config hands to every process (the units
and the dashboard's Python alike); install.sh still never writes .env. Without the flag nothing of it runs and the
code default stays 'off'.
"""

import pytest

from test_deploy_ubuntu import APP, needs_bash, needs_plain_path, run_install


@needs_bash
def test_the_flag_is_in_the_help():
    done = run_install("--help")
    assert done.returncode == 0 and "--with-embeddings" in done.stdout


@needs_bash
@needs_plain_path
def test_without_the_flag_nothing_of_it_runs():
    out = run_install("--dry-run", str(APP), "--local-only").stdout
    assert "onnxruntime" not in out and "backfill_embeddings" not in out


@needs_bash
@needs_plain_path
def test_the_flag_installs_onnxruntime_and_sets_the_model_up_after_the_schema():
    done = run_install("--dry-run", str(APP), "--local-only", "--with-embeddings")
    out = done.stdout
    assert "6b/10 onnxruntime + DINOv2-small model" in out, done.stderr       # (exit 2: a checkout has no .env)
    assert "pip install --quiet --disable-pip-version-check onnxruntime\\>=1.17\\,\\<2" in out
    assert "U2NET_HOME=/var/lib/laqta/models" in out and "backfill_embeddings.py --setup dinov2" in out
    # the setting is saved in the database, so the schema exists first
    assert out.index("local_cache_db.init_db") < out.index("--setup dinov2") < out.index("7/10 systemd units")
    assert "rembg" not in out                                  # independent of --with-birefnet


@pytest.fixture
def config_snapshot():
    """config.load_db_config rewrites module globals: put every one of them back afterwards."""
    import config

    before = dict(vars(config))
    yield config
    for name in list(vars(config)):
        if name not in before:
            delattr(config, name)
    for name, value in before.items():
        if vars(config).get(name) is not value:
            setattr(config, name, value)


@pytest.mark.parametrize("stored, expected", [("dinov2", "dinov2"), (" SIGLIP2 ", "siglip2"), ("off", "off"),
                                              ("clip", None), ("", None)])
def test_the_dashboard_setting_reaches_every_process(config_snapshot, monkeypatch, fake_connection, stored,
                                                     expected):
    import db_connect
    from catalog_match import settings

    config = config_snapshot
    if hasattr(config, "EMBEDDINGS"):
        delattr(config, "EMBEDDINGS")

    def responder(sql, params):
        if sql.startswith("SHOW TABLES"):
            return [{"Tables_in_db": "system_settings"}]
        if sql.startswith("SELECT `key`, `value` FROM system_settings"):
            return [{"key": "embeddings", "value": stored}]
        return []

    monkeypatch.setattr(db_connect, "connect", lambda *a, **k: fake_connection(responder))
    config.load_db_config()
    assert getattr(config, "EMBEDDINGS", None) == expected
    monkeypatch.setattr(settings, "_config", config)
    monkeypatch.setenv("EMBEDDINGS", "dinov2")
    # the dashboard setting wins over the environment; without one the environment (or 'off') stays
    assert settings.embeddings_mode() == (expected or "dinov2")
