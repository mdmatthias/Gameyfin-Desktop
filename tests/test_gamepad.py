"""Tests for the gamepad device layer (GamepadManager)."""

import os
import time

import pytest

from gameyfin_frontend.gamepad import (
    BTN_A, BTN_B, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_LT, BTN_RT, BUTTONS,
    GamepadManager, GamepadState, _init_sdl,
)


class FakeDevice:
    """A scriptable stand-in for a real controller."""

    def __init__(self):
        self.name = "Fake Pad"
        self.buttons = {name: False for name in BUTTONS}
        self.state = GamepadState()
        self.is_attached = True
        self.rumbles = []
        self.closed = False

    def attached(self):
        return self.is_attached

    def read(self):
        return dict(self.buttons), self.state

    def rumble(self, low, high, duration_ms):
        self.rumbles.append((low, high, duration_ms))

    def close(self):
        self.closed = True


@pytest.fixture()
def manager():
    """A manager with a fake device attached and SDL polling stubbed out."""
    mgr = GamepadManager()
    device = FakeDevice()
    mgr._device = device
    mgr._pump_events = lambda: None
    return mgr, device


def collect(signal):
    """Record every payload emitted by *signal*."""
    received = []
    signal.connect(received.append)
    return received


class TestButtonEdges:
    def test_press_and_release_are_edge_triggered(self, manager):
        mgr, device = manager
        pressed = collect(mgr.button_pressed)
        released = collect(mgr.button_released)

        device.buttons[BTN_A] = True
        mgr._poll()
        mgr._poll()  # still held — must not emit again
        assert pressed == [BTN_A]
        assert released == []

        device.buttons[BTN_A] = False
        mgr._poll()
        assert released == [BTN_A]

    def test_multiple_buttons(self, manager):
        mgr, device = manager
        pressed = collect(mgr.button_pressed)

        device.buttons[BTN_A] = True
        device.buttons[BTN_B] = True
        mgr._poll()

        assert set(pressed) == {BTN_A, BTN_B}

    def test_triggers_use_hysteresis(self, manager):
        mgr, device = manager
        pressed = collect(mgr.button_pressed)
        released = collect(mgr.button_released)

        device.state = GamepadState(left_trigger=0.5)
        mgr._poll()
        assert pressed == []

        device.state = GamepadState(left_trigger=0.8)
        mgr._poll()
        assert pressed == [BTN_LT]

        # Between the release and press thresholds — still considered held.
        device.state = GamepadState(left_trigger=0.5)
        mgr._poll()
        assert released == []

        device.state = GamepadState(left_trigger=0.1)
        mgr._poll()
        assert released == [BTN_LT]

    def test_right_trigger_is_independent(self, manager):
        mgr, device = manager
        pressed = collect(mgr.button_pressed)

        device.state = GamepadState(right_trigger=1.0)
        mgr._poll()

        assert pressed == [BTN_RT]


class TestNavigation:
    def test_dpad_emits_direction(self, manager):
        mgr, device = manager
        directions = collect(mgr.navigate)

        device.buttons[BTN_DPAD_DOWN] = True
        mgr._poll()

        assert directions == ["down"]

    def test_dpad_takes_priority_over_stick(self, manager):
        mgr, device = manager
        directions = collect(mgr.navigate)

        device.buttons[BTN_DPAD_LEFT] = True
        device.state = GamepadState(left_y=1.0)
        mgr._poll()

        assert directions == ["left"]

    def test_stick_inside_deadzone_is_ignored(self, manager):
        mgr, device = manager
        directions = collect(mgr.navigate)

        device.state = GamepadState(left_x=0.1)
        mgr._poll()

        assert directions == []

    @pytest.mark.parametrize(
        ("state", "expected"),
        [
            (GamepadState(left_x=0.9), "right"),
            (GamepadState(left_x=-0.9), "left"),
            (GamepadState(left_y=0.9), "down"),
            (GamepadState(left_y=-0.9), "up"),
        ],
    )
    def test_stick_directions(self, manager, state, expected):
        mgr, device = manager
        directions = collect(mgr.navigate)

        device.state = state
        mgr._poll()

        assert directions == [expected]

    def test_held_direction_repeats_only_after_the_initial_delay(self, manager):
        mgr, device = manager
        directions = collect(mgr.navigate)

        device.buttons[BTN_DPAD_DOWN] = True
        mgr._poll()
        mgr._poll()
        assert directions == ["down"]

        # Pretend the initial delay has elapsed.
        mgr._nav_deadline = time.monotonic() - 0.01
        mgr._poll()
        assert directions == ["down", "down"]

    def test_releasing_the_direction_resets_the_repeat(self, manager):
        mgr, device = manager
        directions = collect(mgr.navigate)

        device.buttons[BTN_DPAD_DOWN] = True
        mgr._poll()
        device.buttons[BTN_DPAD_DOWN] = False
        mgr._poll()
        device.buttons[BTN_DPAD_DOWN] = True
        mgr._poll()

        assert directions == ["down", "down"]
        assert mgr._nav_direction == "down"

    def test_direction_change_emits_immediately(self, manager):
        mgr, device = manager
        directions = collect(mgr.navigate)

        device.buttons[BTN_DPAD_DOWN] = True
        mgr._poll()
        device.buttons[BTN_DPAD_DOWN] = False
        device.buttons[BTN_DPAD_LEFT] = True
        mgr._poll()

        assert directions == ["down", "left"]


