"""Build StreamVault as onedir and package a ZIP release asset."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def build():
    root = Path(__file__).parent
    venv_python = root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
        print(f"Перезапускаю сборку через виртуальное окружение: {venv_python}")
        subprocess.check_call([str(venv_python), str(Path(__file__).resolve())])
        return

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller не установлен. Устанавливаю...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])

    dist = root / "dist"

    icon_path = root / ".assets" / "favicon.jpg"
    icon_arg = []
    if icon_path.exists():
        icon_arg = [f"--icon={str(icon_path)}"]

    hidden_packages = [
        "uvicorn",
        "fastapi",
        "starlette",
        "anyio",
        "pydantic",
        "websockets",
        "webview",
    ]

    hidden_args = []
    try:
        from PyInstaller.utils.hooks import collect_submodules

        for pkg in hidden_packages:
            hidden_args.extend([f"--collect-submodules={pkg}"])
            for mod in collect_submodules(pkg):
                if mod != pkg:
                    hidden_args.extend([f"--hidden-import={mod}"])
    except Exception:
        for pkg in hidden_packages:
            hidden_args.extend([f"--hidden-import={pkg}"])

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",
        "--noconsole",
        "--name=StreamVault",
        "--clean",
        f"--add-data={str(root / 'ui')};ui",
        f"--add-data={str(root / 'locales')};locales",
        f"--add-data={str(root / 'yt-dlp.exe')};.",
        *icon_arg,
        *hidden_args,
        "main.py",
    ]

    print("Начинаю сборку...")
    print(f"Команда: {' '.join(cmd)}")

    try:
        subprocess.check_call(cmd)
        bundle_dir = dist / "StreamVault"
        archive_path = Path(
            shutil.make_archive(str(dist / "StreamVault"), "zip", root_dir=dist, base_dir="StreamVault")
        )
        print("\n" + "=" * 50)
        print("СБОРКА ЗАВЕРШЕНА УСПЕШНО!")
        print(f"Папка приложения: {bundle_dir}")
        print(f"ZIP для релиза: {archive_path}")
        print("=" * 50)
    except subprocess.CalledProcessError as e:
        print(f"\nОшибка при сборке: {e}")
        sys.exit(1)


if __name__ == "__main__":
    build()
