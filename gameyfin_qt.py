import os
import time
import sys
from os import getenv

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QSystemTrayIcon, QMenu,
    QFileDialog, QProgressDialog, QMessageBox
)
from PyQt6.QtGui import QIcon, QAction, QDesktopServices
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineScript, QWebEngineDownloadRequest


class UrlCatchingPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, _type, _is_main_frame):
        QDesktopServices.openUrl(url)
        self.deleteLater()
        return False


class CustomWebEnginePage(QWebEnginePage):
    def __init__(self, base_url, parent=None):
        super().__init__(parent)
        self.base_url = base_url

    def createWindow(self, _type):
        return UrlCatchingPage(self)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if is_main_frame:
            requested_host = url.host()
            base_host = self.base_url.host()

            if requested_host != base_host and requested_host != "":
                QDesktopServices.openUrl(url)
                return False

        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class GameyfinWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.expected_total = 0
        self.setWindowTitle("Gameyfin")
        self.setGeometry(0, 0, 1420, 920)

        self.browser = QWebEngineView()

        base_url = QUrl(getenv("GF_URL", "http://10.69.69.159:8090"))
        self.custom_page = CustomWebEnginePage(base_url, self.browser)
        self.browser.setPage(self.custom_page)

        self.browser.setUrl(base_url)
        self.setCentralWidget(self.browser)

        script = QWebEngineScript()
        script.setSourceCode("""
            document.documentElement.style.overflowX = 'hidden';
            document.body.style.overflowX = 'hidden';
        """)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        self.browser.page().scripts().insert(script)

        self.browser.page().profile().downloadRequested.connect(self.on_download_requested)

    def on_download_requested(self, download: QWebEngineDownloadRequest):
        suggested_path = os.path.join(
            os.path.expanduser("~/Downloads"),
            os.path.basename(download.downloadFileName())
        )
        path, _ = QFileDialog.getSaveFileName(self, "Save File As", suggested_path)
        if not path:
            download.cancel()
            return

        download.setDownloadFileName(os.path.basename(path))
        download.accept()

        progress = QProgressDialog("Downloading...", "Cancel", 0, 100, self)
        progress.setWindowTitle("Download Progress")
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)

        def parse_size(text: str) -> int:
            try:
                num, unit = text.split()
                num = float(num.replace(",", "."))
                multipliers = {
                    "B": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4
                }
                return int(num * multipliers.get(unit, 1))
            except Exception:
                return 0

        def fetch_total_size():
            js = """(function() {
                let el = document.querySelector('button .text-xs');
                return el ? el.innerText : "";
            })();"""
            self.browser.page().runJavaScript(js, handle_js_result)

        def handle_js_result(result):
            nonlocal total_size
            total_size = parse_size(result)
            update_progress()

        total_size = 0
        fetch_total_size()

        last_time = time.time()
        last_bytes = 0

        def format_size(nbytes: int) -> str:
            if nbytes >= 1024**3: return f"{nbytes / (1024**3):.2f} GB"
            if nbytes >= 1024**2: return f"{nbytes / (1024**2):.2f} MB"
            return f"{nbytes / 1024:.2f} KB"

        def update_progress():
            nonlocal last_time, last_bytes
            received = download.receivedBytes()
            now = time.time()
            elapsed = now - last_time
            speed_str = ""
            if elapsed > 0.5:
                speed = (received - last_bytes) / elapsed
                speed_str = f"{speed / (1024**2):.2f} MB/s" if speed >= 1024**2 else f"{speed / 1024:.2f} KB/s"
                last_time, last_bytes = now, received

            if total_size > 0:
                percent = int((received / total_size) * 100)
                progress.setValue(min(percent, 100))
                progress.setLabelText(f"Downloading… {format_size(received)} / {format_size(total_size)}  ({speed_str})")
            else:
                progress.setRange(0, 0)
                progress.setLabelText(f"Downloading… {format_size(received)} / ???  ({speed_str})")

        download.receivedBytesChanged.connect(update_progress)
        download.totalBytesChanged.connect(update_progress)
        progress.canceled.connect(download.cancel)
        download.stateChanged.connect(lambda state: self.check_download_state(progress, path, state))

    @staticmethod
    def open_folder(path: str):
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    @staticmethod
    def open_file(path: str):
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def show_download_finished_dialog(self, path: str):
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.setWindowTitle("Download Complete")
        msg_box.setText(f"Finished downloading:\n{os.path.basename(path)}")
        msg_box.setInformativeText("What would you like to do?")
        open_file_button = msg_box.addButton("Open File", QMessageBox.ButtonRole.ActionRole)
        open_folder_button = msg_box.addButton("Open Folder", QMessageBox.ButtonRole.ActionRole)
        msg_box.addButton(QMessageBox.StandardButton.Close)
        msg_box.exec()
        if msg_box.clickedButton() == open_file_button: self.open_file(path)
        elif msg_box.clickedButton() == open_folder_button: self.open_folder(path)

    def check_download_state(self, progress: QProgressDialog, path: str, state):
        if state == QWebEngineDownloadRequest.DownloadState.DownloadCompleted:
            progress.close()
            self.show_download_finished_dialog(path)
        elif state in (QWebEngineDownloadRequest.DownloadState.DownloadCancelled, QWebEngineDownloadRequest.DownloadState.DownloadInterrupted):
            progress.close()


class GameyfinTray:
    def __init__(self, app, window):
        self.app = app
        self.window = window
        self.tray = QSystemTrayIcon()
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
        self.tray.setIcon(QIcon(getenv("GF_ICON_PATH", icon_path)))
        self.menu = QMenu()
        self.show_action = QAction("Show")
        self.quit_action = QAction("Quit")
        self.menu.addAction(self.show_action)
        self.menu.addAction(self.quit_action)
        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("Gameyfin")
        self.show_action.triggered.connect(self.window.show)
        self.quit_action.triggered.connect(self.quit_app)
        self.tray.activated.connect(self.icon_clicked)
        self.tray.show()

    def icon_clicked(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.window.isVisible(): self.window.hide()
            else: self.window.show()

    def quit_app(self):
        self.tray.hide()
        self.app.quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = GameyfinWindow()
    tray_app = GameyfinTray(app, window)
    if int(getenv("GF_START_MINIMIZED", 0)) == 0:
        window.show()
    sys.exit(app.exec())