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
from core.utils import get_resource_path


class DownloadStatus(str, Enum):
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
        self.format_warning = ""
        self.thumbnail = ""
        self.resumed = False
        self.removed = False
        self.process: Optional[asyncio.subprocess.Process] = None

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
            "format_warning": self.format_warning,
            "thumbnail": self.thumbnail,
            "resumed": self.resumed,
        }


class DownloadManager:
    """Управляет очередью загрузок yt-dlp."""

    def __init__(self):
        self.tasks: dict[str, DownloadTask] = {}
        self.ytdlp_path = get_resource_path("yt-dlp.exe")

    def _get_cookie_args(self, settings: Settings) -> list[str]:
        """Получить аргументы для куки на основе настроек."""
        # Режим 1: Куки из браузера
        if settings.use_browser_cookies and settings.selected_browser:
            return ["--cookies-from-browser", settings.selected_browser]
        # Режим 2: Куки из файла .txt (Netscape формат)
        elif settings.cookies_path:
            path = settings.cookies_path.strip()
            if not path:
                return []
            if not os.path.exists(path):
                return []  # Файл не существует — игнорируем без ошибки
            # Проверяем что это не SQLite база Chrome (бинарный файл)
            try:
                with open(path, 'rb') as f:
                    header = f.read(16)
                # SQLite файлы начинаются с 'SQLite format 3'
                if header.startswith(b'SQLite format 3'):
                    return []  # Это база Chrome — игнорируем
                # Проверяем что файл текстовый (Netscape cookies.txt)
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    first_line = f.readline()
                if '# Netscape HTTP Cookie File' in first_line or '# HTTP Cookie File' in first_line or first_line.strip() == '' or '\t' in first_line:
                    return ["--cookies", path]
                return ["--cookies", path]  # Передаём как есть, yt-dlp сам разберётся
            except (OSError, PermissionError):
                return []  # Нет доступа — игнорируем
        return []

    async def get_playlist_info(self, url: str) -> dict:
        """Получить информацию о плейлисте."""
        if not self.ytdlp_path.exists():
            return {"error": "yt-dlp.exe not found"}

        try:
            settings = load_settings()
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
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                return {"error": stderr.decode("utf-8", errors="replace")[:200]}

            lines = stdout.decode("utf-8", errors="replace").strip().split("\n")
            entries = []
            playlist_title = ""

            for line in lines:
                if not line.strip():
                    continue
                try:
                    info = json.loads(line)
                    
                    # Если это объект плейлиста
                    if info.get("_type") == "playlist":
                        if not playlist_title:
                            playlist_title = info.get("title") or info.get("playlist_title") or t("main.playlist_title", lang=settings.language)
                        
                        # Обработка встроенных записей (если они есть в этом же объекте)
                        if "entries" in info and isinstance(info["entries"], list):
                            for entry in info["entries"]:
                                if not entry: continue
                                video_id = entry.get("id", "")
                                video_url = entry.get("url") or entry.get("webpage_url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
                                
                                # Проверка на дубликаты
                                if any(e["id"] == video_id or e["url"] == video_url for e in entries if video_id or video_url):
                                    continue

                                entries.append({
                                    "id": video_id,
                                    "url": video_url,
                                    "title": entry.get("title") or t("main.video_label", lang=settings.language, params={"index": len(entries) + 1}),
                                    "thumbnail": entry.get("thumbnail") or (f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" if video_id else ""),
                                    "index": len(entries) + 1,
                                })
                    
                    # Если это отдельная запись о видео (стандарт для Flat Playlist)
                    elif info.get("url") or info.get("id") or info.get("_type") in ("url", "url_transparent", "video"):
                        video_id = info.get("id", "")
                        video_url = info.get("url") or info.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
                        
                        # Проверка на дубликаты
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

        task = DownloadTask(url=url, title=t("status.loading_metadata", lang=settings.language))
        self.tasks[task.id] = task

        asyncio.create_task(self._run_download(task, settings))
        return task

    async def _run_download(self, task: DownloadTask, settings: Settings) -> None:
        """Выполнить загрузку через yt-dlp."""
        def was_removed() -> bool:
            return task.removed or task.id not in self.tasks

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
            # Используем случайное имя (8 символов) + расширение
            output_template = str(Path(settings.save_location) / f"{uuid.uuid4().hex[:8]}.%(ext)s")
        else:
            output_template = str(Path(settings.save_location) / "%(title)s.%(ext)s")

        cmd = [
            str(self.ytdlp_path),
            "--newline",
            "--no-colors",
            "--continue",  # Поддержка докачки с места паузы
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
            # Получаем заголовок видео через JSON-вывод (всегда UTF-8)
            title_cmd = [
                str(self.ytdlp_path),
                "-j",  # JSON output
                "--no-playlist",
            ]
            title_cmd.extend(self._get_cookie_args(settings))
            title_cmd.append(task.url)

            title_proc = await asyncio.create_subprocess_exec(
                *title_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await title_proc.communicate()
            if title_proc.returncode == 0:
                try:
                    info = json.loads(stdout.decode("utf-8"))
                    task.title = info.get("title", task.url)

                    # Получаем превью
                    task.thumbnail = info.get("thumbnail", "")
                    # Если thumbnail нет, формируем из video_id
                    if not task.thumbnail:
                        video_id = info.get("id", "")
                        if video_id:
                            task.thumbnail = f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"

                    # Проверяем реальный формат видео
                    actual_ext = info.get("ext", "").lower()
                    desired_format = settings.download_format.lower()

                    # Формат не совпадает с желаемым (не для mp3, так как mp3 конвертируется)
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

            task.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Парсим прогресс
            while True:
                # Если задача на паузе, мы просто ждем. 
                # Благодаря psutil.suspend(), процесс yt-dlp замрет,
                # и readline() ниже просто перестанет возвращать данные до возобновления.
                if task.status == DownloadStatus.PAUSED:
                    await asyncio.sleep(1)
                    continue

                line = await task.process.stdout.readline()
                if not line:
                    break

                text = line.decode("utf-8", errors="replace").strip()

                # Пытаемся распарсить прогресс
                parts = text.split()
                if len(parts) >= 5:
                    try:
                        task.downloaded_bytes = int(parts[0])
                        task.total_bytes = int(parts[1])
                        if task.total_bytes > 0:
                            task.progress = round((task.downloaded_bytes / task.total_bytes) * 100, 1)
                        
                        # Форматируем скорость
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
                            
                        # Форматируем ETA
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

                # Сохраняем в историю
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

                # Авто-очистка: ждём 2 секунды чтобы пользователь увидел статус, затем удаляем
                if settings.auto_clear_queue:
                    await asyncio.sleep(2)
                    self.tasks.pop(task.id, None)
            else:
                if was_removed():
                    return
                stderr_output = ""
                if task.process.stderr:
                    stderr_output = await task.process.stderr.read()
                    stderr_output = stderr_output.decode("utf-8", errors="replace")
                task.status = DownloadStatus.ERROR
                task.error_message = stderr_output.strip()[:200] if stderr_output else "Unknown error"
                
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
            task.error_message = str(e)[:200]
            
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
            # Проверяем, жив ли еще процесс
            if task.process and task.process.returncode is None:
                task.status = DownloadStatus.DOWNLOADING
                self._manage_process_tree(task.process.pid, "resume")
            else:
                # Если процесс умер, перезапускаем как раньше
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

    def get_active_count(self) -> int:
        """Получить количество активных загрузок."""
        return sum(1 for t in self.tasks.values() if t.status in (
            DownloadStatus.DOWNLOADING, DownloadStatus.PAUSED, DownloadStatus.PROCESSING
        ))


# Глобальный экземпляр
download_manager = DownloadManager()
