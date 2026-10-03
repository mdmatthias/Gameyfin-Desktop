"""Drop-down for choosing the Proton build a game runs with (``PROTONPATH``)."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QWidget

from gameyfin_frontend.config import DEFAULT_PROTON
from gameyfin_frontend.services import proton_manager


class ProtonComboBox(QComboBox):
    """Lists "latest GE-Proton via umu" plus every installed Proton build.

    Each item's data is the ``PROTONPATH`` value: ``GE-Proton`` (umu keeps it
    up to date) or the absolute directory of an installed build. A value that
    is neither — a path typed by hand before this existed, or a build that has
    since been removed — is kept as an extra item so it is not lost silently.
    """

    def __init__(self, parent: QWidget | None = None, value: str | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(16)
        self.refresh(value if value is not None else DEFAULT_PROTON)

    def refresh(self, value: str | None = None) -> None:
        """Re-scan installed builds, keeping *value* (or the current one) selected."""
        if value is None:
            value = self.value()
        self.blockSignals(True)
        self.clear()
        self.addItem(f"{DEFAULT_PROTON} (latest, auto-updated by umu)", DEFAULT_PROTON)
        for build in proton_manager.list_installed():
            self.addItem(build.name, build.path)
            self.setItemData(self.count() - 1, build.path, Qt.ItemDataRole.ToolTipRole)
        self.blockSignals(False)
        self.set_value(value)

    def set_value(self, value: str | None) -> None:
        """Select the item for *value*, adding it when it is not listed."""
        value = (value or "").strip() or DEFAULT_PROTON
        index = self._index_of(value)
        if index < 0:
            label = proton_manager.display_name(value)
            self.addItem(f"{label} (not installed)" if value.startswith("/") else label, value)
            self.setItemData(self.count() - 1, value, Qt.ItemDataRole.ToolTipRole)
            index = self.count() - 1
        self.setCurrentIndex(index)

    def _index_of(self, value: str) -> int:
        normalized = value.rstrip("/")
        for i in range(self.count()):
            data = self.itemData(i) or ""
            if data == value or data.rstrip("/") == normalized:
                return i
        # Accept a bare build name for a listed build
        for i in range(self.count()):
            if self.itemText(i) == value:
                return i
        return -1

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        """Re-scan before opening, so builds added elsewhere show up."""
        self.refresh()
        super().showPopup()

    def value(self) -> str:
        """Return the selected ``PROTONPATH`` value."""
        data = self.currentData()
        return data if data else DEFAULT_PROTON
