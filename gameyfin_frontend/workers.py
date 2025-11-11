import os
import zipfile

from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, QThread


class UnzipWorker(QObject):
    """
    Runs the zip extraction in a separate thread to avoid freezing the UI.
    """
    progress = pyqtSignal(int)
    current_file = pyqtSignal(str)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, zip_path: str, target_dir: str):
        super().__init__()
        self.zip_path = zip_path
        self.target_dir = target_dir
        self._is_running = True

    @pyqtSlot()
    def run(self):
        """Starts the extraction process."""
        try:
            with zipfile.ZipFile(self.zip_path, 'r') as zip_ref:
                file_list = zip_ref.infolist()
                total_files = len(file_list)

                if total_files == 0:
                    self.finished.emit()
                    return

                for i, member in enumerate(file_list):
                    if not self._is_running:
                        self.error.emit("Extraction cancelled by user.")
                        return

                    zip_ref.extract(member, path=self.target_dir)

                    percentage = int(((i + 1) / total_files) * 100)
                    self.progress.emit(percentage)
                    self.current_file.emit(f"Extracting: {member.filename}")

                self.finished.emit()

        except Exception as e:
            self.error.emit(str(e))

    def stop(self):
        """Flags the worker to stop."""
        self._is_running = False

class ProcessMonitorWorker(QThread):
    """Monitors a process by its PID and emits when it's finished."""
    finished = pyqtSignal()

    def __init__(self, pid, parent=None):
        super().__init__(parent)
        self.pid = pid
        self._running = True

    def run(self):
        if not self.pid > 0:
            print(f"ProcessMonitor: Invalid PID ({self.pid}), stopping.")
            return

        print(f"ProcessMonitor: Monitoring PID {self.pid}")
        self._running = True
        while self._running:
            try:
                os.kill(self.pid, 0)
            except OSError:
                print(f"ProcessMonitor: PID {self.pid} finished.")
                self._running = False
                self.finished.emit()
                break
            else:
                if not self._running:
                    break
                self.msleep(1000)

        print(f"ProcessMonitor: Stopping monitor for {self.pid}")

    def stop(self):
        self._running = False