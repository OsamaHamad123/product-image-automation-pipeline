"""Which local files the review actions may read (and delete).

select_image publishes the image at an http(s) link only: a server path in image_url used to be read and published
(any image on the machine). upload_manual_image reads the file the dashboard saved in the project's temp/ folder and
nothing else (realpath: no '..', no symbolic link out of it); it deletes that file after publishing, so a path
outside temp/ could also have deleted any file.
"""

import os

import pytest

from test_cli_bridge_contract import SELECT_PARAMS, _canvas, bridge, select_env  # noqa: F401 - fixtures


@pytest.mark.parametrize("image_url", ["/etc/passwd", "temp/manual.png", "C:\\Users\\owner\\secret.png",
                                       "file:///etc/hostname", "ftp://example.com/a.jpg", "data:image/png;base64,AA",
                                       "../credentials.json"])
def test_select_image_takes_an_http_link_only(select_env, image_url):
    cli_bridge, events, _state = select_env
    result = cli_bridge.action_select_image(dict(SELECT_PARAMS, image_url=image_url))
    assert (result["status"], result["error_code"]) == ("failed", "bad_image_url")
    assert events == []                                      # nothing read, processed, uploaded or written


def test_select_image_with_an_https_link_still_publishes(select_env):
    cli_bridge, events, _state = select_env
    assert cli_bridge.action_select_image(dict(SELECT_PARAMS))["status"] == "success"
    assert events[0][:2] == ("process", SELECT_PARAMS["image_url"])


def _upload(cli_bridge, path, **extra):
    params = {k: SELECT_PARAMS[k] for k in ("row_number", "product_name", "brand", "barcode", "sku_key")}
    return cli_bridge.action_upload_manual_image(dict(params, file_path=str(path), **extra))


@pytest.fixture
def uploads(select_env, monkeypatch, tmp_path):
    cli_bridge, events, _state = select_env
    folder = tmp_path / "project" / "temp"
    folder.mkdir(parents=True)
    monkeypatch.setattr(cli_bridge, "UPLOAD_DIR", str(folder))
    return cli_bridge, events, folder


def test_an_upload_in_temp_is_published_and_removed(uploads):
    cli_bridge, events, folder = uploads
    upload = folder / "manual_1_abc.png"
    _canvas(upload, (300, 300))
    assert _upload(cli_bridge, upload)["status"] == "success"
    assert events[0][:2] == ("process", os.path.realpath(upload)) and not upload.exists()


def test_a_file_outside_temp_is_neither_read_nor_deleted(uploads, tmp_path):
    cli_bridge, events, folder = uploads
    secret = tmp_path / "project" / "secret.png"
    _canvas(secret, (300, 300))
    outside = [secret, folder / ".." / "secret.png", folder]
    link = folder / "innocent.png"
    try:
        link.symlink_to(secret)
        outside.append(link)
    except (OSError, NotImplementedError):  # pragma: no cover - a Windows account without the symlink right
        pass
    for path in outside:
        result = _upload(cli_bridge, path)
        assert result["status"] == "failed" and "Missing parameters" in result["error"], path
    assert secret.exists() and events == []


def test_a_missing_upload_is_refused(uploads):
    cli_bridge, events, folder = uploads
    assert _upload(cli_bridge, folder / "gone.png")["status"] == "failed"
    assert _upload(cli_bridge, "")["status"] == "failed"
    assert events == []


def test_the_upload_folder_is_the_one_the_dashboard_writes_to():
    import pathlib

    import cli_bridge

    root = pathlib.Path(cli_bridge.__file__).resolve().parent
    assert pathlib.Path(cli_bridge.UPLOAD_DIR) == root / "temp"
    controller = (root / "dashboard" / "app" / "Http" / "Controllers" / "ApiController.php").read_text(encoding="utf-8")
    assert "$tempDir = $this->automationPath('temp');" in controller          # base_path('..') . '/temp'
