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
        self.ytdlp_path = Path(__file__).parent.parent / "yt-dlp.exe"

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
            
            if settings.cookies_path and os.path.exists(settings.cookies_path):
                cmd.extend(["--cookies", settings.cookies_path])
                
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
                    if info.get("_type") == "playlist":
                        playlist_title = info.get("title", "Плейлист")
                    elif info.get("url") or info.get("id"):
                        video_id = info.get("id", "")
                        entries.append({
                            "id": video_id,
                            "url": info.get("url", f"https://www.youtube.com/watch?v={video_id}"),
                            "title": info.get("title", f"Видео #{len(entries) + 1}"),
                            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
                            "index": len(entries) + 1,
                        })
                except json.JSONDecodeError:
                    continue

            return {
                "title": playlist_title or "Плейлист",
                "entries": entries,
            }
        except Exception as e:
            return {"error": str(e)[:200]}

    async def add_download(self, url: str) -> DownloadTask:
        """Добавить новую задачу загрузки."""
        settings = load_settings()

        task = DownloadTask(url=url, title="Загрузка метаданных...")
        self.tasks[task.id] = task

        asyncio.create_task(self._run_download(task, settings))
        return task

    async def _run_download(self, task: DownloadTask, settings: Settings) -> None:
        """Выполнить загрузку через yt-dlp."""
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

        if settings.cookies_path and os.path.exists(settings.cookies_path):
            cmd.extend(["--cookies", settings.cookies_path])

        cmd.append(task.url)

        try:
            # Получаем заголовок видео через JSON-вывод (всегда UTF-8)
            title_cmd = [
                str(self.ytdlp_path),
                "-j",  # JSON output
                "--no-playlist",
            ]
            if settings.cookies_path and os.path.exists(settings.cookies_path):
                title_cmd.extend(["--cookies", settings.cookies_path])
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
                        task.format_warning = f"Формат {actual_ext.upper()} вместо {desired_format.upper()}"
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

            if task.status == DownloadStatus.PAUSED:
                return

            if task.process.returncode == 0:
                task.status = DownloadStatus.PROCESSING
                task.progress = 100.0
                await asyncio.sleep(0.5)
                task.status = DownloadStatus.COMPLETED
                task.progress = 100.0

                # Авто-очистка: ждём 2 секунды чтобы пользователь увидел статус, затем удаляем
                if settings.auto_clear_queue:
                    await asyncio.sleep(2)
                    self.tasks.pop(task.id, None)
            else:
                stderr_output = ""
                if task.process.stderr:
                    stderr_output = await task.process.stderr.read()
                    stderr_output = stderr_output.decode("utf-8", errors="replace")
                task.status = DownloadStatus.ERROR
                task.error_message = stderr_output.strip()[:200] if stderr_output else "Unknown error"

        except Exception as e:
            task.status = DownloadStatus.ERROR
            task.error_message = str(e)[:200]

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
