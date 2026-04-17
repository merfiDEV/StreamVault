"""Модуль для работы с yt-dlp."""

import asyncio
import json
import os
import re
import subprocess
import uuid
from enum import Enum
from pathlib import Path
from typing import Optional

import psutil

from core.config import Settings, load_settings
from core.i18n import t
from core.history import history_manager
from core.utils import get_resource_path, get_data_path, ensure_file_from_resources


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    PREPARING = "preparing"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"
    PROCESSING = "processing"


class DownloadTask:
    """Представляет одну задачу загрузки."""

    def __init__(self, url: str, title: str = "", status: DownloadStatus = DownloadStatus.DOWNLOADING):
        self.id = str(uuid.uuid4())[:8]
        self.url = url
        self.title = title
        self.status = status
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.progress = 0.0
        self.speed = ""
        self.eta = ""
        self.error_message = ""
        self.error_code = ""
        self.error_help = ""
        self.format_warning = ""
        self.thumbnail = ""
        self.file_path: str = ""
        self.resumed = False
        self.removed = False
        self.process: Optional[asyncio.subprocess.Process] = None
        self.log_file: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "status": self.status.value,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "progress": self.progress,
            "speed": self.speed,
            "eta": self.eta,
            "error_message": self.error_message,
            "error_code": self.error_code,
            "error_help": self.error_help,
            "log_file": self.log_file,
            "format_warning": self.format_warning,
            "thumbnail": self.thumbnail,
            "file_path": self.file_path,
            "resumed": self.resumed,
        }


