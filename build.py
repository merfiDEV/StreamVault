"""Build StreamVault for release packaging."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _venv_python(root: Path) -> Path:
    return root / ".venv" / "Scripts" / "python.exe"


def _reexec_in_venv(root: Path) -> None:
    venv_python = _venv_python(root)
    if venv_python.exists() and Path(sys.executable).resolve() != venv_python.resolve():
        print(f"Restarting build through virtual environment: {venv_python}")
        subprocess.check_call([str(venv_python), str(Path(__file__).resolve())])
        raise SystemExit(0)


def _ensure_pyinstaller() -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller is not installed. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def _hidden_import_args() -> list[str]:
    hidden_packages = [
        "uvicorn",
        "fastapi",
        "starlette",
        "anyio",
        "pydantic",
        "websockets",
        "webview",
    ]

    hidden_args: list[str] = []
    try:
        from PyInstaller.utils.hooks import collect_submodules

        for pkg in hidden_packages:
            hidden_args.append(f"--collect-submodules={pkg}")
            for mod in collect_submodules(pkg):
                if mod != pkg:
                    hidden_args.append(f"--hidden-import={mod}")
    except Exception:
        for pkg in hidden_packages:
            hidden_args.append(f"--hidden-import={pkg}")

    return hidden_args


def _build(mode: str) -> None:
    root = Path(__file__).parent
    dist = root / "dist"
    build_dir = root / "build"

    icon_path = root / ".assets" / "favicon.jpg"
    icon_arg = [f"--icon={str(icon_path)}"] if icon_path.exists() else []

    common_args = [
        "--noconfirm",
        f"--add-data={str(root / 'ui')};ui",
        f"--add-data={str(root / 'locales')};locales",
        f"--add-data={str(root / 'yt-dlp.exe')};.",
        *icon_arg,
        *_hidden_import_args(),
    ]

    if mode == "onedir":
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onedir",
            "--noconsole",
            "--name=StreamVault",
            "--clean",
            *common_args,
            "main.py",
        ]
    elif mode == "onefile":
        cmd = [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",
            "--noconsole",
            "--name=StreamVault",
            "--clean",
            *common_args,
            "main.py",
        ]
    else:
        raise ValueError(f"Unsupported BUILD_MODE: {mode}")

    print(f"Building mode: {mode}")
    print(f"Command: {' '.join(cmd)}")
    subprocess.check_call(cmd)

    if mode == "onedir":
        bundle_dir = dist / "StreamVault"
        archive_path = Path(shutil.make_archive(str(dist / "StreamVault"), "zip", root_dir=dist, base_dir="StreamVault"))
        print("\n" + "=" * 50)
        print("ONEDIR BUILD COMPLETE")
        print(f"Bundle folder: {bundle_dir}")
        print(f"Release ZIP: {archive_path}")
        print("=" * 50)
    else:
        exe_path = dist / "StreamVault.exe"
        print("\n" + "=" * 50)
        print("ONEFILE BUILD COMPLETE")
        print(f"Legacy EXE: {exe_path}")
        print("=" * 50)


def main() -> None:
    root = Path(__file__).parent
    _reexec_in_venv(root)
    _ensure_pyinstaller()
    mode = os.environ.get("BUILD_MODE", "onedir").strip().lower()
    _build(mode)


if __name__ == "__main__":
    main()
