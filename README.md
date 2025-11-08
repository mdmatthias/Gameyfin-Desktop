# 🖥️ Gameyfin Desktop

A dedicated desktop client for [Gameyfin](https://github.com/gameyfin/gameyfin) that wraps the web interface in a standalone application for a more integrated experience.

---
### ✨ Features

* **🖥️ Dedicated Desktop Application:** Runs Gameyfin in its own window, separate from your web browser.
* **🔑 Persistent SSO Login:** Supports persistent logins with SSO providers. The application saves your session data, so you only have to log in once. (*Note: This requires the "remember me" feature to be enabled in your SSO provider's settings.*)
* **⚙️ System Tray Integration:** Includes an icon in the system tray for quickly showing, hiding, or quitting the application.
* **📥 Download Manager & Installer (Linux):**
    * Manages all file downloads in a persistent "Downloads" tab.
    * Shows progress, speed, and a complete download history.
    * **Automates `umu-run` for installing games:**
        * Prompts for per-install environment configuration (Wayland, GameID, Store, etc.).
        * Extracts the archive to its own folder.
        * Detects `.exe` files. If multiple are found, it asks you to choose which one to launch.
        * Automatically creates a wineprefix and launches the installer using `umu-run`.

---
### 🗓️ Planned Features
* **Autodetect GameID:** To apply protonfixes without having to look up the GameID.
* **Desktop shortcuts:** Automatically add desktop shortcut (and/or play button in the download manager) to launch the game with the same umu-launcher config as chosen during the installation.
* **Protonfixes and Winecfg button:** To manually configure your Wineprefix if needed.
* **Other ideas?:** Create a new issue/merge request and I will look into it.

---
### 📋 Requirements

**Python Packages:**
* Python
* PyQt6
* PyQt6-WebEngine
* dotenv
* requests

You can install the required packages using one of the methods below.

#### Pip
```bash
pip install -r requirements.txt
```

#### Pacman
```bash
pacman -Syu python-pyqt6 python-pyqt6-webengine python-dotenv python-requests
```
---
**External Dependencies (for Installer):**
* **`umu-launcher`:** The installer feature on Linux **requires** `umu-launcher` to be installed.
#### Pacman
```bash
pacman -Syu umu-launcher
```
---
### 🛠️ Configuration

The application is configured using environment variables. You can either pass them directly via the command line or create a `.env` file in the same directory as the script (see `.env.example`).

| Environment Variable   | Description                                                                                 |
|:-----------------------|:--------------------------------------------------------------------------------------------|
| `GF_URL`               | **(Required)** The URL of your Gameyfin instance, e.g., `http://localhost:8080`.            |
| `GF_SSO_PROVIDER_HOST` | The host of your SSO provider, e.g., `sso.host.com`. **Required if using SSO.**             |
| `GF_START_MINIMIZED`   | Set to `1` to start the application minimized to the tray. Useful for autostarting on boot. |
| `GF_ICON_PATH`         | The absolute file path to a custom tray icon, e.g., `/path/to/icon.png`.                    |
| `GF_WINDOW_WIDTH`      | Window width.                                                                               |
| `GF_WINDOW_HEIGHT`     | Window height.                                                                              |

---
### ▶️ How to Run

* **Basic command:**
    ```
    GF_URL=http://192.168.1.100:8080 python gameyfin_qt.py &
    ```

* **With SSO enabled:**
    ```
    GF_URL=http://192.168.1.100:8080 GF_SSO_PROVIDER_HOST=sso.host.com python gameyfin_qt.py &
    ```

* **Using a `.env` file (Recommended):**
    Create a `.env` file in the root directory and add your variables there. Then, simply run:
    ```
    python gameyfin_qt.py &
    ```
---
### 📝 Notes

#### Data Persistence
The application saves all browser data (cookies, local storage, cache, etc.) to a profile stored in a `.gameyfin-app-data` folder within the application's directory. This allows your login session to persist between launches. To clear your session and all stored data, simply delete this folder.

#### Download Progress
The download progress bar provides an estimation. The total size is calculated based on the uncompressed files within the archive, not the size of the `.zip` file being downloaded. See [this issue](https://github.com/gameyfin/gameyfin/issues/707#issuecomment-2038166299) for more details.

#### Installer
Currently only tested with GOG games, but should work with any installer.

### 🖼️ Screenshots

<img src="preview2.png" alt="SSO" width="800">
<img src="preview6.png" alt="Gameyfin" width="800">
<img src="preview.png" alt="Download manager" width="800">
<img src="preview3.png" alt="Wineprefix config" width="800">
<img src="preview4.png" alt="Unzip" width="800">
<img src="preview5.png" alt="Install" width="800">