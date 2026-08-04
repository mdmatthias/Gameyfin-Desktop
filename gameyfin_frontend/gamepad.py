"""Simple polling-based gamepad / controller support via pyglet.

Discovers the first available controller on start and exposes a minimal API
for reading button states and stick direction.  No signals, no event decorators,
no singleton — just poll methods the window calls each frame.

Button names (Xbox-style layout):

    ``a``, ``b``, ``x``, ``y``              — face buttons
    ``leftshoulder``, ``rightshoulder``     — bumpers
    ``back``, ``start``                     — middle buttons
    ``dpad_up``, ``dpad_down``, ``dpad_left``, ``dpad_right`` — D-pad
    ``leftstick``, ``rightstick``           — stick clicks (L3 / R3)

Stick directions returned by :meth:`get_stick_direction`:
    ``"up"``, ``"down"``, ``"left"``, ``"right"``, or ``""`` (centered).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from PyQt6.QtCore import QObject, QTimer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Button / axis name registry — logical name → pyglet attribute
# ---------------------------------------------------------------------------
BUTTON_NAMES: tuple[str, ...] = (
    "a", "b", "x", "y",
    "leftshoulder", "rightshoulder",
    "back", "start",
    "dpad_up", "dpad_down", "dpad_left", "dpad_right",
    "leftstick", "rightstick",
)

AXIS_NAMES: tuple[str, ...] = (
    "lefttrigger", "righttrigger",
    "leftx", "lefty", "rightx", "righty",
    "dpadx", "dpady",
)


class GamepadManager(QObject):
    """Minimal polling gamepad manager.

    Call :meth:`start` to discover and open the first controller.
    Then call :meth:`is_pressed`, :meth:`was_pressed`,
    :meth:`get_stick_direction`, etc. from your own update loop.

    The manager runs an internal QTimer (50 ms) that pumps pyglet events
    so the controller attributes stay up to date.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller: Any = None
        self._enabled: bool = False
        self._poll_timer: QTimer | None = None

        # Previous-frame button state for edge detection
        self._prev_buttons: dict[str, bool] = {}

        # Stick / dpad values (updated each poll cycle)
        self._stick_x: float = 0.0
        self._stick_y: float = 0.0
        self._dpad_x: float = 0.0
        self._dpad_y: float = 0.0

        # Tuning defaults
        self._deadzone: float = 0.15
        self._nav_repeat_ms: int = 250  # ms between repeat nav events after first press

        # Edge-detection state for get_new_navigation_direction()
        self._last_nav_dir: str = ""
        self._last_nav_time: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        """True if a controller has been discovered and opened."""
        return self._controller is not None

    @property
    def controller_name(self) -> str:
        """Name of the connected controller, or empty string."""
        return self._controller.name if self._controller else ""

    def start(self) -> bool:
        """Discover controllers, open the first one, begin polling.

        Returns:
            ``True`` if a controller was found and opened, ``False`` otherwise.
        """
        if self._enabled:
            return self.is_connected

        try:
            from pyglet.input import get_controllers
        except ImportError:
            logger.debug("GamepadManager: pyglet not available.")
            return False

        controllers = get_controllers()
        if not controllers:
            logger.debug("GamepadManager: no controllers discovered.")
            return False

        self._controller = controllers[0]
        self._controller.open()

        logger.info(
            "GamepadManager: connected to '%s'",
            self._controller.name,
        )

        # Start internal poll timer — keeps controller attrs fresh
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(50)
        self._poll_timer.timeout.connect(self._pump_and_poll)
        self._poll_timer.start()

        self._enabled = True
        return True

    def stop(self) -> None:
        """Stop polling and close the controller."""
        if not self._enabled:
            return

        if self._poll_timer:
            self._poll_timer.stop()
            self._poll_timer.deleteLater()
            self._poll_timer = None

        if self._controller:
            try:
                self._controller.close()
            except Exception:
                pass
            self._controller = None

        self._enabled = False
        self._prev_buttons.clear()
        self._stick_x = self._stick_y = self._dpad_x = self._dpad_y = 0.0
        self._last_nav_dir = ""

    # ------------------------------------------------------------------
    # Public query API
    # ------------------------------------------------------------------

    def is_pressed(self, button: str) -> bool:
        """Check whether *button* is currently held down.

        Args:
            button: One of the names in :data:`BUTTON_NAMES`.

        Returns:
            ``True`` if the button is currently pressed.
        """
        if not self._controller:
            return False
        attr = getattr(self._controller, button, None)
        if attr is None:
            logger.warning("GamepadManager: unknown button '%s'", button)
            return False
        return bool(attr)

    def was_pressed(self, button: str) -> bool:
        """Edge detection — did *button* become pressed this frame?

        Returns ``False`` after the first call following a press (debounced).

        Args:
            button: One of the names in :data:`BUTTON_NAMES`.
        """
        current = self.is_pressed(button)
        prev = self._prev_buttons.get(button, False)
        if current and not prev:
            self._prev_buttons[button] = True
            return True
        self._prev_buttons[button] = current
        return False

    def get_stick_direction(self, deadzone: float | None = None) -> str:
        """Return the current dominant stick / D-pad direction.

        This returns the **raw** direction every call — useful for continuous
        movement (e.g. camera control).  For menu navigation use
        :meth:`get_new_navigation_direction` which only fires on change.

        Stick input takes priority over D-pad.  Falls back to D-pad when
        the stick is centered (within deadzone).

        Args:
            deadzone: Minimum magnitude for the stick to register movement.
                      Defaults to the manager's configured deadzone (0.15).

        Returns:
            One of ``"up"``, ``"down"``, ``"left"``, ``"right"``, or ``""``.
        """
        dz = deadzone if deadzone is not None else self._deadzone

        x = self._stick_x if abs(self._stick_x) > dz else 0.0
        y = self._stick_y if abs(self._stick_y) > dz else 0.0

        if x == 0.0 and y == 0.0:
            dx = self._dpad_x if abs(self._dpad_x) > 0.5 else 0.0
            dy = self._dpad_y if abs(self._dpad_y) > 0.5 else 0.0
            x = dx
            y = dy

        if x == 0.0 and y == 0.0:
            return ""

        if abs(x) > abs(y):
            return "right" if x > 0 else "left"
        else:
            return "down" if y > 0 else "up"

    def get_new_navigation_direction(self) -> str:
        """Edge-triggered navigation direction — like a button press.

        Returns the new direction only once when it changes, then ``""`` until
        the stick moves again.  After the initial press, repeats at
        :attr:`_nav_repeat_ms` intervals while held.

        Returns:
            One of ``"up"``, ``"down"``, ``"left"``, ``"right"``, or ``""``.
        """
        raw = self.get_stick_direction()
        now = time.monotonic()

        if raw == "":
            # Released — allow re-entry next time
            self._last_nav_dir = ""
            self._last_nav_time = 0.0
            return ""

        if raw != self._last_nav_dir:
            # New direction — fire immediately
            self._last_nav_dir = raw
            self._last_nav_time = now
            return raw

        # Same direction as last time — repeat after cooldown
        elapsed_ms = (now - self._last_nav_time) * 1000
        if elapsed_ms >= self._nav_repeat_ms:
            self._last_nav_time = now
            return raw

        return ""

    def get_trigger_value(self, trigger: str) -> float:
        """Return raw trigger value (0.0 – 1.0).

        Args:
            trigger: Either ``"lefttrigger"`` or ``"righttrigger"``.

        Returns:
            Float between 0.0 (released) and 1.0 (fully pressed).
        """
        if not self._controller:
            return 0.0
        val = getattr(self._controller, trigger, 0.0)
        return max(0.0, min(1.0, float(val)))

    # ------------------------------------------------------------------
    # Internal polling
    # ------------------------------------------------------------------

    def _pump_and_poll(self) -> None:
        """Pump pyglet events from the evdev device, then snapshot state."""
        if not self._controller:
            return
        dev = getattr(self._controller, "device", None)
        if dev is None:
            return
        try:
            import select as select_module
            fd = dev.fileno()
            if fd is None:
                return
            readable, _, _ = select_module.select([fd], [], [], 0)
            if readable:
                dev.select()
        except Exception:
            pass

        self._poll()

    def _poll(self) -> None:
        """Snapshot current stick / dpad values from controller attributes."""
        c = self._controller
        if c is None:
            return

        self._stick_x = -float(c.leftx)
        self._stick_y = float(c.lefty)
        self._dpad_x = float(c.dpadx)
        self._dpad_y = -float(c.dpady)


# ---------------------------------------------------------------------------
# Convenience function for quick checks (e.g. in tests)
# ---------------------------------------------------------------------------

def controllers_available() -> int:
    """Return the number of controllers pyglet can discover right now.

    Useful for a quick pre-check before calling :meth:`GamepadManager.start`.
    Requires Qt's event loop to be running (GL context must be initialized).
    """
    try:
        from pyglet.input import get_controllers
    except ImportError:
        return 0
    return len(get_controllers())