class TestConnection:
    def test_polled_state_is_emitted(self, manager):
        mgr, device = manager
        states = collect(mgr.polled)

        device.state = GamepadState(right_x=0.5, right_y=-0.25)
        mgr._poll()

        assert len(states) == 1
        assert states[0].right_x == 0.5
        assert states[0].right_y == -0.25

    def test_detached_device_disconnects(self, manager):
        mgr, device = manager
        events = []
        mgr.disconnected.connect(lambda: events.append(True))
        mgr._scan_for_device = lambda: None

        device.is_attached = False
        mgr._poll()

        assert events == [True]
        assert mgr.is_connected() is False
        assert device.closed is True

    def test_read_failure_disconnects_instead_of_raising(self, manager):
        mgr, device = manager
        mgr._scan_for_device = lambda: None

        def boom():
            raise RuntimeError("device vanished")

        device.read = boom
        mgr._poll()

        assert mgr.is_connected() is False

    def test_stop_releases_the_device(self, manager):
        mgr, device = manager
        mgr._running = True

        mgr.stop()

        assert device.closed is True
        assert mgr.is_connected() is False
        assert mgr.is_running() is False

    def test_rumble_is_forwarded(self, manager):
        mgr, device = manager
        mgr.rumble(0.5, 0.6, 200)
        assert device.rumbles == [(0.5, 0.6, 200)]

    def test_device_name(self, manager):
        mgr, _ = manager
        assert mgr.device_name == "Fake Pad"


class TestSettings:
    def test_deadzone_and_repeat_come_from_settings(self):
        class Settings:
            def get(self, key, fallback=None):
                return {"GF_GAMEPAD_DEADZONE": 40, "GF_GAMEPAD_REPEAT_MS": 300}.get(key, fallback)

        mgr = GamepadManager(Settings())
        assert mgr.deadzone == pytest.approx(0.4)
        assert mgr._repeat_ms == 300

    def test_invalid_settings_fall_back_to_defaults(self):
        class Settings:
            def get(self, key, fallback=None):
                return "not-a-number"

        mgr = GamepadManager(Settings())
        assert 0.0 < mgr.deadzone < 1.0
        assert mgr._repeat_ms > 0

    def test_deadzone_is_clamped(self):
        class Settings:
            def get(self, key, fallback=None):
                return {"GF_GAMEPAD_DEADZONE": 500}.get(key, fallback)

        mgr = GamepadManager(Settings())
        assert mgr.deadzone <= 0.9


class TestSdlInit:
    def test_init_does_not_leak_sdl_env_vars(self):
        """Games are launched with a copy of os.environ — SDL configuration
        must never survive the gamepad init."""
        pytest.importorskip("pygame")
        watched = (
            "SDL_VIDEODRIVER",
            "SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS",
            "PYGAME_HIDE_SUPPORT_PROMPT",
        )
        before = {key: os.environ.get(key) for key in watched}

        _init_sdl()

        assert {key: os.environ.get(key) for key in watched} == before

    def test_init_starts_video_with_the_dummy_driver(self):
        """Controller state only updates while SDL's event queue is pumped, and
        pumping needs the video subsystem — but it must never open a display."""
        pygame = pytest.importorskip("pygame")

        pg, controller = _init_sdl()

        assert pg is not None and controller is not None
        assert pygame.display.get_init() is True
        assert pygame.display.get_driver() == "dummy"
        # The whole point: this is what feeds get_button()/get_axis().
        pygame.event.pump()

    def test_start_without_pygame_returns_false(self, monkeypatch):
        monkeypatch.setattr("gameyfin_frontend.gamepad._init_sdl", lambda: (None, None))
        mgr = GamepadManager()

        assert mgr.start() is False
        assert mgr.is_running() is False
