"""
timer.py
========
Interactive countdown timer that drives a single TextZone object on an LCDDisplay
and responds to two push-buttons.
    
State machine
-------------

    RUNNING   -- pause_button press --> PAUSED
    PAUSED    -- pause_button press --> RUNNING
    PAUSED    -- end_button press   --> ENDED
    RUNNING   -- remaining == 0     --> COMPLETED
    COMPLETED -- any button press   --> ENDED
"""

import threading
import time
from enum import Enum, auto
from typing import Optional

from .button import Button
from .display import LCDDisplay

_TIME_WIDTH = 9   # "MM:SS [p]"


class TimerState(Enum):
    RUNNING   = auto()
    PAUSED    = auto()
    COMPLETED = auto()
    ENDED     = auto()


class CountdownTimer:
    """Interactive countdown timer with a single display zone.

    Args:
        display: Pre-constructed display controller. The zone specified by
            ``zone_name`` is created on construction; ensure the row is free
            before instantiating.
        zone_name: Name of the display zone for the timer.
        duration_seconds: Total countdown duration. Must be positive.
        pause_button: Pauses the timer when it is running; resumes it when it
            is paused.
        end_button: Ends the session immediately, but *only* while the timer
            is paused. Also dismisses the DONE screen after the countdown
            completes.
        update_interval: Seconds between polling cycles. Defaults to ``0.1``.
    """

    def __init__(
        self,
        display: LCDDisplay,
        zone_name: str,
        duration_seconds: int,
        pause_button: Button,
        end_button: Button,
        update_interval: float = 0.1,
    ) -> None:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")

        self._duration        = duration_seconds
        self._display         = display
        self._zone_name       = zone_name
        self._pause_btn       = pause_button
        self._end_btn         = end_button
        self._update_interval = update_interval

        self._state      = TimerState.RUNNING
        self._state_lock = threading.Lock()

        # Monotonic timing
        self._start_time:      float           = 0.0
        self._paused_duration: float           = 0.0
        self._pause_start:     Optional[float] = None

        self._stop_event = threading.Event()
        self._thread:    Optional[threading.Thread] = None

        # Register display zones
        display.add_zone(
            self._zone_name,
            row=0, col=0, width=_TIME_WIDTH,
            text=self._format_time(duration_seconds),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> TimerState:
        """Current timer state (safe to read from any thread)."""
        with self._state_lock:
            return self._state

    @property
    def remaining_seconds(self) -> int:
        """Whole seconds remaining in the countdown (never negative)."""
        return max(0, int(self._remaining_float()))

    def run(self) -> None:
        """Block the calling thread until the timer ends."""
        self._start_time      = time.monotonic()
        self._paused_duration = 0.0
        self._pause_start     = None
        with self._state_lock:
            self._state = TimerState.RUNNING
        self._display.set_zone_text(self._zone_name, self._format_time(self._duration))
        self._stop_event.clear()

        try:
            self._loop()
        finally:
            with self._state_lock:
                self._state = TimerState.ENDED

    def start(self) -> None:
        """Start the timer in a background daemon thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the timer to stop and wait for the thread to exit."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None

    # ------------------------------------------------------------------
    # Internal loop
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            # 1. Poll physical button state
            self._pause_btn.update()
            self._end_btn.update()

            # 2. React to button input (may change _state)
            self._handle_input()

            # 3. Exit if the session was ended by input or stop()
            if self._state == TimerState.ENDED:
                break

            # 4. Update the time zone for the current state
            if self._state == TimerState.RUNNING:
                remaining = self._remaining_float()
                if remaining <= 0:
                    self._state = TimerState.COMPLETED
                    self._display.set_zone_text(self._zone_name, "DONE")
                else:
                    self._display.set_zone_text(
                        self._zone_name, self._format_time(int(remaining))
                    )
            # PAUSED:    display was set by _do_pause / _do_resume; no update.
            # COMPLETED: display already shows DONE; loop waits for button press.

            time.sleep(self._update_interval)

    def _handle_input(self) -> None:
        """Translate button presses into state transitions."""
        state = self._state   # single-threaded read inside the loop

        if state == TimerState.RUNNING:
            if self._pause_btn.just_pressed:
                self._do_pause()

        elif state == TimerState.PAUSED:
            if self._pause_btn.just_pressed:
                self._do_resume()
            elif self._end_btn.just_pressed:
                self._state = TimerState.ENDED

        elif state == TimerState.COMPLETED:
            if self._pause_btn.just_pressed or self._end_btn.just_pressed:
                self._state = TimerState.ENDED

    def _do_pause(self) -> None:
        self._pause_start = time.monotonic()
        self._state = TimerState.PAUSED
        secs = max(0, int(self._remaining_float()))
        self._display.set_zone_text(self._zone_name, f"{self._format_time(secs)} [P]")

    def _do_resume(self) -> None:
        if self._pause_start is not None:
            self._paused_duration += time.monotonic() - self._pause_start
            self._pause_start = None
        self._state = TimerState.RUNNING
        secs = max(0, int(self._remaining_float()))
        self._display.set_zone_text(self._zone_name, self._format_time(secs))

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------

    def _remaining_float(self) -> float:
        """Effective remaining seconds, excluding all time spent paused."""
        if self._start_time == 0.0:
            return float(self._duration)
        now     = time.monotonic()
        elapsed = now - self._start_time - self._paused_duration
        if self._pause_start is not None:
            elapsed -= now - self._pause_start
        return self._duration - elapsed

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_time(seconds: int) -> str:
        """Format *seconds* as zero-padded MM:SS."""
        seconds = max(0, seconds)
        minutes, secs = divmod(seconds, 60)
        return f"{minutes:02d}:{secs:02d}"
