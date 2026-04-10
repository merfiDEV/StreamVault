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
        }


class DownloadManager:
    """Управляет очередью загрузок yt-dlp."""

    def __init__(self):
        self.tasks: dict[str, DownloadTask] = {}
        self.ytdlp_path = Path(__file__).parent.parent / "yt-dlp.exe"

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

        output_template = str(Path(settings.save_location) / "%(title)s.%(ext)s")

        cmd = [
            str(self.ytdlp_path),
            "--newline",
            "--no-colors",
            "-f", format_str,
            "-o", output_template,
            "--progress-template", "%(progress.downloaded_bytes)s %(progress.total_bytes)s %(progress.percentage)s %(progress.speed)s %(progress.eta)s",
        ]

        if settings.download_format.lower() == "mp3":
            cmd.extend(["-x", "--audio-format", "mp3"])

        cmd.append(task.url)

        try:
            # Получаем заголовок видео перед загрузкой
            title_proc = await asyncio.create_subprocess_exec(
                str(self.ytdlp_path),
                "--get-title",
                "--no-playlist",
                task.url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await title_proc.communicate()
            if title_proc.returncode == 0:
                task.title = stdout.decode("utf-8", errors="replace").strip()
            else:
                task.title = task.url

            task.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Парсим прогресс
            while True:
                if task.status == DownloadStatus.PAUSED:
                    if task.process:
                        task.process.kill()
                        await task.process.wait()
                    return

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

    def pause_download(self, task_id: str) -> Optional[DownloadTask]:
        """Приостановить загрузку."""
        task = self.tasks.get(task_id)
        if task and task.status == DownloadStatus.DOWNLOADING:
            task.status = DownloadStatus.PAUSED
        return task

    async def resume_download(self, task_id: str) -> Optional[DownloadTask]:
        """Возобновить загрузку."""
        task = self.tasks.get(task_id)
        if task and task.status == DownloadStatus.PAUSED:
            settings = load_settings()
            task.status = DownloadStatus.DOWNLOADING
            asyncio.create_task(self._run_download(task, settings))
        return task

    def cancel_download(self, task_id: str) -> Optional[DownloadTask]:
        """Отменить загрузку."""
        task = self.tasks.get(task_id)
        if task:
            if task.status in (DownloadStatus.DOWNLOADING, DownloadStatus.PROCESSING):
                if task.process:
                    task.process.kill()
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
            if task.status == DownloadStatus.DOWNLOADING and task.process:
                task.process.kill()
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
