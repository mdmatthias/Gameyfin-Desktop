# Gameyfin-PyQT
To be used together with Gameyfin https://github.com/gameyfin/gameyfin

This app loads Gameyfin in a QT app instead of a browser tab.
Downloads will start in the app itself. External links will open in browser.

Note: the download progress is an estimation, the size is based on the actual files and not on the zip file that's downloading. 
See https://github.com/gameyfin/gameyfin/issues/707#issuecomment-3289234269

![preview.png](preview.png)
# Requirements
```
Python >= 3.9
PyQT6 >= 6.9.1
PyQt6-WebEngine >= 6.9.0
dotenv >= 0.9.9
```

# Environment variables
| Environment Variable | Description                                                                                                              |
| :--- |:-------------------------------------------------------------------------------------------------------------------------|
| `GF_URL` | The URL of the application, for example: `http://localhost:8080`.                                                        |
| `GF_SSO_PROVIDER_HOST` | **Required for SSO.** Sets the SSO provider host, for example: `sso.host.com` |
| `GF_START_MINIMIZED` | Set to `1` to start the application minimized. This is useful if you autostart the app on boot.                          |
| `GF_ICON_PATH` | The file path to a custom tray icon, for example: `/some/path/to/other/tray-icon.png`.                                   |

# Persistent browser storage
The application saves all browsing data from Gameyfin (cookies, local storage, cache, etc.) to a QWebEngineProfile stored in a .gameyfin-app-data folder next to the script. 
This allows your login session to persist between launches, just like a regular web browser.
If you need to clear it for any reason, you can just delete the .gameyfin-app-data folder.

# Running the app

```commandline
GF_URL=http://192.168.1.100:8080 python gameyfin_qt.py &
```
If SSO is enabled:
```commandline
GF_URL=http://192.168.1.100:8080 GF_SSO_PROVIDER_HOST=sso.host.com python gameyfin_qt.py &
```
If you want to start it minimized:
```commandline
GF_START_MINIMIZED=1 GF_URL=http://192.168.1.100:8080 python gameyfin_qt.py &
```
You can also create a .env file (see .env.example) next to the script and just run:
```commandline
python gameyfin_qt.py &
```