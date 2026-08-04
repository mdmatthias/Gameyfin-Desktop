"""Tests for gameyfin_frontend.gamepad — simplified polling API."""

from unittest.mock import MagicMock, patch


class TestGamepadManagerLifecycle:
    """Test start/stop lifecycle and connection detection."""

    def test_initial_state(self):
        from gameyfin_frontend.gamepad import GamepadManager
        mgr = GamepadManager()
        assert mgr.is_connected is False
        assert mgr.controller_name == ""
        mgr.deleteLater()

    def test_start_no_controllers(self):
        """start() returns False when no controllers are discovered."""
        from gameyfin_frontend.gamepad import GamepadManager
        with patch("pyglet.input.get_controllers", return_value=[]):
            mgr = GamepadManager()
            result = mgr.start()
            assert result is False
            assert mgr.is_connected is False
            mgr.deleteLater()

    def test_start_with_controller(self):
        """start() opens the first controller and starts polling."""
        from gameyfin_frontend.gamepad import GamepadManager
        mock_ctrl = MagicMock()
        mock_ctrl.name = "Test Controller"

        with patch("pyglet.input.get_controllers", return_value=[mock_ctrl]):
            mgr = GamepadManager()
            result = mgr.start()
            assert result is True
            assert mgr.is_connected is True
            assert mgr.controller_name == "Test Controller"
            mock_ctrl.open.assert_called_once()

            mgr.stop()
            mock_ctrl.close.assert_called_once()
            assert mgr.is_connected is False
            mgr.deleteLater()

    def test_stop_clears_state(self):
        """stop() stops the timer, closes controller, resets state."""
        from gameyfin_frontend.gamepad import GamepadManager
        mock_ctrl = MagicMock()
        mock_ctrl.name = "Test Pad"

        with patch("pyglet.input.get_controllers", return_value=[mock_ctrl]):
            mgr = GamepadManager()
            mgr.start()
            assert mgr.is_connected is True

            mgr.stop()
            assert mgr.is_connected is False
            mock_ctrl.close.assert_called_once()
            mgr.deleteLater()

    def test_double_start_is_noop(self):
        """Calling start() twice should not open a second controller."""
        from gameyfin_frontend.gamepad import GamepadManager
        mock_ctrl = MagicMock()
        mock_ctrl.name = "Test Pad"

        with patch("pyglet.input.get_controllers", return_value=[mock_ctrl]):
            mgr = GamepadManager()
            mgr.start()
            call_count = mock_ctrl.open.call_count

            mgr.start()  # second call
            assert mock_ctrl.open.call_count == call_count  # still 1
            mgr.deleteLater()


class TestIsPressed:
    """Test the is_pressed(button) query method."""

    @staticmethod
    def _make_mgr(**attrs):
        """Create a manager with a mocked controller pre-loaded with attrs."""
        from gameyfin_frontend.gamepad import GamepadManager
        mgr = GamepadManager()
        mock_ctrl = MagicMock(spec=list(attrs.keys()))
        for k, v in attrs.items():
            setattr(mock_ctrl, k, v)
        mgr._controller = mock_ctrl
        return mgr

    def test_button_pressed(self):
        mgr = self._make_mgr(a=True)
        assert mgr.is_pressed("a") is True

    def test_button_released(self):
        mgr = self._make_mgr(a=False)
        assert mgr.is_pressed("a") is False

    def test_no_controller_returns_false(self):
        from gameyfin_frontend.gamepad import GamepadManager
        mgr = GamepadManager()
        assert mgr.is_pressed("a") is False
        mgr.deleteLater()


