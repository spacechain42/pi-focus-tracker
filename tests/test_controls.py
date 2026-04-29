"""Tests for pi_focus_tracker.controls (Controls)."""

import subprocess
import threading
import time
import unittest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_button(pin=0):
    """Return a mock Button with controllable state attributes."""
    btn = MagicMock()
    btn.pin = pin
    btn.pressed = False
    btn.just_pressed = False
    btn.just_released = False
    btn.held = False
    btn.hold_time = 1.0
    btn.update.return_value = None
    return btn


class TestControlsConstruction(unittest.TestCase):

    def test_requires_non_empty_buttons(self):
        from pi_focus_tracker.controls import Controls
        with self.assertRaises(ValueError):
            Controls({})

    def test_construction(self):
        from pi_focus_tracker.controls import Controls
        btn = _make_mock_button()
        ctrl = Controls({"ok": btn})
        self.assertIsNotNone(ctrl)


class TestControlsRegisterPress(unittest.TestCase):

    def setUp(self):
        from pi_focus_tracker.controls import Controls
        self.ok  = _make_mock_button(pin=17)
        self.esc = _make_mock_button(pin=27)
        self.ctrl = Controls({"ok": self.ok, "esc": self.esc})
        self.events = []

    def _press(self, *names):
        """Simulate all named buttons being pressed in a single poll cycle."""
        for name in names:
            btn = {"ok": self.ok, "esc": self.esc}[name]
            btn.pressed = True
            btn.just_pressed = True
        self.ctrl.poll()
        # Reset just_pressed after poll
        for name in names:
            btn = {"ok": self.ok, "esc": self.esc}[name]
            btn.just_pressed = False

    def _release(self, *names):
        for name in names:
            btn = {"ok": self.ok, "esc": self.esc}[name]
            btn.pressed = False
            btn.just_pressed = False
            btn.just_released = True
        self.ctrl.poll()
        for name in names:
            btn = {"ok": self.ok, "esc": self.esc}[name]
            btn.just_released = False

    # ------------------------------------------------------------------

    def test_single_button_press(self):
        self.ctrl.register_press("ok", lambda: self.events.append("ok"))
        self._press("ok")
        self.assertIn("ok", self.events)

    def test_combo_press(self):
        self.ctrl.register_press(
            ["ok", "esc"], lambda: self.events.append("combo")
        )
        self._press("ok", "esc")
        self.assertIn("combo", self.events)

    def test_combo_does_not_fire_for_partial(self):
        self.ctrl.register_press(
            ["ok", "esc"], lambda: self.events.append("combo")
        )
        self._press("ok")   # only one button pressed
        self.assertNotIn("combo", self.events)

    def test_press_fires_only_on_new_press(self):
        self.ctrl.register_press("ok", lambda: self.events.append("ok"))
        self._press("ok")
        # Keep held, poll again without just_pressed
        self.ok.just_pressed = False
        self.ctrl.poll()
        self.assertEqual(self.events.count("ok"), 1)

    def test_unknown_button_raises(self):
        from pi_focus_tracker.controls import Controls
        with self.assertRaises(KeyError):
            self.ctrl.register_press("unknown", lambda: None)

    def test_multiple_actions_on_same_button(self):
        self.ctrl.register_press("ok", lambda: self.events.append("a1"))
        self.ctrl.register_press("ok", lambda: self.events.append("a2"))
        self._press("ok")
        self.assertIn("a1", self.events)
        self.assertIn("a2", self.events)


