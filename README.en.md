<div align="center">

[English](README.en.md) | [Русский](README.md)

# ⬇️ StreamVault

**Minimalist desktop video downloader based on yt-dlp**

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![yt-dlp](https://img.shields.io/badge/yt--dlp-latest-FF0000?style=flat-square&logo=youtube&logoColor=white)](https://github.com/yt-dlp/yt-dlp)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?style=flat-square&logo=windows&logoColor=white)](https://github.com/merfiDEV/ytdownloader)

<br/>

![StreamVault Main UI](.assets/интерфейс%20главного%20меню.jpg)

</div>

---

## ✨ Features

- 📥 **Video and Audio Download** — support for YouTube and other platforms via yt-dlp
- 🎵 **Formats** — MP4, WEBM, MP3 (audio)
- 🎬 **Quality** — from 360p to 1080p and best available
- 📋 **Playlists** — smart detection, individual video selection for download
- ⏸️ **Pause / Resume** — toggle-style download control
- 🍪 **Cookies** — three modes: disabled / .txt file / browser (Chrome, Firefox, Edge, Brave, Opera, Vivaldi)
- 🌙 **Dark Theme** — one-click switching, synchronization across pages
- 🎲 **Random Filename** — optional file anonymization
- 🌍 **Multilingual** — support for English and Russian interfaces
- 🔍 **Built-in Search** — search YouTube videos directly from the input bar (add `?` before your query)
- 🔄 **Engine Updates** — quick yt-dlp updates right from the settings page
- 🚫 **SponsorBlock** — optional skipping of sponsor segments in videos
- 📊 **Storage Statistics** — display of free disk space and download folder size
- ⚡ **Real-time Updates** — progress bar updates via WebSocket without page refresh

---

## 📸 Screenshots

<table>
  <tr>
    <td align="center">
      <img src=".assets/демонстрация%20загрузок.jpg" alt="Download Settings" width="100%"/>
      <sub><b>Download Settings</b></sub>
    </td>
    <td align="center">
      <img src=".assets/система%20куки.jpg" alt="Cookies System" width="100%"/>
      <sub><b>Cookies System</b></sub>
    </td>
  </tr>
  <tr>
    <td align="center" colspan="2">
      <img src=".assets/история.jpg" alt="Download History" width="50%"/>
      <sub><b>Download History</b></sub>
    </td>
  </tr>
</table>

---

## 🚀 Quick Start

### Requirements

- **Python** 3.11+
- **Windows** (pywebview uses WinRT)
- `yt-dlp.exe` in the project root

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/merfiDEV/ytdownloader.git
cd ytdownloader

# 2. Create virtual environment
python -m venv venv
venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Download yt-dlp.exe
# https://github.com/yt-dlp/yt-dlp/releases/latest
# Place yt-dlp.exe in the project root
```

### Run

```bash
python main.py
```

---

## 🍪 Cookies Setup

Cookies are required for downloading 18+, private, or restricted videos.

| Mode | Description |
|---|---|
| **Disabled** | Cookies are not used (default) |
| **File** | Specify path to `cookies.txt` in Netscape format |
| **Browser** | yt-dlp will extract cookies directly (Chrome, Firefox, Edge, etc.) |

> [!WARNING]
> Chrome's database file (`AppData\...\Network\Cookies`) **is not suitable** — it is an SQLite database.
> For export, use the [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) extension.

---

## 🗂️ Project Structure

```
ytdownloader/
├── main.py              # FastAPI server + pywebview desktop window
├── yt-dlp.exe           # Download engine (not in repo)
├── config.json          # User settings
├── requirements.txt     # Python dependencies
├── core/
│   ├── config.py        # Settings model, config.json read/write
│   └── downloader.py    # DownloadManager, yt-dlp processes, queue
└── ui/
    ├── index.html       # Main page (downloads, queue)
    └── settings.html    # Settings page
```

---

## ⚙️ Tech Stack

| Layer | Technology |
|---|---|
| **Engine** | [yt-dlp](https://github.com/yt-dlp/yt-dlp) — video download |
| **Backend** | [FastAPI](https://fastapi.tiangolo.com) + [uvicorn](https://www.uvicorn.org) |
| **Frontend** | HTML + Vanilla JS + [Tailwind CSS](https://tailwindcss.com) |
| **Desktop** | [pywebview](https://pywebview.flowrl.com) |
| **Real-time** | WebSocket (FastAPI native) |
| **Processes** | [psutil](https://github.com/giampaolo/psutil) — process tree management |

---

## 🔧 Configuration

Settings are stored in `config.json` and edited via UI:

```jsonc
{
  "default_quality": "1080p",   // best | 1080p | 720p | 480p | 360p
  "download_format": "mp4",     // mp4 | webm | mp3
  "save_location": "C:\\Users\\Name\\Videos\\StreamVault",
  "dark_theme": false,
  "auto_clear_queue": false,    // remove tasks after completion
  "random_filename": false,     // use random filename
  "cookies_path": "",           // path to cookies.txt (Netscape format)
  "use_browser_cookies": false, // use browser cookies
  "selected_browser": "chrome", // chrome | firefox | edge | opera | brave | vivaldi
  "enable_sponsorblock": false, // skip sponsor segments using SponsorBlock
  "language": "en"              // interface language (en / ru)
}
```

---

## 📝 License

Distributed under the **MIT** License. See [LICENSE](LICENSE) for details.

---

## 📦 Build to EXE

```bash
python build.py
```
The executable will be located in `dist/StreamVault.exe`.

---

<div align="center">

Made with ❤️ for video lovers · [merfiDEV](https://github.com/merfiDEV)

</div>
