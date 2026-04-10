"""Главный файл приложения — FastAPI сервер + PyWebView."""

import asyncio
import json
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from core.config import Settings, load_settings, save_settings, ensure_save_location
from core.downloader import download_manager, DownloadStatus


# --- Модели запросов/ответов ---

class DownloadRequest(BaseModel):
    url: str


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


async def broadcast_progress():
    """Периодически отправляет прогресс всех активных загрузок."""
    while True:
        tasks = download_manager.get_all_tasks()
        if tasks:
            data = {
                "type": "progress_update",
                "tasks": [t.to_dict() for t in tasks],
                "active_count": download_manager.get_active_count(),
            }
            await manager.broadcast(data)
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


# --- API Endpoints ---

@app.post("/api/download", response_model=TaskResponse)
async def start_download(request: DownloadRequest):
    """Начать загрузку видео по URL."""
    task = await download_manager.add_download(request.url)
    return TaskResponse(**task.to_dict())


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
        return {"status": "removed"}
    return {"error": "Task not found"}


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

UI_DIR = Path(__file__).parent / "ui"


@app.get("/")
async def index():
    """Главная страница."""
    return FileResponse(UI_DIR / "index.html")


@app.get("/settings")
async def settings_page():
    """Страница настроек."""
    return FileResponse(UI_DIR / "settings.html")


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