class TestControlsRegisterHold(unittest.TestCase):

    def setUp(self):
        from pi_focus_tracker.controls import Controls
        self.ok  = _make_mock_button(pin=17)
        self.esc = _make_mock_button(pin=27)
        self.ctrl = Controls({"ok": self.ok, "esc": self.esc})
        self.events = []

    def test_hold_fires_when_button_held(self):
        self.ctrl.register_hold("ok", lambda: self.events.append("hold"))
        self.ok.pressed = True
        self.ok.held = True
        self.ok.just_pressed = True
        self.ctrl.poll()
        self.assertIn("hold", self.events)

    def test_hold_fires_only_once_per_cycle(self):
        self.ctrl.register_hold("ok", lambda: self.events.append("hold"))
        self.ok.pressed = True
        self.ok.held = True
        self.ok.just_pressed = True
        self.ctrl.poll()
        self.ok.just_pressed = False
        self.ctrl.poll()   # still held, should not fire again
        self.assertEqual(self.events.count("hold"), 1)

    def test_hold_resets_after_release(self):
        self.ctrl.register_hold("ok", lambda: self.events.append("hold"))
        # First hold
        self.ok.pressed = True
        self.ok.held = True
        self.ok.just_pressed = True
        self.ctrl.poll()
        # Release
        self.ok.pressed = False
        self.ok.held = False
        self.ok.just_pressed = False
        self.ctrl.poll()
        # Press and hold again
        self.ok.pressed = True
        self.ok.held = True
        self.ok.just_pressed = True
        self.ctrl.poll()
        self.assertEqual(self.events.count("hold"), 2)

    def test_combo_hold(self):
        self.ctrl.register_hold(
            ["ok", "esc"], lambda: self.events.append("combo_hold")
        )
        self.ok.pressed  = True
        self.ok.held     = True
        self.esc.pressed = True
        self.esc.held    = True
        self.ok.just_pressed  = True
        self.esc.just_pressed = True
        self.ctrl.poll()
        self.assertIn("combo_hold", self.events)

    def test_combo_hold_partial_does_not_fire(self):
        self.ctrl.register_hold(
            ["ok", "esc"], lambda: self.events.append("combo_hold")
        )
        self.ok.pressed  = True
        self.ok.held     = True
        self.esc.pressed = False
        self.esc.held    = False
        self.ok.just_pressed = True
        self.ctrl.poll()
        self.assertNotIn("combo_hold", self.events)


class TestControlsExecute(unittest.TestCase):
    """Test the _execute helper directly."""

    def test_callable_action(self):
        from pi_focus_tracker.controls import Controls
        events = []
        btn = _make_mock_button()
        ctrl = Controls({"b": btn})
        ctrl._execute(lambda: events.append("fired"))
        self.assertIn("fired", events)

    def test_string_action_launches_subprocess(self):
        from pi_focus_tracker.controls import Controls
        btn = _make_mock_button()
        ctrl = Controls({"b": btn})
        with patch("pi_focus_tracker.controls.subprocess.Popen") as mock_popen:
            ctrl._execute("echo hello")
            mock_popen.assert_called_once_with(["echo", "hello"])


class TestControlsRunLoop(unittest.TestCase):

    def test_run_stops_on_stop_event(self):
        from pi_focus_tracker.controls import Controls
        btn = _make_mock_button(pin=17)
        ctrl = Controls({"b": btn}, poll_interval=0.01)
        stop_event = threading.Event()

        t = threading.Thread(target=ctrl.run, kwargs={"stop_event": stop_event})
        t.start()
        stop_event.set()
        t.join(timeout=1.0)
        self.assertFalse(t.is_alive())

    def test_start_and_stop(self):
        from pi_focus_tracker.controls import Controls
        btn = _make_mock_button(pin=17)
        ctrl = Controls({"b": btn}, poll_interval=0.01)
        ctrl.start()
        self.assertTrue(ctrl._running)
        ctrl.stop()
        self.assertFalse(ctrl._running)

    def test_start_idempotent(self):
        from pi_focus_tracker.controls import Controls
        btn = _make_mock_button(pin=17)
        ctrl = Controls({"b": btn}, poll_interval=0.01)
        ctrl.start()
        thread_before = ctrl._thread
        ctrl.start()   # second call is a no-op
        self.assertIs(ctrl._thread, thread_before)
        ctrl.stop()

    def test_stop_idempotent(self):
        from pi_focus_tracker.controls import Controls
        btn = _make_mock_button(pin=17)
        ctrl = Controls({"b": btn}, poll_interval=0.01)
        ctrl.stop()   # never started
        ctrl.stop()   # again — should not raise


class TestControlsContextManager(unittest.TestCase):

    def test_context_manager_stops_loop(self):
        from pi_focus_tracker.controls import Controls
        btn = _make_mock_button(pin=17)
        with Controls({"b": btn}, poll_interval=0.01) as ctrl:
            ctrl.start()
        self.assertFalse(ctrl._running)


if __name__ == "__main__":
    unittest.main()