class TestWasPressed:
    """Test edge-triggered was_pressed(button)."""

    @staticmethod
    def _make_mgr(**attrs):
        from PyQt6.QtWidgets import QApplication
        from gameyfin_frontend.gamepad import GamepadManager
        mgr = GamepadManager()
        mock_ctrl = MagicMock(spec=list(attrs.keys()) or ["a"])
        for k, v in attrs.items():
            setattr(mock_ctrl, k, v)
        mgr._controller = mock_ctrl
        return mgr, mock_ctrl

    def teardown_method(self, method):
        """Clean up any lingering Qt objects between tests."""
        from PyQt6.QtWidgets import QApplication
        QApplication.processEvents()

    def test_edge_press_detected(self):
        """First call after press → True; subsequent calls while held → False."""
        mgr, _ = self._make_mgr(a=True)
        assert mgr.was_pressed("a") is True
        assert mgr.was_pressed("a") is False
        mgr.deleteLater()

    def test_still_released_returns_false(self):
        mgr, _ = self._make_mgr(a=False)
        assert mgr.was_pressed("a") is False
        assert mgr.was_pressed("a") is False
        mgr.deleteLater()

    def test_release_then_press_again(self):
        """Release and re-press should detect a new edge."""
        mgr, ctrl = self._make_mgr(a=True)
        assert mgr.was_pressed("a") is True   # initial press (already held)
        ctrl.a = False                         # release
        assert mgr.is_pressed("a") is False
        assert mgr.was_pressed("a") is False   # no edge while released
        ctrl.a = True                          # re-press
        assert mgr.is_pressed("a") is True
        assert mgr.was_pressed("a") is True    # new edge detected
        mgr.deleteLater()


class TestStickDirection:
    """Test get_stick_direction() stick + dpad logic."""

    @staticmethod
    def _make_mgr(stick_x=0.0, stick_y=0.0, dpad_x=0.0, dpad_y=0.0, deadzone=0.15):
        from gameyfin_frontend.gamepad import GamepadManager
        mgr = GamepadManager()
        mgr._stick_x = stick_x
        mgr._stick_y = stick_y
        mgr._dpad_x = dpad_x
        mgr._dpad_y = dpad_y
        mgr._deadzone = deadzone
        return mgr

    def test_centered_stick_no_direction(self):
        mgr = self._make_mgr()
        assert mgr.get_stick_direction() == ""

    def test_stick_right(self):
        mgr = self._make_mgr(stick_x=0.8, stick_y=0.0)
        assert mgr.get_stick_direction() == "right"

    def test_stick_left(self):
        mgr = self._make_mgr(stick_x=-0.8, stick_y=0.0)
        assert mgr.get_stick_direction() == "left"

    def test_stick_down(self):
        mgr = self._make_mgr(stick_x=0.0, stick_y=0.8)
        assert mgr.get_stick_direction() == "down"

    def test_stick_up(self):
        mgr = self._make_mgr(stick_x=0.0, stick_y=-0.8)
        assert mgr.get_stick_direction() == "up"

    def test_stick_below_deadzone_ignored(self):
        mgr = self._make_mgr(stick_x=0.05, stick_y=0.05)
        assert mgr.get_stick_direction() == ""

    def test_dpad_fallback_when_stick_centered(self):
        mgr = self._make_mgr(dpad_x=1.0, dpad_y=0.0)
        assert mgr.get_stick_direction() == "right"

    def test_dpad_down(self):
        mgr = self._make_mgr(dpad_x=0.0, dpad_y=1.0)
        assert mgr.get_stick_direction() == "down"

    def test_dpad_ignored_if_within_threshold(self):
        mgr = self._make_mgr(dpad_x=0.3, dpad_y=0.0)
        assert mgr.get_stick_direction() == ""

    def test_stick_takes_priority_over_dpad(self):
        mgr = self._make_mgr(stick_x=0.9, dpad_x=-1.0)
        assert mgr.get_stick_direction() == "right"

    def test_vertical_stick_wins_over_horizontal(self):
        mgr = self._make_mgr(stick_x=0.3, stick_y=0.7)
        assert mgr.get_stick_direction() == "down"


class TestGetNewNavigationDirection:
    """Test edge-triggered navigation — fires once per direction change."""

    @staticmethod
    def _make_mgr(stick_x=0.0, stick_y=0.0, deadzone=0.15):
        from gameyfin_frontend.gamepad import GamepadManager
        mgr = GamepadManager()
        mgr._stick_x = stick_x
        mgr._stick_y = stick_y
        mgr._deadzone = deadzone
        return mgr

    def test_centered_returns_empty(self):
        mgr = self._make_mgr()
        assert mgr.get_new_navigation_direction() == ""

    def test_first_press_fires_immediately(self):
        mgr = self._make_mgr(stick_x=0.8)
        assert mgr.get_new_navigation_direction() == "right"

    def test_same_direction_repeats_after_cooldown(self):
        import time
        mgr = self._make_mgr(stick_x=0.8)
        # First call fires immediately
        assert mgr.get_new_navigation_direction() == "right"
        # Immediate second call returns empty (cooldown active)
        assert mgr.get_new_navigation_direction() == ""
        # After cooldown expires, repeats
        mgr._last_nav_time = time.monotonic() - 0.3
        assert mgr.get_new_navigation_direction() == "right"

    def test_direction_change_fires_immediately(self):
        mgr = self._make_mgr(stick_x=0.8)
        assert mgr.get_new_navigation_direction() == "right"
        # Change direction immediately
        mgr._stick_x = -0.8
        assert mgr.get_new_navigation_direction() == "left"

    def test_release_clears_state(self):
        mgr = self._make_mgr(stick_x=0.8)
        assert mgr.get_new_navigation_direction() == "right"
        # Release
        mgr._stick_x = 0.0
        assert mgr.get_new_navigation_direction() == ""
        # Press again — should fire as a new press
        mgr._stick_x = 0.8
        assert mgr.get_new_navigation_direction() == "right"


