"""Главный файл приложения — FastAPI сервер + PyWebView."""

import asyncio
import json
import os
import re
import platform
import shutil
import subprocess
import sys
import time
import zipfile
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from textwrap import dedent

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.config import Settings, load_settings, save_settings, ensure_save_location
from core.downloader import download_manager, DownloadStatus
from core.history import history_manager, HistoryRecord
from core.version import APP_VERSION
from core.utils import get_data_path, get_resource_path


# --- i18n (интернационализация) ---

_i18n_cache: dict[str, dict] = {}
GITHUB_REPO = "merfiDEV/StreamVault"
GITHUB_REPO_URL = f"https://github.com/{GITHUB_REPO}"
GITHUB_RELEASES_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
APP_UPDATE_ASSET_NAME = "StreamVault.zip"
APP_UPDATE_EXE_ASSET_NAME = "StreamVault.exe"
APP_UPDATE_INSTALLER_LOG_NAME = "apply_update.log"
APP_UPDATE_WAIT_SECONDS = 180
APP_UPDATE_START_CHECK_SECONDS = 30
_latest_release_cache: dict = {}
_latest_release_cache_time: float = 0.0
_RELEASE_CACHE_TTL: float = 300.0
_last_update_error: dict = {}
_last_update_status: dict = {"stage": "idle"}
_update_in_progress: bool = False


