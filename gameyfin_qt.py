import sys
from os import getenv

from PyQt6.QtWidgets import QApplication
from gameyfin_frontend import GameyfinTray
from gameyfin_frontend import GameyfinWindow
from dotenv import load_dotenv
load_dotenv()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    window = GameyfinWindow()
    tray_app = GameyfinTray(app, window)
    if int(getenv("GF_START_MINIMIZED", 0)) == 0:
        window.show()
    sys.exit(app.exec())