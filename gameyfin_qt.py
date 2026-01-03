import sys
from os import getenv

from PyQt6.QtWidgets import QApplication
from gameyfin_frontend import GameyfinTray
from gameyfin_frontend import GameyfinWindow
from dotenv import load_dotenv

from gameyfin_frontend.umu_database import UmuDatabase

from gameyfin_frontend.settings import settings_manager



load_dotenv()



if __name__ == "__main__":

    app = QApplication(sys.argv)

    umu_database = UmuDatabase()

    app.setApplicationName("Gameyfin")

    app.setOrganizationName("Gameyfin")

    window = GameyfinWindow(umu_database)

    tray_app = GameyfinTray(app, window)

    if int(settings_manager.get("GF_START_MINIMIZED", 0)) == 0:

        window.show()

    sys.exit(app.exec())
