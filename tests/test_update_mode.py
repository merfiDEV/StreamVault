import main
import asyncio
from pathlib import Path
import zipfile


def test_fetch_latest_app_release_prefers_primary_asset(monkeypatch):
    payload = {
        "tag_name": "0.24.0",
        "name": "Release 0.24.0",
        "body": "fixes",
        "published_at": "2026-06-07T10:06:31Z",
        "assets": [
            {"name": "StreamVault.exe", "browser_download_url": "https://example.invalid/StreamVault.exe"},
            {"name": "StreamVault.zip", "browser_download_url": "https://example.invalid/StreamVault.zip"},
        ],
    }

    monkeypatch.setattr(main, "_latest_release_cache", {})
    monkeypatch.setattr(main, "_latest_release_cache_time", 0.0)
    monkeypatch.setattr(main, "_current_install_mode", lambda: "onedir")
    monkeypatch.setattr(main, "_urlopen_json", lambda url, timeout=10: payload)

    release = main._fetch_latest_app_release(force=True)

    assert release["asset_name"] == "StreamVault.zip"
    assert release["download_url"] == "https://example.invalid/StreamVault.zip"


def test_validate_downloaded_asset_accepts_windows_exe(tmp_path):
    exe_path = tmp_path / "StreamVault.exe"
    exe_path.write_bytes(b"MZ" + b"\0" * 16)

    main._validate_downloaded_asset(exe_path, ".exe")


def test_validate_downloaded_asset_rejects_invalid_exe(tmp_path):
    exe_path = tmp_path / "StreamVault.exe"
    exe_path.write_bytes(b"not an exe")

    try:
        main._validate_downloaded_asset(exe_path, ".exe")
    except RuntimeError as exc:
        assert "Windows executable" in str(exc)
    else:
        raise AssertionError("invalid exe was accepted")


def test_extract_release_archive_rejects_zip_slip(monkeypatch, tmp_path):
    archive_path = tmp_path / "StreamVault.zip"
    updates_dir = tmp_path / "updates"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("../evil.txt", "boom")

    monkeypatch.setattr(main, "_update_workspace", lambda: updates_dir)

    try:
        main._extract_release_archive(archive_path, "9.9.9")
    except RuntimeError as exc:
        assert "Unsafe ZIP member path" in str(exc)
    else:
        raise AssertionError("unsafe zip member was accepted")


def test_onedir_installer_runs_outside_install_root(monkeypatch, tmp_path):
    updates_dir = tmp_path / "updates"
    target_dir = tmp_path / "StreamVault"
    source_dir = updates_dir / "staging" / "StreamVault"
    source_dir.mkdir(parents=True)
    target_dir.mkdir()
    (source_dir / "StreamVault.exe").write_bytes(b"MZ")
    calls = {}

    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main, "_current_install_mode", lambda: "onedir")
    monkeypatch.setattr(main, "_install_root", lambda: target_dir)
    monkeypatch.setattr(main, "_update_workspace", lambda: updates_dir)
    monkeypatch.setattr(main.subprocess, "Popen", lambda args, **kwargs: calls.update({"args": args, "kwargs": kwargs}))

    main._spawn_update_installer(source_dir)

    assert calls["kwargs"]["cwd"] == str(updates_dir)


def test_onefile_installer_runs_outside_install_root(monkeypatch, tmp_path):
    updates_dir = tmp_path / "updates"
    target_dir = tmp_path / "app"
    source_file = updates_dir / "StreamVault-9.9.9.exe"
    target_exe = target_dir / "StreamVault.exe"
    updates_dir.mkdir()
    target_dir.mkdir()
    source_file.write_bytes(b"MZ")
    target_exe.write_bytes(b"MZ")
    calls = {}

    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main.sys, "executable", str(target_exe))
    monkeypatch.setattr(main, "_update_workspace", lambda: updates_dir)
    monkeypatch.setattr(main.subprocess, "Popen", lambda args, **kwargs: calls.update({"args": args, "kwargs": kwargs}))

    main._spawn_onefile_update_installer(source_file)

    assert calls["kwargs"]["cwd"] == str(updates_dir)


def test_create_app_shortcut_uses_desktop_lnk(monkeypatch, tmp_path):
    desktop = tmp_path / "Desktop"
    target = tmp_path / "StreamVault.exe"
    target.write_bytes(b"MZ")
    calls = {}

    def fake_create_shortcut(shortcut_path: Path, target_path: Path, arguments: str, working_dir: Path):
        calls["shortcut_path"] = shortcut_path
        calls["target"] = target_path
        calls["arguments"] = arguments
        calls["working_dir"] = working_dir
        shortcut_path.write_text("shortcut", encoding="utf-8")

    monkeypatch.setattr(main.os, "name", "nt")
    monkeypatch.setattr(main, "_desktop_path", lambda: desktop)
    monkeypatch.setattr(main, "_shortcut_target", lambda: (target, "--web", target.parent))
    monkeypatch.setattr(main, "_create_windows_shortcut", fake_create_shortcut)

    shortcut = main._create_app_shortcut()

    assert shortcut == desktop / "StreamVault.lnk"
    assert shortcut.exists()
    assert calls == {
        "shortcut_path": desktop / "StreamVault.lnk",
        "target": target,
        "arguments": "--web",
        "working_dir": target.parent,
    }


def test_create_app_shortcut_reports_windows_shortcut_error(monkeypatch, tmp_path):
    desktop = tmp_path / "Desktop"
    target = tmp_path / "StreamVault.exe"
    target.write_bytes(b"MZ")

    monkeypatch.setattr(main.os, "name", "nt")
    monkeypatch.setattr(main, "_desktop_path", lambda: desktop)
    monkeypatch.setattr(main, "_shortcut_target", lambda: (target, "", target.parent))
    monkeypatch.setattr(main, "_create_windows_shortcut", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))

    try:
        main._create_app_shortcut()
    except RuntimeError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("shortcut creation failure was ignored")


def test_update_info_reports_blocked_when_update_in_progress(monkeypatch):
    monkeypatch.setattr(main, "_current_install_mode", lambda: "onefile")
    monkeypatch.setattr(main, "_fetch_latest_app_release", lambda force=False: {
        "version": "0.24.1",
        "name": "Release 0.24.1",
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
        "version": "0.24.1",
        "name": "Release 0.24.1",
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
        "version": "0.24.1",
        "name": "Release 0.24.1",
        "body": "fixes",
        "published_at": "2026-06-06T23:06:23Z",
        "asset_name": "StreamVault.exe",
        "download_url": "https://example.invalid/StreamVault.exe",
        "source": "github_api",
    }
    archive_path = tmp_path / "StreamVault-0.24.1.exe"
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
