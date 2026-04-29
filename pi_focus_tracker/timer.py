"""
timer.py
========
Interactive countdown timer that drives two TextZone objects on an LCDDisplay
and responds to two push-buttons.

State machine
-------------

    RUNNING   -- pause_button press --> PAUSED
    PAUSED    -- pause_button press --> RUNNING
    PAUSED    -- end_button press   --> ENDED
    RUNNING   -- remaining == 0     --> COMPLETED
    COMPLETED -- any button press   --> ENDED

Display layout (2x16)
---------------------
- Row 0 ``timer_time``  -- remaining time as ``MM:SS``; ``MM:SS [P]`` when
  paused; ``DONE`` when completed.
- Row 1 ``timer_title`` -- session title (scrolling when longer than 6 chars).
"""

import threading
import time
from enum import Enum, auto
from typing import Optional

from .button import Button
from .display import LCDDisplay

_TITLE_ZONE = "timer_title"
_TIME_ZONE  = "timer_time"
_TIME_WIDTH = 9   # "MM:SS"
_TITLE_WIDTH = 6


class TimerState(Enum):
    RUNNING   = auto()
    PAUSED    = auto()
    COMPLETED = auto()
    ENDED     = auto()


class CountdownTimer:
    """Interactive countdown timer with title and remaining-time display zones.

    Parameters
    ----------
    title : str
        Label shown on the first row.  Scrolls automatically when longer
        than 16 characters.
    duration_seconds : int
        Total countdown duration.  Must be positive.
    display : LCDDisplay
        Pre-constructed display controller.  The zones ``timer_title`` (row 0)
        and ``timer_time`` (row 1) are created on construction; ensure both
        rows are free before instantiating.
    pause_button : Button
        Pauses the timer when it is running; resumes it when it is paused.
    end_button : Button
        Ends the session immediately, but *only* while the timer is paused.
        Also dismisses the DONE screen after the countdown completes.
    update_interval : float, optional
        Seconds between polling cycles.  Defaults to ``0.1``.
    """

    def __init__(
        self,
        title: str,
        duration_seconds: int,
        display: LCDDisplay,
        pause_button: Button,
        end_button: Button,
        update_interval: float = 0.1,
    ) -> None:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")

        self._title           = title
        self._duration        = duration_seconds
        self._display         = display
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
            _TITLE_ZONE,
            row=1, col=0, width=_TITLE_WIDTH,
            text=title,
            scrolling=len(title) > _TITLE_WIDTH,
        )
        display.add_zone(
            _TIME_ZONE,
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
        self._display.set_zone_text(_TIME_ZONE, self._format_time(self._duration))
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
                    self._display.set_zone_text(_TIME_ZONE, "DONE")
                else:
                    self._display.set_zone_text(
                        _TIME_ZONE, self._format_time(int(remaining))
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
        self._display.set_zone_text(_TIME_ZONE, f"{self._format_time(secs)} [P]")

    def _do_resume(self) -> None:
        if self._pause_start is not None:
            self._paused_duration += time.monotonic() - self._pause_start
            self._pause_start = None
        self._state = TimerState.RUNNING
        secs = max(0, int(self._remaining_float()))
        self._display.set_zone_text(_TIME_ZONE, self._format_time(secs))

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
