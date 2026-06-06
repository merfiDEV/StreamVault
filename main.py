"""Главный файл приложения — FastAPI сервер + PyWebView."""

import asyncio
import json
import os
import re
import platform
import shutil
import subprocess
import sys
import zipfile
import urllib.request
import urllib.error
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
_latest_release_cache: dict = {}
_last_update_error: dict = {}


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


def _urlopen_json(url: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "StreamVault"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _update_workspace() -> Path:
    appdata_root = Path(get_data_path("version.txt")).parent
    return appdata_root / "updates"


def _install_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def _current_install_mode() -> str:
    install_root = _install_root()
    markers = ("base_library.zip", "python311.dll", "python310.dll", "python39.dll")
    if any((install_root / marker).exists() for marker in markers):
        return "onedir"
    return "onefile"


def _expected_app_asset_name() -> str:
    return "StreamVault.zip" if _current_install_mode() == "onedir" else "StreamVault.exe"


def _asset_fallback_names() -> list[str]:
    primary = _expected_app_asset_name()
    secondary = "StreamVault.exe" if primary.lower().endswith(".zip") else "StreamVault.zip"
    return [primary, secondary]


def _fetch_latest_app_release() -> dict:
    errors: list[dict] = []
    try:
        payload = _urlopen_json(GITHUB_RELEASES_LATEST, timeout=10)
        assets = payload.get("assets") or []
        preferred_assets = [name.lower() for name in _asset_fallback_names()]
        asset = next(
            (
                item
                for item in assets
                if str(item.get("name") or "").lower() in preferred_assets
            ),
            None,
        )
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
        global _latest_release_cache
        _latest_release_cache = release
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


def _download_release_asset(release: dict) -> tuple[Path, list[dict]]:
    asset_name = release.get("asset_name") or _expected_app_asset_name()
    version = release.get("version") or ""
    expected_suffix = Path(asset_name).suffix.lower() or ".zip"
    candidates: list[str] = []

    if release.get("download_url"):
        candidates.append(str(release["download_url"]))
    candidates.extend(_github_release_urls(asset_name, version))

    attempts: list[dict] = []
    updates_dir = _update_workspace()
    updates_dir.mkdir(parents=True, exist_ok=True)

    archive_name = f"StreamVault-{version or 'latest'}{expected_suffix}"
    archive_path = updates_dir / archive_name
    temp_error = None

    for url in candidates:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "StreamVault"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                with open(archive_path, "wb") as f:
                    shutil.copyfileobj(resp, f)
            if archive_path.stat().st_size <= 0:
                raise RuntimeError("Downloaded file is empty")
            return archive_path, attempts
        except Exception as e:
            temp_error = e
            attempts.append({"url": url, "error": _short_error_text(e)})
            try:
                if archive_path.exists():
                    archive_path.unlink()
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


def _extract_release_archive(archive_path: Path, version: str = "") -> Path:
    updates_dir = _update_workspace()
    staging_root = updates_dir / f"staging-{version or 'latest'}"
    if staging_root.exists():
        shutil.rmtree(staging_root, ignore_errors=True)
    staging_root.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(staging_root)
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


def _spawn_update_installer(source_dir: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Автообновление поддерживается только в EXE-сборке.")

    if _current_install_mode() != "onedir":
        raise RuntimeError("Update installer is only supported for onedir builds.")

    target_dir = _install_root()
    updates_dir = _update_workspace()
    updates_dir.mkdir(parents=True, exist_ok=True)
    target_exe = target_dir / "StreamVault.exe"

    updater_script = updates_dir / "apply_update.cmd"
    updater_script.write_text(
        dedent(
            r"""
            @echo off
            setlocal enabledelayedexpansion

            set "PID=%~1"
            set "SOURCE=%~2"
            set "TARGET=%~3"
            set "BACKUP=%TARGET%.bak"
            set "EXE=%~4"
            set "TARGET_EXE=%TARGET%\%EXE%"

            :wait_loop
            tasklist /FI "PID eq %PID%" /NH | findstr /I /C:"%PID%" >nul
            if %errorlevel%==0 (
                timeout /t 1 /nobreak >nul
                goto wait_loop
            )

            if exist "%BACKUP%" rmdir /S /Q "%BACKUP%" >nul 2>&1
            if exist "%TARGET%" (
                move /Y "%TARGET%" "%BACKUP%" >nul
                if errorlevel 1 goto restore_old
            )

            robocopy "%SOURCE%" "%TARGET%" /E /NFL /NDL /NJH /NJS /NC /NS /NP >nul
            if errorlevel 8 goto restore_old

            start "" "%TARGET_EXE%"

            timeout /t 15 /nobreak >nul
            tasklist /FI "IMAGENAME eq %EXE%" /NH | findstr /I /C:"%EXE%" >nul
            if errorlevel 1 goto restore_old

            if exist "%BACKUP%" rmdir /S /Q "%BACKUP%" >nul 2>&1
            if exist "%SOURCE%" rmdir /S /Q "%SOURCE%" >nul 2>&1
            exit /b 0

            :restore_old
            if exist "%TARGET%" rmdir /S /Q "%TARGET%" >nul 2>&1
            if exist "%BACKUP%" move /Y "%BACKUP%" "%TARGET%" >nul 2>&1
            if exist "%TARGET_EXE%" start "" "%TARGET_EXE%"
            exit /b 1
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
        ],
        **kwargs,
    )


def _spawn_onefile_update_installer(source_file: Path) -> None:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Автообновление поддерживается только в EXE-сборке.")

    target_file = Path(sys.executable)
    updates_dir = _update_workspace()
    updates_dir.mkdir(parents=True, exist_ok=True)

    updater_script = updates_dir / "apply_update_onefile.cmd"
    updater_script.write_text(
        dedent(
            r"""
            @echo off
            setlocal enabledelayedexpansion

            set "PID=%~1"
            set "SOURCE=%~2"
            set "TARGET=%~3"
            set "BACKUP=%TARGET%.bak"
            set "IMAGENAME=%~nx3"

            :wait_loop
            tasklist /FI "PID eq %PID%" /NH | findstr /I /C:"%PID%" >nul
            if %errorlevel%==0 (
                timeout /t 1 /nobreak >nul
                goto wait_loop
            )

            copy /Y "%TARGET%" "%BACKUP%" >nul 2>&1
            copy /Y "%SOURCE%" "%TARGET%" >nul
            if errorlevel 1 goto restore_old

            start "" "%TARGET%"

            timeout /t 15 /nobreak >nul
            tasklist /FI "IMAGENAME eq !IMAGENAME!" /NH | findstr /I /C:"!IMAGENAME!" >nul
            if errorlevel 1 goto restore_old

            del /Q "%BACKUP%" >nul 2>&1
            exit /b 0

            :restore_old
            copy /Y "%BACKUP%" "%TARGET%" >nul 2>&1
            start "" "%TARGET%"
            exit /b 1
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
        ],
        **kwargs,
    )


def _download_latest_app_release() -> dict:
    if not getattr(sys, "frozen", False):
        return _build_update_error(
            "app_not_frozen",
            "preflight",
            "Автообновление поддерживается только в EXE-сборке.",
            extra={"fallbacks": _github_release_urls()},
        )

    if download_manager.get_active_count() > 0:
        return _build_update_error(
            "downloads_active",
            "preflight",
            "Есть активные загрузки. Остановите их перед обновлением приложения.",
            extra={"active_downloads": download_manager.get_active_count()},
        )

    install_mode = _current_install_mode()

    try:
        release = _fetch_latest_app_release()
    except Exception as e:
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

    if install_mode == "onedir" and not str(release.get("asset_name") or "").lower().endswith(".zip"):
        return _build_update_error(
            "asset_mismatch",
            "select_asset",
            "Последний релиз не содержит ZIP-архив для onedir-обновления.",
            release=release,
            extra={
                "install_mode": install_mode,
                "fallbacks": _github_release_urls(release.get("asset_name") or _expected_app_asset_name(), release.get("version") or ""),
            },
        )

    if install_mode == "onefile" and not str(release.get("asset_name") or "").lower().endswith(".exe"):
        return _build_update_error(
            "asset_missing",
            "select_asset",
            "Последний релиз не содержит EXE-asset для onefile-обновления.",
            release=release,
            extra={
                "install_mode": install_mode,
                "fallbacks": _github_release_urls(release.get("asset_name") or _expected_app_asset_name(), release.get("version") or ""),
            },
        )

    if release["version"] and _version_tuple(release["version"]) <= _version_tuple(APP_VERSION):
        return {
            "status": "up_to_date",
            **release,
            "current": APP_VERSION,
            "repo_url": GITHUB_REPO_URL,
            "install_mode": install_mode,
            "fallbacks": _github_release_urls(release.get("asset_name") or _expected_app_asset_name(), release.get("version") or ""),
        }

    try:
        archive_path, attempts = _download_release_asset(release)
        if install_mode == "onedir":
            extracted_dir = _extract_release_archive(archive_path, release.get("version") or "")
            _spawn_update_installer(extracted_dir)
        else:
            extracted_dir = None
            _spawn_onefile_update_installer(archive_path)
        return {
            "status": "scheduled",
            "current": APP_VERSION,
            **release,
            "downloaded_to": str(archive_path),
            "extracted_to": str(extracted_dir) if extracted_dir else "",
            "target": str(_install_root()),
            "install_mode": install_mode,
            "fallbacks": _github_release_urls(release.get("asset_name") or _expected_app_asset_name(), release.get("version") or ""),
            "download_attempts": attempts,
        }
    except Exception as e:
        return _build_update_error(
            "release_download_failed",
            "download_release",
            "Не удалось скачать файл обновления.",
            exception=e,
            release=release,
            extra={
                "install_mode": install_mode,
                "fallbacks": _github_release_urls(release.get("asset_name") or _expected_app_asset_name(), release.get("version") or ""),
            },
        )


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
async def app_update_info():
    try:
        release = _fetch_latest_app_release()
        current = APP_VERSION
        latest = release["version"]
        install_mode = _current_install_mode()
        expected_asset = _expected_app_asset_name()
        asset_name = str(release.get("asset_name") or "")
        asset_ok = asset_name.lower().endswith(".zip") if install_mode == "onedir" else asset_name.lower().endswith(".exe")
        is_update_available = bool(latest) and _version_tuple(latest) > _version_tuple(current) and asset_ok
        return {
            "current": current,
            "latest": latest,
            "is_update_available": is_update_available,
            "install_mode": install_mode,
            "expected_asset_name": expected_asset,
            "asset_ok": asset_ok,
            "update_blocked_reason": "" if asset_ok else f"Для этой сборки нужен релизный asset {expected_asset}",
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
            "install_mode": _current_install_mode(),
            "fallbacks": _github_release_urls(cached.get("asset_name") or _expected_app_asset_name(), cached.get("version") or ""),
        }


@app.post("/api/app/update")
async def app_update():
    try:
        result = _download_latest_app_release()
        if "error" in result:
            return result
        return result
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
            "install_mode": _current_install_mode(),
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
def ytdlp_update():
    if download_manager.get_active_count() > 0:
        return {"error": "Есть активные загрузки. Остановите их перед обновлением yt-dlp."}
    url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    target = Path(download_manager.ytdlp_path)
    tmp = target.with_suffix(".tmp")
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        tmp.write_bytes(data)
        tmp.replace(target)
        return {"status": "updated", "current": _run_ytdlp_version()}
    except (urllib.error.URLError, OSError) as e:
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