class DownloadManager:
    """Управляет очередью загрузок yt-dlp."""

    def __init__(self):
        self.tasks: dict[str, DownloadTask] = {}
        data_ytdlp = get_data_path("yt-dlp.exe")
        self.ytdlp_path = ensure_file_from_resources("yt-dlp.exe", data_ytdlp)
        initial_limit = max(1, int(getattr(load_settings(), "max_concurrent_downloads", 2) or 2))
        self._semaphore = asyncio.Semaphore(initial_limit)

    def _ytdlp_subprocess_kwargs(self) -> dict:
        if os.name == "nt":
            return {"creationflags": subprocess.CREATE_NO_WINDOW}
        return {}

    def _refresh_concurrency(self, settings: Settings) -> None:
        try:
            limit = max(1, int(getattr(settings, "max_concurrent_downloads", 2) or 2))
        except Exception:
            limit = 2
        current_limit = getattr(self, "_concurrency_limit", None)
        if current_limit == limit:
            return
        self._concurrency_limit = limit
        self._semaphore = asyncio.Semaphore(limit)

    def _get_task_log_path(self, task: DownloadTask) -> Path:
        logs_dir = get_data_path("logs").parent / "logs"
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return logs_dir / f"task_{task.id}.log"

    def _append_log(self, task: DownloadTask, text: str) -> None:
        try:
            log_path = self._get_task_log_path(task)
            task.log_file = str(log_path)
            with open(log_path, "a", encoding="utf-8", errors="replace") as f:
                f.write(text)
                if not text.endswith("\n"):
                    f.write("\n")
        except Exception:
            pass

    def _try_extract_output_path(self, line: str) -> str:
        if not line:
            return ""
        m = re.search(r"Destination:\s(.+)$", line)
        if m:
            return m.group(1).strip().strip('"')
        m = re.search(r"Merging formats into \"(.+)\"$", line)
        if m:
            return m.group(1).strip()
        return ""

    async def _consume_stderr(self, task: DownloadTask) -> str:
        buf = []
        try:
            if not task.process or not task.process.stderr:
                return ""
            while True:
                line = await task.process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._append_log(task, text)
                    out = self._try_extract_output_path(text)
                    if out:
                        task.file_path = out
                buf.append(text)
        except Exception:
            pass
        return "\n".join([x for x in buf if x])

    def _classify_error(self, stderr_text: str) -> tuple[str, str, str]:
        raw = (stderr_text or "").strip()
        s = raw.lower()
        if not raw:
            return "unknown", "Unknown error", ""
        if "unable to download webpage" in s or "failed to resolve" in s or "name or service not known" in s:
            return "network", "Проблема сети (не удаётся открыть страницу)", "Проверьте интернет/VPN/фаервол."
        if "timed out" in s or "timeout" in s:
            return "network_timeout", "Превышено время ожидания сети", "Попробуйте ещё раз или включите VPN."
        if "http error 429" in s or "too many requests" in s:
            return "rate_limited", "Слишком много запросов (429)", "Подождите или включите cookies/VPN."
        if "confirm you're not a bot" in s or "confirm you’re not a bot" in s or "captcha" in s:
            return "bot_check", "YouTube требует подтверждение (anti-bot)", "Включите cookies (браузер) или попробуйте VPN."
        if "private video" in s:
            return "private", "Приватное видео", "Нужны cookies от аккаунта с доступом."
        if "age-restricted" in s or "age restricted" in s or "confirm your age" in s:
            return "age_restricted", "Возрастное ограничение", "Включите cookies от аккаунта (браузер/файл)."
        if ("not available in your country" in s) or ("geo-restricted" in s) or (("country" in s) and ("blocked" in s)):
            return "geo_blocked", "Ограничено по региону", "Попробуйте VPN или другой регион."
        if "cookies" in s and ("required" in s or "use --cookies" in s):
            return "cookies_required", "Требуются cookies", "Откройте настройки и включите cookies (браузер/файл)."
        if "video unavailable" in s or "this video is unavailable" in s:
            return "unavailable", "Видео недоступно", "Проверьте ссылку или доступность видео."
        return "error", raw[:200], ""

    def _get_cookie_args(self, settings: Settings) -> list[str]:
        """Получить аргументы для куки на основе настроек."""
        if settings.use_browser_cookies and settings.selected_browser:
            return ["--cookies-from-browser", settings.selected_browser]
        elif settings.cookies_path:
            path = settings.cookies_path.strip()
            if not path:
                return []
            if not os.path.exists(path):
                return []
            try:
                with open(path, 'rb') as f:
                    header = f.read(16)
                if header.startswith(b'SQLite format 3'):
                    return []
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    first_line = f.readline()
                if '# Netscape HTTP Cookie File' in first_line or '# HTTP Cookie File' in first_line or first_line.strip() == '' or '\t' in first_line:
                    return ["--cookies", path]
                return ["--cookies", path]
            except (OSError, PermissionError):
                return []
        return []

    async def get_playlist_info(self, url: str) -> dict:
        """Получить информацию о плейлисте."""
        if not self.ytdlp_path.exists():
            return {"error": "yt-dlp.exe not found"}

        try:
            settings = load_settings()
            self._refresh_concurrency(settings)
            cmd = [
                str(self.ytdlp_path),
                "-j",
                "--flat-playlist",
            ]
            
            cmd.extend(self._get_cookie_args(settings))
                
            cmd.append(url)

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self._ytdlp_subprocess_kwargs(),
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                raw = stderr.decode("utf-8", errors="replace")
                _, msg, help_text = self._classify_error(raw)
                return {"error": msg, "help": help_text}

            lines = stdout.decode("utf-8", errors="replace").strip().split("\n")
            entries = []
            playlist_title = ""

            for line in lines:
                if not line.strip():
                    continue
                try:
                    info = json.loads(line)
                    
                    if info.get("_type") == "playlist":
                        if not playlist_title:
                            playlist_title = info.get("title") or info.get("playlist_title") or t("main.playlist_title", lang=settings.language)
                        
                        if "entries" in info and isinstance(info["entries"], list):
                            for entry in info["entries"]:
                                if not entry: continue
                                video_id = entry.get("id", "")
                                video_url = entry.get("url") or entry.get("webpage_url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
                                
                                if any(e["id"] == video_id or e["url"] == video_url for e in entries if video_id or video_url):
                                    continue

                                entries.append({
                                    "id": video_id,
                                    "url": video_url,
                                    "title": entry.get("title") or t("main.video_label", lang=settings.language, params={"index": len(entries) + 1}),
                                    "thumbnail": entry.get("thumbnail") or (f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" if video_id else ""),
                                    "index": len(entries) + 1,
                                })
                    
                    elif info.get("url") or info.get("id") or info.get("_type") in ("url", "url_transparent", "video"):
                        video_id = info.get("id", "")
                        video_url = info.get("url") or info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
                        
                        if any(e["id"] == video_id or e["url"] == video_url for e in entries if video_id or video_url):
                            continue

                        entries.append({
                            "id": video_id,
                            "url": video_url,
                            "title": info.get("title") or t("main.video_label", lang=settings.language, params={"index": len(entries) + 1}),
                            "thumbnail": info.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
                            "index": len(entries) + 1,
                        })
                except (json.JSONDecodeError, KeyError):
                    continue

            return {
                "title": playlist_title or t("main.playlist_title", lang=settings.language),
                "entries": entries,
                "is_playlist": len(entries) >= 2,
            }
        except Exception as e:
            return {"error": str(e)[:200]}

    async def add_download(self, url: str) -> DownloadTask:
        """Добавить новую задачу загрузки."""
        settings = load_settings()
        self._refresh_concurrency(settings)

        task = DownloadTask(url=url, title=t("status.queued", lang=settings.language), status=DownloadStatus.QUEUED)
        self.tasks[task.id] = task

        asyncio.create_task(self._run_download(task, settings))
        return task

    async def _run_download(self, task: DownloadTask, settings: Settings) -> None:
        """Выполнить загрузку через yt-dlp."""
        def was_removed() -> bool:
            return task.removed or task.id not in self.tasks

        task.status = DownloadStatus.QUEUED
        task.progress = 0.0
        self._append_log(task, f"[{task.id}] queued url={task.url}")

        async with self._semaphore:
            if was_removed():
                return

            task.status = DownloadStatus.PREPARING
            task.title = t("status.loading_metadata", lang=settings.language)

        if not self.ytdlp_path.exists():
            task.status = DownloadStatus.ERROR
            task.error_message = "yt-dlp.exe not found"
            return

        quality_map = {
            "1080p": "bestvideo[height<=1080]+bestaudio",
            "720p": "bestvideo[height<=720]+bestaudio",
            "480p": "bestvideo[height<=480]+bestaudio",
            "360p": "bestvideo[height<=360]+bestaudio",
            "best": "bestvideo+bestaudio",
        }

        format_str = quality_map.get(settings.default_quality, "bestvideo+bestaudio")

        if settings.download_format.lower() == "mp3":
            format_str = "bestaudio"

        if settings.random_filename:
            output_template = str(Path(settings.save_location) / f"{uuid.uuid4().hex[:8]}.%(ext)s")
        else:
            output_template = str(Path(settings.save_location) / "%(title)s.%(ext)s")

        cmd = [
            str(self.ytdlp_path),
            "--newline",
            "--no-colors",
            "--continue",
            "-f", format_str,
            "-o", output_template,
            "--progress-template", "%(progress.downloaded_bytes)s %(progress.total_bytes)s %(progress.percentage)s %(progress.speed)s %(progress.eta)s",
        ]

        if settings.download_format.lower() == "mp3":
            cmd.extend(["-x", "--audio-format", "mp3"])

        if settings.enable_sponsorblock:
            cmd.extend(["--sponsorblock-remove", "sponsor,intro,outro,selfpromo,interaction,preview,filler"])

        cmd.extend(self._get_cookie_args(settings))

        cmd.append(task.url)

        try:
            title_cmd = [
                str(self.ytdlp_path),
                "-j",
                "--no-playlist",
            ]
            title_cmd.extend(self._get_cookie_args(settings))
            title_cmd.append(task.url)

            title_proc = await asyncio.create_subprocess_exec(
                *title_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self._ytdlp_subprocess_kwargs(),
            )
            stdout, _ = await title_proc.communicate()
            if title_proc.returncode == 0:
                try:
                    info = json.loads(stdout.decode("utf-8"))
                    task.title = info.get("title", task.url)

                    task.thumbnail = info.get("thumbnail", "")
                    if not task.thumbnail:
                        video_id = info.get("id", "")
                        if video_id:
                            task.thumbnail = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"

                    actual_ext = info.get("ext", "").lower()
                    desired_format = settings.download_format.lower()

                    if desired_format != "mp3" and actual_ext and actual_ext != desired_format:
                        task.format_warning = t(
                            "notifications.format_mismatch",
                            lang=settings.language,
                            params={"actual": actual_ext.upper(), "desired": desired_format.upper()}
                        )
                except (json.JSONDecodeError, UnicodeDecodeError):
                    task.title = task.url
            else:
                task.title = task.url

            task.status = DownloadStatus.DOWNLOADING
            task.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self._ytdlp_subprocess_kwargs(),
            )

            stderr_task = asyncio.create_task(self._consume_stderr(task))

            while True:
                if task.status == DownloadStatus.PAUSED:
                    await asyncio.sleep(1)
                    continue

                line = await task.process.stdout.readline()
                if not line:
                    break

                text = line.decode("utf-8", errors="replace").strip()
                if text:
                    self._append_log(task, text)

                parts = text.split()
                if len(parts) >= 5:
                    try:
                        task.downloaded_bytes = int(parts[0])
                        task.total_bytes = int(parts[1])
                        if task.total_bytes > 0:
                            task.progress = round((task.downloaded_bytes / task.total_bytes) * 100, 1)
                        
                        try:
                            speed_val = float(parts[3])
                            if speed_val > 1024 * 1024:
                                task.speed = f"{speed_val / (1024 * 1024):.1f} MB/s"
                            elif speed_val > 1024:
                                task.speed = f"{speed_val / 1024:.1f} KB/s"
                            else:
                                task.speed = f"{speed_val:.1f} B/s"
                        except:
                            task.speed = parts[3]
                            
                        try:
                            eta_val = int(parts[4])
                            mm, ss = divmod(eta_val, 60)
                            hh, mm = divmod(mm, 60)
                            if hh > 0:
                                task.eta = f"{hh:02}:{mm:02}:{ss:02}"
                            else:
                                task.eta = f"{mm:02}:{ss:02}"
                        except:
                            task.eta = parts[4]
                    except (ValueError, IndexError):
                        pass

            await task.process.wait()
            stderr_output = await stderr_task

            if was_removed():
                return

            if task.status == DownloadStatus.PAUSED:
                return

            if task.process.returncode == 0:
                task.status = DownloadStatus.PROCESSING
                task.progress = 100.0
                await asyncio.sleep(0.5)
                if was_removed():
                    return
                task.status = DownloadStatus.COMPLETED
                task.progress = 100.0

                try:
                    history_manager.add_record(
                        url=task.url,
                        title=task.title,
                        thumbnail=task.thumbnail,
                        file_path=str(Path(settings.save_location)),
                        file_size=task.total_bytes,
                        format=settings.download_format,
                        quality=settings.default_quality,
                        status="completed"
                    )
                except Exception as e:
                    print("Error saving history:", e)

                if settings.auto_clear_queue:
                    await asyncio.sleep(2)
                    self.tasks.pop(task.id, None)
            else:
                if was_removed():
                    return
                task.status = DownloadStatus.ERROR
                if stderr_output:
                    self._append_log(task, "\n[stderr]\n" + stderr_output)
                code, msg, help_text = self._classify_error(stderr_output)
                task.error_code = code
                task.error_message = msg
                task.error_help = help_text
                
                try:
                    history_manager.add_record(
                        url=task.url,
                        title=task.title,
                        thumbnail=task.thumbnail,
                        file_path="",
                        file_size=0,
                        format=settings.download_format,
                        quality=settings.default_quality,
                        status="error",
                        error_msg=task.error_message
                    )
                except:
                    pass

        except Exception as e:
            if was_removed():
                return
            task.status = DownloadStatus.ERROR
            raw = str(e)
            self._append_log(task, "\n[exception]\n" + raw)
            code, msg, help_text = self._classify_error(raw)
            task.error_code = code
            task.error_message = msg
            task.error_help = help_text
            
            try:
                history_manager.add_record(
                    url=task.url,
                    title=task.title,
                    thumbnail=task.thumbnail,
                    file_path="",
                    file_size=0,
                    format=settings.download_format,
                    quality=settings.default_quality,
                    status="error",
                    error_msg=task.error_message
                )
            except:
                pass

    def _manage_process_tree(self, pid: int, action: str) -> None:
        """Рекурсивно управляет деревом процессов."""
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            processes = children + [parent]
            
            for p in processes:
                try:
                    if action == "suspend":
                        p.suspend()
                    elif action == "resume":
                        p.resume()
                    elif action == "kill":
                        p.kill()
                    elif action == "terminate":
                        p.terminate()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    def pause_download(self, task_id: str) -> Optional[DownloadTask]:
        """Приостановить загрузку — приостанавливаем дерево процессов."""
        task = self.tasks.get(task_id)
        if task and task.status == DownloadStatus.DOWNLOADING:
            task.status = DownloadStatus.PAUSED
            if task.process and task.process.returncode is None:
                self._manage_process_tree(task.process.pid, "suspend")
        return task

    async def resume_download(self, task_id: str) -> Optional[DownloadTask]:
        """Возобновить загрузку."""
        task = self.tasks.get(task_id)
        if task and task.status == DownloadStatus.PAUSED:
            if task.process and task.process.returncode is None:
                task.status = DownloadStatus.DOWNLOADING
                self._manage_process_tree(task.process.pid, "resume")
            else:
                settings = load_settings()
                task.status = DownloadStatus.DOWNLOADING
                task.downloaded_bytes = 0
                task.total_bytes = 0
                task.progress = 0.0
                task.speed = ""
                task.eta = ""
                task.resumed = True
                asyncio.create_task(self._run_download(task, settings))
        return task

    def cancel_download(self, task_id: str) -> Optional[DownloadTask]:
        """Отменить загрузку."""
        task = self.tasks.get(task_id)
        if task:
            if task.status in (DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING, DownloadStatus.PAUSED):
                if task.process and task.process.returncode is None:
                    self._manage_process_tree(task.process.pid, "kill")
            task.status = DownloadStatus.ERROR
            task.error_message = "Cancelled by user"
        return task

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        """Получить задачу по ID."""
        return self.tasks.get(task_id)

    def get_all_tasks(self) -> list[DownloadTask]:
        """Получить все задачи."""
        return list(self.tasks.values())

    def remove_task(self, task_id: str) -> bool:
        """Удалить задачу из очереди."""
        if task_id in self.tasks:
            task = self.tasks[task_id]
            task.removed = True
            if task.status in (DownloadStatus.DOWNLOADING, DownloadStatus.PAUSED) and task.process:
                if task.process.returncode is None:
                    self._manage_process_tree(task.process.pid, "kill")
            del self.tasks[task_id]
            return True
        return False

    async def search_videos(self, query: str, limit: int = 10) -> dict:
        """Поиск видео на YouTube через yt-dlp."""
        if not self.ytdlp_path.exists():
            return {"error": "yt-dlp.exe not found"}

        try:
            settings = load_settings()
            self._refresh_concurrency(settings)
            cmd = [
                str(self.ytdlp_path),
                "-j",
                "--flat-playlist",
                "--no-warnings",
                f"ytsearch{limit}:{query}",
            ]

            cmd.extend(self._get_cookie_args(settings))

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **self._ytdlp_subprocess_kwargs(),
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                raw = stderr.decode("utf-8", errors="replace")
                _, msg, help_text = self._classify_error(raw)
                return {"error": msg or "Search failed", "help": help_text}

            lines = stdout.decode("utf-8", errors="replace").strip().split("\n")
            results = []

            for line in lines:
                if not line.strip():
                    continue
                try:
                    info = json.loads(line)
                    video_id = info.get("id", "")
                    video_url = info.get("url") or info.get("webpage_url") or (
                        f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
                    )

                    duration_secs = info.get("duration") or 0
                    if duration_secs:
                        mins, secs = divmod(int(duration_secs), 60)
                        hours, mins = divmod(mins, 60)
                        if hours > 0:
                            duration_str = f"{hours}:{mins:02}:{secs:02}"
                        else:
                            duration_str = f"{mins}:{secs:02}"
                    else:
                        duration_str = ""

                    results.append({
                        "id": video_id,
                        "url": video_url,
                        "title": info.get("title", f"Video {len(results) + 1}"),
                        "thumbnail": info.get("thumbnail") or (
                            f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" if video_id else ""
                        ),
                        "duration": duration_str,
                        "channel": info.get("channel") or info.get("uploader") or "",
                        "view_count": info.get("view_count") or 0,
                        "index": len(results) + 1,
                    })
                except (json.JSONDecodeError, KeyError):
                    continue

            return {
                "query": query,
                "results": results,
                "count": len(results),
            }
        except Exception as e:
            return {"error": str(e)[:200]}

    def get_active_count(self) -> int:
        """Получить количество активных загрузок."""
        return sum(1 for t in self.tasks.values() if t.status in (
            DownloadStatus.DOWNLOADING, DownloadStatus.PAUSED, DownloadStatus.PROCESSING
        ))


download_manager = DownloadManager()
