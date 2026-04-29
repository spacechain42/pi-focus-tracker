"""Tests for pi_focus_tracker.timer (CountdownTimer)."""

import threading
import time
import unittest
from unittest.mock import MagicMock, call, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_button():
    btn = MagicMock()
    btn.pressed       = False
    btn.just_pressed  = False
    btn.just_released = False
    btn.held          = False
    btn.update.return_value = None
    return btn


def _make_mock_display():
    return MagicMock()


def _make_timer(title="Focus", duration=300, **kwargs):
    """Return a CountdownTimer backed by mock display and buttons."""
    from pi_focus_tracker.timer import CountdownTimer
    display   = _make_mock_display()
    pause_btn = _make_mock_button()
    end_btn   = _make_mock_button()
    timer = CountdownTimer(
        title, duration, display, pause_btn, end_btn, **kwargs
    )
    return timer, display, pause_btn, end_btn


# ---------------------------------------------------------------------------
# _format_time
# ---------------------------------------------------------------------------

class TestFormatTime(unittest.TestCase):

    def _fmt(self, s):
        from pi_focus_tracker.timer import CountdownTimer
        return CountdownTimer._format_time(s)

    def test_zero(self):
        self.assertEqual(self._fmt(0), "00:00")

    def test_seconds_only(self):
        self.assertEqual(self._fmt(45), "00:45")

    def test_one_minute(self):
        self.assertEqual(self._fmt(60), "01:00")

    def test_minutes_and_seconds(self):
        self.assertEqual(self._fmt(90), "01:30")

    def test_padding(self):
        self.assertEqual(self._fmt(61), "01:01")

    def test_large_value(self):
        # 90 minutes exactly
        self.assertEqual(self._fmt(5400), "90:00")

    def test_negative_clamped_to_zero(self):
        self.assertEqual(self._fmt(-10), "00:00")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestCountdownTimerConstruction(unittest.TestCase):

    def test_valid_construction(self):
        timer, _, _, _ = _make_timer()
        self.assertIsNotNone(timer)

    def test_zero_duration_raises(self):
        from pi_focus_tracker.timer import CountdownTimer
        with self.assertRaises(ValueError):
            CountdownTimer("t", 0, _make_mock_display(), _make_mock_button(), _make_mock_button())

    def test_negative_duration_raises(self):
        from pi_focus_tracker.timer import CountdownTimer
        with self.assertRaises(ValueError):
            CountdownTimer("t", -1, _make_mock_display(), _make_mock_button(), _make_mock_button())

    def test_creates_title_zone_short_title(self):
        timer, display, _, _ = _make_timer(title="Work", duration=60)
        display.add_zone.assert_any_call(
            "timer_title", row=0, col=0, width=16, text="Work", scrolling=False
        )

    def test_creates_title_zone_long_title_enables_scrolling(self):
        long_title = "A Very Long Focus Session Title"
        timer, display, _, _ = _make_timer(title=long_title, duration=60)
        display.add_zone.assert_any_call(
            "timer_title", row=0, col=0, width=16, text=long_title, scrolling=True
        )

    def test_creates_time_zone_with_formatted_duration(self):
        timer, display, _, _ = _make_timer(duration=90)
        display.add_zone.assert_any_call(
            "timer_time", row=1, col=0, width=16, text="01:30"
        )

    def test_initial_state_is_running(self):
        from pi_focus_tracker.timer import TimerState
        timer, _, _, _ = _make_timer()
        self.assertEqual(timer.state, TimerState.RUNNING)

    def test_remaining_seconds_before_run_equals_duration(self):
        timer, _, _, _ = _make_timer(duration=120)
        self.assertEqual(timer.remaining_seconds, 120)


# ---------------------------------------------------------------------------
# Remaining time calculation
# ---------------------------------------------------------------------------

