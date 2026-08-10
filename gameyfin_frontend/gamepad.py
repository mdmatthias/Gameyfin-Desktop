"""Gamepad / controller input backend built on pygame-ce's SDL2 GameController API.

Follows https://pyga.me/docs/ref/sdl2_controller.html: initialise the module,
open a :class:`Controller` for an index that :func:`is_controller` accepts, and
read it with ``get_button`` / ``get_axis``.  SDL only refreshes that state while
its event queue is being pumped, so the poll timer drains ``pygame.event`` every
tick — that also delivers ``CONTROLLERDEVICEADDED`` / ``CONTROLLERDEVICEREMOVED``
for hot-plugging.

Pumping events requires SDL's video subsystem, so it is initialised with the
``dummy`` driver: no window, no display connection competing with Qt's.  The
env vars SDL reads at init time are restored right after, because the app
launches games via ``subprocess`` with a copy of ``os.environ`` and must never
leak ``SDL_VIDEODRIVER=dummy`` into them.

The manager owns a QTimer that polls the first attached controller ~60 times a
second and turns raw device state into higher level Qt signals:

``button_pressed`` / ``button_released``
    Edge triggered, using logical Xbox-style names (see :data:`BUTTONS`).
    Triggers are reported as the pseudo buttons ``lt`` / ``rt``.
``navigate``
    Auto-repeating direction (``"up"`` / ``"down"`` / ``"left"`` / ``"right"``)
    derived from the D-pad and the left stick.
``polled``
    The full :class:`GamepadState` for continuous consumers (scrolling, the
    virtual mouse), emitted once per poll while a controller is attached.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

logger = logging.getLogger(__name__)

# --- Logical button names (Xbox layout) ------------------------------------
BTN_A = "a"
BTN_B = "b"
BTN_X = "x"
BTN_Y = "y"
BTN_LB = "lb"
BTN_RB = "rb"
BTN_LT = "lt"
BTN_RT = "rt"
BTN_BACK = "back"
BTN_START = "start"
BTN_GUIDE = "guide"
BTN_L3 = "l3"
BTN_R3 = "r3"
BTN_DPAD_UP = "dpad_up"
BTN_DPAD_DOWN = "dpad_down"
BTN_DPAD_LEFT = "dpad_left"
BTN_DPAD_RIGHT = "dpad_right"

BUTTONS: tuple[str, ...] = (
    BTN_A, BTN_B, BTN_X, BTN_Y,
    BTN_LB, BTN_RB, BTN_LT, BTN_RT,
    BTN_BACK, BTN_START, BTN_GUIDE,
    BTN_L3, BTN_R3,
    BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT,
)

_DPAD_DIRECTIONS: dict[str, str] = {
    BTN_DPAD_UP: "up",
    BTN_DPAD_DOWN: "down",
    BTN_DPAD_LEFT: "left",
    BTN_DPAD_RIGHT: "right",
}

# Triggers behave like buttons with hysteresis so they don't chatter.
TRIGGER_PRESS_THRESHOLD = 0.65
TRIGGER_RELEASE_THRESHOLD = 0.45

# Defaults, overridable through settings (see GamepadManager.reload_settings).
DEFAULT_DEADZONE = 0.25
DEFAULT_REPEAT_MS = 140
NAV_INITIAL_DELAY_MS = 420
POLL_INTERVAL_MS = 16

# Axis range reported by SDL: sticks -32768…32767, triggers 0…32768.
_AXIS_MAX = 32767.0


@dataclass
class GamepadState:
    """Continuous axis state, normalised to -1.0 … 1.0 (triggers: 0.0 … 1.0)."""

    left_x: float = 0.0
    left_y: float = 0.0
    right_x: float = 0.0
    right_y: float = 0.0
    left_trigger: float = 0.0
    right_trigger: float = 0.0


class _ControllerDevice:
    """Reads one SDL GameController through ``pygame._sdl2.controller``.

    The GameController API maps every known pad (Xbox, DualSense, DualShock,
    Switch Pro, 8BitDo, …) onto one canonical layout, so no per-device button
    tables are needed here.
    """

    def __init__(self, pygame_mod: Any, controller: Any) -> None:
        self._pygame = pygame_mod
        self._controller = controller
        self.name = self._safe_name()

        pg = pygame_mod
        self._button_map = {
            BTN_A: pg.CONTROLLER_BUTTON_A,
            BTN_B: pg.CONTROLLER_BUTTON_B,
            BTN_X: pg.CONTROLLER_BUTTON_X,
            BTN_Y: pg.CONTROLLER_BUTTON_Y,
            BTN_LB: pg.CONTROLLER_BUTTON_LEFTSHOULDER,
            BTN_RB: pg.CONTROLLER_BUTTON_RIGHTSHOULDER,
            BTN_BACK: pg.CONTROLLER_BUTTON_BACK,
            BTN_START: pg.CONTROLLER_BUTTON_START,
            BTN_GUIDE: pg.CONTROLLER_BUTTON_GUIDE,
            BTN_L3: pg.CONTROLLER_BUTTON_LEFTSTICK,
            BTN_R3: pg.CONTROLLER_BUTTON_RIGHTSTICK,
            BTN_DPAD_UP: pg.CONTROLLER_BUTTON_DPAD_UP,
            BTN_DPAD_DOWN: pg.CONTROLLER_BUTTON_DPAD_DOWN,
            BTN_DPAD_LEFT: pg.CONTROLLER_BUTTON_DPAD_LEFT,
            BTN_DPAD_RIGHT: pg.CONTROLLER_BUTTON_DPAD_RIGHT,
        }

    def _safe_name(self) -> str:
        try:
            return str(self._controller.name)
        except Exception:  # noqa: BLE001 - device may vanish mid-call
            return "Controller"

    def attached(self) -> bool:
        try:
            return bool(self._controller.attached())
        except Exception:  # noqa: BLE001
            return False

    def read(self) -> tuple[dict[str, bool], GamepadState]:
        """Return the current button states and axis state."""
        ctrl = self._controller
        pg = self._pygame

        buttons = {name: bool(ctrl.get_button(idx)) for name, idx in self._button_map.items()}

        def axis(index: int) -> float:
            return max(-1.0, min(1.0, ctrl.get_axis(index) / _AXIS_MAX))

        state = GamepadState(
            left_x=axis(pg.CONTROLLER_AXIS_LEFTX),
            left_y=axis(pg.CONTROLLER_AXIS_LEFTY),
            right_x=axis(pg.CONTROLLER_AXIS_RIGHTX),
            right_y=axis(pg.CONTROLLER_AXIS_RIGHTY),
            left_trigger=max(0.0, axis(pg.CONTROLLER_AXIS_TRIGGERLEFT)),
            right_trigger=max(0.0, axis(pg.CONTROLLER_AXIS_TRIGGERRIGHT)),
        )
        return buttons, state

    def rumble(self, low: float, high: float, duration_ms: int) -> None:
        try:
            self._controller.rumble(low, high, duration_ms)
        except Exception:  # noqa: BLE001 - not every pad supports rumble
            pass

    def close(self) -> None:
        try:
            self._controller.quit()
        except Exception:  # noqa: BLE001
            pass


def _init_sdl() -> tuple[Any, Any] | tuple[None, None]:
    """Initialise SDL for controller input. Returns ``(pygame, controller_module)``.

    Reading controller state only works while SDL's event queue is pumped, and
    pumping requires the video subsystem — hence the ``dummy`` video driver: it
    starts no display connection and creates no window, so Qt is untouched.

    Environment variables SDL reads at init time are set only for the duration
    of this call and restored afterwards; the app launches games with a copy of
    ``os.environ`` and must not leak its own SDL configuration into them.
    """
    scoped_env = {
        # No real window is ever created, so SDL needs no display connection.
        "SDL_VIDEODRIVER": "dummy",
        # Without a focused window SDL would otherwise stop updating pads.
        "SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS": "1",
        "PYGAME_HIDE_SUPPORT_PROMPT": "1",
    }
    previous = {key: os.environ.get(key) for key in scoped_env}
    os.environ.update(scoped_env)
    try:
        import pygame  # noqa: PLC0415 - optional dependency, imported lazily
        from pygame._sdl2 import controller  # noqa: PLC0415

        if not pygame.display.get_init():
            pygame.display.init()
        if not controller.get_init():
            controller.init()
        return pygame, controller
    except Exception as exc:  # noqa: BLE001 - any SDL failure must not kill the app
        logger.warning("Gamepad support unavailable (pygame/SDL init failed): %s", exc)
        return None, None
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class GamepadManager(QObject):
    """Polls the first attached controller and emits Qt signals for it."""

    connected = pyqtSignal(str)
    disconnected = pyqtSignal()
    button_pressed = pyqtSignal(str)
    button_released = pyqtSignal(str)
    navigate = pyqtSignal(str)
    polled = pyqtSignal(object)

    def __init__(self, settings: Any = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings

        self._pygame: Any = None
        self._controller_module: Any = None
        self._device: Any = None
        self._running = False
        self._devices_changed = False

        self._buttons: dict[str, bool] = {name: False for name in BUTTONS}
        self._state = GamepadState()

        self._deadzone = DEFAULT_DEADZONE
        self._repeat_ms = DEFAULT_REPEAT_MS

        self._nav_direction = ""
        self._nav_deadline = 0.0

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

        self.reload_settings()

    # -- lifecycle ---------------------------------------------------------

    def reload_settings(self) -> None:
        """Re-read deadzone and repeat rate from the settings manager."""
        if not self.settings:
            return
        try:
            deadzone_pct = int(self.settings.get("GF_GAMEPAD_DEADZONE", int(DEFAULT_DEADZONE * 100)))
            self._deadzone = min(0.9, max(0.05, deadzone_pct / 100.0))
        except (TypeError, ValueError):
            self._deadzone = DEFAULT_DEADZONE
        try:
            self._repeat_ms = max(40, int(self.settings.get("GF_GAMEPAD_REPEAT_MS", DEFAULT_REPEAT_MS)))
        except (TypeError, ValueError):
            self._repeat_ms = DEFAULT_REPEAT_MS

    def start(self) -> bool:
        """Initialise SDL and begin polling. Returns True when polling started."""
        if self._running:
            return True
        if self._pygame is None:
            self._pygame, self._controller_module = _init_sdl()
        if self._pygame is None or self._controller_module is None:
            return False

        self._running = True
        self._timer.start()
        self._scan_for_device()
        return True

    def stop(self) -> None:
        """Stop polling and release the controller."""
        self._timer.stop()
        was_connected = self._device is not None
        self._release_device()
        self._running = False
        self._reset_input_state()
        if was_connected:
            self.disconnected.emit()

    def is_running(self) -> bool:
        return self._running

    def is_connected(self) -> bool:
        return self._device is not None

    @property
    def device_name(self) -> str:
        return getattr(self._device, "name", "")

    @property
    def deadzone(self) -> float:
        return self._deadzone

    def rumble(self, low: float = 0.4, high: float = 0.4, duration_ms: int = 120) -> None:
        """Best-effort haptic pulse; silently ignored on pads without rumble."""
        if self._device is not None:
            self._device.rumble(low, high, duration_ms)

    # -- device discovery --------------------------------------------------

    def _release_device(self) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None

    def _scan_for_device(self) -> None:
        """Open the first index SDL recognises as a game controller, if any."""
        if self._device is not None or self._controller_module is None:
            return

        controller = self._controller_module
        try:
            for index in range(controller.get_count()):
                if not controller.is_controller(index):
                    continue
                device = _ControllerDevice(self._pygame, controller.Controller(index))
                self._device = device
                logger.info("Gamepad connected: %s", device.name)
                self.connected.emit(device.name)
                return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to open game controller: %s", exc)

    def _pump_events(self) -> None:
        """Drain SDL's event queue.

        This is what makes ``get_button`` / ``get_axis`` return anything: SDL
        refreshes controller state from inside its event pump.  Draining also
        keeps the queue from filling up and gives us hot-plug notifications.
        """
        pg = self._pygame
        if pg is None:
            return
        try:
            events = pg.event.get()
        except Exception as exc:  # noqa: BLE001
            logger.warning("pygame.event.get() failed: %s", exc)
            return

        for event in events:
            if event.type in (pg.CONTROLLERDEVICEADDED, pg.CONTROLLERDEVICEREMOVED):
                self._devices_changed = True

    def _on_device_lost(self) -> None:
        if self._device is None:
            return
        logger.info("Gamepad disconnected: %s", self._device.name)
        self._release_device()
        self._reset_input_state()
        self.disconnected.emit()
        # Another pad may still be attached (e.g. one of two was unplugged).
        self._scan_for_device()

    def _reset_input_state(self) -> None:
        self._buttons = {name: False for name in BUTTONS}
        self._state = GamepadState()
        self._nav_direction = ""
        self._nav_deadline = 0.0

    # -- polling -----------------------------------------------------------

    def _poll(self) -> None:
        """Read the device once and emit whatever changed."""
        self._pump_events()

        if self._device is None:
            if not self._devices_changed:
                return
            self._devices_changed = False
            self._scan_for_device()
            if self._device is None:
                return

        if not self._device.attached():
            self._on_device_lost()
            return

        try:
            buttons, state = self._device.read()
        except Exception as exc:
            logger.warning("Gamepad read failed: %s", exc)
            self._on_device_lost()
            return

        # Triggers are exposed as buttons with hysteresis.
        buttons[BTN_LT] = self._trigger_pressed(BTN_LT, state.left_trigger)
        buttons[BTN_RT] = self._trigger_pressed(BTN_RT, state.right_trigger)

        self._state = state
        self._emit_button_edges(buttons)
        self._update_navigation(buttons, state)
        self.polled.emit(state)

    def _trigger_pressed(self, name: str, value: float) -> bool:
        if self._buttons.get(name):
            return value > TRIGGER_RELEASE_THRESHOLD
        return value > TRIGGER_PRESS_THRESHOLD

    def _emit_button_edges(self, buttons: dict[str, bool]) -> None:
        for name in BUTTONS:
            new_value = bool(buttons.get(name, False))
            if new_value == self._buttons.get(name, False):
                continue
            self._buttons[name] = new_value
            if new_value:
                self.button_pressed.emit(name)
            else:
                self.button_released.emit(name)

    def _current_direction(self, buttons: dict[str, bool], state: GamepadState) -> str:
        """Return the active navigation direction, D-pad taking priority."""
        for button, direction in _DPAD_DIRECTIONS.items():
            if buttons.get(button):
                return direction

        x, y = state.left_x, state.left_y
        if abs(x) < self._deadzone and abs(y) < self._deadzone:
            return ""
        if abs(x) >= abs(y):
            return "right" if x > 0 else "left"
        # SDL reports the stick's Y axis positive-down.
        return "down" if y > 0 else "up"

    def _update_navigation(self, buttons: dict[str, bool], state: GamepadState) -> None:
        direction = self._current_direction(buttons, state)
        now = time.monotonic()

        if not direction:
            self._nav_direction = ""
            self._nav_deadline = 0.0
            return

        if direction != self._nav_direction:
            self._nav_direction = direction
            self._nav_deadline = now + NAV_INITIAL_DELAY_MS / 1000.0
            self.navigate.emit(direction)
            return

        if now >= self._nav_deadline:
            self._nav_deadline = now + self._repeat_ms / 1000.0
            self.navigate.emit(direction)

    # -- introspection helpers (used by the navigator and by tests) ---------

    def is_pressed(self, name: str) -> bool:
        return bool(self._buttons.get(name, False))

    @property
    def state(self) -> GamepadState:
        return self._state
