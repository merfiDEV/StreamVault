import main
import asyncio


def test_update_info_reports_blocked_when_update_in_progress(monkeypatch):
    monkeypatch.setattr(main, "_current_install_mode", lambda: "onefile")
    monkeypatch.setattr(main, "_fetch_latest_app_release", lambda force=False: {
        "version": "0.23.1",
        "name": "Release 0.23.1",
        "body": "fixes",
        "published_at": "2026-06-06T23:06:23Z",
        "asset_name": "StreamVault.exe",
        "download_url": "https://example.invalid/StreamVault.exe",
        "source": "github_api",
    })
    monkeypatch.setattr(main, "_update_in_progress", True)

    data = asyncio.run(main.app_update_info())

    assert data["update_in_progress"] is True
    assert data["update_blocked_reason"] == "Обновление уже выполняется. Дождитесь завершения."


def test_update_info_reports_asset_blocked_reason(monkeypatch):
    monkeypatch.setattr(main, "_current_install_mode", lambda: "onedir")
    monkeypatch.setattr(main, "_fetch_latest_app_release", lambda force=False: {
        "version": "0.23.1",
        "name": "Release 0.23.1",
        "body": "fixes",
        "published_at": "2026-06-06T23:06:23Z",
        "asset_name": "StreamVault.exe",
        "download_url": "https://example.invalid/StreamVault.exe",
        "source": "github_api",
    })
    monkeypatch.setattr(main, "_update_in_progress", False)

    data = asyncio.run(main.app_update_info())

    assert data["update_in_progress"] is False
    assert data["asset_ok"] is False
    assert data["update_blocked_reason"] == "Для этой сборки нужен релизный asset StreamVault.zip"


def test_update_info_force_flag_reaches_fetcher(monkeypatch):
    seen = {}

    def fake_fetch(force=False):
        seen["force"] = force
        return {
            "version": "0.23.1",
            "name": "Release 0.23.1",
            "body": "fixes",
            "published_at": "2026-06-06T23:06:23Z",
            "asset_name": "StreamVault.exe",
            "download_url": "https://example.invalid/StreamVault.exe",
            "source": "github_api",
        }

    monkeypatch.setattr(main, "_current_install_mode", lambda: "onefile")
    monkeypatch.setattr(main, "_fetch_latest_app_release", fake_fetch)
    monkeypatch.setattr(main, "_update_in_progress", False)

    data = asyncio.run(main.app_update_info(force=True))

    assert seen["force"] is True
    assert data["latest"] == "0.23.1"


def test_download_update_rejects_when_not_frozen(monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", False, raising=False)
    monkeypatch.setattr(main, "_update_in_progress", False)

    data = asyncio.run(main._download_latest_app_release_async())

    assert data["error_code"] == "app_not_frozen"


def test_download_update_rejects_when_update_in_progress(monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main, "_update_in_progress", True)

    data = asyncio.run(main._download_latest_app_release_async())

    assert data["error_code"] == "update_in_progress"
    assert data["error_step"] == "preflight"


def test_download_update_rejects_when_downloads_active(monkeypatch):
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main, "_update_in_progress", False)
    monkeypatch.setattr(main.download_manager, "get_active_count", lambda: 1)

    data = asyncio.run(main._download_latest_app_release_async())

    assert data["error_code"] == "downloads_active"


def test_download_update_returns_scheduled_onefile(monkeypatch, tmp_path):
    async def noop_broadcast(*args, **kwargs):
        return None

    release = {
        "version": "0.23.1",
        "name": "Release 0.23.1",
        "body": "fixes",
        "published_at": "2026-06-06T23:06:23Z",
        "asset_name": "StreamVault.exe",
        "download_url": "https://example.invalid/StreamVault.exe",
        "source": "github_api",
    }
    archive_path = tmp_path / "StreamVault-0.23.1.exe"
    calls = {}

    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main, "_update_in_progress", False)
    monkeypatch.setattr(main, "_current_install_mode", lambda: "onefile")
    monkeypatch.setattr(main.download_manager, "get_active_count", lambda: 0)
    monkeypatch.setattr(main, "_fetch_latest_app_release", lambda force=False: release)
    monkeypatch.setattr(main, "_download_release_asset", lambda rel, progress_callback=None: (archive_path, [{"url": "https://example.invalid", "error": ""}]))
    monkeypatch.setattr(main, "_spawn_onefile_update_installer", lambda source_file: calls.setdefault("source", source_file))
    monkeypatch.setattr(main.manager, "broadcast", noop_broadcast)

    data = asyncio.run(main._download_latest_app_release_async())

    assert data["status"] == "scheduled"
    assert data["downloaded_to"] == str(archive_path)
    assert calls["source"] == archive_path


def test_download_update_rejects_asset_mismatch_onedir(monkeypatch):
    release = {
        "version": "0.23.1",
        "name": "Release 0.23.1",
        "body": "fixes",
        "published_at": "2026-06-06T23:06:23Z",
        "asset_name": "StreamVault.exe",
        "download_url": "https://example.invalid/StreamVault.exe",
        "source": "github_api",
    }

    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main, "_update_in_progress", False)
    monkeypatch.setattr(main, "_current_install_mode", lambda: "onedir")
    monkeypatch.setattr(main.download_manager, "get_active_count", lambda: 0)
    monkeypatch.setattr(main, "_fetch_latest_app_release", lambda force=False: release)

    data = asyncio.run(main._download_latest_app_release_async())

    assert data["error_code"] == "asset_mismatch"
    assert data["error_step"] == "select_asset"


def test_current_install_mode_detects_onedir_when_internal_dir_exists(tmp_path, monkeypatch):
    internal_dir = tmp_path / "_internal"
    internal_dir.mkdir()

    monkeypatch.setattr(main, "_install_root", lambda: tmp_path)

    assert main._current_install_mode() == "onedir"


def test_current_install_mode_defaults_to_onefile_without_markers(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "_install_root", lambda: tmp_path)

    assert main._current_install_mode() == "onefile"
