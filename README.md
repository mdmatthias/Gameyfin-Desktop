# Gameyfin-PyQT
To be used together with Gameyfin https://github.com/gameyfin/gameyfin

This app loads Gameyfin in a QT app instead of a browser tab.
Downloads will start in the app itself. External links will open in browser.


![preview.png](preview.png)
# Requirements
```
Python >= 3.9
PyQT6 >= 6.9.1
PyQt6-WebEngine >= 6.9.0
```

# Environment variables
To connect to your Gameyfin server, set this env var with your Gameyfin url:

```
GF_URL=http://localhost:8080
```

Other env vars
```
GF_START_MINIMIZED=1 # Useful if you autostart the app on boot
GF_ICON_PATH=/some/path/to/other/tray-icon.png
```

# Running the app

```commandline
GF_URL=http://192.168.1.100:8080 python gameyfin_qt.py &
```
If you want to start it minimized:
```commandline
GF_START_MINIMIZED=1 GF_URL=http://192.168.1.100:8080 python gameyfin_qt.py &
```