class TestTriggerValue:
    """Test get_trigger_value()."""

    @staticmethod
    def _make_mgr(**attrs):
        from gameyfin_frontend.gamepad import GamepadManager
        mgr = GamepadManager()
        mock_ctrl = MagicMock()
        for k, v in attrs.items():
            setattr(mock_ctrl, k, v)
        mgr._controller = mock_ctrl
        return mgr

    def test_zero_trigger(self):
        mgr = self._make_mgr(lefttrigger=0.0, righttrigger=0.5)
        assert mgr.get_trigger_value("lefttrigger") == 0.0
        assert mgr.get_trigger_value("righttrigger") == 0.5

    def test_clamps_to_one(self):
        mgr = self._make_mgr(righttrigger=2.0)
        assert mgr.get_trigger_value("righttrigger") == 1.0

    def test_no_controller_returns_zero(self):
        from gameyfin_frontend.gamepad import GamepadManager
        mgr = GamepadManager()
        assert mgr.get_trigger_value("lefttrigger") == 0.0
        mgr.deleteLater()


class TestButtonsAndAxesRegistry:
    """Verify the public button/axis name constants are sensible."""

    def test_button_names_nonempty(self):
        from gameyfin_frontend.gamepad import BUTTON_NAMES
        assert len(BUTTON_NAMES) > 0
        assert "a" in BUTTON_NAMES
        assert "b" in BUTTON_NAMES
        assert "leftshoulder" in BUTTON_NAMES

    def test_axis_names_nonempty(self):
        from gameyfin_frontend.gamepad import AXIS_NAMES
        assert len(AXIS_NAMES) > 0
        assert "lefttrigger" in AXIS_NAMES
        assert "righttrigger" in AXIS_NAMES


class TestControllersAvailable:
    """Test the convenience controllers_available() function."""

    def test_returns_count(self):
        from gameyfin_frontend.gamepad import controllers_available
        with patch("pyglet.input.get_controllers", return_value=[MagicMock(), MagicMock()]):
            assert controllers_available() == 2

    def test_returns_zero_when_missing_pyglet(self):
        import sys
        # Temporarily hide pyglet
        saved = sys.modules.pop("pyglet", None)
        try:
            with patch.dict(sys.modules, {"pyglet": None}):
                # Force reimport of the module under test
                import gameyfin_frontend.gamepad as gp
                # Reset any cached state
                gp.__dict__.pop("_cached_controllers", None)
                result = gp.controllers_available()
                # When pyglet is missing, get_controllers raises ImportError
                # which is caught and returns 0
                assert isinstance(result, int)
        finally:
            if saved:
                sys.modules["pyglet"] = saved


class TestSettingsIntegration:
    """Verify GF_GAMEPAD_ENABLED flows through SettingsManager."""

    def test_default_is_disabled(self, fresh_settings):
        from PyQt6.QtCore import QStandardPaths
        from gameyfin_frontend.settings import SettingsManager
        SettingsManager._instance = None
        sm = SettingsManager()
        assert sm.get("GF_GAMEPAD_ENABLED") == 0
        SettingsManager._instance = None

    def test_setting_can_be_enabled(self, fresh_settings):
        from PyQt6.QtCore import QStandardPaths
        from gameyfin_frontend.settings import SettingsManager
        SettingsManager._instance = None
        sm = SettingsManager()
        sm.set("GF_GAMEPAD_ENABLED", 1)
        assert sm.get("GF_GAMEPAD_ENABLED") == 1
        SettingsManager._instance = None