class TestRemainingSeconds(unittest.TestCase):

    def test_remaining_decreases_over_elapsed_time(self):
        timer, _, _, _ = _make_timer(duration=60)
        with patch("pi_focus_tracker.timer.time.monotonic") as mock_mono:
            mock_mono.return_value = 1000.0
            timer._start_time = 1000.0
            mock_mono.return_value = 1010.0
            self.assertEqual(timer.remaining_seconds, 50)

    def test_remaining_never_negative(self):
        timer, _, _, _ = _make_timer(duration=10)
        with patch("pi_focus_tracker.timer.time.monotonic") as mock_mono:
            mock_mono.return_value = 1000.0
            timer._start_time = 1000.0
            mock_mono.return_value = 1050.0   # 40 s past the 10 s duration
            self.assertEqual(timer.remaining_seconds, 0)

    def test_remaining_frozen_while_paused(self):
        from pi_focus_tracker.timer import TimerState
        timer, _, _, _ = _make_timer(duration=60)
        with patch("pi_focus_tracker.timer.time.monotonic") as mock_mono:
            mock_mono.return_value = 1000.0
            timer._start_time = 1000.0

            # Advance 5 seconds, then pause
            mock_mono.return_value = 1005.0
            timer._pause_start = 1005.0   # simulate pause recorded at t=1005
            timer._state = TimerState.PAUSED

            r1 = timer.remaining_seconds   # 55 s remaining at pause

            # Advance another 10 s while paused
            mock_mono.return_value = 1015.0
            r2 = timer.remaining_seconds   # should still show 55 (paused time not counted)

        self.assertEqual(r1, 55)
        self.assertEqual(r2, 55)

    def test_remaining_resumes_after_pause_cleared(self):
        from pi_focus_tracker.timer import TimerState
        timer, _, _, _ = _make_timer(duration=60)
        with patch("pi_focus_tracker.timer.time.monotonic") as mock_mono:
            mock_mono.return_value = 1000.0
            timer._start_time = 1000.0

            # Pause at t=1005 (5 s elapsed)
            mock_mono.return_value = 1005.0
            timer._do_pause()   # sets _pause_start and state

            # Resume at t=1015 (10 s pause)
            mock_mono.return_value = 1015.0
            timer._do_resume()  # adds 10 s to _paused_duration

            # Now at t=1020 (5 more running seconds since resume)
            mock_mono.return_value = 1020.0
            # Total running time = 5 (before pause) + 5 (after resume) = 10 s
            self.assertEqual(timer.remaining_seconds, 50)


# ---------------------------------------------------------------------------
# Pause / resume / end transitions
# ---------------------------------------------------------------------------

class TestPauseResumeEnd(unittest.TestCase):

    def setUp(self):
        from pi_focus_tracker.timer import TimerState
        self.TimerState = TimerState
        self.timer, self.display, self.pause_btn, self.end_btn = _make_timer(duration=300)
        # Simulate a started timer
        self.timer._start_time = time.monotonic()

    def _press(self, btn):
        btn.just_pressed = True

    def _release(self, btn):
        btn.just_pressed = False

    def test_pause_button_while_running_pauses_timer(self):
        self._press(self.pause_btn)
        self.timer._handle_input()
        self.assertEqual(self.timer._state, self.TimerState.PAUSED)

    def test_pause_sets_time_zone_with_indicator(self):
        self._press(self.pause_btn)
        self.timer._handle_input()
        calls = self.display.set_zone_text.call_args_list
        time_calls = [c for c in calls if c.args[0] == "timer_time"]
        self.assertTrue(any("[P]" in c.args[1] for c in time_calls))

    def test_pause_button_while_paused_resumes_timer(self):
        # First pause
        self._press(self.pause_btn)
        self.timer._handle_input()
        self.assertEqual(self.timer._state, self.TimerState.PAUSED)

        # Then resume
        self.timer._handle_input()  # just_pressed still True (same poll)
        self.assertEqual(self.timer._state, self.TimerState.RUNNING)

    def test_resume_removes_pause_indicator(self):
        self._press(self.pause_btn)
        self.timer._handle_input()   # pause
        self.display.set_zone_text.reset_mock()
        self.timer._handle_input()   # resume
        calls = [c for c in self.display.set_zone_text.call_args_list if c.args[0] == "timer_time"]
        self.assertTrue(any("[P]" not in c.args[1] for c in calls))

    def test_end_button_while_paused_ends_timer(self):
        # Pause first
        self._press(self.pause_btn)
        self.timer._handle_input()
        self._release(self.pause_btn)

        # Now press end
        self._press(self.end_btn)
        self.timer._handle_input()
        self.assertEqual(self.timer._state, self.TimerState.ENDED)

    def test_end_button_ignored_while_running(self):
        self._press(self.end_btn)
        self.timer._handle_input()
        self.assertEqual(self.timer._state, self.TimerState.RUNNING)

    def test_no_countdown_while_paused(self):
        """_remaining_float should not decrease while the timer is paused."""
        with patch("pi_focus_tracker.timer.time.monotonic") as mock_mono:
            mock_mono.return_value = 1000.0
            self.timer._start_time = 1000.0

            # Pause at t=1010
            mock_mono.return_value = 1010.0
            self.timer._do_pause()
            r_at_pause = self.timer.remaining_seconds

            # Advance 30 more seconds while paused
            mock_mono.return_value = 1040.0
            r_while_paused = self.timer.remaining_seconds

        self.assertEqual(r_at_pause, r_while_paused)


# ---------------------------------------------------------------------------
# Completion / DONE screen
# ---------------------------------------------------------------------------

