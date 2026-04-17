"""Утилиты для работы с путями и данными приложения."""

import sys
import os
from pathlib import Path

def get_resource_path(relative_path: str) -> Path:
    """Получить абсолютный путь к ресурсу (в т.ч. в PyInstaller onefile)."""
    try:
        if hasattr(sys, '_MEIPASS'):
            base_path = Path(sys._MEIPASS)
        else:
            base_path = Path(__file__).parent.parent
    except Exception:
        base_path = Path(__file__).parent.parent

    return base_path / relative_path


def get_data_path(filename: str) -> Path:
    """Получить путь для изменяемых данных (база данных, конфиг)."""
    if hasattr(sys, 'frozen'):
        appdata = os.environ.get("APPDATA")
        if appdata:
            base_path = Path(appdata) / "StreamVault"
        else:
            base_path = Path.home() / "AppData" / "Roaming" / "StreamVault"
    else:
        base_path = Path(__file__).parent.parent

    try:
        base_path.mkdir(parents=True, exist_ok=True)
    except Exception:
        base_path = Path.cwd()

    return base_path / filename


def ensure_file_from_resources(relative_path: str, target_path: Path) -> Path:
    """Убедиться, что файл доступен по target_path (при необходимости скопировать из ресурсов)."""
    try:
        if target_path.exists():
            return target_path
        src = get_resource_path(relative_path)
        if not src.exists():
            return src
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(src.read_bytes())
        return target_path
    except Exception:
        return get_resource_path(relative_path)
