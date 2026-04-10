"""Модуль управления настройками приложения."""

import json
import os
from pathlib import Path
from pydantic import BaseModel


class Settings(BaseModel):
    """Модель настроек приложения."""
    default_quality: str = "1080p"
    download_format: str = "mp4"
    save_location: str = str(Path.home() / "Videos" / "StreamVault")
    dark_theme: bool = False
    wifi_only: bool = False
    auto_clear_queue: bool = False
    random_filename: bool = False
    cookies_path: str = ""


CONFIG_PATH = Path(__file__).parent.parent / "config.json"


def load_settings() -> Settings:
    """Загрузить настройки из файла или вернуть значения по умолчанию."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Settings(**data)
    return Settings()


def save_settings(settings: Settings) -> None:
    """Сохранить настройки в файл."""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(settings.model_dump(), f, indent=2, ensure_ascii=False)


def ensure_save_location() -> None:
    """Убедиться, что директория для сохранения существует."""
    settings = load_settings()
    os.makedirs(settings.save_location, exist_ok=True)
