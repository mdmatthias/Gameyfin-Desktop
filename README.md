# 🖥️ Gameyfin Desktop

A dedicated desktop client for [Gameyfin](https://github.com/gameyfin/gameyfin) that wraps the web interface in a standalone application for a more integrated experience.

---
### ✨ Features

* **🖥️ Dedicated Desktop Application:** Runs Gameyfin in its own window on both Windows and Linux, separate from your web browser.
* **🔑 Persistent SSO Login:** Supports persistent logins with SSO providers. The application saves your session data, so you only have to log in once. (*Note: This requires the "remember me" feature to be enabled in your SSO provider's settings.*)
* **⚙️ System Tray Integration:** Includes an icon in the system tray for quickly showing, hiding, or quitting the application.
* **⚙️ Integrated Settings:** Configure your Gameyfin URL, window dimensions, and more directly within the app's **Settings** tab.
* **📥 Download Manager:**
  * Manages all file downloads in a persistent "Downloads" tab.
  * Shows progress, speed, and a complete download history.
* **💽 Installer (Windows):**
  * Extracts the downloaded archive.
  * Detects `.exe` files. If multiple are found, it asks you to choose which one to launch.
  * No complex configuration required.
* **💽 Installer (Linux):**
  * Prompts for per-install environment configuration (Wayland, GameID, Store, etc.).
  * Extracts the downloaded archive.
  * Detects `.exe` files. If multiple are found, it asks you to choose which one to launch.
  * Automatically lookup the umu-id for proton fixes by codename, folder name or manual search entry.
  * Automatically creates a wineprefix and launches the installer using `umu-run`.
  * **🛠️ Wine Tools:** Quick access to `winecfg` and `winetricks` for manual prefix configuration during installation.
* **⤴️ Integrated Shortcut Management (Linux):**
  * When a game installation finishes, the app automatically detects any shortcuts created by the installer.
  * You're in Control: A dialog pops up letting you choose exactly which shortcuts (e.g., "Game" "Settings," "Uninstall") you want to add.
  * Dual-Location: Your selected shortcuts are placed on your **Desktop** and in your system's **Application Menu**.
  * Just like Windows: This gives you the simple, familiar "Create a desktop shortcut?" experience.

### 🗓️ Planned Features
* **🍷 Post-Install Configuration (Linux):** Option to edit wine prefix settings (environment variables, winetricks) after a game is installed.
* **Other ideas?:** Create a new issue/merge request and I will look into it.

---

### 🛠️ Configuration

While the application can be configured using environment variables (see below), you can now manage most settings directly within the application's **Settings** tab. Settings saved in the app persist in a `settings.json` file.

| Environment Variable   | Description                                                                      |
|:-----------------------|:---------------------------------------------------------------------------------|
| `GF_URL`               | **(Required)** The URL of your Gameyfin instance, e.g., `http://localhost:8080`. |
| `GF_START_MINIMIZED`   | Set to `1` to start the application minimized to the tray.                       |
| `GF_ICON_PATH`         | The absolute file path to a custom tray icon.                                    |
| `GF_WINDOW_WIDTH`      | Window width.                                                                    |
| `GF_WINDOW_HEIGHT`     | Window height.                                                                   |
| `GF_THEME`             | The UI theme to use (e.g., `dark_teal.xml`, `light_blue.xml`). Set to `auto` for default. |
| `PROTONPATH`           | **(Linux Only)** Path or name of the Proton version to use (default: `GE-Proton`).                |
| `GF_UMU_API_URL`       | **(Linux Only)** URL for the UMU API to search for game fixes.                                    |

---

### ▶️ How to Run

Choose your platform below to get started.

#### 🪟 Windows

**Option 1: Executable (Recommended)**
1. Download the latest `Gameyfin-Desktop-vX.X.X.exe` from the [Releases](https://github.com/mdmatthias/Gameyfin-Desktop/releases) page.
2. Run the executable.

**Option 2: Running from Source (Python)**
1. **Install Python:** Ensure you have Python installed.
2. **Install Dependencies:**
   ```powershell
   py -m pip install -r requirements.txt
   ```
3. **Run the App:**
   ```powershell
   py gameyfin_qt.py
   ```

---

#### 🐧 Linux

**Option 1: Flatpak (Recommended)**
1. **Dependencies:** Ensure `umu-launcher` is installed on your host system (required for the installer feature).
   ```bash
   # Arch
   sudo pacman -S umu-launcher

   # Fedora
   sudo dnf install umu-launcher

   ```
2. **Install:** Download the latest `Gameyfin-Desktop-vX.X.X.flatpak` from the [Releases](https://github.com/mdmatthias/Gameyfin-Desktop/releases) page.
   Install it by opening the file or running:
   ```bash
   flatpak install Gameyfin-Desktop-vX.X.X.flatpak
   ```
3. **Run:** Launch it from your application menu or run:
   ```bash
   flatpak run org.gameyfin.Gameyfin-Desktop
   ```

**Option 2: Running from Source (Python)**
1. **Dependencies:** Install Python, required libraries, and `umu-launcher`.
   *   **Arch:**
       ```bash
       sudo pacman -Syu python-pyqt6 python-pyqt6-webengine python-dotenv python-requests python-qt-material umu-launcher
       ```
   *   **Fedora:**
       ```bash
       sudo dnf install python3-pyqt6 python3-pyqt6-webengine python3-dotenv python3-requests python3-qt-material umu-launcher
       ```
   *   **Pip (General):**
       ```bash
       pip install -r requirements.txt
       ```
2. **Run the App:**
   ```bash
   python gameyfin_qt.py &
   ```

---

### 📝 Notes

#### Data Persistence
The application saves all data (settings, download history, cookies, local storage, and cache) to your system's standard application data directory. This allows your login session and configuration to persist between launches.

*   **Linux:** `~/.local/share/Gameyfin/Gameyfin/`
*   **Windows:** `%APPDATA%\Gameyfin\Gameyfin\`


#### Download Progress
The download progress bar provides an estimation. The total size is calculated based on the uncompressed files within the archive, not the size of the `.zip` file being downloaded. See [this issue](https://github.com/gameyfin/gameyfin/issues/707#issuecomment-2038166299) for more details.

#### Installer
Currently only tested with GOG games, but should work with any installer.

### AI notice
Build with the help from Gemini. If you see something that could be better or looks weird, please let me know!

### 🖼️ Screenshots

<img src="preview2.png" alt="SSO" width="800">
<img src="preview6.png" alt="Gameyfin" width="800">
<img src="preview.png" alt="Download manager" width="800">
<img src="preview3.png" alt="Wineprefix config" width="800">
<img src="preview4.png" alt="Unzip" width="800">
<img src="preview5.png" alt="Install" width="800">
<img src="preview7.png" alt="Settings" width="800">