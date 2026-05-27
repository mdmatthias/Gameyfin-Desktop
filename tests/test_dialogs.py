import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def mock_umu_database():
    db = MagicMock()
    db.search_by_partial_title.return_value = []
    return db


@pytest.fixture()
def mock_settings(monkeypatch):
    from gameyfin_frontend import settings as settings_module

    class MockSettings:
        def __init__(self):
            self._data = {
                "PROTONPATH": "GE-Proton",
                "GF_UMU_DB_STORES": ["none", "gog", "steam"],
            }

        def get(self, key, fallback=None):
            return self._data.get(key, fallback)

    mock = MockSettings()
    monkeypatch.setattr(settings_module, "settings_manager", mock)
    return mock


class TestInstallConfigDialog:
    def test_dialog_initializes(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        dialog = InstallConfigDialog(umu_database=mock_umu_database)
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Installation Configuration"

    def test_default_values(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        dialog = InstallConfigDialog(umu_database=mock_umu_database)
        qtbot.addWidget(dialog)
        assert dialog.gameid_input.text() == "umu-default"
        assert dialog.protonpath_input.text() == "GE-Proton"

    def test_initial_config_populates_fields(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        initial = {
            "PROTON_ENABLE_WAYLAND": "1",
            "MANGOHUD": "1",
            "GAMEID": "UMU-TEST",
            "STORE": "steam",
            "PROTONPATH": "Custom-Proton",
        }
        dialog = InstallConfigDialog(
            umu_database=mock_umu_database,
            default_game_id="umu-default",
            default_store="none",
            initial_config=initial,
        )
        qtbot.addWidget(dialog)
        assert dialog.wayland_checkbox.isChecked()
        assert dialog.mangohud_checkbox.isChecked()
        assert dialog.gameid_input.text() == "UMU-TEST"
        assert dialog.store_combo.currentText() == "steam"
        assert dialog.protonpath_input.text() == "Custom-Proton"

    def test_get_config_returns_dict(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        dialog = InstallConfigDialog(umu_database=mock_umu_database)
        qtbot.addWidget(dialog)
        config = dialog.get_config()
        assert isinstance(config, dict)
        assert "PROTON_ENABLE_WAYLAND" in config
        assert "MANGOHUD" in config
        assert "GAMEID" in config
        assert "PROTONPATH" in config
        # STORE is only included when store is not "none"
        assert config.get("STORE") == "" or config.get("STORE") == "none" or "STORE" not in config

    def test_get_config_with_extra_vars(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        dialog = InstallConfigDialog(umu_database=mock_umu_database)
        qtbot.addWidget(dialog)
        dialog.extra_vars_input.setPlainText("CUSTOM_VAR=value123\nANOTHER=foo")
        config = dialog.get_config()
        assert config["CUSTOM_VAR"] == "value123"
        assert config["ANOTHER"] == "foo"

    def test_get_config_with_store_none(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        dialog = InstallConfigDialog(umu_database=mock_umu_database)
        qtbot.addWidget(dialog)
        config = dialog.get_config()
        # "none" store should not be included
        assert "STORE" not in config or config.get("STORE") == ""

    def test_get_config_with_non_none_store(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        dialog = InstallConfigDialog(umu_database=mock_umu_database)
        qtbot.addWidget(dialog)
        dialog.store_combo.setCurrentText("steam")
        config = dialog.get_config()
        assert config["STORE"] == "steam"


class TestSelectLauncherDialog:
    def test_dialog_initializes(self, qtbot):
        from gameyfin_frontend.dialogs import SelectLauncherDialog
        dialog = SelectLauncherDialog("/target/dir", ["/target/dir/game.exe", "/target/dir/launcher.exe"])
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Select Launcher"

    def test_lists_executables(self, qtbot):
        from gameyfin_frontend.dialogs import SelectLauncherDialog
        dialog = SelectLauncherDialog("/target/dir", ["/target/dir/game.exe", "/target/dir/launcher.exe"])
        qtbot.addWidget(dialog)
        assert dialog.list_widget.count() == 2

    def test_ok_button_disabled_initially(self, qtbot):
        from gameyfin_frontend.dialogs import SelectLauncherDialog
        dialog = SelectLauncherDialog("/target/dir", ["/target/dir/game.exe"])
        qtbot.addWidget(dialog)
        assert dialog.ok_button.isEnabled() is False

    def test_ok_button_enabled_on_selection(self, qtbot):
        from gameyfin_frontend.dialogs import SelectLauncherDialog
        dialog = SelectLauncherDialog("/target/dir", ["/target/dir/game.exe"])
        qtbot.addWidget(dialog)
        dialog.list_widget.setCurrentRow(0)
        assert dialog.ok_button.isEnabled() is True

    def test_get_selected_launcher(self, qtbot):
        from gameyfin_frontend.dialogs import SelectLauncherDialog
        paths = ["/target/dir/a.exe", "/target/dir/b.exe"]
        dialog = SelectLauncherDialog("/target/dir", paths)
        qtbot.addWidget(dialog)
        dialog.list_widget.setCurrentRow(1)
        result = dialog.get_selected_launcher()
        assert result == "/target/dir/b.exe"

    def test_get_selected_launcher_no_selection(self, qtbot):
        from gameyfin_frontend.dialogs import SelectLauncherDialog
        dialog = SelectLauncherDialog("/target/dir", ["/target/dir/game.exe"])
        qtbot.addWidget(dialog)
        result = dialog.get_selected_launcher()
        assert result is None


class TestSelectUmuIdDialog:
    def test_dialog_initializes(self, qtbot):
        from gameyfin_frontend.dialogs import SelectUmuIdDialog
        results = [
            {"umu_id": "UMU-1", "title": "Game A", "store": "steam"},
            {"umu_id": "UMU-2", "title": "Game B", "store": "gog"},
        ]
        dialog = SelectUmuIdDialog(results)
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Select Game Entry"

    def test_lists_results(self, qtbot):
        from gameyfin_frontend.dialogs import SelectUmuIdDialog
        results = [
            {"umu_id": "UMU-1", "title": "Game A", "store": "steam"},
        ]
        dialog = SelectUmuIdDialog(results)
        qtbot.addWidget(dialog)
        assert dialog.list_widget.count() == 1

    def test_display_text_includes_store(self, qtbot):
        from gameyfin_frontend.dialogs import SelectUmuIdDialog
        results = [{"umu_id": "UMU-1", "title": "Test Game", "store": "steam"}]
        dialog = SelectUmuIdDialog(results)
        qtbot.addWidget(dialog)
        item_text = dialog.list_widget.item(0).text()
        assert "Test Game" in item_text
        assert "steam" in item_text

    def test_get_selected_entry(self, qtbot):
        from gameyfin_frontend.dialogs import SelectUmuIdDialog
        results = [
            {"umu_id": "UMU-1", "title": "Game A", "store": "steam"},
            {"umu_id": "UMU-2", "title": "Game B", "store": "gog"},
        ]
        dialog = SelectUmuIdDialog(results)
        qtbot.addWidget(dialog)
        dialog.list_widget.setCurrentRow(1)
        entry = dialog.get_selected_entry()
        assert entry["umu_id"] == "UMU-2"
        assert entry["store"] == "gog"

    def test_get_selected_entry_no_selection(self, qtbot):
        from gameyfin_frontend.dialogs import SelectUmuIdDialog
        results = [{"umu_id": "UMU-1", "title": "Game A", "store": "steam"}]
        dialog = SelectUmuIdDialog(results)
        qtbot.addWidget(dialog)
        entry = dialog.get_selected_entry()
        assert entry is None


class TestSelectShortcutsDialog:
    def test_dialog_initializes(self, qtbot, tmp_path):
        from gameyfin_frontend.dialogs import SelectShortcutsDialog
        desktop_files = [str(tmp_path / "game1.desktop"), str(tmp_path / "game2.desktop")]
        dialog = SelectShortcutsDialog(desktop_files)
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Manage Shortcuts"

    def test_has_desktop_and_apps_sections(self, qtbot, tmp_path):
        from gameyfin_frontend.dialogs import SelectShortcutsDialog
        desktop_files = [str(tmp_path / "game.desktop")]
        dialog = SelectShortcutsDialog(desktop_files)
        qtbot.addWidget(dialog)
        # Should have checkboxes for both Desktop and Apps sections
        assert len(dialog.desktop_checkboxes) == 1
        assert len(dialog.apps_checkboxes) == 1

    def test_all_checked_by_default(self, qtbot, tmp_path):
        from gameyfin_frontend.dialogs import SelectShortcutsDialog
        desktop_files = [str(tmp_path / "game.desktop")]
        dialog = SelectShortcutsDialog(desktop_files)
        qtbot.addWidget(dialog)
        for cb, _ in dialog.desktop_checkboxes + dialog.apps_checkboxes:
            assert cb.isChecked()

    def test_existing_selections_respected(self, qtbot, tmp_path):
        from gameyfin_frontend.dialogs import SelectShortcutsDialog
        desktop_files = [str(tmp_path / "game.desktop")]
        existing_desktop = []  # Not on desktop
        existing_apps = [os.path.basename(desktop_files[0])]  # In apps
        dialog = SelectShortcutsDialog(
            desktop_files,
            existing_desktop=existing_desktop,
            existing_apps=existing_apps,
        )
        qtbot.addWidget(dialog)
        # Desktop checkbox should be unchecked
        assert not dialog.desktop_checkboxes[0][0].isChecked()
        # Apps checkbox should be checked
        assert dialog.apps_checkboxes[0][0].isChecked()

    def test_select_all(self, qtbot, tmp_path):
        from gameyfin_frontend.dialogs import SelectShortcutsDialog
        desktop_files = [str(tmp_path / "game.desktop")]
        dialog = SelectShortcutsDialog(desktop_files)
        qtbot.addWidget(dialog)
        # Uncheck all first
        for cb, _ in dialog.desktop_checkboxes + dialog.apps_checkboxes:
            cb.setChecked(False)
        dialog.select_all()
        for cb, _ in dialog.desktop_checkboxes + dialog.apps_checkboxes:
            assert cb.isChecked()

    def test_deselect_all(self, qtbot, tmp_path):
        from gameyfin_frontend.dialogs import SelectShortcutsDialog
        desktop_files = [str(tmp_path / "game.desktop")]
        dialog = SelectShortcutsDialog(desktop_files)
        qtbot.addWidget(dialog)
        dialog.deselect_all()
        for cb, _ in dialog.desktop_checkboxes + dialog.apps_checkboxes:
            assert not cb.isChecked()

    def test_get_selected_files(self, qtbot, tmp_path):
        from gameyfin_frontend.dialogs import SelectShortcutsDialog
        f1 = str(tmp_path / "game1.desktop")
        f2 = str(tmp_path / "game2.desktop")
        desktop_files = [f1, f2]
        dialog = SelectShortcutsDialog(desktop_files)
        qtbot.addWidget(dialog)
        # Uncheck game2 from desktop
        for cb, fp in dialog.desktop_checkboxes:
            if fp == f2:
                cb.setChecked(False)
        desktop_selected, apps_selected = dialog.get_selected_files()
        assert f1 in desktop_selected
        assert f2 not in desktop_selected

    def test_parse_desktop_name(self):
        from gameyfin_frontend.dialogs import SelectShortcutsDialog
        # Should return basename if file is not a valid desktop file
        result = SelectShortcutsDialog.parse_desktop_name("/some/path/file.txt")
        assert result == "file.txt"
