<div align="center">

# ⬇️ StreamVault

**Минималистичный десктопный загрузчик видео на базе yt-dlp**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-latest-FF0000?style=flat-square&logo=youtube&logoColor=white)](https://github.com/yt-dlp/yt-dlp)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?style=flat-square&logo=windows&logoColor=white)](https://github.com/merfiDEV/ytdownloader)

<br/>

![StreamVault Main UI](.assets/интерфейс%20главного%20меню.jpg)

</div>

---

## ✨ Возможности

- 📥 **Скачивание видео и аудио** — поддержка YouTube и других платформ через yt-dlp
- 🎵 **Форматы** — MP4, WEBM, MP3 (аудио)
- 🎬 **Качество** — от 360p до 1080p и лучшее доступное
- 📋 **Плейлисты** — умное определение, выбор отдельных видео для загрузки
- ⏸️ **Пауза / Возобновление** — приостановка и продолжение загрузок
- 🍪 **Cookies** — три режима: отключено / файл .txt / браузер (chrome, firefox, edge, brave, opera, vivaldi)
- 🌙 **Тёмная тема** — переключение в один клик, синхронизация между страницами
- 🎲 **Случайное имя файла** — опциональная анонимизация файлов
- 📊 **Статистика хранилища** — отображение свободного места на диске и размера папки загрузок
- ⚡ **Реальное время** — WebSocket обновления прогресса без перезагрузки страницы

---

## 📸 Скриншоты

<table>
  <tr>
    <td align="center">
      <img src=".assets/демонстрация%20загрузок.jpg" alt="Настройки загрузки" width="100%"/>
      <sub><b>Настройки загрузки</b></sub>
    </td>
    <td align="center">
      <img src=".assets/система%20куки.jpg" alt="Система Cookies" width="100%"/>
      <sub><b>Система Cookies</b></sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src=".assets/история.jpg" alt="История скачиваний" width="100%"/>
      <sub><b>История скачиваний</b></sub>
    </td>
  </tr>
</table>

---

## 🚀 Быстрый старт

### Требования

- **Python** 3.11+
- **Windows** (pywebview использует WinRT)
- `yt-dlp.exe` в корне проекта

### Установка

```bash
# 1. Клонировать репозиторий
git clone https://github.com/merfiDEV/ytdownloader.git
cd ytdownloader

# 2. Создать виртуальное окружение
python -m venv venv
venv\Scripts\activate

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Скачать yt-dlp.exe
# https://github.com/yt-dlp/yt-dlp/releases/latest
# Положить yt-dlp.exe в корень проекта
```

### Запуск

```bash
python main.py
```

---

## 🍪 Настройка Cookies

Cookies нужны для скачивания видео 18+, приватных и ограниченных видео.

| Режим | Описание |
|---|---|
| **Отключено** | Куки не используются (по умолчанию) |
| **Файл** | Укажите путь к `cookies.txt` в формате Netscape |
| **Браузер** | yt-dlp извлечёт куки напрямую (Chrome, Firefox, Edge и др.) |

> [!WARNING]
> Файл базы данных Chrome (`AppData\...\Network\Cookies`) **не подходит** — это SQLite-база.
> Для экспорта используйте расширение [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc).

---

## 🗂️ Структура проекта

```
ytdownloader/
├── main.py              # FastAPI сервер + pywebview десктопное окно
├── yt-dlp.exe           # Движок загрузки (не в репозитории)
├── config.json          # Настройки пользователя
├── requirements.txt     # Python зависимости
├── core/
│   ├── config.py        # Модель настроек, чтение/запись config.json
│   └── downloader.py    # DownloadManager, yt-dlp процессы, очередь
└── ui/
    ├── index.html       # Главная страница (загрузки, очередь)
    └── settings.html    # Страница настроек
```

---

## ⚙️ Стек технологий

| Слой | Технология |
|---|---|
| **Движок** | [yt-dlp](https://github.com/yt-dlp/yt-dlp) — загрузка видео |
| **Backend** | [FastAPI](https://fastapi.tiangolo.com) + [uvicorn](https://www.uvicorn.org) |
| **Frontend** | HTML + Vanilla JS + [Tailwind CSS](https://tailwindcss.com) |
| **Десктоп** | [pywebview](https://pywebview.flowrl.com) |
| **Real-time** | WebSocket (FastAPI native) |
| **Процессы** | [psutil](https://github.com/giampaolo/psutil) — управление деревом процессов |

---

## 🔧 Конфигурация

Настройки хранятся в `config.json` и редактируются через UI:

```jsonc
{
  "default_quality": "1080p",   // best | 1080p | 720p | 480p | 360p
  "download_format": "mp4",     // mp4 | webm | mp3
  "save_location": "C:\\Users\\Name\\Videos\\StreamVault",
  "dark_theme": false,
  "auto_clear_queue": false,    // удалять задачи после завершения
  "random_filename": false,     // использовать случайное имя файла
  "cookies_path": "",           // путь к cookies.txt (Netscape формат)
  "use_browser_cookies": false, // использовать куки из браузера
  "selected_browser": "chrome"  // chrome | firefox | edge | opera | brave | vivaldi
}
```

---

## 📝 Лицензия

Распространяется под лицензией **MIT**. Подробнее см. [LICENSE](LICENSE).

---

<div align="center">

Сделано с ❤️ для любителей видео · [merfiDEV](https://github.com/merfiDEV)

</div>