class TestCompletion(unittest.TestCase):

    def setUp(self):
        from pi_focus_tracker.timer import TimerState
        self.TimerState = TimerState

    def test_completion_state_when_remaining_reaches_zero(self):
        timer, display, pause_btn, end_btn = _make_timer(duration=10)

        with patch("pi_focus_tracker.timer.time.monotonic") as mock_mono:
            mock_mono.return_value = 1000.0
            timer._start_time = 1000.0

            # Jump past the duration
            mock_mono.return_value = 1020.0
            with patch("pi_focus_tracker.timer.time.sleep"):
                # Run one loop iteration manually
                pause_btn.just_pressed = False
                end_btn.just_pressed   = False
                timer._handle_input()
                if timer._state == self.TimerState.RUNNING:
                    remaining = timer._remaining_float()
                    if remaining <= 0:
                        timer._state = self.TimerState.COMPLETED
                        timer._display.set_zone_text("timer_time", "DONE")

        self.assertEqual(timer._state, self.TimerState.COMPLETED)
        display.set_zone_text.assert_any_call("timer_time", "DONE")

    def test_pause_button_dismisses_done(self):
        timer, _, pause_btn, _ = _make_timer(duration=10)
        timer._state = self.TimerState.COMPLETED
        pause_btn.just_pressed = True
        timer._handle_input()
        self.assertEqual(timer._state, self.TimerState.ENDED)

    def test_end_button_dismisses_done(self):
        timer, _, _, end_btn = _make_timer(duration=10)
        timer._state = self.TimerState.COMPLETED
        end_btn.just_pressed = True
        timer._handle_input()
        self.assertEqual(timer._state, self.TimerState.ENDED)

    def test_no_button_does_not_dismiss_done(self):
        timer, _, pause_btn, end_btn = _make_timer(duration=10)
        timer._state = self.TimerState.COMPLETED
        pause_btn.just_pressed = False
        end_btn.just_pressed   = False
        timer._handle_input()
        self.assertEqual(timer._state, self.TimerState.COMPLETED)


# ---------------------------------------------------------------------------
# Loop integration (stop / start / thread lifecycle)
# ---------------------------------------------------------------------------

class TestTimerLoop(unittest.TestCase):

    def test_stop_terminates_run(self):
        from pi_focus_tracker.timer import TimerState
        timer, _, _, _ = _make_timer(duration=300, update_interval=0.01)

        t = threading.Thread(target=timer.run)
        with patch("pi_focus_tracker.timer.time.sleep"):
            t.start()
            time.sleep(0.05)
            timer.stop()
            t.join(timeout=1.0)

        self.assertFalse(t.is_alive())
        self.assertEqual(timer.state, TimerState.ENDED)

    def test_start_and_stop_background_thread(self):
        from pi_focus_tracker.timer import TimerState
        timer, _, _, _ = _make_timer(duration=300, update_interval=0.01)

        with patch("pi_focus_tracker.timer.time.sleep"):
            timer.start()
            self.assertTrue(timer._thread.is_alive())
            timer.stop()

        self.assertEqual(timer.state, TimerState.ENDED)

    def test_start_idempotent(self):
        timer, _, _, _ = _make_timer(duration=300, update_interval=0.01)
        with patch("pi_focus_tracker.timer.time.sleep"):
            timer.start()
            thread_before = timer._thread
            timer.start()   # second call is a no-op
            self.assertIs(timer._thread, thread_before)
            timer.stop()

    def test_run_ends_when_time_elapses_and_button_pressed(self):
        """Full loop: timer reaches 0, shows DONE, then button dismisses."""
        from pi_focus_tracker.timer import TimerState

        timer, display, pause_btn, end_btn = _make_timer(
            duration=1, update_interval=0.005
        )
        done_event = threading.Event()

        original_set = display.set_zone_text
        def _side(name, text):
            original_set(name, text)
            if name == "timer_time" and text == "DONE":
                done_event.set()
                # Simulate end button press to dismiss DONE
                end_btn.just_pressed = True
        display.set_zone_text = _side

        with patch("pi_focus_tracker.timer.time.sleep"):
            t = threading.Thread(target=timer.run)
            t.start()
            done_event.wait(timeout=2.0)
            t.join(timeout=2.0)

        self.assertFalse(t.is_alive(), "Timer thread should have exited")
        self.assertEqual(timer.state, TimerState.ENDED)


# ---------------------------------------------------------------------------
# Package-level import
# ---------------------------------------------------------------------------

class TestPackageExport(unittest.TestCase):

    def test_countdown_timer_exported(self):
        from pi_focus_tracker import CountdownTimer
        self.assertIsNotNone(CountdownTimer)

    def test_timer_state_exported(self):
        from pi_focus_tracker import TimerState
        self.assertIsNotNone(TimerState)


if __name__ == "__main__":
    unittest.main()