def _load_locale(locale: str) -> dict | None:
    """Загрузить JSON-файл перевода для указанного языка."""
    if locale in _i18n_cache:
        return _i18n_cache[locale]
    locale_file = get_resource_path("locales") / f"{locale}.json"
    if not locale_file.exists():
        return None
    with open(locale_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    _i18n_cache[locale] = data
    return data


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = [int(chunk) for chunk in re.findall(r"\d+", version or "")]
    return tuple(parts[:4]) if parts else (0,)


def _short_error_text(err: Exception | str, limit: int = 500) -> str:
    text = str(err or "").strip()
    return text[:limit]


def _build_update_error(code: str, step: str, message: str, *, exception: Exception | str | None = None, attempts: list | None = None, release: dict | None = None, extra: dict | None = None) -> dict:
    payload = {
        "ok": False,
        "error": message,
        "error_code": code,
        "error_step": step,
        "repo": GITHUB_REPO,
        "repo_url": GITHUB_REPO_URL,
        "current": APP_VERSION,
        "attempts": attempts or [],
    }
    if exception is not None:
        payload["error_details"] = _short_error_text(exception)
    if release:
        payload["current_release"] = {
            "version": release.get("version", ""),
            "name": release.get("name", ""),
            "asset_name": release.get("asset_name", ""),
            "published_at": release.get("published_at", ""),
            "download_url": release.get("download_url", ""),
        }
    if extra:
        payload.update(extra)

    global _last_update_error
    _last_update_error = payload
    return payload


def _set_update_status(stage: str, message: str = "", *, release: dict | None = None, extra: dict | None = None) -> dict:
    payload = {
        "stage": stage,
        "message": message,
        "updated_at": time.time(),
    }
    if release:
        payload["release"] = {
            "version": release.get("version", ""),
            "name": release.get("name", ""),
            "asset_name": release.get("asset_name", ""),
            "published_at": release.get("published_at", ""),
        }
    if extra:
        payload.update(extra)

    global _last_update_status
    _last_update_status = payload
    return payload


def _github_release_urls(asset_name: str = APP_UPDATE_ASSET_NAME, version: str = "") -> list[str]:
    urls = [GITHUB_RELEASES_LATEST]
    normalized_asset = asset_name or APP_UPDATE_ASSET_NAME
    if version:
        clean_version = str(version).lstrip("v")
        tag_candidates = [clean_version, f"v{clean_version}"]
        for tag in tag_candidates:
            urls.append(f"{GITHUB_REPO_URL}/releases/download/{tag}/{normalized_asset}")
    urls.extend([
        f"{GITHUB_REPO_URL}/releases/latest/download/{normalized_asset}",
        f"{GITHUB_REPO_URL}/releases",
    ])
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def _github_asset_download_urls(asset_name: str, version: str = "") -> list[str]:
    normalized_asset = asset_name or _expected_app_asset_name()
    urls: list[str] = []
    if version:
        clean_version = str(version).lstrip("v")
        urls.extend([
            f"{GITHUB_REPO_URL}/releases/download/{clean_version}/{normalized_asset}",
            f"{GITHUB_REPO_URL}/releases/download/v{clean_version}/{normalized_asset}",
        ])
    urls.append(f"{GITHUB_REPO_URL}/releases/latest/download/{normalized_asset}")

    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


def _urlopen_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "StreamVault"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _update_workspace() -> Path:
    appdata_root = Path(get_data_path("version.txt")).parent
    return appdata_root / "updates"


def _update_installer_log_path() -> Path:
    return _update_workspace() / APP_UPDATE_INSTALLER_LOG_NAME


def _decode_update_runtime_error(err: Exception | str) -> dict:
    try:
        data = json.loads(str(err))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def _current_install_mode() -> str:
    install_root = _install_root()
    internal_dir = install_root / "_internal"
    markers = (
        "base_library.zip",
        "python313.dll",
        "python312.dll",
        "python311.dll",
        "python310.dll",
        "python39.dll",
    )
    if internal_dir.exists() and internal_dir.is_dir():
        return "onedir"
    if any((install_root / marker).exists() for marker in markers):
        return "onedir"
    if any((internal_dir / marker).exists() for marker in markers):
        return "onedir"
    return "onefile"


def _expected_app_asset_name() -> str:
    return APP_UPDATE_ASSET_NAME if _current_install_mode() == "onedir" else APP_UPDATE_EXE_ASSET_NAME


def _asset_fallback_names() -> list[str]:
    primary = _expected_app_asset_name()
    secondary = APP_UPDATE_EXE_ASSET_NAME if primary.lower().endswith(".zip") else APP_UPDATE_ASSET_NAME
    return [primary, secondary]


def _asset_matches_install_mode(asset_name: str, install_mode: str | None = None) -> bool:
    suffix = Path(asset_name or "").suffix.lower()
    mode = install_mode or _current_install_mode()
    return suffix == ".zip" if mode == "onedir" else suffix == ".exe"


def _fetch_latest_app_release(force: bool = False) -> dict:
    global _latest_release_cache, _latest_release_cache_time
    now = time.monotonic()

    if not force and _latest_release_cache and (now - _latest_release_cache_time) < _RELEASE_CACHE_TTL:
        cached = dict(_latest_release_cache)
        cached["source"] = "cache"
        return cached

    errors: list[dict] = []
    try:
        payload = _urlopen_json(GITHUB_RELEASES_LATEST, timeout=10)
        assets = payload.get("assets") or []
        assets_by_name = {str(item.get("name") or "").lower(): item for item in assets}
        asset = next((assets_by_name[name.lower()] for name in _asset_fallback_names() if name.lower() in assets_by_name), None)
        if asset is None and assets:
            wanted_suffix = ".zip" if _current_install_mode() == "onedir" else ".exe"
            asset = next(
                (
                    item
                    for item in assets
                    if str(item.get("name") or "").lower().endswith(wanted_suffix)
                ),
                assets[0],
            )

        latest_version = str(payload.get("tag_name") or "").lstrip("v")
        release = {
            "version": latest_version,
            "name": str(payload.get("name") or "").strip(),
            "body": str(payload.get("body") or "").strip(),
            "published_at": str(payload.get("published_at") or "").strip(),
            "asset_name": str(asset.get("name") or "") if asset else "",
            "download_url": str(asset.get("browser_download_url") or "") if asset else "",
            "source": "github_api",
        }
        _latest_release_cache = release
        _latest_release_cache_time = time.monotonic()
        return release
    except Exception as e:
        errors.append({"url": GITHUB_RELEASES_LATEST, "error": _short_error_text(e)})

    if _latest_release_cache:
        cached = dict(_latest_release_cache)
        cached["source"] = "cache"
        cached["stale"] = True
        cached["fetch_errors"] = errors
        return cached

    raise RuntimeError(json.dumps({"code": "release_fetch_failed", "attempts": errors}, ensure_ascii=False))


def _download_release_asset(
    release: dict,
    progress_callback=None,
) -> tuple[Path, list[dict]]:
    asset_name = release.get("asset_name") or _expected_app_asset_name()
    version = release.get("version") or ""
    expected_suffix = Path(asset_name).suffix.lower() or ".zip"
    candidates: list[str] = []

    if release.get("download_url"):
        candidates.append(str(release["download_url"]))
    candidates.extend(_github_asset_download_urls(asset_name, version))

    attempts: list[dict] = []
    updates_dir = _update_workspace()
    updates_dir.mkdir(parents=True, exist_ok=True)

    archive_name = f"StreamVault-{version or 'latest'}{expected_suffix}"
    archive_path = updates_dir / archive_name
    temp_path = updates_dir / f"{archive_name}.part"
    temp_error = None
    chunk_size = 512 * 1024

    for url in candidates:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "StreamVault"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                last_report = 0.0
                with open(temp_path, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        now = time.monotonic()
                        if progress_callback and (now - last_report) >= 0.4:
                            progress_callback(downloaded, total)
                            last_report = now
                if progress_callback:
                    progress_callback(downloaded, total)
            _validate_downloaded_asset(temp_path, expected_suffix)
            if archive_path.exists():
                archive_path.unlink()
            temp_path.replace(archive_path)
            return archive_path, attempts
        except Exception as e:
            temp_error = e
            attempts.append({"url": url, "error": _short_error_text(e)})
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass

    raise RuntimeError(
        json.dumps(
            {
                "code": "release_download_failed",
                "attempts": attempts,
                "last_error": _short_error_text(temp_error),
            },
            ensure_ascii=False,
        )
    )


def _validate_downloaded_asset(path: Path, expected_suffix: str) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        raise RuntimeError("Downloaded file is empty")

    suffix = expected_suffix.lower()
    if suffix == ".exe":
        with open(path, "rb") as f:
            if f.read(2) != b"MZ":
                raise RuntimeError("Downloaded EXE asset is not a Windows executable")
        return

    if suffix == ".zip":
        if not zipfile.is_zipfile(path):
            raise RuntimeError("Downloaded ZIP asset is not a valid archive")
        with zipfile.ZipFile(path, "r") as zf:
            broken = zf.testzip()
            if broken:
                raise RuntimeError(f"Downloaded ZIP archive contains a corrupted member: {broken}")
        return

    raise RuntimeError(f"Unsupported update asset type: {suffix or 'unknown'}")


def _safe_extract_zip(zf: zipfile.ZipFile, destination: Path) -> None:
    root = destination.resolve()
    for member in zf.infolist():
        member_path = (destination / member.filename).resolve()
        try:
            member_path.relative_to(root)
        except ValueError as e:
            raise RuntimeError(f"Unsafe ZIP member path: {member.filename}") from e
        zf.extract(member, destination)


def _extract_release_archive(archive_path: Path, version: str = "") -> Path:
    updates_dir = _update_workspace()
    staging_root = updates_dir / f"staging-{version or 'latest'}"
    if staging_root.exists():
        shutil.rmtree(staging_root, ignore_errors=True)
    staging_root.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            broken = zf.testzip()
            if broken:
                raise RuntimeError(f"Corrupted ZIP member: {broken}")
            _safe_extract_zip(zf, staging_root)
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"Invalid ZIP archive: {e}") from e

    candidates: list[Path] = []
    nested_bundle = staging_root / "StreamVault"
    if nested_bundle.exists():
        candidates.append(nested_bundle)
    candidates.append(staging_root)
    candidates.extend([item for item in staging_root.iterdir() if item.is_dir()])

    for candidate in candidates:
        if (candidate / "StreamVault.exe").exists():
            return candidate

    raise RuntimeError("Extracted archive does not contain StreamVault.exe")


def _spawn_update_installer(source_dir: Path) -> Path:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Автообновление поддерживается только в EXE-сборке.")

    if _current_install_mode() != "onedir":
        raise RuntimeError("Update installer is only supported for onedir builds.")

    target_dir = _install_root()
    updates_dir = _update_workspace()
    updates_dir.mkdir(parents=True, exist_ok=True)
    target_exe = target_dir / "StreamVault.exe"
    log_path = _update_installer_log_path()

    updater_script = updates_dir / "apply_update.cmd"
    updater_script.write_text(
        dedent(
            fr"""
            @echo off
            setlocal EnableExtensions EnableDelayedExpansion

            set "PID=%~1"
            set "SOURCE=%~2"
            set "TARGET=%~3"
            set "BACKUP=%TARGET%.bak"
            set "EXE=%~4"
            set "LOG=%~5"
            set "TARGET_EXE=%TARGET%\%EXE%"
            set "WAIT_LIMIT={APP_UPDATE_WAIT_SECONDS}"
            set "START_LIMIT={APP_UPDATE_START_CHECK_SECONDS}"
            set "WAITED=0"

            call :log "onedir update started"
            call :log "source=%SOURCE%"
            call :log "target=%TARGET%"

            if not exist "%SOURCE%\%EXE%" (
                call :log "source exe missing"
                goto fail_no_touch
            )

            :wait_loop
            tasklist /FI "PID eq %PID%" /NH | findstr /I /C:"%PID%" >nul
            if %errorlevel%==0 (
                set /a WAITED+=1
                if !WAITED! GEQ %WAIT_LIMIT% (
                    call :log "wait timeout"
                    goto fail_no_touch
                )
                timeout /t 1 /nobreak >nul
                goto wait_loop
            )
            call :log "current app exited"

            if exist "%BACKUP%" rmdir /S /Q "%BACKUP%" >nul 2>&1
            if exist "%TARGET%" (
                move /Y "%TARGET%" "%BACKUP%" >nul
                if errorlevel 1 (
                    call :log "could not move current install to backup"
                    goto fail_no_touch
                )
            )

            robocopy "%SOURCE%" "%TARGET%" /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
            if errorlevel 8 (
                call :log "robocopy failed"
                goto restore_old
            )

            if not exist "%TARGET_EXE%" (
                call :log "target exe missing after copy"
                goto restore_old
            )

            call :log "starting updated app"
            start "" "%TARGET_EXE%"

            set "START_WAIT=0"
            :start_check
            tasklist /FI "IMAGENAME eq %EXE%" /NH | findstr /I /C:"%EXE%" >nul
            if %errorlevel%==0 goto success
            set /a START_WAIT+=1
            if !START_WAIT! GEQ %START_LIMIT% (
                call :log "updated app did not start"
                goto restore_old
            )
            timeout /t 1 /nobreak >nul
            goto start_check

            :success
            call :log "update completed"
            if exist "%BACKUP%" rmdir /S /Q "%BACKUP%" >nul 2>&1
            if exist "%SOURCE%" rmdir /S /Q "%SOURCE%" >nul 2>&1
            exit /b 0

            :restore_old
            call :log "restoring previous app"
            if exist "%TARGET%" rmdir /S /Q "%TARGET%" >nul 2>&1
            if exist "%BACKUP%" move /Y "%BACKUP%" "%TARGET%" >nul 2>&1
            if exist "%TARGET_EXE%" start "" "%TARGET_EXE%"
            exit /b 1

            :fail_no_touch
            call :log "update failed before replacing files"
            exit /b 1

            :log
            >> "%LOG%" echo [%date% %time%] %~1
            exit /b 0
            """
        ).strip(),
        encoding="utf-8",
    )

    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    subprocess.Popen(
        [
            "cmd.exe",
            "/c",
            str(updater_script),
            str(os.getpid()),
            str(source_dir),
            str(target_dir),
            "StreamVault.exe",
            str(log_path),
        ],
        cwd=str(updates_dir),
        **kwargs,
    )
    return log_path


def _spawn_onefile_update_installer(source_file: Path) -> Path:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Автообновление поддерживается только в EXE-сборке.")

    target_file = Path(sys.executable)
    updates_dir = _update_workspace()
    updates_dir.mkdir(parents=True, exist_ok=True)
    log_path = _update_installer_log_path()

    updater_script = updates_dir / "apply_update_onefile.cmd"
    updater_script.write_text(
        dedent(
            fr"""
            @echo off
            setlocal EnableExtensions EnableDelayedExpansion

            set "PID=%~1"
            set "SOURCE=%~2"
            set "TARGET=%~3"
            set "BACKUP=%TARGET%.bak"
            set "LOG=%~4"
            set "IMAGENAME=%~nx3"
            set "WAIT_LIMIT={APP_UPDATE_WAIT_SECONDS}"
            set "START_LIMIT={APP_UPDATE_START_CHECK_SECONDS}"
            set "WAITED=0"

            call :log "onefile update started"
            call :log "source=%SOURCE%"
            call :log "target=%TARGET%"

            if not exist "%SOURCE%" (
                call :log "source exe missing"
                goto fail_no_touch
            )

            :wait_loop
            tasklist /FI "PID eq %PID%" /NH | findstr /I /C:"%PID%" >nul
            if %errorlevel%==0 (
                set /a WAITED+=1
                if !WAITED! GEQ %WAIT_LIMIT% (
                    call :log "wait timeout"
                    goto fail_no_touch
                )
                timeout /t 1 /nobreak >nul
                goto wait_loop
            )
            call :log "current app exited"

            copy /Y "%TARGET%" "%BACKUP%" >nul 2>&1
            if errorlevel 1 (
                call :log "could not create backup"
                goto fail_no_touch
            )
            copy /Y "%SOURCE%" "%TARGET%" >nul
            if errorlevel 1 (
                call :log "could not copy new exe"
                goto restore_old
            )

            call :log "starting updated app"
            start "" "%TARGET%"

            set "START_WAIT=0"
            :start_check
            tasklist /FI "IMAGENAME eq !IMAGENAME!" /NH | findstr /I /C:"!IMAGENAME!" >nul
            if %errorlevel%==0 goto success
            set /a START_WAIT+=1
            if !START_WAIT! GEQ %START_LIMIT% (
                call :log "updated app did not start"
                goto restore_old
            )
            timeout /t 1 /nobreak >nul
            goto start_check

            :success
            call :log "update completed"
            del /Q "%BACKUP%" >nul 2>&1
            del /Q "%SOURCE%" >nul 2>&1
            exit /b 0

            :restore_old
            call :log "restoring previous app"
            copy /Y "%BACKUP%" "%TARGET%" >nul 2>&1
            start "" "%TARGET%"
            exit /b 1

            :fail_no_touch
            call :log "update failed before replacing files"
            exit /b 1

            :log
            >> "%LOG%" echo [%date% %time%] %~1
            exit /b 0
            """
        ).strip(),
        encoding="utf-8",
    )

    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    subprocess.Popen(
        [
            "cmd.exe",
            "/c",
            str(updater_script),
            str(os.getpid()),
            str(source_file),
            str(target_file),
            str(log_path),
        ],
        cwd=str(updates_dir),
        **kwargs,
    )
    return log_path


async def _download_latest_app_release_async() -> dict:
    global _update_in_progress

    if not getattr(sys, "frozen", False):
        _set_update_status("blocked", "Автообновление поддерживается только в EXE-сборке.")
        return _build_update_error(
            "app_not_frozen",
            "preflight",
            "Автообновление поддерживается только в EXE-сборке.",
            extra={"fallbacks": _github_release_urls()},
        )

    if _update_in_progress:
        _set_update_status("busy", "Обновление уже выполняется. Дождитесь завершения.")
        return _build_update_error(
            "update_in_progress",
            "preflight",
            "Обновление уже выполняется. Дождитесь завершения.",
        )

    if download_manager.get_active_count() > 0:
        active_downloads = download_manager.get_active_count()
        _set_update_status("blocked", "Есть активные загрузки. Остановите их перед обновлением приложения.", extra={"active_downloads": active_downloads})
        return _build_update_error(
            "downloads_active",
            "preflight",
            "Есть активные загрузки. Остановите их перед обновлением приложения.",
            extra={"active_downloads": active_downloads},
        )

    _update_in_progress = True
    loop = asyncio.get_running_loop()
    install_mode = _current_install_mode()
    scheduled = False

    try:
        _set_update_status("fetching", "Проверка последнего релиза.")
        try:
            release = await loop.run_in_executor(None, lambda: _fetch_latest_app_release(force=True))
        except Exception as e:
            _set_update_status("error", "Не удалось получить информацию о релизе с GitHub.")
            return _build_update_error(
                "release_fetch_failed",
                "fetch_release",
                "Не удалось получить информацию о релизе с GitHub.",
                exception=e,
                extra={
                    "fallbacks": _github_release_urls(),
                    "repo_url": GITHUB_REPO_URL,
                },
            )

        asset_name = str(release.get("asset_name") or "")
        if not _asset_matches_install_mode(asset_name, install_mode):
            expected_asset = _expected_app_asset_name()
            code = "asset_mismatch" if install_mode == "onedir" else "asset_missing"
            message = (
                "Последний релиз не содержит ZIP-архив для onedir-обновления."
                if install_mode == "onedir"
                else "Последний релиз не содержит EXE-asset для onefile-обновления."
            )
            _set_update_status("blocked", message, release=release, extra={"install_mode": install_mode, "expected_asset_name": expected_asset})
            return _build_update_error(
                code,
                "select_asset",
                message,
                release=release,
                extra={
                    "install_mode": install_mode,
                    "expected_asset_name": expected_asset,
                    "fallbacks": _github_release_urls(release.get("asset_name") or _expected_app_asset_name(), release.get("version") or ""),
                },
            )

        if release["version"] and _version_tuple(release["version"]) <= _version_tuple(APP_VERSION):
            _set_update_status("idle", "Установлена актуальная версия.", release=release, extra={"install_mode": install_mode})
            return {
                "status": "up_to_date",
                **release,
                "current": APP_VERSION,
                "repo_url": GITHUB_REPO_URL,
                "install_mode": install_mode,
                "fallbacks": _github_release_urls(release.get("asset_name") or _expected_app_asset_name(), release.get("version") or ""),
            }

        def _send_progress(downloaded: int, total: int) -> None:
            pct = round(downloaded / total * 100, 1) if total > 0 else 0
            loop.call_soon_threadsafe(
                asyncio.create_task,
                manager.broadcast({
                    "type": "update_progress",
                    "stage": "downloading",
                    "downloaded": downloaded,
                    "total": total,
                    "percent": pct,
                }),
            )

        def _sync_download() -> tuple[Path, list[dict]]:
            return _download_release_asset(release, progress_callback=_send_progress)

        _set_update_status("downloading", "Загрузка обновления.", release=release, extra={"install_mode": install_mode})
        await manager.broadcast({"type": "update_progress", "stage": "start", "percent": 0, "downloaded": 0, "total": 0})

        try:
            archive_path, attempts = await loop.run_in_executor(None, _sync_download)
        except Exception as e:
            details = _decode_update_runtime_error(e)
            attempts = details.get("attempts") if isinstance(details.get("attempts"), list) else []
            _set_update_status("error", "Не удалось скачать файл обновления.", release=release, extra={"attempts": attempts})
            await manager.broadcast({"type": "update_progress", "stage": "error", "percent": 0, "downloaded": 0, "total": 0})
            return _build_update_error(
                "release_download_failed",
                "download_release",
                "Не удалось скачать файл обновления.",
                exception=e,
                attempts=attempts,
                release=release,
                extra={
                    "install_mode": install_mode,
                    "fallbacks": _github_release_urls(release.get("asset_name") or _expected_app_asset_name(), release.get("version") or ""),
                },
            )

        _set_update_status("preparing", "Подготовка обновления.", release=release, extra={"downloaded_to": str(archive_path), "install_mode": install_mode})
        await manager.broadcast({"type": "update_progress", "stage": "extracting", "percent": 100, "downloaded": 0, "total": 0})
        try:
            if install_mode == "onedir":
                extracted_dir = await loop.run_in_executor(
                    None, lambda: _extract_release_archive(archive_path, release.get("version") or "")
                )
                installer_log = _spawn_update_installer(extracted_dir)
            else:
                extracted_dir = None
                installer_log = _spawn_onefile_update_installer(archive_path)
        except Exception as e:
            _set_update_status("error", "Ошибка при подготовке установщика обновления.", release=release)
            await manager.broadcast({"type": "update_progress", "stage": "error", "percent": 100, "downloaded": 0, "total": 0})
            return _build_update_error(
                "install_failed",
                "install",
                "Ошибка при установке обновления.",
                exception=e,
                release=release,
            )

        scheduled = True
        _set_update_status(
            "scheduled",
            "Обновление загружено и будет установлено после закрытия приложения.",
            release=release,
            extra={
                "downloaded_to": str(archive_path),
                "extracted_to": str(extracted_dir) if extracted_dir else "",
                "installer_log": str(installer_log) if installer_log else "",
                "install_mode": install_mode,
            },
        )
        await manager.broadcast({"type": "update_progress", "stage": "done", "percent": 100, "downloaded": 0, "total": 0})
        return {
            "status": "scheduled",
            "current": APP_VERSION,
            **release,
            "downloaded_to": str(archive_path),
            "extracted_to": str(extracted_dir) if extracted_dir else "",
            "target": str(_install_root()),
            "installer_log": str(installer_log) if installer_log else "",
            "install_mode": install_mode,
            "fallbacks": _github_release_urls(release.get("asset_name") or _expected_app_asset_name(), release.get("version") or ""),
            "download_attempts": attempts,
        }
    finally:
        if not scheduled:
            _update_in_progress = False


# --- Модели запросов/ответов ---

class DownloadRequest(BaseModel):
    url: str


class PlaylistDownloadRequest(BaseModel):
    url: str
    selected_indices: list[int]


class SearchRequest(BaseModel):
    query: str
    limit: int = 10


class SettingsRequest(BaseModel):
    settings: Settings


class TaskResponse(BaseModel):
    id: str
    url: str
    title: str
    status: str
    downloaded_bytes: int
    total_bytes: int
    progress: float
    speed: str
    eta: str
    error_message: str
    error_code: str = ""
    error_help: str = ""
    thumbnail: str = ""
    detailed_status: str = ""
    resumed: bool = False
    log_file: str = ""
    file_path: str = ""


# --- WebSocket для real-time обновлений ---

class ConnectionManager:
    """Управляет WebSocket подключениями."""

    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, data: dict):
        message = json.dumps(data)
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass


manager = ConnectionManager()

# Хранилище отправленных уведомлений (чтобы не спамить)
sent_notifications: set[str] = set()


async def broadcast_progress():
    """Периодически отправляет прогресс всех активных загрузок."""
    while True:
        tasks = download_manager.get_all_tasks()

        # Всегда отправляем обновление, даже если очередь пуста
        data = {
            "type": "progress_update",
            "tasks": [t.to_dict() for t in tasks],
            "active_count": download_manager.get_active_count(),
        }
        await manager.broadcast(data)

        # Отправляем уведомления о несовпадении формата
        for task in tasks:
            if task.format_warning and task.id not in sent_notifications:
                sent_notifications.add(task.id)
                notification = {
                    "type": "notification",
                    "task_id": task.id,
                    "message": task.format_warning,
                    "title": task.title,
                }
                await manager.broadcast(notification)
        await asyncio.sleep(0.5)


# --- Lifespan ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Запуск фоновых задач при старте приложения."""
    ensure_save_location()
    task = asyncio.create_task(broadcast_progress())
    yield
    task.cancel()


# --- Приложение ---

app = FastAPI(title="StreamVault", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- i18n API ---

@app.get("/api/i18n/{lang}")
async def get_translations(lang: str):
    """Отдать переводы для указанного языка."""
    data = _load_locale(lang)
    if data is None:
        return JSONResponse(status_code=404, content={"error": "Locale not found"})
    return data


# --- Search API ---

@app.post("/api/search")
async def search_videos(request: SearchRequest):
    """Поиск видео на YouTube через yt-dlp."""
    result = await download_manager.search_videos(request.query, request.limit)
    return result


@app.get("/api/download/{task_id}/log")
async def get_task_log(task_id: str):
    task = download_manager.get_task(task_id)
    if not task or not getattr(task, "log_file", ""):
        return {"error": "Log not found"}
    try:
        path = Path(task.log_file)
        if not path.exists():
            return {"error": "Log not found"}
        content = path.read_text(encoding="utf-8", errors="replace")
        if len(content) > 20000:
            content = content[-20000:]
        return {"task_id": task_id, "log": content}
    except Exception as e:
        return {"error": str(e)[:200]}


def _run_ytdlp_version() -> str:
    try:
        cmd = [str(download_manager.ytdlp_path), "--version"]
        p = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        out = (p.stdout or p.stderr or "").strip()
        return out.splitlines()[0] if out else ""
    except Exception:
        return ""


def _fetch_latest_ytdlp_tag() -> str:
    try:
        req = urllib.request.Request(
            "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest",
            headers={"User-Agent": "StreamVault"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return str(data.get("tag_name") or "").lstrip("v")
    except Exception:
        return ""


@app.get("/api/ytdlp/info")
async def ytdlp_info():
    current = _run_ytdlp_version()
    latest = _fetch_latest_ytdlp_tag()
    return {"current": current, "latest": latest, "path": str(download_manager.ytdlp_path)}


@app.get("/api/app/update/info")
async def app_update_info(force: bool = False):
    try:
        loop = asyncio.get_running_loop()
        release = await loop.run_in_executor(None, lambda: _fetch_latest_app_release(force=force))
        current = APP_VERSION
        latest = release["version"]
        install_mode = _current_install_mode()
        expected_asset = _expected_app_asset_name()
        asset_name = str(release.get("asset_name") or "")
        asset_ok = _asset_matches_install_mode(asset_name, install_mode)
        is_update_available = bool(latest) and _version_tuple(latest) > _version_tuple(current) and asset_ok
        blocked_reason = ""
        if _update_in_progress:
            blocked_reason = _last_update_status.get("message") or "Обновление уже выполняется. Дождитесь завершения."
        elif not asset_ok:
            blocked_reason = f"Для этой сборки нужен релизный asset {expected_asset}"
        return {
            "current": current,
            "latest": latest,
            "is_update_available": is_update_available,
            "update_in_progress": _update_in_progress,
            "install_mode": install_mode,
            "expected_asset_name": expected_asset,
            "asset_ok": asset_ok,
            "update_status": dict(_last_update_status),
            "update_blocked_reason": blocked_reason,
            "release_name": release["name"],
            "asset_name": release["asset_name"],
            "published_at": release["published_at"],
            "notes": release["body"][:2000],
            "source": release.get("source", "unknown"),
            "fallbacks": _github_release_urls(release.get("asset_name") or _expected_app_asset_name(), release.get("version") or ""),
        }
    except Exception as e:
        cached = dict(_latest_release_cache) if _latest_release_cache else {}
        return {
            "error": str(e)[:200],
            "current": APP_VERSION,
            "latest": cached.get("version", ""),
            "source": cached.get("source", "error"),
            "stale": bool(cached),
            "update_in_progress": _update_in_progress,
            "install_mode": _current_install_mode(),
            "update_status": dict(_last_update_status),
            "update_blocked_reason": (_last_update_status.get("message") or "Обновление уже выполняется. Дождитесь завершения.") if _update_in_progress else "",
            "fallbacks": _github_release_urls(cached.get("asset_name") or _expected_app_asset_name(), cached.get("version") or ""),
        }


@app.post("/api/app/update")
async def app_update():
    try:
        return await _download_latest_app_release_async()
    except Exception as e:
        return {"error": str(e)[:200]}


@app.get("/api/app/update/diagnostics")
async def app_update_diagnostics():
    settings = load_settings()
    release = dict(_latest_release_cache) if _latest_release_cache else {}
    return {
        "app": {
            "name": "StreamVault",
            "version": APP_VERSION,
            "repo": GITHUB_REPO,
            "repo_url": GITHUB_REPO_URL,
            "frozen": bool(getattr(sys, "frozen", False)),
            "executable": str(sys.executable),
        },
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "os_name": os.name,
            "cwd": str(Path.cwd()),
        },
        "update": {
            "current": APP_VERSION,
            "cached_release": release,
            "last_error": dict(_last_update_error) if _last_update_error else {},
            "last_status": dict(_last_update_status) if _last_update_status else {},
            "update_in_progress": _update_in_progress,
            "install_mode": _current_install_mode(),
            "installer_log": str(_update_installer_log_path()),
            "fallbacks": _github_release_urls(release.get("asset_name") or _expected_app_asset_name(), release.get("version") or ""),
        },
        "downloads": {
            "active_count": download_manager.get_active_count(),
        },
        "settings": {
            "language": getattr(settings, "language", ""),
            "save_location": getattr(settings, "save_location", ""),
            "download_format": getattr(settings, "download_format", ""),
        },
    }


@app.post("/api/ytdlp/update")
async def ytdlp_update():
    if download_manager.get_active_count() > 0:
        return {"error": "Есть активные загрузки. Остановите их перед обновлением yt-dlp."}
    url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    target = Path(download_manager.ytdlp_path)
    tmp = target.with_suffix(".tmp")
    loop = asyncio.get_running_loop()

    def _do_download():
        req = urllib.request.Request(url, headers={"User-Agent": "StreamVault"})
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(tmp, "wb") as f:
                shutil.copyfileobj(resp, f, length=512 * 1024)
        tmp.replace(target)

    try:
        await loop.run_in_executor(None, _do_download)
        return {"status": "updated", "current": _run_ytdlp_version()}
    except Exception as e:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return {"error": str(e)[:200]}


@app.post("/api/download/{task_id}/retry", response_model=TaskResponse)
async def retry_download(task_id: str):
    task = download_manager.get_task(task_id)
    if not task:
        return JSONResponse(status_code=404, content={"error": "Task not found"})
    new_task = await download_manager.add_download(task.url)
    return TaskResponse(**new_task.to_dict())


@app.post("/api/open-file")
async def open_file(request: Request):
    try:
        body = await request.json()
        p = body.get("path") or ""
        if not p:
            return {"error": "Path required"}
        path = Path(p)
        if path.is_dir():
            os.startfile(str(path))
        else:
            if path.exists():
                os.startfile(str(path))
            else:
                return {"error": "File not found"}
        return {"status": "opened"}
    except Exception as e:
        return {"error": str(e)}


# --- API Endpoints ---

@app.post("/api/download", response_model=TaskResponse)
async def start_download(request: DownloadRequest):
    """Начать загрузку видео по URL."""
    task = await download_manager.add_download(request.url)
    return TaskResponse(**task.to_dict())


@app.post("/api/playlist/info")
async def get_playlist_info(request: DownloadRequest):
    """Получить информацию о плейлисте."""
    info = await download_manager.get_playlist_info(request.url)
    if "error" in info:
        return {"error": info["error"]}
    return info


@app.post("/api/info")
async def get_url_info(request: DownloadRequest):
    """Получить информацию о видео или плейлисте для превью."""
    return await download_manager.get_url_info(request.url)


@app.post("/api/playlist/download")
async def download_playlist(request: PlaylistDownloadRequest):
    """Скачать выбранные видео из плейлиста."""
    # Получаем информацию о плейлисте
    info = await download_manager.get_playlist_info(request.url)
    if "error" in info or "entries" not in info:
        return {"error": "Не удалось получить информацию о плейлисте"}

    # Создаём задачи для выбранных видео
    created_tasks = []
    for entry in info["entries"]:
        if entry["index"] in request.selected_indices:
            task = await download_manager.add_download(entry["url"])
            created_tasks.append(task.to_dict())

    return {"tasks": created_tasks, "count": len(created_tasks)}


@app.post("/api/download/{task_id}/pause")
async def pause_download(task_id: str):
    """Приостановить загрузку."""
    task = download_manager.pause_download(task_id)
    if task:
        return TaskResponse(**task.to_dict())
    return {"error": "Task not found or not downloading"}


@app.post("/api/download/{task_id}/resume")
async def resume_download(task_id: str):
    """Возобновить загрузку."""
    task = await download_manager.resume_download(task_id)
    if task:
        return TaskResponse(**task.to_dict())
    return {"error": "Task not found or not paused"}


@app.post("/api/download/{task_id}/cancel")
async def cancel_download(task_id: str):
    """Отменить загрузку."""
    task = download_manager.cancel_download(task_id)
    if task:
        return TaskResponse(**task.to_dict())
    return {"error": "Task not found"}


@app.delete("/api/download/{task_id}")
async def remove_download(task_id: str):
    """Удалить задачу из очереди."""
    success = download_manager.remove_task(task_id)
    if success:
        sent_notifications.discard(task_id)
        return {"status": "removed"}
    return {"error": "Task not found"}


@app.post("/api/open-folder/{task_id}")
@app.post("/api/open-folder")
async def open_folder(task_id: str = None, request: Request = None):
    """Открыть папку с загруженным файлом в проводнике."""
    settings = load_settings()
    
    # Пытаемся достать путь из json (если передан)
    target_path = None
    try:
        if request:
            body = await request.json()
            if "path" in body and body["path"]:
                tp = Path(body["path"])
                target_path = tp if tp.is_dir() else tp.parent
    except:
        pass
        
    save_path = target_path if target_path and target_path.exists() else Path(settings.save_location)

    if not save_path.exists():
        return {"error": "Папка сохранения не найдена"}

    # Открываем папку в проводнике Windows
    try:
        os.startfile(str(save_path))
        return {"status": "opened"}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/downloads", response_model=list[TaskResponse])
async def get_downloads():
    """Получить все задачи загрузки."""
    return [TaskResponse(**t.to_dict()) for t in download_manager.get_all_tasks()]


@app.get("/api/settings", response_model=Settings)
async def get_settings():
    """Получить текущие настройки."""
    return load_settings()


@app.post("/api/settings")
async def update_settings(request: SettingsRequest):
    """Обновить настройки."""
    save_settings(request.settings)
    return request.settings


@app.get("/api/status")
async def get_status():
    """Получить статус приложения."""
    return {
        "active_downloads": download_manager.get_active_count(),
        "total_tasks": len(download_manager.tasks),
    }


@app.get("/api/storage")
async def get_storage_info():
    """Получить информацию об использовании хранилища."""
    settings = load_settings()
    save_path = Path(settings.save_location)
    
    # Считаем размер файлов в папке
    folder_size = 0
    file_count = 0
    
    if save_path.exists():
        for file_path in save_path.rglob("*"):
            if file_path.is_file():
                folder_size += file_path.stat().st_size
                file_count += 1
    
    # Получаем информацию о диске
    drive = save_path.anchor if save_path.exists() else str(save_path.drive) + "\\"
    if not drive:
        drive = "."
    
    try:
        total, used, free = shutil.disk_usage(drive)
    except Exception:
        total, used, free = 0, 0, 0
    
    def format_size(size_bytes):
        """Конвертировать байты в читаемый формат."""
        if size_bytes == 0: return 0, 'B'
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return round(size_bytes, 2), unit
            size_bytes /= 1024.0
        return round(size_bytes, 2), 'PB'
    
    folder_val, folder_unit = format_size(folder_size)
    free_val, free_unit = format_size(free)
    total_val, total_unit = format_size(total)
    
    return {
        "folder_size_bytes": folder_size,
        "folder_size_formatted": f"{folder_val} {folder_unit}",
        "file_count": file_count,
        "disk_free_bytes": free,
        "disk_free_formatted": f"{free_val} {free_unit}",
        "disk_total_bytes": total,
        "disk_total_formatted": f"{total_val} {total_unit}",
        "disk_used_percent": round((used / total * 100), 1) if total > 0 else 0,
        "save_location": str(save_path),
    }


# --- API History ---

@app.get("/api/history", response_model=list[HistoryRecord])
async def get_history():
    """Получить всю историю загрузок."""
    return history_manager.get_all()


@app.delete("/api/history/{record_id}")
async def remove_history_record(record_id: str):
    """Удалить запись из истории."""
    success = history_manager.delete_record(record_id)
    if success:
        return {"status": "removed"}
    return {"error": "Record not found"}


@app.delete("/api/history")
async def clear_history():
    """Очистить всю историю."""
    history_manager.clear_all()
    return {"status": "cleared"}


# --- WebSocket ---

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint для real-time обновлений."""
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# --- Статика и UI ---

UI_DIR = get_resource_path("ui")


@app.get("/")
async def index():
    """Главная страница."""
    return FileResponse(UI_DIR / "index.html")


@app.get("/settings")
async def settings_page():
    """Страница настроек."""
    return FileResponse(UI_DIR / "settings.html")


@app.get("/history")
async def history_page():
    """Страница истории."""
    return FileResponse(UI_DIR / "history.html")


app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")


# --- Запуск ---

def run_server():
    """Запустить uvicorn сервер."""
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


def run_desktop():
    """Запустить приложение в десктопном окне через pywebview."""
    import threading
    import webview

    class WebViewAPI:
        """API для взаимодействия JavaScript с Python."""
        
        def close(self):
            """Закрыть приложение."""
            for window in webview.windows:
                window.destroy()

        def choose_folder(self, current_path: str = ""):
            """Открыть системный диалог выбора папки и вернуть путь."""
            try:
                initial_dir = current_path if current_path and Path(current_path).exists() else None
                for window in webview.windows:
                    result = window.create_file_dialog(webview.FOLDER_DIALOG, directory=initial_dir)
                    if result:
                        return str(result[0])
            except Exception:
                return ""
            return ""

    # Запускаем сервер в отдельном потоке
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    # Ждём пока сервер запустится
    import time
    time.sleep(1)

    # Создаём окно в полноэкранном режиме без рамок
    window = webview.create_window(
        'StreamVault',
        'http://127.0.0.1:8765',
        fullscreen=True,
        frameless=True,
        js_api=WebViewAPI(),
    )

    # Запускаем pywebview
    webview.start(debug=False)


if __name__ == "__main__":
    import sys
    if '--web' in sys.argv:
        # Запуск только сервера (для доступа через браузер)
        run_server()
    else:
        # Запуск в десктопном окне
        run_desktop()
