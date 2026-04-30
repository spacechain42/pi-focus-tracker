"""
button.py
=========
GPIO push-button interface for Raspberry Pi.

Each :class:`Button` monitors a single GPIO pin and tracks:

- **pressed** – whether the button is currently held down,
- **just_pressed** – whether the button transitioned from released → pressed
  since the last :meth:`~Button.update` call,
- **just_released** – whether the button transitioned from pressed → released
  since the last :meth:`~Button.update` call,
- **held** – whether the button has been continuously pressed for at least
  *hold_time* seconds.

Callbacks
---------
Register Python callables for press, release, and hold events::

    btn = Button(pin=17)
    btn.on_press(lambda: print("pressed!"))
    btn.on_hold(lambda: print("held for 1 s"))

:meth:`Button.update` must be called periodically (e.g. in a tight loop or
via :class:`~pi_focus_tracker.controls.Controls`) to poll the GPIO pin and
fire any pending callbacks.

Dependencies
------------
``RPi.GPIO`` must be installed to use real hardware.  When it is absent the
class still works in a *headless* mode where GPIO reads always return the
*released* level, which is useful for unit testing.
"""

import time
import threading
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# Optional hardware import
# ---------------------------------------------------------------------------
try:
    import RPi.GPIO as GPIO   # type: ignore[import]
    _HAS_GPIO = True
except RuntimeError:
    GPIO = None               # type: ignore[assignment]
    _HAS_GPIO = False

_UNSET = object()   # sentinel for "no GPIO value injected"


class Button:
    """Tracks the state of a single GPIO push-button.

    Parameters
    ----------
    pin : int
        BCM GPIO pin number.
    pull_up : bool, optional
        When ``False`` (default),  assumes active-high wiring with an
        external pull-down resistor.  Set to ``True`` to enable the internal pull-up resistor and
        the button is considered pressed when the pin reads ``LOW``
        (active-low wiring).    hold_time : float, optional
        Seconds the button must be continuously pressed before the *held*
        state becomes active and the hold callback fires.  Defaults to ``1.0``.
    bounce_time : int, optional
        Debounce time in milliseconds passed to ``RPi.GPIO.setup``.
        Defaults to ``50``.
    """

    def __init__(
        self,
        pin: int,
        pull_up: bool = False,
        hold_time: float = 1.0,
        bounce_time: int = 50,
    ) -> None:
        if pin < 0:
            raise ValueError("pin must be a non-negative GPIO number")
        if hold_time <= 0:
            raise ValueError("hold_time must be positive")

        self.pin = pin
        self.pull_up = pull_up
        self.hold_time = hold_time

        # State
        self._pressed: bool = False
        self._just_pressed: bool = False
        self._just_released: bool = False
        self._press_start: Optional[float] = None
        self._hold_fired: bool = False   # prevent repeated hold callbacks

        # Callbacks
        self._press_callbacks: List[Callable] = []
        self._release_callbacks: List[Callable] = []
        self._hold_callbacks: List[Callable] = []

        self._lock = threading.Lock()

        # Configure GPIO if available
        if _HAS_GPIO:
            GPIO.setmode(GPIO.BCM)
            pud = GPIO.PUD_UP if pull_up else GPIO.PUD_DOWN
            GPIO.setup(pin, GPIO.IN, pull_up_down=pud)

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def on_press(self, callback: Callable) -> None:
        """Register *callback* to be called when the button is pressed."""
        self._press_callbacks.append(callback)

    def on_release(self, callback: Callable) -> None:
        """Register *callback* to be called when the button is released."""
        self._release_callbacks.append(callback)

    def on_hold(self, callback: Callable) -> None:
        """Register *callback* to be called once when the button is held.

        The callback fires the first time the button has been continuously
        pressed for at least *hold_time* seconds.  It will not fire again
        until the button is released and pressed again.
        """
        self._hold_callbacks.append(callback)

    # ------------------------------------------------------------------
    # State properties
    # ------------------------------------------------------------------

    @property
    def pressed(self) -> bool:
        """``True`` if the button is currently pressed."""
        return self._pressed

    @property
    def just_pressed(self) -> bool:
        """``True`` if the button was pressed since the last :meth:`update`."""
        return self._just_pressed

    @property
    def just_released(self) -> bool:
        """``True`` if the button was released since the last :meth:`update`."""
        return self._just_released

    @property
    def held(self) -> bool:
        """``True`` if the button is pressed and has been held for at least
        *hold_time* seconds."""
        if not self._pressed or self._press_start is None:
            return False
        return (time.monotonic() - self._press_start) >= self.hold_time

    # ------------------------------------------------------------------
    # Update / polling
    # ------------------------------------------------------------------

    def _read_pin(self) -> bool:
        """Read the current physical state of the GPIO pin.

        Returns ``True`` when the button is *pressed*, accounting for
        active-low vs active-high wiring.
        """
        if not _HAS_GPIO:
            return False   # headless / no hardware
        raw = GPIO.input(self.pin)
        # Active-low (pull_up=True): pressed when pin is LOW (0)
        # Active-high (pull_up=False): pressed when pin is HIGH (1)
        return (raw == GPIO.LOW) if self.pull_up else (raw == GPIO.HIGH)

    def update(self, _inject_state=_UNSET) -> None:
        """Poll the GPIO pin and update internal state.

        Fires registered callbacks for press, release, and hold events.

        Parameters
        ----------
        _inject_state : bool, optional
            *Testing only.*  Pass a boolean to override the GPIO read so
            tests can exercise state transitions without real hardware.
        """
        with self._lock:
            if _inject_state is _UNSET:
                now_pressed = self._read_pin()
            else:
                now_pressed = bool(_inject_state)

            previously_pressed = self._pressed
            self._just_pressed = False
            self._just_released = False

            if now_pressed and not previously_pressed:
                # Rising edge (press)
                self._pressed = True
                self._just_pressed = True
                self._press_start = time.monotonic()
                self._hold_fired = False
                for cb in self._press_callbacks:
                    cb()

            elif not now_pressed and previously_pressed:
                # Falling edge (release)
                self._pressed = False
                self._just_released = True
                self._press_start = None
                self._hold_fired = False
                for cb in self._release_callbacks:
                    cb()

            # Hold check (fires once per continuous hold)
            if self._pressed and not self._hold_fired and self.held:
                self._hold_fired = True
                for cb in self._hold_callbacks:
                    cb()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Release the GPIO pin resource.

        Call when the button is no longer needed, or use as a context
        manager (``with Button(17) as btn:``) for automatic cleanup.
        """
        if _HAS_GPIO:
            GPIO.cleanup(self.pin)

    def __enter__(self) -> "Button":
        return self

    def __exit__(self, *_) -> None:
        self.cleanup()
