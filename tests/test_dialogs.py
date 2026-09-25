import os
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLineEdit


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

    def test_get_config_with_game_args(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        dialog = InstallConfigDialog(umu_database=mock_umu_database)
        qtbot.addWidget(dialog)
        dialog.game_args_input.setText("-windowed -memory=2048")
        config = dialog.get_config()
        assert config["GAME_ARGS"] == "-windowed -memory=2048"

    def test_get_config_game_args_empty(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        dialog = InstallConfigDialog(umu_database=mock_umu_database)
        qtbot.addWidget(dialog)
        config = dialog.get_config()
        assert "GAME_ARGS" not in config

    def test_initial_config_populates_game_args(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        initial = {
            "GAMEID": "UMU-TEST",
            "STORE": "gog",
            "GAME_ARGS": "-noaudio",
        }
        dialog = InstallConfigDialog(
            umu_database=mock_umu_database,
            default_game_id="umu-default",
            default_store="none",
            initial_config=initial,
        )
        qtbot.addWidget(dialog)
        assert dialog.game_args_input.text() == "-noaudio"

    def test_per_script_game_args_dict_format(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        initial = {
            "GAMEID": "UMU-TEST",
            "GAME_ARGS": {"game.sh": "-windowed", "shortcut.sh": "-vulkan"},
        }
        scripts = ["/path/to/game.sh", "/path/to/shortcut.sh"]
        dialog = InstallConfigDialog(
            umu_database=mock_umu_database,
            default_game_id="UMU-TEST",
            default_store="none",
            initial_config=initial,
            scripts=scripts,
        )
        qtbot.addWidget(dialog)

        # Script selector has 2 scripts (no "All scripts" option)
        assert dialog.script_selector.count() == 2
        assert dialog.script_selector.itemText(0) == "game.sh"
        assert dialog.script_selector.itemText(1) == "shortcut.sh"

        # Default selection is first script
        assert dialog.script_selector.currentIndex() == 0
        assert dialog.game_args_input.text() == "-windowed"

        # Select second script and verify args
        dialog.script_selector.setCurrentIndex(1)
        assert dialog.game_args_input.text() == "-vulkan"

        # Modify args and save
        dialog.game_args_input.setText("-newargs")
        config = dialog.get_config()
        # Each script has its full config
        assert "game.sh" in config
        assert "shortcut.sh" in config
        assert config["shortcut.sh"]["GAME_ARGS"] == "-newargs"
        assert config["game.sh"]["GAME_ARGS"] == "-windowed"  # unchanged

    def test_per_script_all_config_fields(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        initial = {
            "GAMEID": "UMU-GAME",
            "STORE": "steam",
            "PROTONPATH": "Proton 9",
            "PROTON_ENABLE_WAYLAND": "1",
            "MANGOHUD": "0",
            "PROTON_USE_WOW64": "1",
            "EXTRA_KEY": "extra_val",
            "GAME_ARGS": {"game.sh": "-windowed", "shortcut.sh": "-vulkan"},
        }
        scripts = ["/path/to/game.sh", "/path/to/shortcut.sh"]
        dialog = InstallConfigDialog(
            umu_database=mock_umu_database,
            default_game_id="UMU-TEST",
            default_store="none",
            initial_config=initial,
            scripts=scripts,
        )
        qtbot.addWidget(dialog)

        # Default selection is first script with its full config
        assert dialog.script_selector.currentIndex() == 0
        assert dialog.gameid_input.text() == "UMU-GAME"
        assert dialog.store_combo.currentText() == "steam"
        assert dialog.protonpath_input.text() == "Proton 9"
        assert dialog.wayland_checkbox.isChecked()
        assert not dialog.mangohud_checkbox.isChecked()
        assert dialog.wow64_checkbox.isChecked()
        assert dialog.extra_vars_input.toPlainText() == "EXTRA_KEY=extra_val"

        # Select second script - should show its specific values
        dialog.script_selector.setCurrentIndex(1)
        assert dialog.game_args_input.text() == "-vulkan"

        # Modify this script's GAMEID
        dialog.gameid_input.setText("UMU-SHORTCUT")
        config = dialog.get_config()

        # Each script has its full config (no ALL_SCRIPTS baseline)
        assert "game.sh" in config
        assert "shortcut.sh" in config
        assert config["game.sh"]["GAMEID"] == "UMU-GAME"
        assert config["shortcut.sh"]["GAMEID"] == "UMU-SHORTCUT"
        assert config["game.sh"]["GAME_ARGS"] == "-windowed"
        assert config["shortcut.sh"]["GAME_ARGS"] == "-vulkan"

    def test_legacy_game_args_migrated_to_dict(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        initial = {
            "GAMEID": "UMU-TEST",
            "GAME_ARGS": "-legacy-args",
        }
        scripts = ["/path/to/game.sh"]
        dialog = InstallConfigDialog(
            umu_database=mock_umu_database,
            default_game_id="UMU-TEST",
            default_store="none",
            initial_config=initial,
            scripts=scripts,
        )
        qtbot.addWidget(dialog)

        # Legacy args should be under the first script's basename
        assert dialog._per_script_config["game.sh"]["GAME_ARGS"] == "-legacy-args"

        # Default selection is first script
        assert dialog.script_selector.currentIndex() == 0
        assert dialog.game_args_input.text() == "-legacy-args"

        # Save - should produce new per-script format with each script's full config
        config = dialog.get_config()
        assert "game.sh" in config
        assert config["game.sh"]["GAME_ARGS"] == "-legacy-args"

    def test_per_script_format_round_trip_preserves_isolation(self, qtbot, mock_umu_database):
        """Verify that reloading a saved per-script config keeps each script's
        EXTRA_VARS isolated — selecting one script must not show another's vars.

        This is the format that get_config() produces when scripts are present:
        { "script.sh": { ...full config... }, ... }  (no ALL_SCRIPTS key).
        """
        from gameyfin_frontend.dialogs import InstallConfigDialog

        # Simulate a saved config (the exact format get_config() produces)
        initial = {
            "Battlenet.sh": {
                "PROTON_ENABLE_WAYLAND": "1",
                "MANGOHUD": "0",
                "PROTON_USE_WOW64": "0",
                "GAMEID": "umu-battle",
                "STORE": "battlenet",
                "PROTONPATH": "GE-Proton",
                "EXTRA_VARS": "BATTLE_VAR=hello",
                "GAME_ARGS": "",
            },
            "World of Warcraft Forever.sh": {
                "PROTON_ENABLE_WAYLAND": "0",
                "MANGOHUD": "1",
                "PROTON_USE_WOW64": "0",
                "GAMEID": "umu-wow",
                "STORE": "none",
                "PROTONPATH": "GE-Proton",
                "EXTRA_VARS": "WOW_VAR=world\nANOTHER=var",
                "GAME_ARGS": "-windowed",
            },
        }
        scripts = [
            "/path/to/Battlenet.sh",
            "/path/to/World of Warcraft Forever.sh",
        ]
        dialog = InstallConfigDialog(
            umu_database=mock_umu_database,
            default_game_id="umu-default",
            default_store="none",
            initial_config=initial,
            scripts=scripts,
        )
        qtbot.addWidget(dialog)

        # First script (Battlenet.sh) should show its own vars only
        assert dialog.script_selector.currentIndex() == 0
        assert dialog.extra_vars_input.toPlainText() == "BATTLE_VAR=hello"
        assert dialog.gameid_input.text() == "umu-battle"
        assert dialog.store_combo.currentText() == "battlenet"

        # Second script (WoW) should show only its own vars
        dialog.script_selector.setCurrentIndex(1)
        assert dialog.extra_vars_input.toPlainText() == "WOW_VAR=world\nANOTHER=var"
        assert dialog.gameid_input.text() == "umu-wow"
        assert dialog.game_args_input.text() == "-windowed"

        # Switch back to first — still only its own vars
        dialog.script_selector.setCurrentIndex(0)
        assert dialog.extra_vars_input.toPlainText() == "BATTLE_VAR=hello"
        assert dialog.mangohud_checkbox.isChecked() is False

        # Modify first script's vars and save — round trip should preserve isolation
        dialog.gameid_input.setText("umu-battle-updated")
        dialog.extra_vars_input.setPlainText("BATTLE_VAR=updated\nNEW_BATTLE=yes")
        config = dialog.get_config()

        # Reload with the saved config
        dialog2 = InstallConfigDialog(
            umu_database=mock_umu_database,
            default_game_id="umu-default",
            default_store="none",
            initial_config=config,
            scripts=scripts,
        )
        qtbot.addWidget(dialog2)

        # First script should show updated vars only
        dialog2.script_selector.setCurrentIndex(0)
        expected = "BATTLE_VAR=updated\nNEW_BATTLE=yes"
        assert dialog2.extra_vars_input.toPlainText() == expected
        assert dialog2.gameid_input.text() == "umu-battle-updated"

        # Second script should still show its original vars (unchanged)
        dialog2.script_selector.setCurrentIndex(1)
        assert dialog2.extra_vars_input.toPlainText() == "WOW_VAR=world\nANOTHER=var"


    def test_script_without_entry_gets_shared_settings(self, qtbot, mock_umu_database):
        """A script added after the config was saved (e.g. via "Run exe on
        prefix") shows the game's protonfix and Proton path, not blank fields."""
        from gameyfin_frontend.dialogs import InstallConfigDialog

        initial = {
            "Game.sh": {"GAMEID": "umu-367500", "STORE": "steam", "PROTONPATH": "Proton-Custom",
                        "EXTRA_VARS": "", "GAME_ARGS": "-game-only"},
        }
        dialog = InstallConfigDialog(
            umu_database=mock_umu_database,
            initial_config=initial,
            scripts=["/p/Game.sh", "/p/tool.sh"],
        )
        qtbot.addWidget(dialog)

        dialog.script_selector.setCurrentIndex(1)
        assert dialog.gameid_input.text() == "umu-367500"
        assert dialog.protonpath_input.text() == "Proton-Custom"
        assert dialog.store_combo.currentText() == "steam"
        assert dialog.game_args_input.text() == ""

        config = dialog.get_config()
        assert config["tool.sh"]["GAMEID"] == "umu-367500"
        assert config["Game.sh"]["GAME_ARGS"] == "-game-only"

    def test_scripts_without_stored_config_still_produce_a_config(self, qtbot, mock_umu_database):
        """Without config.json, OK must not return {} and wipe every script's env."""
        from gameyfin_frontend.dialogs import InstallConfigDialog

        dialog = InstallConfigDialog(
            umu_database=mock_umu_database,
            default_game_id="umu-42",
            initial_config={},
            scripts=["/p/Game.sh"],
        )
        qtbot.addWidget(dialog)

        config = dialog.get_config()
        assert config["Game.sh"]["GAMEID"] == "umu-42"
        assert config["Game.sh"]["PROTONPATH"] == "GE-Proton"


    def test_xalia_checkbox_defaults_on(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        dialog = InstallConfigDialog(umu_database=mock_umu_database)
        qtbot.addWidget(dialog)
        assert dialog.xalia_checkbox.isChecked()
        assert dialog.get_config()["ENABLE_XALIA"] == "1"

    def test_xalia_checkbox_per_script(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import InstallConfigDialog
        initial = {
            "A.sh": {"GAMEID": "umu-1", "ENABLE_XALIA": "0"},
            "B.sh": {"GAMEID": "umu-1"},  # saved before the checkbox existed
        }
        dialog = InstallConfigDialog(
            umu_database=mock_umu_database, initial_config=initial,
            scripts=["/p/A.sh", "/p/B.sh"],
        )
        qtbot.addWidget(dialog)
        assert not dialog.xalia_checkbox.isChecked()
        dialog.script_selector.setCurrentIndex(1)
        assert dialog.xalia_checkbox.isChecked()
        config = dialog.get_config()
        assert config["A.sh"]["ENABLE_XALIA"] == "0"
        assert config["B.sh"]["ENABLE_XALIA"] == "1"


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


class TestUmuSearchDialog:
    def test_dialog_initializes(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import UmuSearchDialog
        dialog = UmuSearchDialog(mock_umu_database)
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Search UMU Database"
        assert isinstance(dialog.search_input, QLineEdit)
        assert dialog.search_input.placeholderText() != ""

    def test_search_populates_results(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import UmuSearchDialog
        mock_umu_database.search_by_partial_title.return_value = [
            {"umu_id": "UMU-1", "title": "Game A", "store": "steam"},
            {"umu_id": "UMU-2", "title": "Game B", "store": "gog"},
        ]
        dialog = UmuSearchDialog(mock_umu_database)
        qtbot.addWidget(dialog)
        dialog.search_input.setText("Game")
        dialog._perform_search()
        assert dialog.list_widget.count() == 2

    def test_display_text_includes_store(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import UmuSearchDialog
        mock_umu_database.search_by_partial_title.return_value = [
            {"umu_id": "UMU-1", "title": "Test Game", "store": "steam"},
        ]
        dialog = UmuSearchDialog(mock_umu_database)
        qtbot.addWidget(dialog)
        dialog.search_input.setText("Test")
        dialog._perform_search()
        item_text = dialog.list_widget.item(0).text()
        assert "Test Game" in item_text
        assert "steam" in item_text

    def test_get_selected_entry(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import UmuSearchDialog
        mock_umu_database.search_by_partial_title.return_value = [
            {"umu_id": "UMU-1", "title": "Game A", "store": "steam"},
            {"umu_id": "UMU-2", "title": "Game B", "store": "gog"},
        ]
        dialog = UmuSearchDialog(mock_umu_database)
        qtbot.addWidget(dialog)
        dialog.search_input.setText("Game")
        dialog._perform_search()
        dialog.list_widget.setCurrentRow(1)
        dialog._accept()
        entry = dialog.get_selected_entry()
        assert entry["umu_id"] == "UMU-2"
        assert entry["store"] == "gog"

    def test_no_results_shows_message(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import UmuSearchDialog
        mock_umu_database.search_by_partial_title.return_value = []
        dialog = UmuSearchDialog(mock_umu_database)
        qtbot.addWidget(dialog)
        dialog.search_input.setText("Nonexistent")
        dialog._perform_search()
        assert "No games found" in dialog.label.text()

    def test_ok_disabled_before_selection(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import UmuSearchDialog
        dialog = UmuSearchDialog(mock_umu_database)
        qtbot.addWidget(dialog)
        assert dialog.ok_button.isEnabled() is False

    def test_ok_enabled_after_selection(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import UmuSearchDialog
        mock_umu_database.search_by_partial_title.return_value = [
            {"umu_id": "UMU-1", "title": "Game A", "store": "steam"},
        ]
        dialog = UmuSearchDialog(mock_umu_database)
        qtbot.addWidget(dialog)
        dialog.search_input.setText("Game")
        dialog._perform_search()
        dialog.list_widget.setCurrentRow(0)
        assert dialog.ok_button.isEnabled() is True

    def test_search_on_return_pressed(self, qtbot, mock_umu_database):
        from gameyfin_frontend.dialogs import UmuSearchDialog
        mock_umu_database.search_by_partial_title.return_value = [
            {"umu_id": "UMU-1", "title": "Game A", "store": "steam"},
        ]
        dialog = UmuSearchDialog(mock_umu_database)
        qtbot.addWidget(dialog)
        dialog.search_input.setText("Game")
        qtbot.keyClick(dialog.search_input, Qt.Key.Key_Return)
        assert dialog.list_widget.count() == 1

    def test_results_only_mode(self, qtbot):
        """Pass a results list directly — no search input shown."""
        from gameyfin_frontend.dialogs import UmuSearchDialog
        results = [
            {"umu_id": "UMU-1", "title": "Game A", "store": "steam"},
            {"umu_id": "UMU-2", "title": "Game B", "store": "gog"},
        ]
        dialog = UmuSearchDialog(results)
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Select Game Entry"
        assert dialog.list_widget.count() == 2
        # No search input in results-only mode
        assert dialog.search_input is None  # type: ignore[union-attr]

    def test_results_only_get_selected(self, qtbot):
        """Select from pre-fetched results."""
        from gameyfin_frontend.dialogs import UmuSearchDialog
        results = [
            {"umu_id": "UMU-1", "title": "Game A", "store": "steam"},
            {"umu_id": "UMU-2", "title": "Game B", "store": "gog"},
        ]
        dialog = UmuSearchDialog(results)
        qtbot.addWidget(dialog)
        dialog.list_widget.setCurrentRow(1)
        dialog._accept()
        entry = dialog.get_selected_entry()
        assert entry["umu_id"] == "UMU-2"
        assert entry["store"] == "gog"

    def test_single_result_auto_selected(self, qtbot):
        """When search returns exactly 1 result, it should be auto-selected."""
        from gameyfin_frontend.dialogs import UmuSearchDialog
        mock_umu_database = MagicMock(spec=["search_by_partial_title"])
        mock_umu_database.search_by_partial_title.return_value = [
            {"umu_id": "UMU-42", "title": "Only Game", "store": "steam"},
        ]
        dialog = UmuSearchDialog(mock_umu_database)
        qtbot.addWidget(dialog)
        dialog.search_input.setText("Only Game")
        qtbot.keyClick(dialog.search_input, Qt.Key.Key_Return)
        assert dialog.list_widget.count() == 1
        # Row 0 should be auto-selected, OK button enabled
        assert dialog.list_widget.currentRow() == 0
        assert dialog.ok_button.isEnabled()
        assert "auto-selected" in dialog.label.text()


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


class TestLaunchLoadingDialog:
    def test_dialog_initializes(self, qtbot):
        from PyQt6.QtWidgets import QLabel
        from gameyfin_frontend.dialogs import LaunchLoadingDialog
        dialog = LaunchLoadingDialog(game_name="TestGame")
        qtbot.addWidget(dialog)
        assert dialog.windowTitle() == "Launching Game"
        # Check that game name appears in one of the labels
        labels = dialog.findChildren(QLabel)
        names = [l.text() for l in labels]
        assert any("TestGame" in n for n in names)

    def test_dialog_shows_and_hides(self, qtbot):
        from unittest.mock import patch
        from gameyfin_frontend.dialogs import LaunchLoadingDialog
        with patch.object(LaunchLoadingDialog, "_wineserver_running", return_value=True):
            dialog = LaunchLoadingDialog(game_name="Dark Earth")
            qtbot.addWidget(dialog)
            # wineserver detected during init → dialog auto-closes immediately
            assert not dialog.isVisible()

    def test_dialog_starts_grace_period_on_wineserver(self, qtbot):
        from gameyfin_frontend.dialogs import LaunchLoadingDialog
        dialog = LaunchLoadingDialog(game_name="Test")
        qtbot.addWidget(dialog)
        dialog.show()
        assert dialog.isVisible()
        # Simulate wineserver appearing after a poll cycle
        dialog._wineserver_running = lambda: True
        dialog._on_poll()
        # Grace timer should be running, dialog still visible
        assert dialog._grace_timer.isActive()
        assert dialog.isVisible()
        # Verify _close_now is connected to grace timer by triggering it
        dialog._grace_timer.timeout.emit()
        assert not dialog.isVisible()

    def test_polling_starts_with_safety_timeout(self, qtbot):
        from gameyfin_frontend.dialogs import LaunchLoadingDialog
        dialog = LaunchLoadingDialog(game_name="Test")
        qtbot.addWidget(dialog)
        dialog.show()
        assert dialog._poll_timer is not None
        assert dialog._poll_timer.isActive()
        assert dialog._safety_timer is not None
        assert dialog._safety_timer.isActive()

    def test_spinner_animates(self, qtbot):
        from gameyfin_frontend.dialogs import _SpinnerWidget
        spinner = _SpinnerWidget()
        qtbot.addWidget(spinner)
        spinner.start()
        initial_angle = spinner._angle
        qtbot.wait(200)
        assert spinner._angle != initial_angle or spinner._angle == 4.0

    def test_spinner_stops_cleanly(self, qtbot):
        from gameyfin_frontend.dialogs import _SpinnerWidget
        spinner = _SpinnerWidget()
        qtbot.addWidget(spinner)
        spinner.start()
        spinner.stop()
        assert not spinner._running
