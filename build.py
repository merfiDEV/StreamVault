"""Скрипт для сборки StreamVault в один EXE файл."""

import subprocess
import sys
from pathlib import Path

def build():
    # 1. Проверяем наличие PyInstaller
    try:
        import PyInstaller
    except ImportError:
        print("PyInstaller не установлен. Устанавливаю...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    # 2. Определяем пути
    root = Path(__file__).parent
    dist = root / "dist"
    build_dir = root / "build"
    
    # 3. Настройка иконки
    icon_path = root / ".assets" / "favicon.jpg"
    icon_arg = []
    if icon_path.exists():
        # В идеале нужен .ico, но попробуем передать как есть 
        # или просто добавим в ресурсы если PyInstaller не примет как иконку EXE
        icon_arg = [f"--icon={str(icon_path)}"]

    # 4. Команда сборки
    # --onefile: собрать в один файл
    # --noconsole: не показывать окно консоли (только GUI)
    # --add-data: добавить папки и файлы внутрь EXE
    # Разделитель для --add-data в Windows это ';'
    
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconsole",
        "--name=StreamVault",
        "--clean",
        f"--add-data={str(root / 'ui')};ui",
        f"--add-data={str(root / 'locales')};locales",
        f"--add-data={str(root / 'yt-dlp.exe')};.",
        *icon_arg,
        "main.py"
    ]

    print("Начинаю сборку...")
    print(f"Команда: {' '.join(cmd)}")
    
    try:
        subprocess.check_call(cmd)
        print("\n" + "="*50)
        print("СБОРКА ЗАВЕРШЕНА УСПЕШНО!")
        print(f"Ваш файл находится здесь: {dist / 'StreamVault.exe'}")
        print("="*50)
    except subprocess.CalledProcessError as e:
        print(f"\nОшибка при сборке: {e}")
        sys.exit(1)

if __name__ == "__main__":
    build()
